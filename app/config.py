"""Konfiguracja czytana ze zmiennych srodowiskowych (.env)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    fernet_key: str
    anthropic_api_key: str     # opisy (claude-sonnet-4-6); pusty = podglad
    openai_api_key: str        # zdjecia (gpt-image-2); pusty = podglad
    worker_id: str
    lock_timeout_seconds: int
    data_dir: str              # skladowanie zdjec (wolumen)
    write_enabled: bool        # bezpiecznik: czy wolno pisac do sklepu

    @staticmethod
    def load() -> "Settings":
        return Settings(
            database_url=os.environ["DATABASE_URL"],
            fernet_key=os.environ["FERNET_KEY"],
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            worker_id=os.environ.get("WORKER_ID", "worker-1"),
            lock_timeout_seconds=int(os.environ.get("LOCK_TIMEOUT_SECONDS", "600")),
            data_dir=os.environ.get("DATA_DIR", "/data"),
            write_enabled=os.environ.get("WRITE_ENABLED", "0") == "1",
        )
