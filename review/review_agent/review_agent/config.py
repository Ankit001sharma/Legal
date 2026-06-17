"""Review agent configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class ReviewSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    document_server_url: str = "http://localhost:8003"
    document_timeout_seconds: float = 60.0
    document_max_retries: int = 3
    review_host: str = "0.0.0.0"
    review_port: int = 8090


@lru_cache
def get_settings() -> ReviewSettings:
    return ReviewSettings()
