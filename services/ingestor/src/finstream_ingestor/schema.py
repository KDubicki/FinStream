"""The `raw` schema as SQLAlchemy Core tables (AGENTS.md GR-6).

docs/data-model.md is normative: this module must produce exactly the DDL documented there.
All DDL is idempotent so it can run on every startup.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

SCHEMA = "raw"

#: Value columns of a price bar: the ones an upsert compares and overwrites.
PRICE_VALUE_COLUMNS = ("open", "high", "low", "close", "adj_close", "volume")
#: Natural key of a bar (the primary key contains `ts`, which TimescaleDB requires).
PRICE_KEY_COLUMNS = ("source", "symbol", "bar_interval", "ts")

RUN_STATUSES = ("running", "success", "empty", "failed")

metadata = sa.MetaData(schema=SCHEMA)

market_prices = sa.Table(
    "market_prices",
    metadata,
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("symbol", sa.Text, nullable=False),
    sa.Column("bar_interval", sa.Text, nullable=False),
    sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("open", sa.Double),
    sa.Column("high", sa.Double),
    sa.Column("low", sa.Double),
    sa.Column("close", sa.Double),
    sa.Column("adj_close", sa.Double),
    sa.Column("volume", sa.BigInteger),
    sa.Column(
        "ingested_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.PrimaryKeyConstraint(*PRICE_KEY_COLUMNS, name="market_prices_pk"),
)

ingestion_runs = sa.Table(
    "ingestion_runs",
    metadata,
    sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("job", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("symbol", sa.Text, nullable=False),
    sa.Column("bar_interval", sa.Text, nullable=False),
    sa.Column(
        "started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("rows_received", sa.Integer),
    sa.Column("rows_upserted", sa.Integer),
    sa.Column("error", sa.Text),
    sa.CheckConstraint(
        "status IN ('running', 'success', 'empty', 'failed')",
        name="ingestion_runs_status_check",
    ),
    sa.Index("ingestion_runs_started_at_idx", sa.text("started_at DESC")),
    sa.Index(
        "ingestion_runs_symbol_idx",
        "source",
        "symbol",
        "bar_interval",
        sa.text("started_at DESC"),
    ),
)

#: Registers the hypertable. `if_not_exists` makes re-runs a no-op and `migrate_data` allows a
#: database that started with TIMESCALEDB_ENABLED=false to be converted later
#: (signature verified in docs/research/2026-09-16-timescaledb-testcontainers.md).
CREATE_HYPERTABLE = sa.text(
    "SELECT create_hypertable(CAST(:table AS regclass), by_range('ts'),"
    " if_not_exists => TRUE, migrate_data => TRUE)"
).bindparams(table=f"{SCHEMA}.market_prices")

CREATE_EXTENSION = sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb")

#: True when the table is registered as a hypertable (TimescaleDB catalog).
IS_HYPERTABLE = sa.text(
    "SELECT count(*) FROM timescaledb_information.hypertables"
    " WHERE hypertable_schema = :schema AND hypertable_name = :name"
).bindparams(schema=SCHEMA, name="market_prices")
