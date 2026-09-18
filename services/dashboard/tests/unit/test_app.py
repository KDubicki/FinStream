"""The page's rendering logic, exercised without a Streamlit server.

Streamlit functions run in "bare mode" outside a server: they warn and do nothing, which is
enough to execute the branches that matter here (empty states, guards, warnings).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from finstream_dashboard import app, comparison
from finstream_dashboard.config import Settings

TS = datetime(2026, 9, 1, tzinfo=UTC)


def make_prices(rows: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": [TS + timedelta(days=i) for i in range(rows)],
            "open": [100.0 + i for i in range(rows)],
            "high": [101.0 + i for i in range(rows)],
            "low": [99.0 + i for i in range(rows)],
            "close": [100.5 + i for i in range(rows)],
            "adj_close": [100.4 + i for i in range(rows)],
            "volume": [1000 + i for i in range(rows)],
        }
    )


def make_coverage() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["SPY", "QQQ"],
            "bar_interval": ["1d", "1d"],
            "bars": [5, 3],
            "first_ts": [TS, TS],
            "last_ts": [TS + timedelta(days=4), TS + timedelta(days=2)],
        }
    )


@pytest.fixture
def settings(make_settings) -> Settings:
    return make_settings()


@pytest.fixture
def stub_loaders(monkeypatch: pytest.MonkeyPatch, settings: Settings):
    """Replace the cached database loaders; no test touches a database."""

    def install(
        *,
        coverage: pd.DataFrame | None = None,
        prices: pd.DataFrame | None = None,
        runs: pd.DataFrame | None = None,
        status_counts: pd.DataFrame | None = None,
        latest: pd.DataFrame | None = None,
    ) -> None:
        monkeypatch.setattr(app, "get_settings", lambda: settings)
        monkeypatch.setattr(
            app, "load_coverage", lambda: coverage if coverage is not None else pd.DataFrame()
        )
        monkeypatch.setattr(
            app,
            "load_prices",
            lambda *_args, **_kwargs: prices if prices is not None else pd.DataFrame(),
        )
        monkeypatch.setattr(app, "load_runs", lambda: runs if runs is not None else pd.DataFrame())
        monkeypatch.setattr(
            app,
            "load_run_status_counts",
            lambda: status_counts if status_counts is not None else pd.DataFrame(),
        )
        monkeypatch.setattr(
            app,
            "load_latest_bars",
            lambda *_args, **_kwargs: latest if latest is not None else make_prices(),
        )

    return install


def test_empty_database_shows_a_hint_instead_of_charts(stub_loaders) -> None:
    """A fresh install must explain itself rather than look broken."""
    stub_loaders()
    app.main()  # no data anywhere: must not raise


def test_main_renders_all_views_when_data_exists(stub_loaders) -> None:
    stub_loaders(
        coverage=make_coverage(),
        prices=make_prices(),
        runs=pd.DataFrame(
            {
                "started_at": [TS],
                "finished_at": [TS],
                "job": ["daily"],
                "symbol": ["SPY"],
                "bar_interval": ["1d"],
                "status": ["success"],
                "rows_received": [5],
                "rows_upserted": [5],
                "error": [None],
            }
        ),
        status_counts=pd.DataFrame({"status": ["success"], "runs": [1]}),
    )
    app.main()


def test_prices_view_handles_no_rows_in_range(stub_loaders, settings: Settings) -> None:
    stub_loaders(coverage=make_coverage(), prices=pd.DataFrame())
    app.render_prices(make_coverage(), settings)


def test_prices_view_warns_when_the_row_limit_is_hit(stub_loaders, make_settings) -> None:
    limited = make_settings(dashboard_max_rows=100)
    stub_loaders(coverage=make_coverage(), prices=make_prices(rows=3))
    app.render_prices(make_coverage(), limited)


def test_failed_runs_are_surfaced(stub_loaders) -> None:
    runs = pd.DataFrame(
        {
            "started_at": [TS, TS],
            "finished_at": [TS, TS],
            "job": ["daily", "daily"],
            "symbol": ["SPY", "GC=F"],
            "bar_interval": ["1d", "1d"],
            "status": ["success", "failed"],
            "rows_received": [5, 0],
            "rows_upserted": [5, 0],
            "error": [None, "boom"],
        }
    )
    stub_loaders(
        runs=runs, status_counts=pd.DataFrame({"status": ["success", "failed"], "runs": [1, 1]})
    )
    app.render_health()


def test_health_view_without_any_runs(stub_loaders) -> None:
    stub_loaders()
    app.render_health()


def test_coverage_view(stub_loaders) -> None:
    stub_loaders(coverage=make_coverage())
    app.render_coverage(make_coverage())


def test_cache_ttl_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The TTL is read at import time, so it must not require full Settings."""
    assert isinstance(app.CACHE_TTL_SECONDS, int)
    assert app.CACHE_TTL_SECONDS >= 0


def make_compare_coverage(symbols: tuple[str, ...] = ("GC=F", "SI=F")) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": list(symbols),
            "bar_interval": ["1d"] * len(symbols),
            "bars": [5] * len(symbols),
            "first_ts": [TS] * len(symbols),
            "last_ts": [TS + timedelta(days=4)] * len(symbols),
        }
    )


def priced(base: float, rows: int = 5) -> pd.DataFrame:
    frame = make_prices(rows=rows)
    frame["close"] = [base + i for i in range(rows)]
    frame["adj_close"] = frame["close"]
    return frame


@pytest.fixture
def stub_per_symbol(monkeypatch: pytest.MonkeyPatch):
    """Give each symbol its own bars, which a comparison needs and `stub_loaders` cannot."""

    def install(frames: dict[str, pd.DataFrame]) -> None:
        monkeypatch.setattr(
            app,
            "load_prices",
            lambda symbol, *_args, **_kwargs: frames.get(symbol, pd.DataFrame()),
        )

    return install


def test_compare_opens_on_gold_versus_silver() -> None:
    """The question this tab exists for, so it must not need two clicks to ask."""
    assert app.default_compare_selection(["SPY", "GC=F", "SI=F", "QQQ"]) == ["GC=F", "SI=F"]


def test_compare_falls_back_to_the_first_two_instruments() -> None:
    assert app.default_compare_selection(["SPY", "QQQ", "TLT"]) == ["SPY", "QQQ"]


def test_compare_selection_survives_a_single_instrument() -> None:
    assert app.default_compare_selection(["SPY"]) == ["SPY"]


def test_union_span_covers_every_selected_series() -> None:
    coverage = pd.DataFrame(
        {
            "symbol": ["GC=F", "SI=F"],
            "bar_interval": ["1d", "1d"],
            "bars": [5, 3],
            "first_ts": [TS, TS + timedelta(days=2)],
            "last_ts": [TS + timedelta(days=4), TS + timedelta(days=9)],
        }
    )
    first, last = app.union_span(coverage, ["GC=F", "SI=F"], "1d")
    assert (first, last) == (TS, TS + timedelta(days=9))


def test_union_span_without_a_matching_series() -> None:
    assert app.union_span(make_compare_coverage(), ["GC=F"], "1h") == (None, None)


def test_compare_view_renders_two_instruments(stub_loaders, stub_per_symbol, settings) -> None:
    stub_loaders(coverage=make_compare_coverage())
    stub_per_symbol({"GC=F": priced(4400.0), "SI=F": priced(52.0)})
    app.render_compare(make_compare_coverage(), settings)


def test_compare_view_asks_for_a_second_instrument(stub_loaders, settings) -> None:
    """One collected symbol cannot be compared with anything."""
    coverage = make_compare_coverage(("GC=F",))
    stub_loaders(coverage=coverage)
    app.render_compare(coverage, settings)


def test_compare_view_names_a_symbol_with_no_bars(stub_loaders, stub_per_symbol, settings) -> None:
    stub_loaders(coverage=make_compare_coverage())
    stub_per_symbol({"GC=F": priced(4400.0)})  # silver returns nothing for the range
    app.render_compare(make_compare_coverage(), settings)


def test_compare_view_survives_an_all_nan_series(stub_loaders, stub_per_symbol, settings) -> None:
    dead = priced(52.0)
    dead["close"] = [None] * len(dead)
    stub_loaders(coverage=make_compare_coverage())
    stub_per_symbol({"GC=F": priced(4400.0), "SI=F": dead})
    app.render_compare(make_compare_coverage(), settings)


def test_compare_view_warns_on_the_row_limit(stub_loaders, stub_per_symbol, make_settings) -> None:
    """A series that fills the per-symbol LIMIT is truncated history, and must say so."""
    limited = make_settings(dashboard_max_rows=100)
    stub_loaders(coverage=make_compare_coverage())
    stub_per_symbol({"GC=F": priced(4400.0, rows=100), "SI=F": priced(52.0, rows=100)})
    app.render_compare(make_compare_coverage(), limited)


def test_six_instruments_are_cut_to_the_readable_five() -> None:
    """Six lines on one axis stop being readable, so the extras are reported, not silently lost."""
    symbols = ["GC=F", "SI=F", "CL=F", "SPY", "QQQ", "TLT"]
    kept, too_many = app.limit_to_readable(symbols)
    assert kept == symbols[: comparison.MAX_COMPARE_SYMBOLS]
    assert too_many == ["TLT"]


def test_a_selection_within_the_limit_is_untouched() -> None:
    kept, too_many = app.limit_to_readable(["GC=F", "SI=F"])
    assert kept == ["GC=F", "SI=F"]
    assert too_many == []


def test_compare_view_waits_for_a_complete_date_range(
    monkeypatch: pytest.MonkeyPatch, stub_loaders, stub_per_symbol, settings
) -> None:
    """Streamlit hands back a single date between the two clicks of a range pick."""
    monkeypatch.setattr(app.st, "date_input", lambda *_args, **_kwargs: date(2026, 9, 1))
    stub_loaders(coverage=make_compare_coverage())
    stub_per_symbol({"GC=F": priced(4400.0), "SI=F": priced(52.0)})
    app.render_compare(make_compare_coverage(), settings)


def test_compare_view_when_nothing_is_comparable(stub_loaders, stub_per_symbol, settings) -> None:
    """Both series present but unusable: a message, not an empty chart."""
    dead = priced(52.0)
    dead["close"] = [None] * len(dead)
    stub_loaders(coverage=make_compare_coverage())
    stub_per_symbol({"GC=F": dead.copy(), "SI=F": dead.copy()})
    app.render_compare(make_compare_coverage(), settings)


def test_ratio_panel_renders_for_two_instruments() -> None:
    app.render_ratio({"GC=F": priced(4400.0), "SI=F": priced(52.0)}, ["GC=F", "SI=F"])


def test_ratio_panel_reports_disjoint_series() -> None:
    """A 24/7 series against an index can share no timestamps at all."""
    later = priced(52.0)
    later["ts"] = [ts + timedelta(days=30) for ts in later["ts"]]
    app.render_ratio({"GC=F": priced(4400.0), "SI=F": later}, ["GC=F", "SI=F"])


def test_ratio_panel_reads_a_flat_ratio() -> None:
    flat = make_prices(rows=3)
    flat["close"] = [100.0, 100.0, 100.0]
    app.render_ratio({"A": flat, "B": flat}, ["A", "B"])
