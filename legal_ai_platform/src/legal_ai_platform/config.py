"""Platform configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Always resolve legal_ai_platform/.env regardless of process working directory.
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class PlatformSettings(BaseSettings):
    """Runtime settings for the Legal AI Platform."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    retrieval_server_url: str = "http://localhost:8001"
    retrieval_timeout_seconds: float = 30.0
    retrieval_max_retries: int = 3
    legal_search_backend: str = "custom"
    # Upper bound on a single agent run (the full pipeline can be long). 0 = no limit.
    agent_timeout_seconds: float = 300.0
    platform_host: str = "0.0.0.0"
    platform_port: int = 8080
    platform_log_level: str = "INFO"
    # When true, serve over HTTPS with HTTP/2 via Hypercorn (requires TLS cert + key).
    platform_http2: bool = False
    platform_ssl_certfile: str = ""
    platform_ssl_keyfile: str = ""
    database_url: str = "sqlite:///./legal_ai_platform.db"
    jwt_secret: str = "change-me-in-production"
    jwt_expire_minutes: int = 60 * 24
    auth_required: bool = True
    dev_anonymous_user_id: str = "dev-anonymous"
    dev_anonymous_tenant_id: str | None = "demo-tenant"


@lru_cache
def get_settings() -> PlatformSettings:
    """Return cached platform settings."""
    return PlatformSettings()
