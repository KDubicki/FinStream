"""PriceBar guards the EL boundary: timezone-aware timestamps, no invented values (GR-1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from finstream_ingestor.sources.base import PriceBar


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PriceBar(source="yahoo", symbol="SPY", bar_interval="1d", ts=datetime(2026, 9, 15))


def test_non_utc_timestamp_is_accepted_and_kept_as_is() -> None:
    """Sources deliver exchange-local instants; conversion to UTC happens on the way in."""
    ny = timezone(timedelta(hours=-4))
    bar = PriceBar(
        source="yahoo", symbol="SPY", bar_interval="1d", ts=datetime(2026, 9, 15, tzinfo=ny)
    )
    assert bar.ts.utcoffset() == timedelta(hours=-4)


def test_missing_values_stay_none() -> None:
    bar = PriceBar(
        source="yahoo", symbol="^GSPC", bar_interval="1d", ts=datetime(2026, 9, 15, tzinfo=UTC)
    )
    assert (bar.open, bar.high, bar.low, bar.close, bar.adj_close, bar.volume) == (None,) * 6


def test_bars_are_immutable() -> None:
    bar = PriceBar(
        source="yahoo", symbol="SPY", bar_interval="1d", ts=datetime(2026, 9, 15, tzinfo=UTC)
    )
    with pytest.raises(AttributeError):
        bar.close = 1.0  # type: ignore[misc]
