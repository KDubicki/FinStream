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


def percent_frame(symbols: list[str], rows: int = 5) -> pd.DataFrame:
    """Long-form output of `comparison.compare`: one row per instrument per bar."""
    return pd.DataFrame(
        {
            "ts": [TS + timedelta(days=i) for _ in symbols for i in range(rows)],
            "symbol": [s for s in symbols for _ in range(rows)],
            "close": [100.0 + i for _ in symbols for i in range(rows)],
            "percent": [float(i) for _ in symbols for i in range(rows)],
        }
    )


def test_percent_domain_always_contains_the_baseline() -> None:
    """0% is the line every series starts from, so it must never be cropped out."""
    low, high = charts.percent_domain(percent_frame(["GC=F"]))
    assert low < 0.0 < high


def test_percent_domain_contains_zero_even_when_everything_fell() -> None:
    frame = percent_frame(["GC=F"])
    frame["percent"] = [-1.0, -2.0, -3.0, -4.0, -5.0]
    low, high = charts.percent_domain(frame)
    assert low < -5.0 and high >= 0.0


def test_percent_domain_of_an_empty_frame() -> None:
    assert charts.percent_domain(pd.DataFrame()) == [-1.0, 1.0]


@pytest.mark.parametrize("symbols", [["GC=F", "SI=F"], ["GC=F", "SI=F", "CL=F", "SPY", "BTC-USD"]])
def test_percent_chart_builds_for_two_and_five_instruments(symbols: list[str]) -> None:
    chart = charts.percent_change_chart(
        percent_frame(symbols),
        labels={s: f"{s} name" for s in symbols},
        title="% change · 1d",
    )
    spec = chart.to_dict()
    assert spec["title"] == "% change · 1d"
    rules = [layer for layer in spec["layer"] if layer["mark"]["type"] == "rule"]
    assert rules, "the 0% baseline rule has to be drawn"
    assert rules[0]["encoding"]["y"]["datum"] == 0


def test_percent_chart_labels_the_legend_with_display_names() -> None:
    spec = charts.percent_change_chart(
        percent_frame(["GC=F"]), labels={"GC=F": "Gold futures · GC=F"}, title="t"
    ).to_dict()
    values = spec["datasets"][next(iter(spec["datasets"]))]
    assert {row["instrument"] for row in values} == {"Gold futures · GC=F"}


def test_percent_chart_falls_back_to_the_bare_symbol() -> None:
    """An unknown ticker still has to render, as elsewhere in the dashboard."""
    spec = charts.percent_change_chart(percent_frame(["NEW"]), labels={}, title="t").to_dict()
    values = spec["datasets"][next(iter(spec["datasets"]))]
    assert {row["instrument"] for row in values} == {"NEW"}


def test_percent_chart_uses_one_hover_selection() -> None:
    spec = charts.percent_change_chart(percent_frame(["GC=F"]), labels={}, title="t").to_dict()
    hover = next(layer for layer in spec["layer"] if layer["mark"]["type"] == "circle")
    # `.interactive()` adds an interval param for pan/zoom; only one point selection is ours.
    points = [param for param in spec["params"] if param["select"]["type"] == "point"]
    assert len(points) == 1
    assert hover["encoding"]["opacity"]["condition"]["param"] == points[0]["name"]


@pytest.mark.parametrize("rows", [1, 5, 50])
def test_ratio_chart_builds_for_various_sizes(rows: int) -> None:
    ratio = pd.DataFrame(
        {
            "ts": [TS + timedelta(days=i) for i in range(rows)],
            "ratio": [80.0 + i * 0.5 for i in range(rows)],
        }
    )
    spec = charts.ratio_chart(ratio, title="Gold / Silver").to_dict()
    assert spec["title"] == "Gold / Silver"
    assert spec["layer"][0]["encoding"]["y"]["scale"]["zero"] is False


def test_ratio_chart_without_a_title_has_no_title_block() -> None:
    ratio = pd.DataFrame({"ts": [TS], "ratio": [80.0]})
    assert "title" not in charts.ratio_chart(ratio).to_dict()
