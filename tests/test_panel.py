"""Test panelu przez TestClient (bez sieci). Uruchom: python -m tests.test_panel"""
import json
import os

os.environ.setdefault("SESSION_SECRET", "test-secret-do-testow")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, credits, db  # noqa: E402
from app.crypto import encrypt, generate_key  # noqa: E402

FERNET = os.environ["FERNET_KEY"] = generate_key()


def seed():
    with db.connection() as c:
        c.execute("TRUNCATE users, prompt_history, jobs, credit_ledger, shops, tenant_credits, tenants RESTART IDENTITY CASCADE")
        tid = c.execute("INSERT INTO tenants (name) VALUES ('Merebilo') RETURNING id").fetchone()["id"]
        credits.topup(c, tid, 42)
        enc = encrypt("tajny-klucz-ws", FERNET)
        sid = c.execute(
            "INSERT INTO shops (tenant_id, base_url, auth_key_encrypted, prompt, params) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (tid, "https://sklep.merebilo.eu", enc, "STARY PROMPT", json.dumps({"jezyk": "pl"})),
        ).fetchone()["id"]
        auth.create_user(c, tid, "ty@merebilo.pl", "haslo123")
        c.commit()
        return tid, sid


def run():
    from app.web import app  # import po ustawieniu env
    client = TestClient(app)

    tid, sid = seed()

    # 1. bez logowania dashboard przekierowuje na /login
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login", r.status_code
    print("  [ok] ochrona: bez sesji dashboard -> /login")

    # 2. zle haslo -> odbicie z bledem
    login = client.get("/login")
    csrf = login.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = client.post("/login", data={"email": "ty@merebilo.pl", "password": "zle", "csrf_token": csrf}, follow_redirects=False)
    assert "error=" in r.headers["location"], r.headers["location"]
    print("  [ok] logowanie: zle haslo odrzucone")

    # 3. poprawne logowanie
    r = client.post("/login", data={"email": "ty@merebilo.pl", "password": "haslo123", "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/", r.headers["location"]
    print("  [ok] logowanie: poprawne dane -> panel")

    # 4. dashboard pokazuje saldo i sklep
    r = client.get("/")
    assert "42" in r.text and "sklep.merebilo.eu" in r.text
    print("  [ok] dashboard: saldo 42 i sklep widoczne")

    # 5. zapis nowego promptu
    shop_page = client.get(f"/shop/{sid}")
    assert "STARY PROMPT" in shop_page.text
    csrf2 = shop_page.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = client.post(f"/shop/{sid}", data={
        "prompt": "NOWY PROMPT B2B",
        "base_url": "https://sklep.merebilo.eu",
        "params_json": '{"jezyk": "pl", "ton": "B2B"}',
        "new_auth_key": "",
        "csrf_token": csrf2,
    }, follow_redirects=False)
    assert r.status_code == 303 and "saved=1" in r.headers["location"]

    with db.connection() as c:
        shop = c.execute("SELECT prompt, params FROM shops WHERE id=%s", (sid,)).fetchone()
        assert shop["prompt"] == "NOWY PROMPT B2B", shop["prompt"]
        assert shop["params"]["ton"] == "B2B"
        hist = c.execute("SELECT prompt FROM prompt_history WHERE shop_id=%s", (sid,)).fetchone()
        assert "STARY PROMPT" in hist["prompt"], "poprzednia wersja powinna trafic do historii"
    print("  [ok] zapis: prompt zmieniony, params zapisane, stara wersja w historii")

    # 6. izolacja — cudzy sklep (id nieistniejacy dla najemcy) -> redirect na /
    r = client.get("/shop/9999", follow_redirects=False)
    assert r.headers["location"] == "/", r.headers["location"]
    print("  [ok] izolacja: brak dostepu do nie-swojego sklepu")


if __name__ == "__main__":
    db.init_pool(os.environ["DATABASE_URL"])
    print("Test panelu Kombajn:")
    run()
    print("\nWszystko zielone.")
