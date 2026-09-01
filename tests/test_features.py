"""Testy nowych funkcji. Uruchom: python -m tests.test_features"""
import io
import json
import os

os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("DATA_DIR", "/tmp/kombajn-data")

from PIL import Image  # noqa: E402

from app import auth, credits, db, jobs, pipeline  # noqa: E402
from app.crypto import encrypt, generate_key  # noqa: E402
from app.prestashop import filter_scan_ids  # noqa: E402

FERNET = os.environ["FERNET_KEY"] = generate_key()


def test_scan_modes():
    # (id, dlugosc opisu) — 10,30,50 wymagaja opisu (<500), 20,40 maja opisy
    items = [(10, 0), (20, 900), (30, 120), (40, 800), (50, 0)]
    assert filter_scan_ids(items, "newest", None, 500, 50) == [50, 30, 10]
    assert filter_scan_ids(items, "oldest", None, 500, 50) == [10, 30, 50]
    assert filter_scan_ids(items, "from_id_down", 30, 500, 50) == [30, 10]
    assert filter_scan_ids(items, "from_id_up", 30, 500, 50) == [30, 50]
    assert filter_scan_ids(items, "newest", None, 500, 2) == [50, 30], "limit dziala"
    print("  [ok] skan: newest/oldest/od-ID-w-dol/od-ID-w-gore + limit")


def _sample_jpeg(w=2400, h=1600) -> bytes:
    img = Image.new("RGB", (w, h), (180, 40, 60))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_image_job_preview():
    with db.connection() as c:
        c.execute("TRUNCATE users, prompt_history, jobs, credit_ledger, shops, tenant_credits, tenants RESTART IDENTITY CASCADE")
        tid = c.execute("INSERT INTO tenants (name) VALUES ('T') RETURNING id").fetchone()["id"]
        credits.topup(c, tid, 5)
        c.commit()

        os.makedirs("/tmp/kombajn-data/incoming/1", exist_ok=True)
        src = "/tmp/kombajn-data/incoming/1/test.jpg"
        with open(src, "wb") as f:
            f.write(_sample_jpeg())

        jid = jobs.enqueue(c, tid, None, None, job_type="image",
                           payload={"input_path": src, "orig_name": "kolczyki.jpg"})
        c.commit()
        job = jobs.claim(c, "w1", 600); c.commit()
        pipeline.run_image_job(c, job, shop_prompt_image="", openai_key="",
                               data_dir="/tmp/kombajn-data")

        row = c.execute("SELECT status, result FROM jobs WHERE id=%s", (jid,)).fetchone()
        assert row["status"] == "done", row["status"]
        out = row["result"]["output_path"]
        assert os.path.exists(out)
        img = Image.open(out)
        assert img.size == (1500, 1500), img.size
        assert os.path.getsize(out) / 1024 <= 300
        assert row["result"]["_preview"] is True
        assert credits.get_balance(c, tid) == 5, "podglad zdjecia = 0 kredytow"
    print("  [ok] zdjecie: normalizacja -> kanwa 1500x1500 -> JPEG<=300KB, podglad 0 kredytow")


def test_panel_pages():
    from fastapi.testclient import TestClient
    from app.web import app
    client = TestClient(app)

    with db.connection() as c:
        c.execute("TRUNCATE users, prompt_history, jobs, credit_ledger, shops, tenant_credits, tenants RESTART IDENTITY CASCADE")
        tid = c.execute("INSERT INTO tenants (name) VALUES ('Merebilo') RETURNING id").fetchone()["id"]
        credits.topup(c, tid, 10)
        sid = c.execute(
            "INSERT INTO shops (tenant_id, base_url, auth_key_encrypted, prompt) VALUES (%s,%s,%s,%s) RETURNING id",
            (tid, "https://test.merebilo.eu", encrypt("k", FERNET), "P"),
        ).fetchone()["id"]
        auth.create_user(c, tid, "t@m.pl", "haslo123")
        c.commit()

    csrf = client.get("/login").text.split('name="csrf_token" value="')[1].split('"')[0]
    client.post("/login", data={"email": "t@m.pl", "password": "haslo123", "csrf_token": csrf})

    r = client.get("/images")
    assert r.status_code == 200 and "obróbki" in r.text.lower()
    r = client.get("/generate")
    assert r.status_code == 200 and "od najnowszych" in r.text

    # upload zdjecia przez formularz
    page = client.get("/images")
    csrf2 = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = client.post("/images/upload",
                    files=[("files", ("foto.jpg", _sample_jpeg(800, 600), "image/jpeg"))],
                    data={"csrf_token": csrf2}, follow_redirects=False)
    assert r.status_code == 303 and "Przyjeto+1" in r.headers["location"]
    with db.connection() as c:
        n = c.execute("SELECT count(*) n FROM jobs WHERE type='image'").fetchone()["n"]
        assert n == 1
    print("  [ok] panel: strony Zdjecia i Opisy dzialaja, upload tworzy zadanie")

    # zapis czterech promptow w ustawieniach
    page = client.get(f"/shop/{sid}")
    csrf3 = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = client.post(f"/shop/{sid}", data={
        "prompt": "OPIS", "prompt_analysis": "ANALIZA", "prompt_meta": "META",
        "prompt_image": "ZDJECIA", "base_url": "https://test.merebilo.eu",
        "params_json": "{}", "new_auth_key": "", "csrf_token": csrf3,
    }, follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    with db.connection() as c:
        s = c.execute("SELECT prompt, prompt_analysis, prompt_meta, prompt_image FROM shops WHERE id=%s", (sid,)).fetchone()
        assert (s["prompt"], s["prompt_analysis"], s["prompt_meta"], s["prompt_image"]) == ("OPIS", "ANALIZA", "META", "ZDJECIA")
        hist = c.execute("SELECT count(*) n FROM prompt_history WHERE shop_id=%s", (sid,)).fetchone()["n"]
        assert hist >= 1
    print("  [ok] ustawienia: cztery prompty zapisane, historia dziala")


def test_retry_held_jobs():
    """Zadania 'held' i 'failed' wracaja do kolejki po kliknieciu Wznow."""
    from fastapi.testclient import TestClient
    from app.web import app
    client = TestClient(app)

    with db.connection() as c:
        c.execute("TRUNCATE users, prompt_history, jobs, credit_ledger, shops, "
                  "tenant_credits, tenants RESTART IDENTITY CASCADE")
        tid = c.execute("INSERT INTO tenants (name) VALUES ('T') RETURNING id").fetchone()["id"]
        credits.topup(c, tid, 10)
        sid = c.execute("INSERT INTO shops (tenant_id, base_url, auth_key_encrypted) "
                        "VALUES (%s,%s,%s) RETURNING id",
                        (tid, "https://test.merebilo.eu", encrypt("k", FERNET))).fetchone()["id"]
        auth.create_user(c, tid, "r@m.pl", "haslo123")

        held = jobs.enqueue(c, tid, sid, "1", payload={"product_id": 1})
        failed = jobs.enqueue(c, tid, sid, "2", payload={"product_id": 2})
        done = jobs.enqueue(c, tid, sid, "3", payload={"product_id": 3})
        c.execute("UPDATE jobs SET status='held', last_error='brak kredytow', attempts=3 WHERE id=%s", (held,))
        c.execute("UPDATE jobs SET status='failed', attempts=3 WHERE id=%s", (failed,))
        c.execute("UPDATE jobs SET status='done' WHERE id=%s", (done,))
        c.commit()

    csrf = client.get("/login").text.split('name="csrf_token" value="')[1].split('"')[0]
    client.post("/login", data={"email": "r@m.pl", "password": "haslo123", "csrf_token": csrf})

    page = client.get("/generate")
    assert "Wznów wstrzymane" in page.text
    csrf2 = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = client.post("/jobs/retry", data={"job_type": "description", "csrf_token": csrf2},
                    follow_redirects=False)
    assert "Wznowiono+2" in r.headers["location"], r.headers["location"]

    with db.connection() as c:
        rows = {r["id"]: r for r in c.execute(
            "SELECT id, status, attempts, last_error FROM jobs ORDER BY id").fetchall()}
        assert rows[held]["status"] == "pending" and rows[held]["attempts"] == 0
        assert rows[held]["last_error"] is None
        assert rows[failed]["status"] == "pending"
        assert rows[done]["status"] == "done", "ukonczone zadania nietkniete"
    print("  [ok] wznawianie: held i failed -> pending (licznik prob wyzerowany), done nietkniete")


if __name__ == "__main__":
    db.init_pool(os.environ["DATABASE_URL"])
    print("Test nowych funkcji:")
    test_scan_modes()
    test_image_job_preview()
    test_panel_pages()
    test_retry_held_jobs()
    print("\nWszystko zielone.")


