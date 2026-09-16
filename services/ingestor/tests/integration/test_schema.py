"""Schema creation must be idempotent and satisfy TimescaleDB's constraint rules (GR-6)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from finstream_ingestor.db import init_schema
from finstream_ingestor.schema import IS_HYPERTABLE, PRICE_KEY_COLUMNS, SCHEMA

pytestmark = pytest.mark.integration


def test_init_schema_is_reentrant(engine: Engine) -> None:
    """Running startup twice must not fail: the service re-runs DDL on every boot."""
    init_schema(engine, timescaledb_enabled=True)
    init_schema(engine, timescaledb_enabled=True)

    inspector = sa.inspect(engine)
    assert set(inspector.get_table_names(schema=SCHEMA)) >= {"market_prices", "ingestion_runs"}


def test_market_prices_is_a_hypertable(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(IS_HYPERTABLE).scalar_one() == 1


def test_primary_key_contains_the_partitioning_column(engine: Engine) -> None:
    """TimescaleDB rejects a unique constraint that omits the partitioning column."""
    primary_key = sa.inspect(engine).get_pk_constraint("market_prices", schema=SCHEMA)
    assert tuple(primary_key["constrained_columns"]) == PRICE_KEY_COLUMNS
    assert "ts" in primary_key["constrained_columns"]


def test_runs_table_rejects_an_unknown_status(engine: Engine) -> None:
    with engine.begin() as conn, pytest.raises(sa.exc.IntegrityError):
        conn.execute(
            sa.text(
                f"INSERT INTO {SCHEMA}.ingestion_runs"
                " (run_id, job, source, symbol, bar_interval, started_at, status)"
                " VALUES (gen_random_uuid(), 'daily', 'yahoo', 'SPY', '1d', now(), 'bogus')"
            )
        )


def test_plain_postgresql_mode_creates_tables_without_timescaledb(plain_engine: Engine) -> None:
    """TIMESCALEDB_ENABLED=false must still produce a usable schema."""
    inspector = sa.inspect(plain_engine)
    assert set(inspector.get_table_names(schema=SCHEMA)) >= {"market_prices", "ingestion_runs"}

    with plain_engine.connect() as conn:
        extensions = conn.execute(
            sa.text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'")
        ).scalar_one()
    assert extensions == 0
