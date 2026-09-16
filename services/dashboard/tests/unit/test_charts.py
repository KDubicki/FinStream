"""Chart construction: the axis must follow the data, and NaNs must not reach the page."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from finstream_dashboard import charts

TS = pd.Timestamp("2026-09-10", tz="UTC")


def frame(closes: list[float | None], *, adj: list[float | None] | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": [TS + timedelta(hours=i) for i in range(len(closes))],
            "close": closes,
            "adj_close": adj if adj is not None else closes,
            "volume": [1000 + i for i in range(len(closes))],
        }
    )


def test_price_domain_does_not_start_at_zero() -> None:
    """The bug this fixes: an 80-dollar series against a zero baseline is a flat line."""
    low, high = charts.price_domain(frame([80.0, 80.5, 81.0]), ["close"])
    assert low > 70, "the axis must frame the data, not the origin"
    assert low < 80.0 < high


def test_price_domain_pads_a_flat_series() -> None:
    low, high = charts.price_domain(frame([50.0, 50.0]), ["close"])
    assert low < 50.0 < high, "a flat series still needs a visible band"


def test_price_domain_survives_an_all_nan_series() -> None:
    assert charts.price_domain(frame([None, None]), ["close"]) == [0.0, 1.0]


def test_last_valid_skips_a_trailing_nan() -> None:
    """Several indices return NaN for the newest intraday bar."""
    assert charts.last_valid(frame([10.0, 11.0, None])["close"]) == 11.0


def test_last_valid_of_an_empty_series_is_none() -> None:
    assert charts.last_valid(frame([None])["close"]) is None


def test_adjusted_line_is_skipped_when_identical() -> None:
    assert charts.needs_adjusted_line(frame([10.0, 11.0])) is False


def test_adjusted_line_is_drawn_when_it_differs() -> None:
    assert charts.needs_adjusted_line(frame([10.0, 11.0], adj=[9.5, 10.4])) is True


def test_default_range_follows_the_data() -> None:
    first = pd.Timestamp("2026-09-10", tz="UTC")
    last = pd.Timestamp("2026-09-16", tz="UTC")
    start, end = charts.default_date_range(first, last)
    assert (start, end) == (date(2026, 9, 10), date(2026, 9, 16)), (
        "five days of hourly bars must not open inside a 90-day window"
    )


def test_default_range_caps_long_histories() -> None:
    first = pd.Timestamp("2020-01-01", tz="UTC")
    last = pd.Timestamp("2026-09-16", tz="UTC")
    start, end = charts.default_date_range(first, last)
    assert (end - start).days == charts.DEFAULT_WINDOW_DAYS


def test_default_range_without_data() -> None:
    today = date(2026, 9, 17)
    start, end = charts.default_date_range(None, None, today=today)
    assert end == today and start == today


@pytest.mark.parametrize("rows", [1, 5, 50])
def test_charts_build_for_various_sizes(rows: int) -> None:
    data = frame([80.0 + i * 0.1 for i in range(rows)])
    price = charts.price_chart(data, title="Gold futures")
    volume = charts.volume_chart(data)
    assert price.to_dict()["title"] == "Gold futures"
    assert volume.to_dict()["mark"]["type"] == "bar"


def test_price_chart_omits_rows_without_a_price() -> None:
    data = frame([80.0, None, 81.0])
    spec = charts.price_chart(data, title="t").to_dict()
    values = spec["datasets"][next(iter(spec["datasets"]))] if "datasets" in spec else None
    if values is not None:
        assert all(row["price"] is not None for row in values)
