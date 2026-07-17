"""Typed settings loaded from .env — the only module that touches the environment.

Everything else imports `get_settings()` from here. Nothing in this repo reads
os.environ directly; that keeps secrets handling auditable in one place.

API keys are SecretStr so they can never leak via repr/str in logs or tracebacks.
Call `.get_secret_value()` at the point of use (an HTTP request), nowhere else.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Postgres: application data (system of record)
    database_url: str = "postgresql://localhost/civic"
    # Postgres: DBOS workflow state — separate database, same instance
    dbos_system_database_url: str = "postgresql://localhost/civic_dbos"

    fec_api_key: SecretStr = SecretStr("")
    # Spare FEC key, used only if the primary hits its rate limit
    fec_api_key_backup: SecretStr = SecretStr("")
    congress_gov_api_key: SecretStr = SecretStr("")
    lda_api_key: SecretStr = SecretStr("")
    youtube_api_key: SecretStr = SecretStr("")

    # GPU-dependent stages (transcription, local LLM) run on Lambda instances,
    # never on this Mac. Code must check gpu_available before invoking them.
    vllm_base_url: str = ""
    local_model: str = ""
    gpu_available: bool = False

    # Where bulk downloads land (gitignored)
    raw_data_dir: Path = Path("data/raw")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached after first load)."""
    return Settings()
