"""Etap 0 — punkt wejscia do testu na Twoim sklepie.

Znajduje produkty bez opisu i tworzy dla nich zadania. TYLKO ODCZYT ze sklepu
— nic nie zapisuje. Pierwsze uruchomienie warto zrobic wlasnie tak,
zeby zobaczyc, ze silnik poprawnie widzi produkty, zanim wlaczysz zapis.

Uzycie:
    python -m scripts.enqueue_missing_descriptions --shop-id 1 --limit 5
"""
import argparse

from app import db, jobs
from app.config import Settings
from app.crypto import decrypt
from app.prestashop import PrestaShopClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shop-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    settings = Settings.load()
    db.init_pool(settings.database_url)

    with db.connection() as conn:
        shop = conn.execute("SELECT * FROM shops WHERE id = %s", (args.shop_id,)).fetchone()
        if shop is None:
            raise SystemExit(f"sklep {args.shop_id} nie istnieje")
        auth_key = decrypt(bytes(shop["auth_key_encrypted"]), settings.fernet_key)

        ps = PrestaShopClient(shop["base_url"], auth_key)
        try:
            ids, scanned = ps.scan_products(mode='newest', max_jobs=args.limit)
            print(f'Przeskanowano {scanned} produktow')
        finally:
            ps.close()

        print(f"Znaleziono {len(ids)} produktow bez opisu: {ids}")
        for pid in ids:
            job_id = jobs.enqueue(
                conn, shop["tenant_id"], shop["id"],
                product_ref=str(pid), payload={"product_id": pid},
            )
            print(f"  -> zadanie {job_id} dla produktu {pid}")
        conn.commit()


if __name__ == "__main__":
    main()
