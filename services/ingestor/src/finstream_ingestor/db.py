"""Database access: engine, idempotent DDL, upserts and run records.

SQLAlchemy Core only, no ORM (ADR-0002). This module never calls external APIs.
Every write to a data table is an upsert on the natural key (GR-2).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, TypeVar

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from finstream_ingestor.schema import (
    CREATE_EXTENSION,
    CREATE_HYPERTABLE,
    PRICE_KEY_COLUMNS,
    PRICE_VALUE_COLUMNS,
    SCHEMA,
    ingestion_runs,
    market_prices,
    metadata,
)
from finstream_ingestor.sources.base import PriceBar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Rows per INSERT statement. Large enough to keep round-trips low, small enough to bound memory.
UPSERT_BATCH_SIZE = 1000
#: Error text stored in `ingestion_runs.error`; trimmed so one traceback cannot bloat the table.
MAX_ERROR_LENGTH = 2000


def create_db_engine(database_url: str) -> Engine:
    """Build the engine. `pool_pre_ping` drops connections the database closed underneath us."""
    engine = sa.create_engine(database_url, pool_pre_ping=True, future=True)
    # Never log credentials (GR-5).
    logger.info(
        "database engine created", extra={"url": engine.url.render_as_string(hide_password=True)}
    )
    return engine


def build_retryer(max_attempts: int, max_wait_seconds: int) -> Retrying:
    """Retry policy for transient database failures (GR-3)."""
    return Retrying(
        retry=retry_if_exception_type(OperationalError),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=1, max=max_wait_seconds),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


def _run(retryer: Retrying | None, func: Callable[[], T]) -> T:
    """Run `func`, retrying it when a retryer is supplied."""
    if retryer is None:
        return func()
    return retryer(func)


def wait_for_db(engine: Engine, retryer: Retrying | None = None) -> None:
    """Block until the database answers, so startup fails loudly rather than mid-job."""

    def probe() -> None:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))

    _run(retryer, probe)
    logger.info("database is reachable")


def init_schema(engine: Engine, *, timescaledb_enabled: bool = True) -> None:
    """Create the schema, tables and (optionally) the hypertable. Idempotent (GR-6)."""
    with engine.begin() as conn:
        if timescaledb_enabled:
            conn.execute(CREATE_EXTENSION)
        conn.execute(sa.schema.CreateSchema(SCHEMA, if_not_exists=True))
        metadata.create_all(conn, checkfirst=True)
        if timescaledb_enabled:
            conn.execute(CREATE_HYPERTABLE)
    logger.info("schema ready", extra={"schema": SCHEMA, "timescaledb": timescaledb_enabled})


def _bar_to_row(bar: PriceBar) -> dict[str, Any]:
    return {
        "source": bar.source,
        "symbol": bar.symbol,
        "bar_interval": bar.bar_interval,
        "ts": bar.ts,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "adj_close": bar.adj_close,
        "volume": bar.volume,
    }


def upsert_bars(
    engine: Engine,
    bars: Sequence[PriceBar],
    *,
    retryer: Retrying | None = None,
    batch_size: int = UPSERT_BATCH_SIZE,
) -> int:
    """Insert or update bars, returning how many rows were actually written.

    Re-running a job must not change the row count (GR-2). Rows are updated rather than ignored
    because the source revises the still-forming latest bar and adjusted closes; unchanged rows
    are left untouched, so `updated_at` keeps meaning "last real change".
    """
    if not bars:
        return 0

    affected = 0
    for start in range(0, len(bars), batch_size):
        rows = [_bar_to_row(bar) for bar in bars[start : start + batch_size]]
        statement = pg_insert(market_prices).values(rows)
        excluded = statement.excluded
        current = sa.tuple_(*(market_prices.c[name] for name in PRICE_VALUE_COLUMNS))
        incoming = sa.tuple_(*(excluded[name] for name in PRICE_VALUE_COLUMNS))
        # Count with RETURNING rather than rowcount: the driver reports -1 for this statement,
        # and rows the IS DISTINCT FROM guard skips must not be counted as written.
        upsert = statement.on_conflict_do_update(
            index_elements=list(PRICE_KEY_COLUMNS),
            set_={
                **{name: excluded[name] for name in PRICE_VALUE_COLUMNS},
                "updated_at": sa.func.now(),
            },
            where=current.is_distinct_from(incoming),
        ).returning(market_prices.c.ts)

        def execute(statement: sa.Executable = upsert) -> int:
            with engine.begin() as conn:
                return len(conn.execute(statement).all())

        affected += _run(retryer, execute)

    return affected


def start_run(
    engine: Engine,
    *,
    job: str,
    source: str,
    symbol: str,
    bar_interval: str,
    retryer: Retrying | None = None,
) -> uuid.UUID:
    """Record an attempt as `running` and return its id."""
    run_id = uuid.uuid4()
    statement = ingestion_runs.insert().values(
        run_id=run_id,
        job=job,
        source=source,
        symbol=symbol,
        bar_interval=bar_interval,
        started_at=datetime.now(UTC),
        status="running",
    )

    def execute() -> None:
        with engine.begin() as conn:
            conn.execute(statement)

    _run(retryer, execute)
    return run_id


def finish_run(
    engine: Engine,
    run_id: uuid.UUID,
    *,
    status: str,
    rows_received: int | None = None,
    rows_upserted: int | None = None,
    error: str | None = None,
    retryer: Retrying | None = None,
) -> None:
    """Close an attempt with its final status. Never raises past the caller's job boundary."""
    statement = (
        ingestion_runs.update()
        .where(ingestion_runs.c.run_id == run_id)
        .values(
            finished_at=datetime.now(UTC),
            status=status,
            rows_received=rows_received,
            rows_upserted=rows_upserted,
            error=error[:MAX_ERROR_LENGTH] if error else None,
        )
    )

    def execute() -> None:
        with engine.begin() as conn:
            conn.execute(statement)

    _run(retryer, execute)
