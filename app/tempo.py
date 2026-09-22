"""Wspolne tempo wywolan API dla wielu workerow.

Dlaczego w bazie, a nie w pamieci:
    threading.Lock pilnuje odstepu tylko wewnatrz JEDNEGO procesu. Gdy
    uruchomimy trzy kontenery worker, kazdy ma wlasny licznik i razem
    wysylaja trzy razy wiecej zapytan niz wolno. OpenAI odpowiada 429,
    a my placimy za ponowienia.

Zasada dzialania:
    Kazdy worker nie "sprawdza, czy juz moze", tylko REZERWUJE sobie moment.
    Jedno UPDATE przesuwa wspolny znacznik next_at o odstep i zwraca moment,
    ktory wlasnie zostal zajety. Blokada wiersza w Postgresie ustawia
    wszystkich w kolejce — nikt nie dostanie tego samego momentu.

    Transakcje zamykamy PRZED czekaniem, zeby sen odbywal sie poza blokada.
"""
import os
import time

from app import db

# Ten sam odstep co dotychczas: 4 s = ok. 15 obrazow/min przy limicie ~20/min.
DOMYSLNY_ODSTEP = float(os.environ.get("IMAGE_MIN_INTERVAL_SECONDS", "4"))

# Bezpiecznik: gdyby w kolejce ustawilo sie absurdalnie duzo workerow,
# nie chcemy zablokowac zadania na pol godziny.
MAKS_CZEKANIE = float(os.environ.get("TEMPO_MAX_WAIT_SECONDS", "300"))

_REZERWACJA = """
INSERT INTO rate_slots (klucz, next_at)
VALUES (%(klucz)s, now() + make_interval(secs => %(odstep)s))
ON CONFLICT (klucz) DO UPDATE
    SET next_at    = GREATEST(rate_slots.next_at, now())
                     + make_interval(secs => %(odstep)s),
        updated_at = now()
RETURNING EXTRACT(EPOCH FROM
    (next_at - make_interval(secs => %(odstep)s) - now())) AS czekaj
"""


def zarezerwuj(klucz: str, odstep_s: float | None = None) -> float:
    """Zarezerwuj kolejny moment wywolania i poczekaj do niego.

    Zwraca liczbe sekund, ktore faktycznie przeczekano.
    Rzuca wyjatek, gdy baza jest niedostepna — wywolujacy ma wtedy
    wrocic do tempa lokalnego.
    """
    odstep = DOMYSLNY_ODSTEP if odstep_s is None else odstep_s

    with db.connection() as conn:
        row = conn.execute(_REZERWACJA, {"klucz": klucz, "odstep": odstep}).fetchone()
        conn.commit()

    czekaj = float(row["czekaj"] or 0.0)
    if czekaj <= 0:
        return 0.0
    if czekaj > MAKS_CZEKANIE:
        czekaj = MAKS_CZEKANIE
    time.sleep(czekaj)
    return czekaj
