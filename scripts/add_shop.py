"""Dodanie sklepu do najemcy. Klucz webservice jest szyfrowany przy zapisie.

Uzycie (sklep testowy):
    python -m scripts.add_shop --tenant-id 1 \
        --base-url https://test.merebilo.eu \
        --auth-key TWOJ_KLUCZ_WEBSERVICE

Klucz mozna tez podac interaktywnie (bez --auth-key), zeby nie zostal w historii powloki.
"""
import argparse
import getpass

from app import db
from app.config import Settings
from app.crypto import encrypt


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tenant-id", type=int, required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--auth-key", help="jesli pominiete, zapyta interaktywnie")
    args = p.parse_args()

    settings = Settings.load()
    db.init_pool(settings.database_url)
    auth_key = args.auth_key or getpass.getpass("Klucz webservice: ")

    with db.connection() as conn:
        exists = conn.execute("SELECT 1 FROM tenants WHERE id = %s", (args.tenant_id,)).fetchone()
        if not exists:
            raise SystemExit(f"najemca {args.tenant_id} nie istnieje")
        enc = encrypt(auth_key.strip(), settings.fernet_key)
        row = conn.execute(
            "INSERT INTO shops (tenant_id, base_url, auth_key_encrypted) VALUES (%s,%s,%s) RETURNING id",
            (args.tenant_id, args.base_url.rstrip("/"), enc),
        ).fetchone()
        conn.commit()
        print(f"Dodano sklep id={row['id']}: {args.base_url}")
        print("Prompt i parametry ustawisz teraz w panelu.")


if __name__ == "__main__":
    main()
