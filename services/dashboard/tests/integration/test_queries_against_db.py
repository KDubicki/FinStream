"""The dashboard's SQL, executed against a real database."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from finstream_dashboard import queries

pytestmark = pytest.mark.integration

FIRST_TS = datetime(2026, 9, 1, tzinfo=UTC)


def seed_bars(
    engine: Engine, *, symbol: str = "SPY", bar_interval: str = "1d", count: int = 5
) -> None:
    rows = [
        {
            "source": "yahoo",
            "symbol": symbol,
            "bar_interval": bar_interval,
            "ts": FIRST_TS + timedelta(days=i),
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "adj_close": 100.4 + i,
            "volume": 1000 + i,
        }
        for i in range(count)
    ]
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO raw.market_prices"
                " (source, symbol, bar_interval, ts, open, high, low, close, adj_close, volume)"
                " VALUES (:source, :symbol, :bar_interval, :ts, :open, :high, :low, :close,"
                " :adj_close, :volume)"
            ),
            rows,
        )


def seed_run(engine: Engine, *, status: str = "success", symbol: str = "SPY") -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO raw.ingestion_runs"
                " (run_id, job, source, symbol, bar_interval, started_at, finished_at, status,"
                " rows_received, rows_upserted, error)"
                " VALUES (:run_id, 'daily', 'yahoo', :symbol, '1d', now(), now(), :status, 5, 5,"
                " :error)"
            ),
            {
                "run_id": str(uuid.uuid4()),
                "symbol": symbol,
                "status": status,
                "error": "boom" if status == "failed" else None,
            },
        )


@pytest.mark.usefixtures("clean_tables")
def test_empty_database_returns_empty_frames_not_errors(engine: Engine) -> None:
    """The page must render on a fresh install, before any data exists."""
    assert queries.coverage(engine).empty
    assert queries.recent_runs(engine).empty
    assert queries.run_status_counts(engine).empty
    assert queries.price_history(
        engine,
        symbol="SPY",
        bar_interval="1d",
        start=date(2026, 9, 1),
        end=date(2026, 9, 30),
        max_rows=100,
    ).empty


@pytest.mark.usefixtures("clean_tables")
def test_coverage_summarises_each_series(engine: Engine) -> None:
    seed_bars(engine, symbol="SPY", count=5)
    seed_bars(engine, symbol="QQQ", count=3)

    frame = queries.coverage(engine).set_index("symbol")
    assert int(frame.loc["SPY", "bars"]) == 5
    assert int(frame.loc["QQQ", "bars"]) == 3
    assert frame.loc["SPY", "first_ts"] == FIRST_TS


@pytest.mark.usefixtures("clean_tables")
def test_price_history_filters_by_range_and_orders_by_time(engine: Engine) -> None:
    seed_bars(engine, count=5)
    frame = queries.price_history(
        engine,
        symbol="SPY",
        bar_interval="1d",
        start=date(2026, 9, 2),
        end=date(2026, 9, 4),
        max_rows=100,
    )
    assert len(frame) == 2
    assert list(frame["ts"]) == sorted(frame["ts"])


@pytest.mark.usefixtures("clean_tables")
def test_price_history_respects_max_rows(engine: Engine) -> None:
    seed_bars(engine, count=10)
    frame = queries.price_history(
        engine,
        symbol="SPY",
        bar_interval="1d",
        start=date(2026, 9, 1),
        end=date(2026, 10, 1),
        max_rows=4,
    )
    assert len(frame) == 4


@pytest.mark.usefixtures("clean_tables")
def test_latest_bars_are_newest_first(engine: Engine) -> None:
    seed_bars(engine, count=5)
    frame = queries.latest_bars(engine, symbol="SPY", bar_interval="1d", limit=3)
    assert len(frame) == 3
    assert list(frame["ts"]) == sorted(frame["ts"], reverse=True)


@pytest.mark.usefixtures("clean_tables")
def test_run_views_report_failures(engine: Engine) -> None:
    seed_run(engine, status="success")
    seed_run(engine, status="failed", symbol="GC=F")

    runs = queries.recent_runs(engine, limit=10)
    assert len(runs) == 2
    assert set(runs["status"]) == {"success", "failed"}

    counts = queries.run_status_counts(engine, hours=24).set_index("status")
    assert int(counts.loc["failed", "runs"]) == 1
