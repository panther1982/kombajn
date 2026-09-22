"""Wielozadaniowosc: rozdzial kolejek i wspolne tempo wywolan.

Uruchom:
    DATABASE_URL=... python -m tests.test_wielozadaniowosc
"""
import multiprocessing as mp
import os
import sys
import time

from app import db, jobs


def _reset(conn):
    conn.execute("TRUNCATE jobs, credit_ledger, shops, tenant_credits, tenants "
                 "RESTART IDENTITY CASCADE")
    conn.execute("DELETE FROM rate_slots")
    conn.commit()


def test_rozdzial_kolejek():
    """Worker opisow nie tyka zdjec, worker mediow nie tyka opisow."""
    with db.connection() as c:
        _reset(c)
        tid = c.execute("INSERT INTO tenants (name) VALUES ('T') RETURNING id").fetchone()["id"]
        c.commit()
        for t in ["image", "image", "description", "image", "description", "product"]:
            jobs.enqueue(c, tid, None, f"ref-{t}", job_type=t)
        c.commit()

        opisy = []
        while (j := jobs.claim(c, "w-opisy", 600, types=["description"])):
            c.commit()
            opisy.append(j["type"])
        media = []
        while (j := jobs.claim(c, "w-media", 600, types=["image", "product"])):
            c.commit()
            media.append(j["type"])
        c.commit()

        assert opisy == ["description", "description"], opisy
        assert sorted(media) == ["image", "image", "image", "product"], media
        n = c.execute("SELECT count(*) AS n FROM jobs WHERE status='running'").fetchone()["n"]
        assert n == 6, n  # kazde zadanie wziete DOKLADNIE raz
    print("  [ok] rozdzial kolejek: opisy i media w osobnych pulach, bez dubli")


def test_worker_bez_filtra():
    """Stare zachowanie (jeden worker od wszystkiego) musi dalej dzialac."""
    with db.connection() as c:
        _reset(c)
        tid = c.execute("INSERT INTO tenants (name) VALUES ('T') RETURNING id").fetchone()["id"]
        c.commit()
        jobs.enqueue(c, tid, None, "ref", job_type="image")
        c.commit()
        j = jobs.claim(c, "w-all", 600, types=None)
        c.commit()
        assert j is not None and j["type"] == "image"
    print("  [ok] worker bez WORKER_TYPES bierze wszystko (zgodnosc wstecz)")


def _robotnik(dsn, kolejka):
    from app import db as d, tempo
    d.init_pool(dsn)
    for _ in range(3):
        tempo.zarezerwuj("test_tempo", 1.0)
        kolejka.put(time.time())


def test_wspolne_tempo():
    """Cztery OSOBNE procesy nie moga przekroczyc wspolnego odstepu.

    To jest sedno sprawy: threading.Lock tego nie zapewnia, bo kazdy proces
    ma wlasny. Rezerwacja w bazie zapewnia.
    """
    dsn = os.environ["DATABASE_URL"]
    with db.connection() as c:
        c.execute("DELETE FROM rate_slots")
        c.commit()

    # spawn, nie fork: przy fork dziecko odziedziczyloby pule polaczen rodzica
    # i zawiesiloby sie na cudzym gniezdzie. Tu kazdy proces laczy sie sam.
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procy = [ctx.Process(target=_robotnik, args=(dsn, q)) for _ in range(4)]
    for p in procy:
        p.start()
    for p in procy:
        p.join(60)

    czasy = sorted(q.get() for _ in range(12))
    odstepy = [czasy[i + 1] - czasy[i] for i in range(len(czasy) - 1)]
    zle = [round(o, 2) for o in odstepy if o < 0.9]
    assert not zle, f"odstep ponizej limitu: {zle}"
    print(f"  [ok] wspolne tempo: 4 procesy, 12 wywolan, min odstep "
          f"{min(odstepy):.2f} s (limit 1.00 s)")


if __name__ == "__main__":
    db.init_pool(os.environ["DATABASE_URL"])
    test_rozdzial_kolejek()
    test_worker_bez_filtra()
    test_wspolne_tempo()
    print("\nWszystko zielone.")
