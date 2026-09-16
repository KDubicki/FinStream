"""Every SQL statement the dashboard issues.

All SQL lives here so it can be tested without Streamlit, and so the read-only rule is
verifiable in one place: this service never writes (ADR-0001 keeps serving separate from
ingestion). Statements are parameter-bound, never string-formatted with user input.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Engine

#: Rows the ingestion-health view shows by default.
DEFAULT_RUN_LIMIT = 50

COVERAGE_SQL = sa.text("""
    SELECT symbol,
           bar_interval,
           count(*)  AS bars,
           min(ts)   AS first_ts,
           max(ts)   AS last_ts
    FROM raw.market_prices
    GROUP BY symbol, bar_interval
    ORDER BY symbol, bar_interval
""")

PRICE_HISTORY_SQL = sa.text("""
    SELECT ts, open, high, low, close, adj_close, volume
    FROM raw.market_prices
    WHERE symbol = :symbol
      AND bar_interval = :bar_interval
      AND ts >= :start_ts
      AND ts < :end_ts
    ORDER BY ts
    LIMIT :max_rows
""")

LATEST_BARS_SQL = sa.text("""
    SELECT ts, open, high, low, close, adj_close, volume, ingested_at, updated_at
    FROM raw.market_prices
    WHERE symbol = :symbol
      AND bar_interval = :bar_interval
    ORDER BY ts DESC
    LIMIT :limit
""")

RECENT_RUNS_SQL = sa.text("""
    SELECT started_at, finished_at, job, symbol, bar_interval, status,
           rows_received, rows_upserted, error
    FROM raw.ingestion_runs
    ORDER BY started_at DESC
    LIMIT :limit
""")

RUN_STATUS_COUNTS_SQL = sa.text("""
    SELECT status, count(*) AS runs
    FROM raw.ingestion_runs
    WHERE started_at >= now() - make_interval(hours => :hours)
    GROUP BY status
    ORDER BY status
""")

#: Used by a test that asserts the dashboard cannot write.
ALL_STATEMENTS = (
    COVERAGE_SQL,
    PRICE_HISTORY_SQL,
    LATEST_BARS_SQL,
    RECENT_RUNS_SQL,
    RUN_STATUS_COUNTS_SQL,
)


def create_read_engine(database_url: str) -> Engine:
    """Engine for read-only use. `pool_pre_ping` survives a database restart underneath us."""
    return sa.create_engine(database_url, pool_pre_ping=True, future=True)


def _frame(engine: Engine, statement: sa.TextClause, **params: Any) -> pd.DataFrame:
    with engine.connect() as conn:
        result = conn.execute(statement, params)
        return pd.DataFrame(result.mappings().all(), columns=list(result.keys()))


def coverage(engine: Engine) -> pd.DataFrame:
    """What the database holds: bars per symbol and interval, with their time span."""
    return _frame(engine, COVERAGE_SQL)


def price_history(
    engine: Engine,
    *,
    symbol: str,
    bar_interval: str,
    start: date,
    end: date,
    max_rows: int,
) -> pd.DataFrame:
    """Bars for one symbol in a date range. `end` is exclusive."""
    return _frame(
        engine,
        PRICE_HISTORY_SQL,
        symbol=symbol,
        bar_interval=bar_interval,
        start_ts=start,
        end_ts=end,
        max_rows=max_rows,
    )


def latest_bars(engine: Engine, *, symbol: str, bar_interval: str, limit: int = 20) -> pd.DataFrame:
    """The most recent bars, newest first, including their bookkeeping columns."""
    return _frame(engine, LATEST_BARS_SQL, symbol=symbol, bar_interval=bar_interval, limit=limit)


def recent_runs(engine: Engine, *, limit: int = DEFAULT_RUN_LIMIT) -> pd.DataFrame:
    """The ingestion log: what ran, what it wrote, and what failed."""
    return _frame(engine, RECENT_RUNS_SQL, limit=limit)


def run_status_counts(engine: Engine, *, hours: int = 24) -> pd.DataFrame:
    """Runs per status over a recent window, to spot a symbol that keeps failing."""
    return _frame(engine, RUN_STATUS_COUNTS_SQL, hours=hours)
