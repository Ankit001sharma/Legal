"""Runtime configuration for the Document MCP server."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_NAME = "document-mcp"
VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
