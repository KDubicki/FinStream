"""Database helpers that need no database."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import OperationalError

from finstream_ingestor.db import _bar_to_row, build_retryer, upsert_bars
from finstream_ingestor.schema import PRICE_KEY_COLUMNS, PRICE_VALUE_COLUMNS
from finstream_ingestor.sources.base import PriceBar

BAR = PriceBar(
    source="yahoo",
    symbol="SPY",
    bar_interval="1d",
    ts=datetime(2026, 9, 15, tzinfo=UTC),
    open=1.0,
    high=2.0,
    low=0.5,
    close=1.5,
    adj_close=1.4,
    volume=99,
)


def operational_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("connection reset"))


def test_bar_maps_to_every_column() -> None:
    row = _bar_to_row(BAR)
    assert set(row) == set(PRICE_KEY_COLUMNS) | set(PRICE_VALUE_COLUMNS)
    assert row["symbol"] == "SPY"
    assert row["adj_close"] == 1.4


def test_upsert_with_no_bars_touches_no_database() -> None:
    # Passing None as the engine proves nothing is executed when there is nothing to write.
    assert upsert_bars(None, []) == 0  # type: ignore[arg-type]


def test_retryer_retries_operational_errors() -> None:
    attempts = 0

    def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise operational_error()
        return "ok"

    retryer = build_retryer(max_attempts=5, max_wait_seconds=0)
    assert retryer(flaky) == "ok"
    assert attempts == 3


def test_retryer_gives_up_after_max_attempts() -> None:
    attempts = 0

    def always_failing() -> None:
        nonlocal attempts
        attempts += 1
        raise operational_error()

    retryer = build_retryer(max_attempts=3, max_wait_seconds=0)
    with pytest.raises(OperationalError):
        retryer(always_failing)
    assert attempts == 3


def test_retryer_does_not_retry_other_errors() -> None:
    attempts = 0

    def broken() -> None:
        nonlocal attempts
        attempts += 1
        raise ValueError("not a database problem")

    retryer = build_retryer(max_attempts=5, max_wait_seconds=0)
    with pytest.raises(ValueError, match="not a database problem"):
        retryer(broken)
    assert attempts == 1, "a bug must fail fast, not be retried"
