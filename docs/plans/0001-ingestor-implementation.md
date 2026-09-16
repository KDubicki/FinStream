# Plan 0001: FinStream Ingestor implementation

- **Status:** In progress <!-- Draft | Approved | In progress | Done | Abandoned. Only the user sets Approved. -->
- **Created:** 2026-09-15
- **Branch:** feat/0001-ingestor
- **Related:** [ADR-0001](../adr/0001-ingestor-is-extract-load-only.md), [ADR-0002](../adr/0002-postgresql-timescaledb-raw-storage.md), [ADR-0003](../adr/0003-apscheduler-and-tenacity.md), [architecture](../architecture.md), [data model](../data-model.md), [configuration](../configuration.md)

> No code under `services/ingestor/` may be written until the user sets **Status: Approved** (AGENTS.md §4).

## Context

FinStream needs a reliable raw data foundation before any processing service can exist. The first service, the **FinStream Ingestor**, continuously collects market data (gold, ETFs, stock indices) from Yahoo Finance and loads it, unmodified, into PostgreSQL + TimescaleDB. Free market-data APIs are unofficial, rate limited and fail routinely, and the service runs unattended, so **idempotency and resilience** are the primary design drivers.

## Goals

- A Dockerised Python 3.12 daemon that fetches OHLCV bars for configured Yahoo symbols:
  - on an intraday interval
  - daily after the market close
  - optionally as a one-off backfill at startup
- Raw bars upserted into `raw.market_prices` (TimescaleDB hypertable) with no duplicates. Every attempt recorded in `raw.ingestion_runs`.
- Transient API and DB failures retried with backoff. Persistent failures logged and recorded, without stopping the daemon or other symbols.
- Everything configurable through `.env`. `docker compose up` starts the database and the Ingestor.
- A test suite that meets the Definition of Done.

## Non-goals

- Any transformation or aggregation (GR-1, ADR-0001).
- Sources other than Yahoo Finance. They're covered by plan 0002, and the `PriceSource` protocol keeps that possible.
- Alembic, a metrics endpoint, a CI pipeline, and compression/retention policies (see Follow-ups).

## Deliverables mapping

| Requested deliverable | Where | Milestone |
|---|---|---|
| 1. Directory structure | [Design § Layout](#layout) | M1 |
| 2. `requirements.txt` | `services/ingestor/requirements.txt` (+ `requirements-dev.txt`) | M1 |
| 3. Main script with scheduler + ingestion logic | `main.py`, `scheduler.py`, `jobs.py`, `sources/yahoo.py` | M3, M4 |
| 4. Database connection + schema creation | `db.py`, `schema.py` | M2 |
| 5. Optimised Dockerfile | `services/ingestor/Dockerfile` | M5 |
| 6. `docker-compose.yml` (Ingestor + PostgreSQL) | repo root `docker-compose.yml` | M5 |

## Design

### Layout

```
.
├── docker-compose.yml                  # db (TimescaleDB) + ingestor
├── .env.example                        # mirrors docs/configuration.md one-to-one
└── services/ingestor/
    ├── Dockerfile
    ├── .dockerignore
    ├── pyproject.toml                  # tool config only: ruff, mypy (strict), pytest (pythonpath=src, markers)
    ├── requirements.txt                # runtime deps, exact pins
    ├── requirements-dev.txt            # -r requirements.txt + test/lint tools, exact pins
    ├── src/finstream_ingestor/
    │   ├── __init__.py
    │   ├── main.py                     # settings → logging → wait_for_db → init_schema → scheduler; SIGTERM/SIGINT
    │   ├── config.py                   # Settings (pydantic-settings) + validation
    │   ├── logging_setup.py            # JSON / text logging
    │   ├── schema.py                   # SQLAlchemy Core tables + idempotent DDL (extension, schema, tables, hypertable)
    │   ├── db.py                       # create_engine, wait_for_db, init_schema, upsert_bars, start_run, finish_run
    │   ├── sources/
    │   │   ├── __init__.py
    │   │   ├── base.py                 # PriceBar, PriceSource Protocol, Transient/RateLimited/PermanentSourceError
    │   │   └── yahoo.py                # YahooSource: yfinance call, error classification, retry, normalisation
    │   ├── jobs.py                     # ingest(): per-symbol isolation + run recording, returns JobSummary
    │   ├── scheduler.py                # build_scheduler(): intraday, daily, backfill, heartbeat, listeners
    │   └── healthcheck.py              # Docker HEALTHCHECK: heartbeat freshness
    └── tests/
        ├── conftest.py                 # settings factory, no-network guard, PriceBar / DataFrame fixtures
        ├── unit/                       # test_config, test_logging, test_yahoo_normalise, test_yahoo_retry,
        │                               # test_jobs, test_scheduler, test_healthcheck
        └── integration/                # conftest (TimescaleDB testcontainer), test_schema, test_upsert,
                                        # test_runs, test_ingest_end_to_end
```

### Data model

The normative DDL and semantics are in [docs/data-model.md](../data-model.md). Summary:

- **`raw.market_prices`**
  - Primary key `(source, symbol, bar_interval, ts)`.
  - Values: `open`, `high`, `low`, `close`, `adj_close` (`DOUBLE PRECISION`) and `volume BIGINT`.
  - Bookkeeping: `ingested_at`, `updated_at`.
  - A hypertable on `ts` when `TIMESCALEDB_ENABLED=true`.
- **`raw.ingestion_runs`**
  - One row per (job, symbol) attempt, keyed by `run_id`.
  - `status` is one of `running`, `success`, `empty` or `failed`.
  - Also records `rows_received`, `rows_upserted` and `error`.
- **Upsert:** `INSERT … ON CONFLICT (pk) DO UPDATE SET …, updated_at = now() WHERE (values) IS DISTINCT FROM (EXCLUDED values)`. Rows are updated rather than ignored because Yahoo revises the still-forming latest bar and adjusted closes.

### Source: Yahoo Finance

- **Interface:** `YahooSource.fetch(symbol: str, interval: str, period: str) -> list[PriceBar]`.
- **Call (R1):** `yf.Ticker(symbol).history(period=…, interval=…, auto_adjust=False, actions=False)`, one call per symbol, so both `close` and `adj_close` are stored raw. At startup the service sets `yf.config.debug.hide_exceptions = False`, because otherwise yfinance logs failures and returns an empty DataFrame instead of raising. The `raise_errors` parameter is deprecated and must not be used.
- **Error classification** (confirmed in R1):

  | yfinance raises | Our type | Retried? | Run status |
  |---|---|---|---|
  | `YFRateLimitError` (always propagates) | `RateLimitedError` | yes | `failed` after exhaustion |
  | network / timeout / connection errors | `TransientSourceError` | yes | `failed` after exhaustion |
  | `YFPricesMissingError` | *(not an error)* → `[]` | no | `empty` |
  | `YFTzMissingError`, `YFTickerMissingError` | `PermanentSourceError` | no | `failed` |
  | `YFInvalidPeriodError` | `PermanentSourceError` | no | `failed` (config bug) |
  | empty DataFrame, no exception | `[]` | no | `empty` |

- **Normalisation** (GR-1 allowed list only): rename columns, convert the index to UTC-aware `datetime`, `NaN` → `None`, drop all-NaN rows, cast `volume` to `int`.
- **Retry:** a tenacity `Retrying` built from settings:
  - `retry_if_exception_type((TransientSourceError, RateLimitedError))`
  - `stop_after_attempt(RETRY_MAX_ATTEMPTS)`
  - `wait_exponential_jitter(initial=1, max=RETRY_MAX_WAIT_SECONDS)`
  - `before_sleep_log`, `reraise=True`
  - Tests inject a zero wait.

### Jobs

`ingest(job, source, engine, symbols, interval, period) -> JobSummary`. For each symbol:
1. `start_run`
2. `fetch`
3. `upsert_bars`
4. `finish_run` with `success`, or `empty` if no bars

**Failure handling:**
- `except Exception` is allowed **only** at this boundary. It leads to `finish_run(failed, error)` plus `logger.exception`.
- A failure while writing the run record is logged and not raised.
- The function returns counts per status and never raises (GR-3).

### Database access

- **Engine:** `create_engine(DATABASE_URL, pool_pre_ping=True)` with the psycopg 3 driver. The URL is logged masked only.
- **`wait_for_db`:** tenacity-retried `SELECT 1` at startup. If it's still failing afterwards, it exits non-zero so Compose restarts the container.
- **`init_schema(engine, timescaledb_enabled)`:** idempotent DDL in one transaction. With TimescaleDB enabled it runs `CREATE EXTENSION IF NOT EXISTS timescaledb` before `create_hypertable(…, by_range('ts'), if_not_exists => TRUE, migrate_data => TRUE)` (signature verified in R3).
- **`upsert_bars`:** batched (≤ 1 000 rows per statement), retried on `OperationalError`, returns the affected row count.

### Scheduler

- **Scheduler:** APScheduler 3.11.x `BlockingScheduler(timezone=SCHEDULER_TIMEZONE)`.
- **Jobs:**
  - **`intraday`:** `IntervalTrigger(minutes=INTRADAY_EVERY_MINUTES)`, first run immediately (if `INTRADAY_ENABLED`).
  - **`daily`:** `CronTrigger.from_crontab(DAILY_CRON, timezone=…)`, plus one immediate run at startup (if `DAILY_ENABLED`).
  - **`backfill`:** a one-shot at startup, `1d` bars for `BACKFILL_PERIOD` (if `BACKFILL_ON_START`).
  - **`heartbeat`:** every `HEARTBEAT_EVERY_SECONDS`, touches `HEARTBEAT_FILE`.
- **Job defaults:** `max_instances=1`, `coalesce=True`, `misfire_grace_time=SCHEDULER_MISFIRE_GRACE_SECONDS`.
- **Listener:** `EVENT_JOB_ERROR | EVENT_JOB_MISSED` → log. This is defence in depth, since jobs shouldn't raise.
- **Shutdown:** `main.py` handles SIGTERM/SIGINT with `scheduler.shutdown(wait=False)` and then `engine.dispose()`.

### Configuration

Every variable, its default and its validation rule are in [docs/configuration.md](../configuration.md). `.env.example` mirrors it one-to-one.

### Docker (sketch; tags and pins come from R3/R4)

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" PYTHONPATH=/app/src
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY src/ ./src/
USER app
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD ["python", "-m", "finstream_ingestor.healthcheck"]
CMD ["python", "-m", "finstream_ingestor.main"]
```

```yaml
# docker-compose.yml (repo root)
services:
  db:
    image: timescale/timescaledb:2.30.0-pg16
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-finstream}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: ${POSTGRES_DB:-finstream}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 20
    restart: unless-stopped

  ingestor:
    build: ./services/ingestor
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
    init: true

volumes:
  pgdata:
```

## Research items (M0)

A PyPI snapshot from 2026-09-15 was the starting point. **Final pins are in the [R4 note](../research/2026-09-16-dependency-pins.md)**, and the outcomes of all four items are summarised [below](#outcomes-2026-09-16).

| Package | Latest stable | Notes |
|---|---|---|
| yfinance | 1.7.0 | 1.x major line: check API changes vs older examples |
| pandas | 3.0.5 | Major 3.x, requires Python ≥ 3.11: check yfinance compatibility |
| SQLAlchemy | 2.0.54 | 2.1 in release candidates: stay on 2.0.x |
| psycopg | 3.3.5 | |
| APScheduler | 3.11.3 | 4.0 still alpha (4.0.0a6) |
| tenacity | 9.1.4 | |
| pydantic-settings | 2.15.0 | |
| python-json-logger | 4.2.0 | |
| testcontainers | 4.15.0 | |
| pytest / ruff / mypy | 9.1.1 / 0.16.7 / 2.3.1 | |

- [x] **R1: yfinance behaviour** → [note](../research/2026-09-16-yfinance-behaviour.md)
  - Current stable version and pandas 3 compatibility.
  - `Ticker.history()` vs `yf.download()` for per-symbol fetches.
  - How failures surface: exceptions vs empty DataFrame, the `raise_errors` option.
  - The rate-limit exception class and recommended mitigations.
  - Invalid-symbol behaviour.
  - Column names with auto-adjust disabled.
  - Index timezone for `1h` vs `1d` bars.
  - Maximum history for intraday intervals.
- [x] **R2: APScheduler and tenacity** → [note](../research/2026-09-16-apscheduler-tenacity.md)
  - Confirm that 3.11.x is the right choice.
  - `CronTrigger.from_crontab` day-of-week semantics.
  - `misfire_grace_time`, `coalesce`, `max_instances`, and listener events.
  - tenacity `Retrying` API (`wait_exponential_jitter`, `before_sleep_log`).
  - Then accept or supersede [ADR-0003](../adr/0003-apscheduler-and-tenacity.md).
- [x] **R3: TimescaleDB** → [note](../research/2026-09-16-timescaledb-testcontainers.md)
  - The image tag to pin (`<version>-pg16`, or whether pg17 is now recommended).
  - `create_hypertable(…, by_range('ts'), if_not_exists => TRUE, migrate_data => TRUE)` syntax.
  - Unique-constraint rules.
  - testcontainers `PostgresContainer` with a custom image and the psycopg 3 driver.
- [x] **R4: pins** → [note](../research/2026-09-16-dependency-pins.md)
  - Exact, mutually compatible versions of all runtime and dev dependencies for Python 3.12.
  - mypy `additional_dependencies` for the pre-commit hook.
  - Refresh `.pre-commit-config.yaml` revs.

### Outcomes (2026-09-16)

| Item | Decision |
|---|---|
| **R1** | `Ticker.history(auto_adjust=False, actions=False)` per symbol, with `yf.config.debug.hide_exceptions = False`. Error classification is fixed (table above). The index is exchange-local and tz-aware, so normalisation only needs `tz_convert("UTC")`. Columns: `Open, High, Low, Close, Adj Close, Volume`. |
| **R2** | [ADR-0003](../adr/0003-apscheduler-and-tenacity.md) **accepted**. APScheduler 3.11.3 treats `0` as Monday while crontab treats it as Sunday, and `from_crontab` keeps APScheduler's numbering, so `DAILY_CRON` keeps day names and config rejects a numeric day-of-week. `max_instances` already defaults to 1. |
| **R3** | `create_hypertable(…, by_range('ts'), if_not_exists => TRUE, migrate_data => TRUE)` confirmed against TimescaleDB's source. Every unique constraint must include the partitioning column, which our primary key does. Integration tests use the pinned image through testcontainers. |
| **R4** | Exact pins chosen; image pinned to `timescale/timescaledb:2.30.0-pg16` (ADR-0002 specifies PostgreSQL 16). ruff-pre-commit bumped to `v0.16.8` to match the ruff pin. pandas 3.0.5 with yfinance 1.7.0 **verified working** during M1 (see the Verification log). |

## Tasks

Each milestone is one Conventional Commit (code, tests and doc/plan updates together), made once its gate is green.

- [ ] **M0: Research.** R1–R4 notes in `docs/research/`; ADR-0003 accepted or superseded; this plan updated with findings (a material design change sends it back for approval). Commit: `docs(research): …`
- [x] **M1: Scaffold, config, logging.** Commit: `feat(ingestor): scaffold service with config and logging`
  - [x] `services/ingestor/` layout, `pyproject.toml`, `requirements*.txt`, `.dockerignore`
  - [x] `config.py` (all variables from configuration.md, with validation) and `logging_setup.py`
  - [x] Validation rejecting a numeric day-of-week in `DAILY_CRON`, with an error explaining APScheduler's Monday=0 mismatch (R2)
  - [x] Verify pandas 3.0.5 works with yfinance 1.7.0; if not, fall back to `pandas==2.*` and update the R4 note — **verified, no fallback needed**
  - [x] root `.env.example`; mypy `additional_dependencies` in `.pre-commit-config.yaml`
  - [x] Tests: config fail-fast, defaults, symbol list parsing, cron validation, JSON log shape
- [x] **M2: Schema and upsert.** Commit: `feat(ingestor): add raw schema and idempotent upsert`
  - [x] `schema.py`, `db.py` (`create_engine`, `wait_for_db`, `init_schema`, `upsert_bars`, `start_run`, `finish_run`)
  - [x] Session-scoped container fixture: `PostgresContainer("timescale/timescaledb:2.30.0-pg16", driver="psycopg")` imported from `testcontainers.community.postgres` (the old `testcontainers.postgres` path warns it is deprecated)
  - [x] Integration tests: schema re-entrancy + hypertable exists; idempotency; revision (`updated_at` bumped only on change); plain-PostgreSQL mode; run lifecycle
  - [x] `sources/base.py`: the `PriceBar` dataclass (the upsert needs a row type; the `PriceSource` protocol and error types still arrive in M3)
- [x] **M3: Yahoo source.** Commit: `feat(ingestor): add yahoo finance source with retries`
  - [x] `sources/base.py`, `sources/yahoo.py`
  - [x] Unit tests with mocked yfinance: normalisation / EL boundary, error classification, retry count, no retry on permanent errors, empty frame
- [x] **M4: Jobs, scheduler, main.** Commit: `feat(ingestor): schedule ingestion jobs with isolation and heartbeat`
  - [x] `jobs.py`, `scheduler.py`, `healthcheck.py`, `main.py`
  - [x] Unit tests: resilience (one symbol fails), exhaustion → failed run, empty run, jobs registered according to flags with `max_instances=1` / `coalesce=True`, heartbeat + healthcheck freshness
  - [x] Integration test: `ingest()` with a fake source against a real DB, twice → identical row count
- [ ] **M5: Docker and Compose.** Commit: `build(ingestor): add dockerfile and compose stack`
  - [ ] `Dockerfile`, and add the `ingestor` service to the existing root `docker-compose.yml` (created with `db` + `dashboard` by plan 0003)
  - [ ] Re-enable the Dependabot `docker` ecosystem in `.github/dependabot.yml` (removed until a Dockerfile exists)
  - [ ] Smoke test per the `test` skill (the user provides `.env`); `docker compose restart ingestor` → no duplicates
- [ ] **M6: Review and document.** Commit: `docs: finalize ingestor documentation` + `docs(plans): mark plan 0001 done`
  - [ ] Golden-rules review of the full diff; Definition of Done
  - [ ] README status and quickstart; architecture, data model and configuration match the implementation; `docs/README.md` indexes updated

## Test plan

| Mandatory test type (test skill) | Covered in |
|---|---|
| Idempotency, Revision | M2 `tests/integration/test_upsert.py`; M4 `tests/integration/test_ingest_end_to_end.py` |
| Re-entrancy | M2 `tests/integration/test_schema.py` |
| Retry, No retry on permanent | M3 `tests/unit/test_yahoo_retry.py` |
| Exhaustion, Resilience, Empty | M4 `tests/unit/test_jobs.py` |
| Fail fast | M1 `tests/unit/test_config.py` |
| EL boundary | M3 `tests/unit/test_yahoo_normalise.py` |
| Smoke | M5, a manual procedure recorded in the Verification log |

Coverage gate: ≥ 85% on `finstream_ingestor`.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Yahoo (unofficial API) changes or blocks requests; yfinance breaks | High | High | Pin yfinance; error classification + retries; failures visible in `raw.ingestion_runs`; source abstraction for plan 0002 |
| Rate limiting with many symbols | Medium | Medium | Sequential per-symbol fetches, backoff with jitter, conservative schedules; R1 evaluates batch download |
| pandas 3.x / yfinance 1.x incompatibilities | Medium | Medium | R1/R4 verify a compatible pair before pinning |
| Daily-bar timestamp semantics (exchange-local session date → UTC) confuse downstream | Medium | Medium | Documented in data-model.md; R1 verifies actual behaviour |
| APScheduler cron day-of-week numbering differs from classic cron | Medium | Medium | Day names (`mon-fri`) in `DAILY_CRON`; unit test on the trigger; R2 |
| Integration tests need Docker | Low | Medium | `integration` marker; reported as "not run" when Docker is unavailable |
| Switching `TIMESCALEDB_ENABLED` from false to true on existing data | Low | Low | `migrate_data => TRUE`; documented |

## Exceptions

None.

## Follow-ups

- Plan 0002: additional free sources (e.g. Alpha Vantage, Twelve Data, FRED) through `PriceSource`.
- ~~CI (GitHub Actions): pre-commit, unit + integration tests, image build.~~ Covered by [plan 0002](0002-ci-pipeline.md).
- Alembic once a non-additive schema change is needed.
- Metrics endpoint (Prometheus) and alerting on consecutive failed runs.
- TimescaleDB compression and retention policies.
- Upgrading to PostgreSQL 17/18 (images exist as `2.30.0-pg17` / `-pg18`). ADR-0002 fixes PostgreSQL 16, so this needs a new ADR that supersedes it.
- Dependency audit and update policy (e.g. `pip-audit`).
- Instrument metadata (`raw.instruments`: exchange timezone, currency, asset class).
- Version the Claude Code hook test-suite in the repo (it currently lives in a scratch directory) and wire it into pre-commit or CI, so the guards themselves are covered by GR-7.

## Definition of Done

- [ ] `ruff check`, `ruff format --check`, `mypy src` clean
- [ ] `pytest` green, coverage ≥ 85% on `src/`
- [ ] Idempotency and resilience tests exist for new or changed writers/jobs
- [ ] `docker compose up` smoke test passed
- [ ] `.env.example`, `docs/`, ADRs and README updated
- [ ] All tasks ticked, Verification log filled in, Status `Done`

## Verification log

### 2026-09-16 · M1 scaffold, config, logging

Run from `services/ingestor` in `.venv` (Python 3.12.13).

- `pip install -r requirements-dev.txt` → PASS. The pinned set resolves; **pandas 3.0.5 works with yfinance 1.7.0**, so the R4 fallback to pandas 2.x isn't needed.
- Smoke test → PASS: `yf.config.debug.hide_exceptions` defaults to `True` and is settable (R1 confirmed at runtime); `YFRateLimitError`, `YFPricesMissingError`, `YFTzMissingError`, `YFInvalidPeriodError` import; `pythonjsonlogger.json.JsonFormatter` imports.
- `ruff check .` → PASS (one E501 found and fixed first).
- `ruff format --check .` → PASS: `7 files already formatted`.
- `mypy src` → PASS: `Success: no issues found in 4 source files`.
- `pytest -m "not integration" -q --cov=finstream_ingestor --cov-fail-under=85` → PASS: `33 passed`, coverage **100%** on `src/`.
- `.env.example` ↔ `docs/configuration.md` parity → PASS: 23 variables, none undocumented, none missing.
- Integration tests → **NOT RUN**: the Docker daemon is not reachable on this machine. M2 needs Docker Desktop started.

### 2026-09-17 · M2 schema, upsert and run records

Local (Python 3.12.13 venv, run from `services/ingestor`):

- `ruff check .` → PASS, after fixing a root cause it exposed: ruff's `target-version` was `py312`, so it proposed PEP 695 generics (`def _run[T]`) which are a **syntax error on Python 3.11**. ruff now targets `py311` and mypy type-checks against 3.11, matching the support floor CI tests.
- `ruff format --check .` → PASS: `16 files already formatted`.
- `mypy src` → PASS: `Success: no issues found in 7 source files`.
- `pytest -m "not integration" -q` → PASS: `42 passed, 16 deselected`.
- `python3.11 -m compileall src tests` → PASS: every module parses on the oldest supported version.
- `pytest -m integration --collect-only` → 16 integration tests collected.
- Unit-only coverage is 78%: `db.py` is covered by the integration tests, so the 85% gate is evaluated by CI over the whole suite.
- **`pre-commit run mypy --all-files` initially FAILED** where the venv's mypy passed: the hook runs mypy in an isolated environment and `tenacity` was missing from its `additional_dependencies`, which also degraded a return type to `Any`. Adding `tenacity==9.1.4` fixed both errors. Lesson recorded: verifying a hook means running the hook, not the equivalent local command.

- **Integration tests → PASS.** Docker Desktop was started, so all 16 ran locally against `timescale/timescaledb:2.30.0-pg16`.
- `pytest --cov=finstream_ingestor --cov-fail-under=85` → **PASS: `58 passed`, coverage 95.76%** on `src/` (the first time the real Definition-of-Done gate could run on this machine).
- `pre-commit run --all-files` → PASS, every hook in its isolated environment.

**Two real defects the integration tests caught** (both invisible to unit tests, which is exactly why GR-7 forbids mocking the database):

1. `upsert_bars` returned `-1`, and `-3` for a three-batch write, because it reported `CursorResult.rowcount`, which this driver leaves at `-1` for this statement. It now counts rows via `RETURNING`, which also has the right semantics: rows skipped by the `IS DISTINCT FROM` guard are not counted as written, so a repeated run correctly reports `0`.
2. The plain-PostgreSQL test asserted that the `timescaledb` extension was absent, and failed with `assert 1 == 0`. The cause is not a bug in the service: the `timescale/timescaledb` image installs the extension into `template1`, so every database created from it inherits the extension. The honest assertion is that `market_prices` is **not registered as a hypertable**, which is what the test now checks. Recorded in [data-model.md](../data-model.md).

Two further issues found while getting the gate green: ruff's `target-version` was `py312` and proposed PEP 695 syntax that cannot parse on Python 3.11 (fixed by targeting `py311`), and the RETURNING change first broke mypy by assigning a `ReturningInsert` to a variable typed `Insert` (fixed by binding it to its own name).

### 2026-09-17 · M3 Yahoo source

- `ruff check .` / `ruff format --check .` → PASS.
- `mypy src` → PASS: `Success: no issues found in 8 source files`.
- `pytest --cov=finstream_ingestor --cov-fail-under=85` → **PASS: `76 passed`, coverage 96.94%**; `yahoo.py` itself is at 100%.
- `python3.11 -m compileall src tests` → PASS.
- `pre-commit run --all-files` → PASS in isolated environments.

Behaviour locked down by tests, all with yfinance mocked (GR-7): UTC conversion from the exchange-local index (09:30 New York → 13:30 UTC), `NaN` → `None`, all-NaN rows dropped, row count preserved (no resampling), `auto_adjust=False` so both `close` and `adj_close` are stored, rate limits and network errors retried, missing prices treated as *empty* rather than failure, wrong symbols failing fast without burning the retry budget, and naive timestamps refused rather than silently localised.

**One correction to M2's tooling change:** setting mypy's `python_version` to 3.11 broke as soon as `yahoo.py` imported pandas, because numpy ships stubs using PEP 695 `type` statements that mypy only parses when targeting 3.12+. mypy is back on 3.12; 3.11 compatibility is still enforced where it bites, by ruff (`target-version = "py311"`), by CI's 3.11 unit-test job, and by a `compileall` check.

### 2026-09-17 · M4 jobs, scheduler, main

- `ruff check .` / `ruff format --check .` → PASS.
- `mypy src` → PASS: `Success: no issues found in 12 source files`.
- `pytest --cov=finstream_ingestor --cov-fail-under=85` → **PASS: `107 passed`, coverage 95.95%**. `main.py` went from 0% to 96% and `healthcheck.py` from 67% to 94% once the wiring tests were added; without them the gate would have been scraping past at 85.69%.
- `python3.11 -m compileall src tests` → PASS.

**The R2 cron trap, now proven against the library rather than the docs.** A probe of APScheduler 3.11.3 shows `CronTrigger.from_crontab("30 22 * * 1-5")` returning Saturday 2026-09-19 22:30 as its next fire time, while `mon-fri` does not. That is a full day of wrong schedule, and it is exactly what the config validator refuses. It is now a regression test in `test_scheduler.py`.

**Two things the tests corrected in my own work:**

1. The first scheduler test asserted `job.max_instances == 1` and failed with `AttributeError`. The probe showed why: APScheduler copies job defaults onto a job only when it moves into the job store on `start()`, so a pending job carries none of them. Rather than assert on library internals, the defaults are now a small function of mine (`job_defaults(settings)`) that is tested directly.
2. `main.py` had no tests at all. It now has wiring tests covering the startup order (wait for the database, create the schema, only then schedule), that the engine is released even when startup fails, and that SIGTERM calls `shutdown(wait=False)` so `docker compose down` is not a kill.

Jobs behave as GR-3 requires: a failing symbol is recorded and the loop continues, an empty result is `empty` rather than `failed`, a database failure mid-job is contained, and even a failure while *writing the failure* does not escape.

## Change log

- 2026-09-15: created (Draft) during repository bootstrap.
