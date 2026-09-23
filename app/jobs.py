"""Silnik kolejki zadan oparty o Postgres.

- claim(): pobiera jedno zadanie przez FOR UPDATE SKIP LOCKED, wiec wiele
  workerow nie wejdzie sobie w droge (odpowiednik locka w n8n, tylko bez
  ryzyka zaciecia).
- Osierocone zadania: job "running" ktorego worker padl, po LOCK_TIMEOUT
  wraca do puli i zostaje przejety. To jest wznowienie po awarii z Zalozen.
"""
import json


def enqueue(conn, tenant_id: int, shop_id: int | None, product_ref: str | None,
            job_type: str = "description", payload: dict | None = None,
            batch_id: int | None = None, created_by: int | None = None) -> int:
    row = conn.execute(
        "INSERT INTO jobs (tenant_id, shop_id, type, product_ref, payload, batch_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (tenant_id, shop_id, job_type, product_ref, json.dumps(payload or {}), batch_id, created_by),
    ).fetchone()
    return row["id"]


def claim(conn, worker_id: str, lock_timeout_seconds: int) -> dict | None:
    """Przejmij jedno zadanie. Zwraca wiersz joba albo None (pusta kolejka)."""
    row = conn.execute(
        """
        UPDATE jobs SET
            status    = 'running',
            locked_at = now(),
            locked_by = %(worker)s,
            attempts  = attempts + 1,
            updated_at = now()
        WHERE id = (
            SELECT id FROM jobs
            WHERE status = 'pending'
               OR (status = 'running'
                   AND locked_at < now() - (%(timeout)s || ' seconds')::interval)
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING *
        """,
        {"worker": worker_id, "timeout": lock_timeout_seconds},
    ).fetchone()
    return row


def set_stage(conn, job_id: int, stage: str, result_patch: dict | None = None) -> None:
    """Zapisz postep etapu. Job wznowi sie stad, jesli worker padnie dalej."""
    if result_patch:
        conn.execute(
            "UPDATE jobs SET stage = %s, result = result || %s::jsonb, updated_at = now() "
            "WHERE id = %s",
            (stage, json.dumps(result_patch), job_id),
        )
    else:
        conn.execute(
            "UPDATE jobs SET stage = %s, updated_at = now() WHERE id = %s",
            (stage, job_id),
        )


def complete(conn, job_id: int) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', stage = 'done', locked_by = NULL, "
        "updated_at = now() WHERE id = %s",
        (job_id,),
    )


def fail(conn, job_id: int, error: str, max_attempts: int = 3) -> None:
    """Blad. Ponizej limitu prob wraca do 'pending', powyzej laduje w 'failed'."""
    conn.execute(
        """
        UPDATE jobs SET
            status = CASE WHEN attempts >= %s THEN 'failed' ELSE 'pending' END,
            last_error = %s,
            locked_by = NULL,
            locked_at = NULL,
            updated_at = now()
        WHERE id = %s
        """,
        (max_attempts, error[:2000], job_id),
    )


def hold(conn, job_id: int, reason: str) -> None:
    """Wstrzymanie (np. brak kredytow) — nie liczy sie jako blad techniczny."""
    conn.execute(
        "UPDATE jobs SET status = 'held', last_error = %s, locked_by = NULL, "
        "updated_at = now() WHERE id = %s",
        (reason[:2000], job_id),
    )
