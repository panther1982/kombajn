"""Worker: pobiera zadania i uruchamia wlasciwy pipeline wg typu."""
import time

from app import db, jobs
from app.config import Settings
from app.crypto import decrypt
from app.pipeline import run_description_job, run_image_job, run_product_job


def _load_shop(conn, shop_id: int) -> dict:
    row = conn.execute("SELECT * FROM shops WHERE id = %s", (shop_id,)).fetchone()
    if row is None:
        raise ValueError(f"sklep {shop_id} nie istnieje")
    return row


def _tenant_image_prompt(conn, tenant_id: int) -> str:
    row = conn.execute(
        "SELECT prompt_image FROM shops WHERE tenant_id=%s ORDER BY id LIMIT 1",
        (tenant_id,),
    ).fetchone()
    return row["prompt_image"] if row else ""


def process_one(settings: Settings) -> bool:
    with db.connection() as conn:
        job = jobs.claim(conn, settings.worker_id, settings.lock_timeout_seconds)
        conn.commit()
        if job is None:
            return False
        try:
            if job["type"] == "description":
                shop = _load_shop(conn, job["shop_id"])
                auth_key = decrypt(bytes(shop["auth_key_encrypted"]), settings.fernet_key)
                run_description_job(conn, job, shop, auth_key,
                                    settings.anthropic_api_key, settings.write_enabled)
            elif job["type"] == "product":
                shop = _load_shop(conn, job["shop_id"])
                auth_key = decrypt(bytes(shop["auth_key_encrypted"]), settings.fernet_key)
                run_product_job(conn, job, shop, auth_key, settings.openai_api_key,
                                settings.data_dir, settings.write_enabled)
            elif job["type"] == "image":
                prompt = _tenant_image_prompt(conn, job["tenant_id"])
                run_image_job(conn, job, prompt, settings.openai_api_key, settings.data_dir)
            else:
                raise ValueError(f"nieobslugiwany typ: {job['type']}")
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            jobs.fail(conn, job["id"], f"{type(e).__name__}: {e}")
            conn.commit()
        return True


def main() -> None:
    settings = Settings.load()
    db.init_pool(settings.database_url)
    print(f"[{settings.worker_id}] start (write_enabled={settings.write_enabled})")
    while True:
        if not process_one(settings):
            time.sleep(3)


if __name__ == "__main__":
    main()
