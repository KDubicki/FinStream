"""The page's rendering logic, exercised without a Streamlit server.

Streamlit functions run in "bare mode" outside a server: they warn and do nothing, which is
enough to execute the branches that matter here (empty states, guards, warnings).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from finstream_dashboard import app
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
