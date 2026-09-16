"""Every ingestion attempt is recorded, so operators can see what failed and why (GR-3)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from finstream_ingestor.db import MAX_ERROR_LENGTH, finish_run, start_run
from finstream_ingestor.schema import ingestion_runs

pytestmark = pytest.mark.integration


def fetch_run(engine: Engine, run_id: object) -> sa.Row[object]:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(ingestion_runs).where(ingestion_runs.c.run_id == run_id)
        ).one()


@pytest.mark.usefixtures("clean_tables")
def test_run_starts_as_running(engine: Engine) -> None:
    run_id = start_run(engine, job="daily", source="yahoo", symbol="SPY", bar_interval="1d")
    row = fetch_run(engine, run_id)
    assert row.status == "running"
    assert row.finished_at is None
    assert row.started_at is not None


@pytest.mark.usefixtures("clean_tables")
def test_successful_run_records_counts(engine: Engine) -> None:
    run_id = start_run(engine, job="daily", source="yahoo", symbol="SPY", bar_interval="1d")
    finish_run(engine, run_id, status="success", rows_received=10, rows_upserted=7)

    row = fetch_run(engine, run_id)
    assert (row.status, row.rows_received, row.rows_upserted, row.error) == ("success", 10, 7, None)
    assert row.finished_at >= row.started_at


@pytest.mark.usefixtures("clean_tables")
def test_empty_run_is_not_a_failure(engine: Engine) -> None:
    run_id = start_run(engine, job="intraday", source="yahoo", symbol="GC=F", bar_interval="1h")
    finish_run(engine, run_id, status="empty", rows_received=0, rows_upserted=0)
    assert fetch_run(engine, run_id).status == "empty"


@pytest.mark.usefixtures("clean_tables")
def test_failed_run_stores_a_trimmed_error(engine: Engine) -> None:
    run_id = start_run(engine, job="daily", source="yahoo", symbol="SPY", bar_interval="1d")
    finish_run(engine, run_id, status="failed", error="boom " * 2_000)

    row = fetch_run(engine, run_id)
    assert row.status == "failed"
    assert len(row.error) == MAX_ERROR_LENGTH


@pytest.mark.usefixtures("clean_tables")
def test_each_attempt_is_its_own_row(engine: Engine) -> None:
    first = start_run(engine, job="daily", source="yahoo", symbol="SPY", bar_interval="1d")
    second = start_run(engine, job="daily", source="yahoo", symbol="SPY", bar_interval="1d")
    assert first != second

    with engine.connect() as conn:
        total = conn.execute(sa.select(sa.func.count()).select_from(ingestion_runs)).scalar_one()
    assert total == 2
