"""Partie zadan i powiadomienie mailem po zakonczeniu calej partii.

Uzytkownik wgrywa np. 200 zdjec -> powstaje partia. Gdy ostatnie zadanie
z partii sie konczy, wysylamy jedno podsumowanie zamiast kazac czekac
przy panelu.
"""
from __future__ import annotations

KIND_LABEL = {
    "product": "tworzenie produktow ze zdjec",
    "image": "obrobka zdjec",
    "description": "generowanie opisow",
}


def create(conn, tenant_id: int, user: dict, kind: str, total: int) -> int | None:
    """Zaklada partie. Zwraca jej id albo None, gdy uzytkownik nie chce maili."""
    if total <= 0:
        return None
    email = user.get("email") if user.get("notify_batches", True) else None
    row = conn.execute(
        "INSERT INTO batches (tenant_id, user_id, kind, total, notify_email) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (tenant_id, user.get("id"), kind, total, email)).fetchone()
    return row["id"]


def status(conn, batch_id: int) -> dict:
    """Stan partii: ile gotowych, ile bledow, ile jeszcze w toku."""
    r = conn.execute(
        "SELECT count(*) AS wszystkie, "
        "  count(*) FILTER (WHERE status = 'done') AS gotowe, "
        "  count(*) FILTER (WHERE status IN ('failed','held')) AS bledy, "
        "  count(*) FILTER (WHERE status IN ('pending','running')) AS w_toku "
        "FROM jobs WHERE batch_id = %s", (batch_id,)).fetchone()
    return dict(r) if r else {"wszystkie": 0, "gotowe": 0, "bledy": 0, "w_toku": 0}


def maybe_notify(conn, batch_id: int | None) -> bool:
    """Gdy partia zakonczona i mail jeszcze nie poszedl - wysyla podsumowanie.

    Zwraca True, gdy mail zostal wyslany. Bledy wysylki nie przerywaja pracy
    workera - powiadomienie jest dodatkiem, nie czescia przetwarzania.
    """
    if not batch_id:
        return False
    b = conn.execute(
        "SELECT id, kind, total, notify_email, notified_at FROM batches WHERE id = %s",
        (batch_id,)).fetchone()
    if not b or b["notified_at"] or not b["notify_email"]:
        return False

    s = status(conn, batch_id)
    if s["wszystkie"] == 0 or s["w_toku"] > 0:
        return False        # partia jeszcze trwa

    # oznaczamy PRZED wyslaniem - inaczej rownolegle workery moglyby wyslac dwa maile
    zmienione = conn.execute(
        "UPDATE batches SET notified_at = now() "
        "WHERE id = %s AND notified_at IS NULL RETURNING id", (batch_id,)).fetchone()
    conn.commit()
    if not zmienione:
        return False

    from app import mailer
    etykieta = KIND_LABEL.get(b["kind"], b["kind"])
    temat = f"Kombajn: zakonczono {etykieta} ({s['gotowe']}/{s['wszystkie']})"
    tresc = (
        f"Partia zakonczona.\n\n"
        f"Zadanie: {etykieta}\n"
        f"Gotowe:  {s['gotowe']}\n"
        f"Bledy:   {s['bledy']}\n"
        f"Razem:   {s['wszystkie']}\n\n")
    if s["bledy"]:
        pozycje = conn.execute(
            "SELECT product_ref, left(coalesce(last_error,''), 140) AS blad "
            "FROM jobs WHERE batch_id = %s AND status IN ('failed','held') "
            "ORDER BY id LIMIT 15", (batch_id,)).fetchall()
        tresc += "Pozycje z bledem:\n"
        for p in pozycje:
            tresc += f"  - {p['product_ref'] or '?'}: {p['blad']}\n"
        if s["bledy"] > 15:
            tresc += f"  ... i {s['bledy'] - 15} wiecej\n"
        tresc += "\nMozesz je wznowic w panelu przyciskiem 'Wznow wstrzymane i nieudane'.\n"
    try:
        mailer.send(b["notify_email"], temat, tresc)
        return True
    except Exception as e:
        print(f"[batches] nie udalo sie wyslac podsumowania partii {batch_id}: {e}", flush=True)
        return False
