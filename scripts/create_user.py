"""Utworzenie pierwszego uzytkownika panelu (bootstrap — brak rejestracji w MVP).

Uzycie:
    python -m scripts.create_user --tenant-id 1 --email ty@merebilo.pl --password ...
Jesli tenant nie istnieje, mozna go najpierw stworzyc flaga --new-tenant "Nazwa".
"""
import argparse
import getpass

from app import auth, db
from app.config import Settings


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tenant-id", type=int)
    p.add_argument("--new-tenant", help="utworz nowego najemce o tej nazwie")
    p.add_argument("--email", required=True)
    p.add_argument("--password", help="jesli pominiete, zapyta interaktywnie")
    args = p.parse_args()

    settings = Settings.load()
    db.init_pool(settings.database_url)
    password = args.password or getpass.getpass("Haslo: ")

    with db.connection() as conn:
        if args.new_tenant:
            tid = conn.execute("INSERT INTO tenants (name) VALUES (%s) RETURNING id",
                               (args.new_tenant,)).fetchone()["id"]
            conn.execute("INSERT INTO tenant_credits (tenant_id, balance) VALUES (%s, 0) "
                         "ON CONFLICT DO NOTHING", (tid,))
        elif args.tenant_id:
            tid = args.tenant_id
        else:
            raise SystemExit("podaj --tenant-id albo --new-tenant")

        uid = auth.create_user(conn, tid, args.email, password)
        conn.commit()
        print(f"Utworzono uzytkownika id={uid} dla najemcy {tid}: {args.email}")


if __name__ == "__main__":
    main()
