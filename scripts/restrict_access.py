"""Jednorazowo: zostaw tylko jedno konto, wylacz reszte, wylacz kredyty.

Uruchom w kontenerze:
    docker compose run --rm -T web python -m scripts.restrict_access

Domyslnie tryb podgladu (nic nie zmienia). Zeby wykonac:
    docker compose run --rm -T web python -m scripts.restrict_access --apply
"""
import sys
from app.config import Settings
from app import db

KEEP_EMAIL = "l.pintera@gmail.com"
APPLY = "--apply" in sys.argv


def main():
    s = Settings.load(); db.init_pool(s.database_url)
    with db.connection() as c:
        keeper = c.execute("SELECT id, tenant_id, email FROM users WHERE email = %s",
                           (KEEP_EMAIL.lower(),)).fetchone()
        if not keeper:
            print(f"STOP: konto {KEEP_EMAIL} nie istnieje. Najpierw je zaloz, "
                  f"zeby nie zablokowac sobie dostepu.")
            all_users = c.execute("SELECT email FROM users ORDER BY id").fetchall()
            print("Istniejace konta:", ", ".join(u["email"] for u in all_users) or "(brak)")
            return

        others = c.execute("SELECT id, email, is_active FROM users WHERE email <> %s ORDER BY id",
                          (KEEP_EMAIL.lower(),)).fetchall()

        print(f"{'PODGLAD' if not APPLY else 'WYKONANIE'} — zmiany:")
        print(f"  ZOSTAJE aktywne:  {keeper['email']}  (tenant {keeper['tenant_id']})")
        for u in others:
            stan = "juz wylaczone" if not u["is_active"] else "-> WYLACZAM"
            print(f"  {u['email']}: {stan}")
        print(f"  kredyty tenanta {keeper['tenant_id']}: -> WYLACZAM")

        if not APPLY:
            print("\nTo byl tylko podglad. Aby wykonac, dodaj --apply")
            return

        c.execute("UPDATE users SET is_active = true WHERE id = %s", (keeper["id"],))
        c.execute("UPDATE users SET is_active = false WHERE email <> %s", (KEEP_EMAIL.lower(),))
        c.execute("UPDATE tenants SET credits_enabled = false WHERE id = %s",
                  (keeper["tenant_id"],))
        c.commit()
        print("\nWYKONANO. Zalogowac moze sie tylko", KEEP_EMAIL,
              "- kredyty na jego koncie wylaczone.")


if __name__ == "__main__":
    main()
