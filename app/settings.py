from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    emqx_base_url: str
    emqx_api_key: str
    emqx_secret_key: str
    emqx_timeout_seconds: float
    emqx_sync_timeout: str
    clients_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        # Explicitly use the launch directory so behavior is predictable under
        # uvicorn, scripts and service managers. Existing process variables win.
        dotenv_path = Path.cwd() / ".env"
        if dotenv_path.exists():
            load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
        else:
            load_dotenv("/app/data/.env", override=False)
        return cls(
            emqx_base_url=os.getenv(
                "EMQX_BASE_URL", "http://192.168.4.244:18083/api/v5"
            ).rstrip("/"),
            emqx_api_key=os.getenv("EMQX_API_KEY", ""),
            emqx_secret_key=os.getenv("EMQX_SECRET_KEY", ""),
            emqx_timeout_seconds=float(os.getenv("EMQX_TIMEOUT_SECONDS", "10")),
            emqx_sync_timeout=os.getenv("EMQX_SYNC_TIMEOUT", "5s"),
            clients_file=Path(os.getenv("CLIENTS_FILE", "clients.yml")),
        )
