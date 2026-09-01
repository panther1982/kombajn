"""Zaklada konta admin i oznacza konta wlasciciela.

  ty@merebilo.pl          -> konto wlasciciela (logowanie do Kombajna)
  l.pintera@gmail.com     -> admin + wlasciciel (zaklada, jesli brak)
  kontakt@agent007.com.pl -> admin + wlasciciel (zaklada, jesli brak)

Wszystkie trzy: is_owner_account=true (nigdy nie naliczaja kredytow),
te same ustawienia promptow co ty@merebilo.pl (ten sam tenant, wspolny sklep).

Uruchom:
    docker compose run --rm -it web python -m scripts.setup_admins
"""
import getpass
from app.config import Settings
from app import db, auth

OWNER_EMAIL = "ty@merebilo.pl"
ADMIN_EMAILS = ["l.pintera@gmail.com", "kontakt@agent007.com.pl"]


def main():
    s = Settings.load(); db.init_pool(s.database_url)
    with db.connection() as c:
        owner = c.execute("SELECT id, tenant_id FROM users WHERE email = %s",
                         (OWNER_EMAIL,)).fetchone()
        if not owner:
            print(f"STOP: nie ma konta {OWNER_EMAIL}. Najpierw musi istniec.")
            return
        tenant_id = owner["tenant_id"]
        print(f"Tenant wlasciciela: {tenant_id}")

        # ty@merebilo.pl -> konto wlasciciela (rola bez zmian - loguje sie do Kombajna)
        c.execute("UPDATE users SET is_owner_account = true WHERE id = %s", (owner["id"],))
        print(f"  {OWNER_EMAIL}: oznaczone jako wlasciciel (kredyty wylaczone)")

        for email in ADMIN_EMAILS:
            existing = c.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
            if existing:
                c.execute("UPDATE users SET role='admin', is_owner_account=true, "
                          "is_active=true WHERE id = %s", (existing["id"],))
                print(f"  {email}: istnialo -> ustawiono admin + wlasciciel")
            else:
                haslo = getpass.getpass(f"Haslo dla {email} (min 8 znakow): ")
                if len(haslo) < 8:
                    print(f"  POMINIETO {email}: haslo za krotkie")
                    continue
                uid = auth.create_user(c, tenant_id, email, haslo, role="admin")
                c.execute("UPDATE users SET is_owner_account = true WHERE id = %s", (uid,))
                print(f"  {email}: UTWORZONO jako admin + wlasciciel (id {uid})")

        c.commit()
        print("\nGotowe. Konta wlasciciela wspoldziela tenant, sklep i prompty.")
        print("Panel /admin widoczny dla:", ", ".join(ADMIN_EMAILS))


if __name__ == "__main__":
    main()
