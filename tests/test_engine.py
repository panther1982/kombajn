"""Test silnika bez sieci. Uruchom: python -m tests.test_engine"""
import json

from app import db, jobs, credits, pipeline
from app.crypto import encrypt, generate_key

FERNET = generate_key()


def reset(conn):
    conn.execute("TRUNCATE jobs, credit_ledger, shops, tenant_credits, tenants RESTART IDENTITY CASCADE")
    conn.commit()


def seed(conn, credits_amount=5):
    tid = conn.execute("INSERT INTO tenants (name) VALUES ('Merebilo') RETURNING id").fetchone()["id"]
    credits.topup(conn, tid, credits_amount)
    enc = encrypt("dummy-webservice-key", FERNET)
    sid = conn.execute(
        "INSERT INTO shops (tenant_id, base_url, auth_key_encrypted) VALUES (%s,%s,%s) RETURNING id",
        (tid, "https://sklep.merebilo.eu", enc),
    ).fetchone()["id"]
    conn.commit()
    return tid, sid


def test_skip_locked():
    """Dwa rownolegle claim() musza dostac ROZNE zadania."""
    with db.connection() as c:
        reset(c); tid, sid = seed(c)
        jobs.enqueue(c, tid, sid, "111", payload={"product_id": 111})
        jobs.enqueue(c, tid, sid, "222", payload={"product_id": 222})
        c.commit()

    # dwa niezalezne polaczenia = dwa workery
    with db.connection() as w1, db.connection() as w2:
        j1 = jobs.claim(w1, "worker-A", 600)
        j2 = jobs.claim(w2, "worker-B", 600)
        assert j1 and j2 and j1["id"] != j2["id"], "double-claim!"
        assert j1["locked_by"] == "worker-A" and j2["locked_by"] == "worker-B"
        w1.commit(); w2.commit()
    print("  [ok] SKIP LOCKED: dwa workery, dwa rozne zadania, brak kolizji")


def test_credits_atomic():
    with db.connection() as c:
        reset(c); tid, sid = seed(c, credits_amount=2)
        assert credits.get_balance(c, tid) == 2
        credits.charge(c, tid, 1, "charge:description")
        c.commit()
        assert credits.get_balance(c, tid) == 1
        try:
            credits.charge(c, tid, 5, "charge:description")
            assert False, "powinno rzucic InsufficientCredits"
        except credits.InsufficientCredits:
            c.rollback()
        ledger = c.execute("SELECT count(*) n FROM credit_ledger WHERE tenant_id=%s", (tid,)).fetchone()["n"]
        assert ledger == 2, f"ksiega: {ledger}"  # topup + 1 charge
    print("  [ok] kredyty: pobranie atomowe, blokada ponizej zera, wpis w ksiedze")


def test_orphan_recovery():
    """Job 'running' po padnietym workerze wraca do puli po timeoucie."""
    with db.connection() as c:
        reset(c); tid, sid = seed(c)
        jid = jobs.enqueue(c, tid, sid, "333", payload={"product_id": 333})
        # symuluj workera, ktory przejal joba i padl 20 min temu
        c.execute("UPDATE jobs SET status='running', locked_by='dead', "
                  "locked_at = now() - interval '20 minutes' WHERE id=%s", (jid,))
        c.commit()
        # nowy worker z timeoutem 600s musi go przejac
        j = jobs.claim(c, "worker-new", 600)
        c.commit()
        assert j and j["id"] == jid and j["locked_by"] == "worker-new"
    print("  [ok] wznawianie: osierocony job przejety po timeoucie (koniec zacinajacego sie locka)")


def test_full_description_job(monkeypatch_target):
    """Pelny job 'description': tryb podgladu (bez klucza) ORAZ pelny zapis."""
    calls = {}

    class FakePS:
        def __init__(self, *a, **k): pass
        def get_product_for_generation(self, pid):
            return {"id": pid, "name": f"Produkt {pid}", "reference": f"REF{pid}",
                    "description": "", "description_short": "", "manufacturer_name": "",
                    "image_id": "", "raw_xml": "<x/>"}
        def download_product_image(self, pid, iid): return b""
        def update_product_texts(self, pid, fields, raw_xml=None):
            calls["pid"] = pid; calls["fields"] = fields
            return {}   # brak zmian w polach krytycznych
        def close(self): pass

    monkeypatch_target(pipeline, "PrestaShopClient", FakePS, submodule="app.prestashop")

    # A) tryb podgladu: brak klucza -> wynik zapisany do joba, sklep NIETKNIETY, 0 kredytow
    with db.connection() as c:
        reset(c); tid, sid = seed(c, credits_amount=3)
        jid = jobs.enqueue(c, tid, sid, "444", payload={"product_id": 444})
        c.commit()
        job = jobs.claim(c, "worker-1", 600); c.commit()
        shop = c.execute("SELECT * FROM shops WHERE id=%s", (sid,)).fetchone()
        pipeline.run_description_job(c, job, dict(shop), auth_key="dummy",
                                     api_key="", write_enabled=False)
        row = c.execute("SELECT status, result FROM jobs WHERE id=%s", (jid,)).fetchone()
        assert row["status"] == "held", row["status"]
        assert row["result"].get("meta_title"), "podglad powinien miec meta_title"
        assert "alt" in row["result"], "podglad powinien miec ALT"
        assert "pid" not in calls, "w podgladzie sklep ma byc NIETKNIETY"
        assert credits.get_balance(c, tid) == 3, "podglad = 0 kredytow"
    print("  [ok] podglad: wynik (opis+meta+ALT) w jobie, sklep nietkniety, 0 kredytow")

    # B) pelny zapis: klucz + write_enabled (podmieniamy generator na pseudo-realny)
    import app.ai_gateway as gw
    real_single = gw.generate_description_single
    def fake_single(product, prompt, image_bytes, api_key):
        # wynik zgodny z regulami produkcyjnego promptu: tylko <p>/<ul>/<li>
        body = "<p>" + "Opis produktu w tonie B2B. " * 20 + "</p><ul><li>cecha</li></ul>"
        return gw.Generation(fields={"description": body,
                                     "description_short": "Krotki opis produktu.",
                                     "meta_title": "t", "meta_description": "d",
                                     "alt": "alt"},
                             cost_credits=1)
    gw.generate_description_single = fake_single
    try:
        with db.connection() as c:
            reset(c); tid, sid = seed(c, credits_amount=3)
            jid = jobs.enqueue(c, tid, sid, "445", payload={"product_id": 445})
            c.commit()
            job = jobs.claim(c, "worker-1", 600); c.commit()
            shop = c.execute("SELECT * FROM shops WHERE id=%s", (sid,)).fetchone()
            pipeline.run_description_job(c, job, dict(shop), auth_key="dummy",
                                         api_key="klucz", write_enabled=True)
            row = c.execute("SELECT status FROM jobs WHERE id=%s", (jid,)).fetchone()
            assert row["status"] == "done", row["status"]
            assert calls["pid"] == 445 and "description" in calls["fields"]
            assert credits.get_balance(c, tid) == 2, "zapis = 1 kredyt"
    finally:
        gw.generate_description_single = real_single
    print("  [ok] zapis: klucz+write_enabled -> PUT do sklepu, pobrany 1 kredyt, done")


def _patch(module, attr, value, submodule):
    """Prosty monkeypatch importu wewnatrz pipeline (bez pytest)."""
    import importlib
    mod = importlib.import_module(submodule)
    setattr(mod, attr, value)


if __name__ == "__main__":
    import os
    db.init_pool(os.environ["DATABASE_URL"])
    print("Test silnika Kombajn (bez sieci):")
    test_skip_locked()
    test_credits_atomic()
    test_orphan_recovery()
    test_full_description_job(_patch)
    print("\nWszystko zielone.")
