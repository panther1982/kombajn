"""Ustawia role: superadmin dla wlascicieli platformy, member dla reszty.

  l.pintera@gmail.com      -> superadmin (widzi wszystkie tenanty)
  kontakt@agent007.com.pl  -> superadmin
  ty@merebilo.pl           -> member (zwykly uzytkownik Kombajna)

Superadmin ma wglad i zarzadzanie kontami WSZYSTKICH klientow.
Member loguje sie do Kombajna, panelu zarzadzania nie widzi.

Uruchom:
    docker compose run --rm -T web python -m scripts.setup_superadmins
"""
from app.config import Settings
from app import db

SUPERADMINS = ["l.pintera@gmail.com", "kontakt@agent007.com.pl"]
MEMBERS = ["ty@merebilo.pl"]


def main():
    s = Settings.load(); db.init_pool(s.database_url)
    with db.connection() as c:
        for email in SUPERADMINS:
            r = c.execute("UPDATE users SET role='superadmin', is_active=true "
                          "WHERE email=%s RETURNING id", (email.lower(),)).fetchone()
            print(f"  {email}: {'superadmin' if r else 'BRAK KONTA - pomijam'}")
        for email in MEMBERS:
            r = c.execute("UPDATE users SET role='member' WHERE email=%s RETURNING id",
                          (email.lower(),)).fetchone()
            print(f"  {email}: {'member' if r else 'BRAK KONTA - pomijam'}")
        c.commit()

        print("\nStan kont:")
        for row in c.execute("SELECT email, role, is_owner_account FROM users "
                             "ORDER BY role DESC, id").fetchall():
            wl = " [wlasciciel]" if row["is_owner_account"] else ""
            print(f"  {row['email']}: {row['role']}{wl}")


if __name__ == "__main__":
    main()
