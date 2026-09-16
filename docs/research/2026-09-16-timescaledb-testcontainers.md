# Research: TimescaleDB hypertables and integration-test containers (plan 0001, R3)

- **Date:** 2026-09-16
- **Author:** Claude Opus 5 (Claude Code), for review by the FinStream owner
- **Related plan / item:** [plan 0001](../plans/0001-ingestor-implementation.md), R3; [ADR-0002](../adr/0002-postgresql-timescaledb-raw-storage.md)
- **Status:** Final

## Question

Which `create_hypertable` syntax is valid on the image we pin, what are the rules for primary keys and unique constraints on a hypertable, and how do the integration tests get a real TimescaleDB instance? This unblocks `schema.py`, `db.py` and the M2 test fixtures.

## Findings

### `create_hypertable` signatures
From TimescaleDB's own `sql/ddl_api.sql` (main branch, checked 2026-09-16):

```sql
-- generalised form (dimension builder)
create_hypertable(
    relation                REGCLASS,
    dimension               _timescaledb_internal.dimension_info,
    create_default_indexes  BOOLEAN = TRUE,
    if_not_exists           BOOLEAN = FALSE,
    migrate_data            BOOLEAN = FALSE
) RETURNS TABLE(hypertable_id INT, created BOOL)

-- dimension builders
by_range(column_name NAME, partition_interval ANYELEMENT = NULL, partition_func regproc = NULL)
by_hash(column_name NAME, number_partitions INTEGER, partition_func regproc = NULL)
```

The older positional form (`relation, time_column_name, …, chunk_time_interval, create_default_indexes, if_not_exists, partitioning_func, migrate_data, …`) still exists for backward compatibility.

**Conclusion:** the call planned in [data-model.md](../data-model.md) is valid exactly as written:
```sql
SELECT create_hypertable('raw.market_prices', by_range('ts'),
                         if_not_exists => TRUE, migrate_data => TRUE);
```

### Unique constraints must include the partitioning column
TimescaleDB's `src/indexing.c` rejects a unique index that misses a partitioning column:

> error: `cannot create a unique index without the column "%s" (used in partitioning)`
> hint: "If you're creating a hypertable on a table with a primary key, ensure the partitioning column is part of the primary or composite key."

The rule covers unique, primary key and exclusion indexes: they must contain **all** partitioning dimensions, so the constraint holds across the whole hypertable rather than per chunk. Our primary key `(source, symbol, bar_interval, ts)` contains `ts`, so it satisfies this, and it's also what makes `ON CONFLICT` usable for the upsert (GR-2).

### Legacy vs. modern syntax
The TigerData docs now present `CREATE TABLE … WITH (tsdb.hypertable, …)` as the recommended way to create a **new** hypertable and describe `create_hypertable()` as a legacy function kept for converting existing tables, recommended below TimescaleDB 2.20.0.

**Decision: keep `create_hypertable()`.** It separates "create the table" from "make it a hypertable", which is exactly what lets the same DDL run with `TIMESCALEDB_ENABLED=false` on plain PostgreSQL (ADR-0002). The `WITH (tsdb.hypertable)` form would fork the DDL into two variants.

### Image
- `timescale/timescaledb` publishes `2.30.0` for pg16, pg17 and pg18 (updated 2026-09-10), each with an `-oss` variant.
- **Pinned: `timescale/timescaledb:2.30.0-pg16`**, because [ADR-0002](../adr/0002-postgresql-timescaledb-raw-storage.md) is Accepted and specifies PostgreSQL 16. Moving to pg17/pg18 needs a new ADR (now a plan follow-up).

### testcontainers (4.15.0)
From `src/testcontainers/community/postgres/__init__.py` (main, checked 2026-09-16):

```python
class PostgresContainer(DbContainer):
    def __init__(self, image: str = "postgres:latest", port: int = 5432,
                 username: Optional[str] = None, password: Optional[str] = None,
                 dbname: Optional[str] = None, driver: Optional[str] = "psycopg2", **kwargs)
```

- **Import path:** `testcontainers.community.postgres`. The old `testcontainers.postgres` module is a shim that raises `DeprecationWarning`.
- **Driver:** defaults to `psycopg2`, and `get_connection_url()` renders `postgresql+<driver>://…`. We use psycopg 3, so the fixture **must** pass `driver="psycopg"`.
- **Any Postgres-compatible image works:** `_configure()` only sets `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`, which the TimescaleDB image honours because it builds on the official Postgres entrypoint. Username, password and dbname default to env values or `test`.
- **Readiness:** `_connect()` waits with an `ExecWaitStrategy` that runs `psql` inside the container, so no host client is needed.
- The `postgres` extra exists (`provides_extra` on PyPI), so `testcontainers[postgres]==4.15.0` is the correct pin.

### Local Docker
`docker info` currently fails with "Cannot connect to the Docker daemon", checked 2026-09-16. Docker Desktop must be running before M2's integration tests, otherwise they count as **not run** (test skill), never as passed.

## Recommendation

1. Keep the DDL as documented: `CREATE EXTENSION IF NOT EXISTS timescaledb`, then `create_hypertable('raw.market_prices', by_range('ts'), if_not_exists => TRUE, migrate_data => TRUE)`, all guarded by `TIMESCALEDB_ENABLED`.
2. Keep the primary key `(source, symbol, bar_interval, ts)`. It satisfies TimescaleDB's rule and drives the upsert.
3. Integration fixture (session-scoped):
   ```python
   from testcontainers.community.postgres import PostgresContainer

   with PostgresContainer("timescale/timescaledb:2.30.0-pg16", driver="psycopg") as pg:
       engine = create_engine(pg.get_connection_url())  # postgresql+psycopg://...
   ```
4. Assert the hypertable exists by querying `timescaledb_information.hypertables` rather than trusting the DDL to have worked.
5. Test plain-PostgreSQL mode with the **same image** and `TIMESCALEDB_ENABLED=false`, asserting no hypertable is registered. That avoids pulling a second image.
6. Tell the user to start Docker Desktop before M2.

## Risks & open questions

- `create_hypertable()` is "legacy", so a future major release could remove it. Then the DDL moves to `CREATE TABLE … WITH (tsdb.hypertable)` under a new plan.
- The TimescaleDB image is the community (Timescale License) build. That's fine self-hosted, as ADR-0002 records.

## Sources

1. <https://github.com/timescale/timescaledb/blob/main/sql/ddl_api.sql> (main, 2026-09-16)
2. <https://github.com/timescale/timescaledb/blob/main/src/indexing.c> (main, 2026-09-16)
3. <https://www.tigerdata.com/docs/api/latest/hypertable/create_hypertable>, <https://www.tigerdata.com/docs/reference/timescaledb/hypertables/create_table> (2026-09-16)
4. <https://hub.docker.com/v2/repositories/timescale/timescaledb/tags> (2026-09-16)
5. <https://github.com/testcontainers/testcontainers-python/blob/main/src/testcontainers/community/postgres/__init__.py> (main, 2026-09-16), <https://pypi.org/pypi/testcontainers/json> (4.15.0)
