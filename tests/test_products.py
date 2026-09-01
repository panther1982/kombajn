"""Test etapu tworzenia produktow. Uruchom: python -m tests.test_products"""
import io
import os
from decimal import Decimal

os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("DATA_DIR", "/tmp/kombajn-data")

from PIL import Image  # noqa: E402

from app import auth, credits, db, jobs, pipeline  # noqa: E402
from app.crypto import encrypt, generate_key  # noqa: E402

FERNET = os.environ["FERNET_KEY"] = generate_key()
CALLS = {}


class FakePS:
    """Udawany PrestaShop - notuje wywolania, symuluje istniejacy produkt."""
    existing_ref = None

    def __init__(self, *a, **k):
        CALLS.setdefault("images", [])
        CALLS.setdefault("features", [])

    def find_by_reference(self, ref):
        return 555 if ref == FakePS.existing_ref else None

    def create_product(self, *, name, reference, price_net, category_id,
                       id_tax_rules_group=1, active=False, id_manufacturer=None,
                       category_ids=None):
        CALLS["created"] = {"name": name, "reference": reference, "price_net": price_net,
                            "category_id": category_id, "tax_group": id_tax_rules_group,
                            "active": active, "id_manufacturer": id_manufacturer,
                            "category_ids": category_ids}
        return 777

    def category_path(self, category_id):
        # udawane drzewo: 20 (Spinki) pod 34 (Ozdoby)
        return {20: [34, 20], 15: [15], 81: [81]}.get(category_id, [category_id])

    def fix_default_category(self, pid, cid):
        CALLS["fixed_category"] = (pid, cid)

    def upload_product_image(self, pid, data, filename):
        CALLS["images"].append((pid, filename, len(data)))

    def set_product_feature(self, pid, id_feature, value):
        CALLS["features"].append((pid, id_feature, value))

    def close(self):
        pass


def _jpeg(w=1200, h=900) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 160)).save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _seed(conn, vat="0.23", size_feature=9):
    conn.execute("TRUNCATE users, prompt_history, category_map, jobs, credit_ledger, "
                 "shops, tenant_credits, tenants RESTART IDENTITY CASCADE")
    tid = conn.execute("INSERT INTO tenants (name) VALUES ('T') RETURNING id").fetchone()["id"]
    credits.topup(conn, tid, 20)
    sid = conn.execute(
        "INSERT INTO shops (tenant_id, base_url, auth_key_encrypted, vat_rate, "
        "id_tax_rules_group, id_size_feature) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (tid, "https://test.merebilo.eu", encrypt("k", FERNET), vat, 1, size_feature),
    ).fetchone()["id"]
    conn.commit()
    return tid, sid


def _make_photos(tid, names):
    d = f"/tmp/kombajn-data/incoming/{tid}"
    os.makedirs(d, exist_ok=True)
    out = []
    for i, n in enumerate(names, start=1):
        p = f"{d}/{i}_{n}"
        with open(p, "wb") as f:
            f.write(_jpeg())
        out.append({"path": p, "orig_name": n, "index": i})
    return out


def _run(conn, tid, sid, payload, write_enabled=True):
    jid = jobs.enqueue(conn, tid, sid, payload["symbol"], job_type="product", payload=payload)
    conn.commit()
    job = jobs.claim(conn, "w1", 600)
    conn.commit()
    shop = dict(conn.execute("SELECT * FROM shops WHERE id=%s", (sid,)).fetchone())
    pipeline.run_product_job(conn, job, shop, auth_key="k", openai_key="",
                             data_dir="/tmp/kombajn-data", write_enabled=write_enabled)
    return conn.execute("SELECT * FROM jobs WHERE id=%s", (jid,)).fetchone()


def test_missing_category_holds():
    with db.connection() as c:
        tid, sid = _seed(c)
        payload = {"symbol": "MF1", "category": "Nieznana kategoria", "price_gross": "9",
                   "size": None, "photos": _make_photos(tid, ["a.jpg"])}
        row = _run(c, tid, sid, payload)
        assert row["status"] == "held", row["status"]
        assert "Brak kategorii" in row["last_error"]
        assert "created" not in CALLS, "nic nie powinno powstac w sklepie"
    print("  [ok] brak kategorii w mapie -> job wstrzymany, sklep nietkniety")


def test_full_product_creation():
    CALLS.clear()
    with db.connection() as c:
        tid, sid = _seed(c)
        c.execute("INSERT INTO category_map (shop_id, source_name, ps_category_id) "
                  "VALUES (%s,%s,%s)", (sid, "Kolczyki Xuping Stal 316L", 15))
        c.commit()
        payload = {"symbol": "MF25000", "category": "Kolczyki Xuping Stal 316L",
                   "price_gross": "9", "size": "4.5",
                   "photos": _make_photos(tid, ["p1.jpg", "p2.jpg", "p3.jpg"])}
        row = _run(c, tid, sid, payload)

        assert row["status"] == "done", (row["status"], row["last_error"])
        # cena: 9 brutto / 1.23 = 7.317073 netto
        assert CALLS["created"]["price_net"] == "7.317073", CALLS["created"]["price_net"]
        assert CALLS["created"]["reference"] == "MF25000"
        assert CALLS["created"]["name"] == "Kolczyki Xuping Stal 316L - MF25000", CALLS["created"]["name"]
        assert CALLS["created"]["category_id"] == 15
        assert CALLS["created"]["id_manufacturer"] == 2  # brak marki -> Merebilo
        # kwirk PrestaShop: poprawka kategorii domyslnej po utworzeniu
        assert CALLS["fixed_category"] == (777, 15), CALLS.get("fixed_category")
        # wszystkie 3 zdjecia wgrane, w kolejnosci
        assert len(CALLS["images"]) == 3, CALLS["images"]
        # nazwy plikow zachowane co do znaku (niosa dane produktu)
        assert [f for _, f, _ in CALLS["images"]] == ["p1.jpg", "p2.jpg", "p3.jpg"], CALLS["images"]
        # rozmiar jako cecha
        assert CALLS["features"] == [(777, 9, "4.5 cm")], CALLS["features"]
    assert CALLS["created"]["category_ids"] == [15], CALLS["created"]["category_ids"]
    print("  [ok] produkt: nazwa z myslnikiem, netto 7.317073, sciezka kategorii, 3 zdjecia, cecha 4.5 cm")


def test_duplicate_reference():
    CALLS.clear()
    FakePS.existing_ref = "MF25000"
    try:
        with db.connection() as c:
            tid, sid = _seed(c)
            c.execute("INSERT INTO category_map (shop_id, source_name, ps_category_id) "
                      "VALUES (%s,%s,%s)", (sid, "Kolczyki", 15))
            c.commit()
            payload = {"symbol": "MF25000", "category": "Kolczyki", "price_gross": "9",
                       "size": None, "photos": _make_photos(tid, ["d1.jpg"])}
            row = _run(c, tid, sid, payload)
            assert row["status"] == "done"
            assert "created" not in CALLS, "nie wolno tworzyc duplikatu"
            assert row["result"].get("duplicate") is True
            assert CALLS["images"][0][0] == 555, "zdjecia do istniejacego produktu"
    finally:
        FakePS.existing_ref = None
    print("  [ok] duplikat symbolu: bez nowego produktu, zdjecia dopiete do istniejacego")


def test_write_disabled():
    CALLS.clear()
    with db.connection() as c:
        tid, sid = _seed(c)
        c.execute("INSERT INTO category_map (shop_id, source_name, ps_category_id) "
                  "VALUES (%s,%s,%s)", (sid, "Kolczyki", 15))
        c.commit()
        payload = {"symbol": "MF9", "category": "Kolczyki", "price_gross": "12",
                   "size": None, "photos": _make_photos(tid, ["w1.jpg"])}
        row = _run(c, tid, sid, payload, write_enabled=False)
        assert row["status"] == "held", row["status"]
        assert "created" not in CALLS
        assert row["result"].get("processed"), "zdjecia i tak powinny byc obrobione"
    print("  [ok] WRITE_ENABLED=0: zdjecia obrobione, produkt NIE utworzony")


def test_inactive_creation():
    """Domyslnie produkt powstaje WYLACZONY, do recznej akceptacji."""
    CALLS.clear()
    with db.connection() as c:
        tid, sid = _seed(c)
        c.execute("INSERT INTO category_map (shop_id, source_name, ps_category_id) "
                  "VALUES (%s,%s,%s)", (sid, "Kolczyki", 15))
        c.commit()
        payload = {"symbol": "MF77", "category": "Kolczyki", "price_gross": "9",
                   "size": None, "photos": _make_photos(tid, ["i1.jpg"])}
        row = _run(c, tid, sid, payload)
        assert row["status"] == "done"
        assert CALLS["created"]["active"] is False, "produkt ma byc nieaktywny"
        assert row["result"].get("created_inactive") is True
    print("  [ok] nowy produkt tworzony jako NIEAKTYWNY (do recznej akceptacji)")

    # gdy wylaczysz opcje w ustawieniach -> produkt aktywny od razu
    CALLS.clear()
    with db.connection() as c:
        tid, sid = _seed(c)
        c.execute("UPDATE shops SET create_inactive=false WHERE id=%s", (sid,))
        c.execute("INSERT INTO category_map (shop_id, source_name, ps_category_id) "
                  "VALUES (%s,%s,%s)", (sid, "Kolczyki", 15))
        c.commit()
        payload = {"symbol": "MF78", "category": "Kolczyki", "price_gross": "9",
                   "size": None, "photos": _make_photos(tid, ["i2.jpg"])}
        _run(c, tid, sid, payload)
        assert CALLS["created"]["active"] is True
    print("  [ok] po wylaczeniu opcji produkt powstaje aktywny")


def test_brand_detection():
    """Producent po ID: Merebilo=2, Xuping=3, Chuangmei=107; brak -> Merebilo."""
    def _brand(cat, sym, price="9"):
        CALLS.clear()
        with db.connection() as c:
            tid, sid = _seed(c)
            c.execute("INSERT INTO category_map (shop_id, source_name, ps_category_id) VALUES (%s,%s,%s)",
                      (sid, cat, 81))
            c.commit()
            _run(c, tid, sid, {"symbol": sym, "category": cat, "price_gross": price,
                               "size": None, "photos": _make_photos(tid, ["b.jpg"])})
        return CALLS["created"]["id_manufacturer"]

    assert _brand("Kolczyki Xuping Stal 316L", "MFX1") == 3
    print("  [ok] Xuping -> producent ID 3")
    assert _brand("Kolczyki CM miedziane", "CM99") == 107
    print("  [ok] CM -> Chuangmei ID 107")
    assert _brand("Bransoletka Merebilo", "MB1") == 2
    print("  [ok] Merebilo w nazwie -> ID 2")
    # brak marki w nazwie -> Merebilo domyslnie (wlasny towar)
    assert _brand("Spinki do wlosow", "MF34344") == 2
    print("  [ok] brak marki -> Merebilo domyslnie (ID 2)")
    # konflikt Xuping + CM -> wygrywa CM
    assert _brand("Kolczyki Xuping", "CM12") == 107
    print("  [ok] konflikt Xuping+CM -> Chuangmei (ID 107)")


def test_resume_after_partial_upload():
    """Awaria w srodku wgrywania zdjec: wznowienie NIE duplikuje zdjec."""
    CALLS.clear()
    fail_on = {"n": 2}   # drugi upload pada

    orig_upload = FakePS.upload_product_image
    def flaky_upload(self, pid, data, filename):
        if len(CALLS.get("images", [])) + 1 == fail_on["n"]:
            fail_on["n"] = -1   # tylko raz
            raise RuntimeError("symulowana awaria sieci")
        orig_upload(self, pid, data, filename)
    FakePS.upload_product_image = flaky_upload
    try:
        with db.connection() as c:
            tid, sid = _seed(c)
            c.execute("INSERT INTO category_map (shop_id, source_name, ps_category_id) VALUES (%s,%s,%s)",
                      (sid, "Kolczyki", 15))
            c.commit()
            payload = {"symbol": "MFR1", "category": "Kolczyki", "price_gross": "9",
                       "size": None, "photos": _make_photos(tid, ["r1.jpg", "r2.jpg", "r3.jpg"])}
            jid = jobs.enqueue(c, tid, sid, "MFR1", job_type="product", payload=payload)
            c.commit()
            shop = dict(c.execute("SELECT * FROM shops WHERE id=%s", (sid,)).fetchone())

            # 1. przebieg: pada na drugim zdjeciu
            job = jobs.claim(c, "w1", 600); c.commit()
            try:
                pipeline.run_product_job(c, job, shop, "k", "", "/tmp/kombajn-data", True)
                assert False, "powinno rzucic"
            except RuntimeError:
                c.rollback()
                jobs.fail(c, job["id"], "symulowana awaria"); c.commit()

            row = c.execute("SELECT result FROM jobs WHERE id=%s", (jid,)).fetchone()
            assert row["result"].get("photos_uploaded") == [1], row["result"].get("photos_uploaded")

            # 2. przebieg: wznowienie
            job = jobs.claim(c, "w1", 600); c.commit()
            pipeline.run_product_job(c, job, shop, "k", "", "/tmp/kombajn-data", True)

            row = c.execute("SELECT status, result FROM jobs WHERE id=%s", (jid,)).fetchone()
            assert row["status"] == "done"
            names = [f for _, f, _ in CALLS["images"]]
            assert len(names) == 3 and len(set(names)) == 3, names
            assert "created" in CALLS and CALLS["created"]["reference"] == "MFR1"
    finally:
        FakePS.upload_product_image = orig_upload
    print("  [ok] wznowienie po awarii: 3 zdjecia wgrane DOKLADNIE raz, bez duplikatow")


def test_category_tree():
    """Produkt trafia do calej sciezki kategorii (lisc + nadrzedne)."""
    CALLS.clear()
    with db.connection() as c:
        tid, sid = _seed(c)
        c.execute("INSERT INTO category_map (shop_id, source_name, ps_category_id) VALUES (%s,%s,%s)",
                  (sid, "Spinki do wlosow", 20))
        c.commit()
        _run(c, tid, sid, {"symbol": "MFT1", "category": "Spinki do wlosow",
                           "price_gross": "12", "size": None,
                           "photos": _make_photos(tid, ["t1.jpg"])})
        # 20 (Spinki) pod 34 (Ozdoby) -> produkt w obu
        assert CALLS["created"]["category_ids"] == [34, 20], CALLS["created"]["category_ids"]
    print("  [ok] drzewo: produkt w Spinki(20) I Ozdoby(34), nie tylko w lisciu")


def test_vat_variants():
    from app.naming import parse_photo_filename
    p = parse_photo_filename("Kat - X!100!5.jpg")
    assert p.price_net(Decimal("0.23")) == Decimal("81.300813")
    assert p.price_net(Decimal("0.08")) == Decimal("92.592593")
    assert p.price_net(Decimal("0")) == Decimal("100.000000")
    print("  [ok] przeliczanie VAT: 23%, 8% i 0% poprawne")


if __name__ == "__main__":
    db.init_pool(os.environ["DATABASE_URL"])
    import app.prestashop as ps_mod
    ps_mod.PrestaShopClient = FakePS  # podmiana klienta na czas testow

    print("Test tworzenia produktow:")
    test_missing_category_holds()
    test_full_product_creation()
    test_duplicate_reference()
    test_write_disabled()
    test_inactive_creation()
    test_brand_detection()
    test_category_tree()
    test_resume_after_partial_upload()
    test_vat_variants()
    print("\nWszystko zielone.")
