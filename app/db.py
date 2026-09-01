"""Pula polaczen do Postgres (psycopg 3)."""
from contextlib import contextmanager

from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

_pool: ConnectionPool | None = None


def init_pool(database_url: str) -> None:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(database_url, min_size=1, max_size=10, kwargs={"row_factory": dict_row})


@contextmanager
def connection():
    """Zwraca polaczenie z puli. Commit reczny w wywolaniu."""
    assert _pool is not None, "init_pool() nie zostalo wywolane"
    with _pool.connection() as conn:
        yield conn
