"""Fixtures backed by a real TimescaleDB container.

Upsert and schema behaviour cannot be faithfully mocked (`ON CONFLICT`, hypertables), so these
tests run against the image pinned in docker-compose (AGENTS.md GR-7, research R3).
Requires a running Docker daemon; without one these tests do not run, and they never count
as passed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from testcontainers.community.postgres import PostgresContainer

from finstream_ingestor.db import init_schema
from finstream_ingestor.schema import ingestion_runs, market_prices

#: Pinned in docs/research/2026-09-16-dependency-pins.md; ADR-0002 fixes PostgreSQL 16.
TIMESCALEDB_IMAGE = "timescale/timescaledb:2.30.0-pg16"
#: Separate database used to prove the service also works on plain PostgreSQL.
PLAIN_DATABASE = "finstream_plain"


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    # driver="psycopg" because the default is psycopg2, which this project does not use.
    with PostgresContainer(TIMESCALEDB_IMAGE, driver="psycopg") as container:
        yield container


@pytest.fixture(scope="session")
def engine(postgres_container: PostgresContainer) -> Iterator[Engine]:
    """Engine against a database with TimescaleDB enabled, schema already created."""
    engine = sa.create_engine(
        postgres_container.get_connection_url(), pool_pre_ping=True, future=True
    )
    init_schema(engine, timescaledb_enabled=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def plain_engine(postgres_container: PostgresContainer) -> Iterator[Engine]:
    """Engine against a second database where TimescaleDB is deliberately not enabled."""
    base_url = postgres_container.get_connection_url()
    admin = sa.create_engine(base_url, isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": PLAIN_DATABASE}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{PLAIN_DATABASE}"'))
    admin.dispose()

    engine = sa.create_engine(base_url.rsplit("/", 1)[0] + f"/{PLAIN_DATABASE}", future=True)
    init_schema(engine, timescaledb_enabled=False)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_tables(engine: Engine) -> Iterator[None]:
    """Start each test from empty tables, so tests stay order-independent."""
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {market_prices.fullname}, {ingestion_runs.fullname}"))
    yield
