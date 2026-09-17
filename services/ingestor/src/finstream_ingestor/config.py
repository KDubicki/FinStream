"""Settings loaded and validated from the environment (AGENTS.md GR-4).

Instantiated once in main.py and passed down explicitly. Invalid configuration fails at
startup with a message naming the variable, rather than at the first job run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Intervals yfinance accepts; validated eagerly so a typo cannot reach a job.
VALID_INTERVALS = frozenset(
    {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}
)
#: yfinance period strings, e.g. 5d, 3mo, 2y, ytd, max.
PERIOD_PATTERN = re.compile(r"^(\d+(d|wk|mo|y)|ytd|max)$")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "text"]


class Settings(BaseSettings):
    """All FinStream Ingestor configuration. See docs/configuration.md."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- Database ---
    database_url: SecretStr
    timescaledb_enabled: bool = True

    # --- Source ---
    # Kept as a raw string: pydantic-settings parses list-typed fields as JSON, which would
    # reject a comma-separated value. Use Settings.symbols for the parsed tuple.
    yahoo_symbols: str = (
        "GC=F,SI=F,GLD,SLV,"  # metals
        "CL=F,NG=F,"  # energy
        "^GSPC,^IXIC,^DJI,^RUT,^VIX,^FTSE,^STOXX50E,"  # indices
        "SPY,QQQ,IWM,TLT,"  # ETFs
        "EURUSD=X,USDPLN=X,DX-Y.NYB,"  # FX
        "BTC-USD,ETH-USD,"  # crypto
        "^TNX"  # rates
    )

    # --- Schedules ---
    scheduler_timezone: str = "UTC"
    scheduler_misfire_grace_seconds: int = Field(default=300, ge=1)
    # After a long stall (a suspended laptop, a lost network) the ingestion jobs should catch up
    # at once rather than wait for the next interval. With coalesce=True a backlog still becomes
    # a single run, and idempotent upserts make repeating a window harmless (GR-2).
    scheduler_catch_up_missed_runs: bool = True
    intraday_enabled: bool = True
    intraday_interval: str = "1h"
    intraday_every_minutes: int = Field(default=60, ge=1)
    intraday_lookback: str = "5d"
    daily_enabled: bool = True
    daily_cron: str = "30 22 * * mon-fri"
    daily_lookback: str = "10d"
    backfill_on_start: bool = False
    backfill_period: str = "2y"

    # --- Retries ---
    retry_max_attempts: int = Field(default=5, ge=1)
    retry_max_wait_seconds: int = Field(default=60, ge=0)

    # --- Health & logging ---
    heartbeat_file: Path = Path("/tmp/finstream-ingestor.heartbeat")  # noqa: S108 - container-local
    heartbeat_every_seconds: int = Field(default=60, ge=1)
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    @property
    def symbols(self) -> tuple[str, ...]:
        """Configured Yahoo symbols, in order, without duplicates or blanks."""
        seen: dict[str, None] = {}
        for raw in self.yahoo_symbols.split(","):
            symbol = raw.strip()
            if symbol:
                seen.setdefault(symbol, None)
        return tuple(seen)

    @field_validator("yahoo_symbols")
    @classmethod
    def _non_empty_symbols(cls, value: str) -> str:
        if not [part for part in value.split(",") if part.strip()]:
            raise ValueError("YAHOO_SYMBOLS must list at least one symbol")
        return value

    @field_validator("intraday_interval")
    @classmethod
    def _known_interval(cls, value: str) -> str:
        if value not in VALID_INTERVALS:
            raise ValueError(
                f"INTRADAY_INTERVAL '{value}' is not a valid yfinance interval; "
                f"expected one of: {', '.join(sorted(VALID_INTERVALS))}"
            )
        return value

    @field_validator("intraday_lookback", "daily_lookback", "backfill_period")
    @classmethod
    def _known_period(cls, value: str) -> str:
        if not PERIOD_PATTERN.match(value):
            raise ValueError(
                f"'{value}' is not a valid yfinance period; expected e.g. 5d, 3mo, 2y, ytd or max"
            )
        return value

    @field_validator("scheduler_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"SCHEDULER_TIMEZONE '{value}' is not a valid IANA timezone") from exc
        return value

    @field_validator("daily_cron")
    @classmethod
    def _cron_with_named_weekdays(cls, value: str) -> str:
        fields = value.split()
        if len(fields) != 5:
            raise ValueError(
                "DAILY_CRON must have 5 fields (minute hour day month day-of-week), "
                f"got {len(fields)}"
            )
        day_of_week = fields[4]
        if any(char.isdigit() for char in day_of_week):
            raise ValueError(
                f"DAILY_CRON day-of-week '{day_of_week}' must use names such as 'mon-fri'. "
                "APScheduler counts 0 as Monday while classic cron counts it as Sunday, so "
                "numeric values silently shift the schedule by a day."
            )
        return value
