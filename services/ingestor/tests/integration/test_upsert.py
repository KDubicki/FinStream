"""Upserts must be idempotent, and must update a bar the source revised (GR-2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from finstream_ingestor.db import upsert_bars
from finstream_ingestor.schema import market_prices
from finstream_ingestor.sources.base import PriceBar

pytestmark = pytest.mark.integration

BASE_TS = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)


def make_bar(symbol: str = "SPY", *, offset: int = 0, close: float = 101.5) -> PriceBar:
    return PriceBar(
        source="yahoo",
        symbol=symbol,
        bar_interval="1h",
        ts=BASE_TS + timedelta(hours=offset),
        open=100.0,
        high=102.0,
        low=99.5,
        close=close,
        adj_close=close,
        volume=1_000 + offset,
    )


def count_rows(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(market_prices)).scalar_one()


def fetch_row(engine: Engine, symbol: str = "SPY", offset: int = 0) -> sa.Row[object]:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(market_prices).where(
                market_prices.c.symbol == symbol,
                market_prices.c.ts == BASE_TS + timedelta(hours=offset),
            )
        ).one()


@pytest.mark.usefixtures("clean_tables")
def test_upsert_inserts_new_bars(engine: Engine) -> None:
    written = upsert_bars(engine, [make_bar(offset=i) for i in range(3)])
    assert written == 3
    assert count_rows(engine) == 3


@pytest.mark.usefixtures("clean_tables")
def test_upsert_is_idempotent(engine: Engine) -> None:
    """The core guarantee: running the same job twice changes nothing."""
    bars = [make_bar(offset=i) for i in range(5)]
    upsert_bars(engine, bars)
    before = fetch_row(engine)

    written_again = upsert_bars(engine, bars)

    assert count_rows(engine) == 5
    assert written_again == 0, "unchanged rows must not be rewritten"
    after = fetch_row(engine)
    assert after.updated_at == before.updated_at
    assert after.ingested_at == before.ingested_at
    assert after.close == before.close


@pytest.mark.usefixtures("clean_tables")
def test_upsert_updates_a_revised_bar(engine: Engine) -> None:
    """Yahoo revises the latest bar and adjusted closes, so changed values must land."""
    upsert_bars(engine, [make_bar(close=101.5)])
    before = fetch_row(engine)

    written = upsert_bars(engine, [make_bar(close=123.75)])

    assert written == 1
    after = fetch_row(engine)
    assert after.close == pytest.approx(123.75)
    assert after.updated_at > before.updated_at
    assert after.ingested_at == before.ingested_at, "ingested_at records first arrival"
    assert count_rows(engine) == 1


@pytest.mark.usefixtures("clean_tables")
def test_upsert_leaves_other_rows_untouched(engine: Engine) -> None:
    upsert_bars(engine, [make_bar(symbol="SPY"), make_bar(symbol="QQQ")])
    before = fetch_row(engine, "QQQ")

    upsert_bars(engine, [make_bar(symbol="SPY", close=222.0)])

    after = fetch_row(engine, "QQQ")
    assert after.updated_at == before.updated_at
    assert after.close == before.close


@pytest.mark.usefixtures("clean_tables")
def test_upsert_batches_large_inputs(engine: Engine) -> None:
    bars = [make_bar(offset=i) for i in range(2_500)]
    written = upsert_bars(engine, bars, batch_size=1_000)
    assert written == 2_500
    assert count_rows(engine) == 2_500


@pytest.mark.usefixtures("clean_tables")
def test_same_timestamp_different_interval_is_a_separate_row(engine: Engine) -> None:
    """bar_interval is part of the natural key, so 1h and 1d bars never collide."""
    hourly = make_bar()
    daily = PriceBar(
        source=hourly.source,
        symbol=hourly.symbol,
        bar_interval="1d",
        ts=hourly.ts,
        close=hourly.close,
    )
    assert upsert_bars(engine, [hourly, daily]) == 2
    assert count_rows(engine) == 2
