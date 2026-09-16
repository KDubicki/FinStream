"""Dashboard settings, loaded and validated from the environment (AGENTS.md GR-4)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Configuration for the dashboard. See docs/configuration.md."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    #: Same database as the Ingestor, but this service only issues SELECTs.
    database_url: SecretStr

    #: How long query results are cached, so a widget change does not re-query every rerun.
    dashboard_cache_ttl_seconds: int = Field(default=60, ge=0)
    #: Bar interval selected when the page first loads.
    dashboard_default_interval: str = "1d"
    #: Upper bound on rows pulled into a single chart, to keep a growing table safe to browse.
    dashboard_max_rows: int = Field(default=5000, ge=100)

    log_level: LogLevel = "INFO"
