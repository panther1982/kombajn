"""Ksiega kredytow.

Pobranie kredytow i zmiana statusu zadania dziej sie w JEDNEJ transakcji
(bo kredyty i zadania sa w tej samej bazie). Saldo blokowane wierszowo,
wiec dwa workery nie zejda ponizej zera.
"""


class InsufficientCredits(Exception):
    pass


def get_balance(conn, tenant_id: int) -> int:
    row = conn.execute(
        "SELECT balance FROM tenant_credits WHERE tenant_id = %s", (tenant_id,)
    ).fetchone()
    return row["balance"] if row else 0


def topup(conn, tenant_id: int, amount: int, reason: str = "topup") -> int:
    """Doladowanie. Zwraca nowe saldo. NIE commituje — robi to wywolujacy."""
    assert amount > 0
    conn.execute(
        "INSERT INTO tenant_credits (tenant_id, balance) VALUES (%s, %s) "
        "ON CONFLICT (tenant_id) DO UPDATE SET balance = tenant_credits.balance + EXCLUDED.balance",
        (tenant_id, amount),
    )
    return _log(conn, tenant_id, amount, reason, None)


def credits_enabled(conn, tenant_id: int, user_id: int | None = None) -> bool:
    """Czy naliczac kredyty. Flaga uzytkownika ma pierwszenstwo nad flaga tenanta.

    users.credits_enabled = NULL  -> dziedzicz z tenanta
                          = false -> ten uzytkownik NIE nalicza (nawet gdy tenant tak)
                          = true  -> nalicza
    """
    if user_id is not None:
        u = conn.execute(
            "SELECT credits_enabled, is_owner_account FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        if u:
            if u["is_owner_account"]:
                return False   # konta wlasciciela nigdy nie naliczaja
            if u["credits_enabled"] is not None:
                return bool(u["credits_enabled"])
    row = conn.execute(
        "SELECT credits_enabled FROM tenants WHERE id = %s", (tenant_id,)
    ).fetchone()
    return bool(row["credits_enabled"]) if row else True


def charge(conn, tenant_id: int, amount: int, reason: str, job_id: int | None = None,
           user_id: int | None = None) -> int:
    """Pobranie kredytow z blokada wierszowa. Rzuca InsufficientCredits gdy brak.

    Zwraca nowe saldo. Wykonaj w tej samej transakcji co zmiana statusu joba.
    Gdy najemca ma wylaczone kredyty, przechodzi bez potracenia (zapis 0 w ksiedze).
    """
    assert amount > 0
    if not credits_enabled(conn, tenant_id, user_id):
        return _log(conn, tenant_id, 0, f"{reason} (kredyty wylaczone)", job_id)
    row = conn.execute(
        "SELECT balance FROM tenant_credits WHERE tenant_id = %s FOR UPDATE",
        (tenant_id,),
    ).fetchone()
    balance = row["balance"] if row else 0
    if balance < amount:
        raise InsufficientCredits(f"tenant {tenant_id}: {balance} < {amount}")
    conn.execute(
        "UPDATE tenant_credits SET balance = balance - %s WHERE tenant_id = %s",
        (amount, tenant_id),
    )
    return _log(conn, tenant_id, -amount, reason, job_id)


def refund(conn, tenant_id: int, amount: int, reason: str, job_id: int | None = None) -> int:
    """Zwrot kredytow gdy zadanie sie nie powiodlo po pobraniu."""
    assert amount > 0
    conn.execute(
        "UPDATE tenant_credits SET balance = balance + %s WHERE tenant_id = %s",
        (amount, tenant_id),
    )
    return _log(conn, tenant_id, amount, reason, job_id)


def _log(conn, tenant_id: int, delta: int, reason: str, job_id: int | None) -> int:
    row = conn.execute(
        "SELECT balance FROM tenant_credits WHERE tenant_id = %s", (tenant_id,)
    ).fetchone()
    balance_after = row["balance"] if row else 0
    conn.execute(
        "INSERT INTO credit_ledger (tenant_id, delta, balance_after, reason, job_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, delta, balance_after, reason, job_id),
    )
    return balance_after
