# ADR-0002: PostgreSQL + TimescaleDB for raw storage

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** FinStream owner
- **Related:** [data model](../data-model.md), [plan 0001](../plans/0001-ingestor-implementation.md) (R3), AGENTS.md GR-2, GR-6

## Context

The Ingestor stores OHLCV time series for a modest number of symbols at hourly and daily granularity. Requirements:
- runs locally for free
- atomic, idempotent upserts on a natural key
- SQL access for several downstream services
- room to grow into time-series features (compression, continuous aggregates) that processing services may use later

## Decision

We will use **PostgreSQL 16 with the TimescaleDB extension**, via the official `timescale/timescaledb` Docker image with a pinned tag (chosen in plan 0001 R3).

- **Schema and hypertable:** raw data lives in the `raw` schema. `raw.market_prices` is a hypertable partitioned on `ts`, with primary key `(source, symbol, bar_interval, ts)`.
- **Optional TimescaleDB:** a `TIMESCALEDB_ENABLED` flag skips the extension and the hypertable, so plain PostgreSQL keeps working (useful for managed Postgres without Timescale).
- **Access layer:** SQLAlchemy 2.0 **Core** with the **psycopg 3** driver. Writes use `INSERT … ON CONFLICT DO UPDATE`.
- **Schema management:** idempotent DDL at service startup. **Alembic is deferred** until the first non-additive schema change.

## Alternatives considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| PostgreSQL + TimescaleDB (chosen) | Full SQL, native upserts, hypertables/compression available, one DB for all services | Extension adds an image dependency | **Accepted** |
| Plain PostgreSQL only | Simplest; enough for current volume | No time-series features later without migration | Supported via flag |
| InfluxDB / QuestDB | Purpose-built time series | Weaker relational/upsert semantics for our key model; another ecosystem for downstream SQL | Rejected |
| SQLite / Parquet files | Zero infrastructure | Poor concurrent multi-service access; no server-side upserts across processes | Rejected |
| ORM (SQLAlchemy ORM) | Convenient models | Overhead and indirection for bulk upserts | Rejected; Core instead |
| Raw psycopg without SQLAlchemy | Minimal dependency | Hand-built SQL composition; no engine pooling or dialect helpers | Rejected |
| Alembic from day one | Versioned migrations | Overhead while the schema is purely additive; hypertable DDL is raw SQL anyway | Deferred |

## Consequences

### Positive
- The database guarantees idempotency (primary key + `ON CONFLICT`), not just application logic.
- Downstream services get standard SQL and can adopt TimescaleDB features later.

### Negative / risks
- TimescaleDB's community features are under the Timescale License. That's fine for self-hosted use, but should be re-checked if FinStream is ever offered as a hosted database service.
- Idempotent DDL at startup covers additive changes only. Breaking changes require introducing Alembic (GR-6).
- Integration tests need Docker (testcontainers).

## Compliance

- **GR-2 / GR-6** in AGENTS.md.
- Integration tests for re-entrancy, idempotency and revision run against the real TimescaleDB image (test skill).
- `docs/data-model.md` is the normative schema.
