"""A real TimescaleDB with the raw schema, so the dashboard's SQL is tested for real (GR-7).

The schema DDL is repeated here rather than imported from the Ingestor: the services are
deliberately decoupled, and `docs/data-model.md` is the normative definition both follow.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from testcontainers.community.postgres import PostgresContainer

TIMESCALEDB_IMAGE = "timescale/timescaledb:2.30.0-pg16"

SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.market_prices (
    source        TEXT             NOT NULL,
    symbol        TEXT             NOT NULL,
    bar_interval  TEXT             NOT NULL,
    ts            TIMESTAMPTZ      NOT NULL,
    open          DOUBLE PRECISION,
    high          DOUBLE PRECISION,
    low           DOUBLE PRECISION,
    close         DOUBLE PRECISION,
    adj_close     DOUBLE PRECISION,
    volume        BIGINT,
    ingested_at   TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ      NOT NULL DEFAULT now(),
    CONSTRAINT market_prices_pk PRIMARY KEY (source, symbol, bar_interval, ts)
);

CREATE TABLE IF NOT EXISTS raw.ingestion_runs (
    run_id         UUID         PRIMARY KEY,
    job            TEXT         NOT NULL,
    source         TEXT         NOT NULL,
    symbol         TEXT         NOT NULL,
    bar_interval   TEXT         NOT NULL,
    started_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         TEXT         NOT NULL,
    rows_received  INTEGER,
    rows_upserted  INTEGER,
    error          TEXT
);
"""


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    with PostgresContainer(TIMESCALEDB_IMAGE, driver="psycopg") as container:
        engine = sa.create_engine(container.get_connection_url(), future=True)
        with engine.begin() as conn:
            for statement in filter(None, (s.strip() for s in SCHEMA_DDL.split(";"))):
                conn.execute(sa.text(statement))
        yield engine
        engine.dispose()


@pytest.fixture
def clean_tables(engine: Engine) -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE raw.market_prices, raw.ingestion_runs"))
    yield
