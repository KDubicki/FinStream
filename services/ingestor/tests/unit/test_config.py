"""Configuration must fail fast and explain which variable is wrong (GR-4)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from finstream_ingestor.config import Settings


def test_defaults_match_documentation(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    assert settings.timescaledb_enabled is True
    assert settings.intraday_interval == "1h"
    assert settings.intraday_every_minutes == 60
    assert settings.daily_cron == "30 22 * * mon-fri"
    assert settings.scheduler_timezone == "UTC"
    assert settings.retry_max_attempts == 5
    assert settings.log_format == "json"


def test_catch_up_defaults_to_on(make_settings: Callable[..., Settings]) -> None:
    assert make_settings().scheduler_catch_up_missed_runs is True


def test_missing_database_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    assert "database_url" in str(excinfo.value).lower()


def test_database_url_is_not_printed(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(database_url="postgresql+psycopg://u:sup3rsecret@db:5432/x")
    assert "sup3rsecret" not in repr(settings)
    assert "sup3rsecret" not in str(settings)
    assert settings.database_url.get_secret_value().endswith("/x")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SPY", ("SPY",)),
        (" SPY , QQQ ", ("SPY", "QQQ")),
        ("SPY,QQQ,SPY", ("SPY", "QQQ")),
        ("GC=F,^GSPC", ("GC=F", "^GSPC")),
        ("SPY,,QQQ", ("SPY", "QQQ")),
    ],
)
def test_symbols_are_parsed(
    make_settings: Callable[..., Settings], raw: str, expected: tuple[str, ...]
) -> None:
    assert make_settings(yahoo_symbols=raw).symbols == expected


def test_empty_symbol_list_is_rejected(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="at least one symbol"):
        make_settings(yahoo_symbols=" , ")


def test_numeric_day_of_week_is_rejected(make_settings: Callable[..., Settings]) -> None:
    """APScheduler counts 0 as Monday, classic cron as Sunday (research R2)."""
    with pytest.raises(ValidationError, match="must use names"):
        make_settings(daily_cron="30 22 * * 1-5")


def test_cron_needs_five_fields(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="5 fields"):
        make_settings(daily_cron="30 22 * mon-fri")


def test_named_day_of_week_is_accepted(make_settings: Callable[..., Settings]) -> None:
    assert make_settings(daily_cron="0 21 * * mon-fri").daily_cron == "0 21 * * mon-fri"


def test_unknown_interval_is_rejected(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="not a valid yfinance interval"):
        make_settings(intraday_interval="7h")


@pytest.mark.parametrize("period", ["5d", "10d", "3mo", "2y", "ytd", "max"])
def test_valid_periods(make_settings: Callable[..., Settings], period: str) -> None:
    assert make_settings(backfill_period=period).backfill_period == period


@pytest.mark.parametrize("period", ["5", "d5", "2years", ""])
def test_invalid_periods(make_settings: Callable[..., Settings], period: str) -> None:
    with pytest.raises(ValidationError, match="not a valid yfinance period"):
        make_settings(backfill_period=period)


def test_unknown_timezone_is_rejected(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="not a valid IANA timezone"):
        make_settings(scheduler_timezone="Mars/Olympus_Mons")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retry_max_attempts", 0),
        ("intraday_every_minutes", 0),
        ("heartbeat_every_seconds", 0),
        ("scheduler_misfire_grace_seconds", 0),
        ("retry_max_wait_seconds", -1),
    ],
)
def test_out_of_range_numbers_are_rejected(
    make_settings: Callable[..., Settings], field: str, value: int
) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_settings_are_immutable(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    with pytest.raises(ValidationError):
        settings.intraday_interval = "1d"  # type: ignore[misc]
