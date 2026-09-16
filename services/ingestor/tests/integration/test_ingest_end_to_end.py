"""The whole job against a real database: fetch, store, record, and do it again safely."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from finstream_ingestor.jobs import ingest
from finstream_ingestor.schema import ingestion_runs, market_prices
from finstream_ingestor.sources.base import PriceBar, TransientSourceError

pytestmark = pytest.mark.integration

START = datetime(2026, 9, 14, tzinfo=UTC)


class ScriptedSource:
    name = "yahoo"

    def __init__(self, bars_by_symbol: dict[str, object]) -> None:
        self.bars_by_symbol = bars_by_symbol

    def fetch(self, symbol: str, *, interval: str, period: str) -> list[PriceBar]:
        outcome = self.bars_by_symbol[symbol]
        if isinstance(outcome, BaseException):
            raise outcome
        return list(outcome)  # type: ignore[arg-type]


def bars_for(symbol: str, count: int = 3, *, close: float = 100.0) -> list[PriceBar]:
    return [
        PriceBar(
            source="yahoo",
            symbol=symbol,
            bar_interval="1d",
            ts=START + timedelta(days=i),
            open=99.0,
            high=101.0,
            low=98.0,
            close=close + i,
            adj_close=close + i,
            volume=1_000 + i,
        )
        for i in range(count)
    ]


def count_rows(engine: Engine, table: sa.Table) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


@pytest.mark.usefixtures("clean_tables")
def test_running_the_same_job_twice_changes_nothing(engine: Engine) -> None:
    """End-to-end idempotency: the schedules overlap on purpose (GR-2)."""
    source = ScriptedSource({"SPY": bars_for("SPY")})

    first = ingest(
        engine=engine, source=source, job="daily", symbols=["SPY"], interval="1d", period="10d"
    )
    rows_after_first = count_rows(engine, market_prices)

    second = ingest(
        engine=engine, source=source, job="daily", symbols=["SPY"], interval="1d", period="10d"
    )

    assert first.rows_upserted == 3
    assert second.rows_upserted == 0, "nothing changed, so nothing should be rewritten"
    assert count_rows(engine, market_prices) == rows_after_first == 3
    assert count_rows(engine, ingestion_runs) == 2, "each attempt is still recorded"


@pytest.mark.usefixtures("clean_tables")
def test_a_failing_symbol_is_isolated_and_recorded(engine: Engine) -> None:
    source = ScriptedSource(
        {
            "SPY": bars_for("SPY"),
            "BROKEN": TransientSourceError("yahoo is down"),
            "QQQ": bars_for("QQQ"),
        }
    )

    summary = ingest(
        engine=engine,
        source=source,
        job="intraday",
        symbols=["SPY", "BROKEN", "QQQ"],
        interval="1d",
        period="5d",
    )

    assert (summary.succeeded, summary.failed) == (2, 1)
    assert count_rows(engine, market_prices) == 6

    with engine.connect() as conn:
        failed = conn.execute(
            sa.select(
                ingestion_runs.c.symbol, ingestion_runs.c.status, ingestion_runs.c.error
            ).where(ingestion_runs.c.status == "failed")
        ).all()
    assert len(failed) == 1
    assert failed[0].symbol == "BROKEN"
    assert "yahoo is down" in failed[0].error


@pytest.mark.usefixtures("clean_tables")
def test_revised_bars_are_updated_on_the_next_run(engine: Engine) -> None:
    """Yahoo revises the latest bar, so a second run must write the new value."""
    ingest(
        engine=engine,
        source=ScriptedSource({"SPY": bars_for("SPY", 1, close=100.0)}),
        job="daily",
        symbols=["SPY"],
        interval="1d",
        period="10d",
    )
    summary = ingest(
        engine=engine,
        source=ScriptedSource({"SPY": bars_for("SPY", 1, close=123.5)}),
        job="daily",
        symbols=["SPY"],
        interval="1d",
        period="10d",
    )

    assert summary.rows_upserted == 1
    with engine.connect() as conn:
        close = conn.execute(sa.select(market_prices.c.close)).scalar_one()
    assert close == pytest.approx(123.5)
    assert count_rows(engine, market_prices) == 1


@pytest.mark.usefixtures("clean_tables")
def test_empty_results_are_recorded_without_writing_rows(engine: Engine) -> None:
    summary = ingest(
        engine=engine,
        source=ScriptedSource({"SPY": []}),
        job="daily",
        symbols=["SPY"],
        interval="1d",
        period="10d",
    )

    assert (summary.empty, summary.succeeded, summary.failed) == (1, 0, 0)
    assert count_rows(engine, market_prices) == 0
    with engine.connect() as conn:
        status = conn.execute(sa.select(ingestion_runs.c.status)).scalar_one()
    assert status == "empty"
