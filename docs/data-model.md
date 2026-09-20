# Data model

> **Status: current.** The schema below is live and normative: `schema.py` must produce exactly this. Changes follow GR-6: idempotent, backward compatible, and documented here in the same change.

## 1. Conventions

- **Schema `raw`** is owned and written **only** by the FinStream Ingestor. Downstream services have read-only access.
- **Timestamps** are `TIMESTAMPTZ`, always UTC.
- **Prices** are `DOUBLE PRECISION`. The source delivers floats, and raw storage keeps them as delivered. Downstream layers may cast to `NUMERIC`.
- **Natural keys** are enforced with primary keys, and every write is an upsert (GR-2).
- **The Ingestor never deletes data rows.** Retention policies, if ever needed, get their own plan.
- **Identifiers** use snake_case. Column names that are SQL keywords (such as `interval`) are avoided, hence `bar_interval`.

## 2. `raw.market_prices`

One row per bar: source × symbol × bar interval × bar start time.

```sql
CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.market_prices (
    source        TEXT              NOT NULL,              -- data source id, e.g. 'yahoo'
    symbol        TEXT              NOT NULL,              -- source symbol as-is, e.g. 'GC=F', 'SPY', '^GSPC'
    bar_interval  TEXT              NOT NULL,              -- source interval string, e.g. '1h', '1d'
    ts            TIMESTAMPTZ       NOT NULL,              -- bar start, UTC
    open          DOUBLE PRECISION,
    high          DOUBLE PRECISION,
    low           DOUBLE PRECISION,
    close         DOUBLE PRECISION,                        -- unadjusted close
    adj_close     DOUBLE PRECISION,                        -- source-adjusted close (splits/dividends)
    volume        BIGINT,
    ingested_at   TIMESTAMPTZ       NOT NULL DEFAULT now(), -- first time this bar was stored
    updated_at    TIMESTAMPTZ       NOT NULL DEFAULT now(), -- last time any value changed
    CONSTRAINT market_prices_pk PRIMARY KEY (source, symbol, bar_interval, ts)
);
```

**TimescaleDB** (only when `TIMESCALEDB_ENABLED=true`):

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
SELECT create_hypertable('raw.market_prices', by_range('ts'),
                         if_not_exists => TRUE, migrate_data => TRUE);
```

- **Why the primary key includes `ts`:** TimescaleDB requires every unique index, primary key or exclusion constraint on a hypertable to contain all partitioning columns. Its source rejects anything else with `cannot create a unique index without the column "%s" (used in partitioning)`, hinting: "If you're creating a hypertable on a table with a primary key, ensure the partitioning column is part of the primary or composite key." (verified in [research R3](research/2026-09-16-timescaledb-testcontainers.md))
- **Chunk interval:** the default for now. Data volume is small (a handful of symbols, hourly and daily bars). Tuning, compression and retention are follow-ups.
- **`migrate_data => TRUE`:** lets a database that started with `TIMESCALEDB_ENABLED=false` be converted later. On an existing hypertable, `if_not_exists` makes the call a no-op.
- **Signature (verified 2026-09-16** against TimescaleDB's `sql/ddl_api.sql`**):** the generalised form is `create_hypertable(relation REGCLASS, dimension _timescaledb_internal.dimension_info, create_default_indexes BOOLEAN = TRUE, if_not_exists BOOLEAN = FALSE, migrate_data BOOLEAN = FALSE)`, and the dimension builder is `by_range(column_name NAME, partition_interval ANYELEMENT = NULL, partition_func regproc = NULL)`. So the call above is valid on the pinned 2.30 image.
- **Newer alternative:** TimescaleDB also supports declaring a hypertable inline, `CREATE TABLE … WITH (tsdb.hypertable, …)`, and the docs now call `create_hypertable()` a legacy function kept for existing tables. We deliberately keep `create_hypertable()`: it separates "create the table" from "make it a hypertable", which is what lets the same DDL run with `TIMESCALEDB_ENABLED=false`.
- **Plain PostgreSQL mode** (`TIMESCALEDB_ENABLED=false`) uses the same tables without the hypertable. Queries are unaffected.
- **Careful when checking that mode:** the `timescale/timescaledb` image installs the extension into `template1`, so every database created from it already reports `timescaledb` in `pg_extension`, whether or not the service enabled it (verified 2026-09-17 while writing the M2 tests). The honest check for plain mode is therefore that `market_prices` is **not registered in `timescaledb_information.hypertables`**, not that the extension is missing.

### Column semantics

| Column | Meaning |
|---|---|
| `source` | Stable lowercase id of the data source (`yahoo`). Part of the key, so multiple sources can coexist |
| `symbol` | Symbol exactly as the source uses it. No mapping to a FinStream-wide instrument id (that's downstream) |
| `bar_interval` | Interval string passed to the source (`1h`, `1d`) |
| `ts` | Bar start instant in UTC. **Daily bars:** the source labels them with the exchange-local session date, so after UTC conversion `ts` can land at e.g. `04:00Z` or `05:00Z` depending on DST. Derive the trading date downstream using the exchange timezone. Exact behaviour gets verified in plan 0001 R1 |
| `close` / `adj_close` | Both are stored raw (fetched with auto-adjust disabled). `adj_close` for past bars can change when dividends or splits happen, and such revisions update the row |
| `volume` | As reported. May be `NULL` or `0` for indices and some futures |
| `ingested_at` | Set on first insert, never updated |
| `updated_at` | Bumped only when a value actually changes |

## 3. `raw.ingestion_runs`

One row per ingestion attempt of one symbol in one job. This is the operational log.

```sql
CREATE TABLE IF NOT EXISTS raw.ingestion_runs (
    run_id         UUID          PRIMARY KEY,
    job            TEXT          NOT NULL,                 -- 'intraday' | 'daily' | 'backfill'
    source         TEXT          NOT NULL,
    symbol         TEXT          NOT NULL,
    bar_interval   TEXT          NOT NULL,
    started_at     TIMESTAMPTZ   NOT NULL,
    finished_at    TIMESTAMPTZ,
    status         TEXT          NOT NULL
                   CHECK (status IN ('running', 'success', 'empty', 'failed')),
    rows_received  INTEGER,                                -- bars returned by the source
    rows_upserted  INTEGER,                                -- rows inserted or changed
    error          TEXT                                    -- repr of the final exception, if failed
);

CREATE INDEX IF NOT EXISTS ingestion_runs_started_at_idx
    ON raw.ingestion_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_runs_symbol_idx
    ON raw.ingestion_runs (source, symbol, bar_interval, started_at DESC);
```

- **Row lifecycle:** a row is inserted with `running` at start, then updated (by `run_id`) to its final status.
- **Why this table uses a plain `INSERT`, and GR-2 still holds:** GR-2 requires upserts on *data* tables, where a repeated job must not change the row count. `ingestion_runs` is an append-only **event log**: every attempt is a distinct event with its own generated `run_id`, so two runs of the same job legitimately produce two rows, and the only update is the one that closes a row by its primary key. No natural key is being duplicated, which is what GR-2 protects against.
- **Stuck runs:** a row left in `running` means the process died mid-run.
- **Error text:** `error` must never contain credentials (GR-5).

## 4. Upsert semantics

```sql
INSERT INTO raw.market_prices AS t
    (source, symbol, bar_interval, ts, open, high, low, close, adj_close, volume)
VALUES
    (:source, :symbol, :bar_interval, :ts, :open, :high, :low, :close, :adj_close, :volume)
    -- , ... batched
ON CONFLICT (source, symbol, bar_interval, ts) DO UPDATE
SET open       = EXCLUDED.open,
    high       = EXCLUDED.high,
    low        = EXCLUDED.low,
    close      = EXCLUDED.close,
    adj_close  = EXCLUDED.adj_close,
    volume     = EXCLUDED.volume,
    updated_at = now()
WHERE (t.open, t.high, t.low, t.close, t.adj_close, t.volume)
      IS DISTINCT FROM
      (EXCLUDED.open, EXCLUDED.high, EXCLUDED.low, EXCLUDED.close, EXCLUDED.adj_close, EXCLUDED.volume);
```

- **Why update instead of ignore:** Yahoo revises data. The latest bar is still forming during the session, and adjusted closes change after corporate actions. The raw layer must reflect the source's current view.
- **Why the `WHERE … IS DISTINCT FROM`:** unchanged rows aren't rewritten. `updated_at` stays meaningful ("last real change"), and table churn stays low.
- **Consequence:** running any job twice leaves the row count and values unchanged (idempotency test), and a changed value updates exactly that row (revision test).
- **Implementation:** SQLAlchemy Core `postgresql.insert(...).on_conflict_do_update(...)`.

## 5. Example downstream queries

```sql
-- latest daily close per symbol
SELECT DISTINCT ON (symbol) symbol, ts, close, adj_close
FROM raw.market_prices
WHERE source = 'yahoo' AND bar_interval = '1d'
ORDER BY symbol, ts DESC;

-- ingestion health over the last 24 hours
SELECT symbol, bar_interval, status, count(*)
FROM raw.ingestion_runs
WHERE started_at > now() - interval '24 hours'
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;
```

## 6. Change policy

- **Additive changes** (new nullable column, new table, new index) go through a plan, idempotent DDL in `schema.py`, and an update to this document.
- **Breaking changes** (rename, type change, key change, drop) need an ADR and a plan with a migration strategy. At that point, introduce Alembic ([ADR-0002](adr/0002-postgresql-timescaledb-raw-storage.md)).
