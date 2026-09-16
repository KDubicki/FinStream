---
name: test
description: Testing procedure and quality gates for the FinStream repo. Use when writing or running tests; checking lint, types or coverage; running TimescaleDB integration tests (testcontainers); smoke-testing the Docker Compose stack; verifying idempotent upserts, tenacity retries or scheduler/job resilience; or whenever you need evidence that a change works before ticking a plan task or claiming Done. Defines the mandatory test types, the exact commands and how to report results.
---

# Test skill

Tests are the evidence behind every "done" claim (AGENTS.md GR-9). A task is complete only when its tests exist, the gate is green, and the real output is recorded.

## Test pyramid

| Level | Marker | Scope | Needs | Location |
|---|---|---|---|---|
| Unit | *(none)* | Pure logic: config validation, normalisation, error classification, retry policy, job orchestration with fakes, scheduler wiring | Nothing: no network, no Docker | `tests/unit/` |
| Integration | `@pytest.mark.integration` | Schema creation, upsert semantics, run recording, `ingest()` end to end with a fake source against **real** TimescaleDB | Docker (testcontainers) | `tests/integration/` |
| Smoke | manual procedure | Whole stack via Docker Compose, real Yahoo | Docker, network, a user-provided `.env` | [below](#smoke-test-docker-compose) |

## Rules

- **No network in automated tests (GR-7).** Yahoo and yfinance are always replaced, with a fake `PriceSource` or a monkeypatched yfinance call fed from DataFrame fixtures. An autouse fixture in `tests/conftest.py` fails any test that opens a TCP connection to a non-local host.
- **No DB mocks for DB logic (GR-7).** Upsert and schema tests run against the TimescaleDB image pinned in `docker-compose.yml`, via a session-scoped `testcontainers` fixture. The schema is created with the production `init_schema()`.
- **Deterministic.** No real sleeping: build retryers with zero wait in tests. No dependence on wall-clock time: inject `now` where needed.
- **Independent.** Each integration test uses unique symbols, or truncates tables in a fixture. Tests pass in any order.
- **Never weaken, skip or delete a failing test** to get to green. Fix the code, or raise the issue with the user.

## Mandatory test types

Every change that adds or modifies the listed component MUST include these tests:

| Component | Test | Assertion |
|---|---|---|
| Writer (upsert) | **Idempotency** | Upserting the same bars twice leaves row count and values unchanged |
| Writer (upsert) | **Revision** | Upserting a changed bar (same key, new `close`) updates the value and bumps `updated_at`. Unchanged rows keep their `updated_at` |
| Schema init | **Re-entrancy** | `init_schema()` runs twice without error. The hypertable exists when `TIMESCALEDB_ENABLED=true` |
| Source with retry | **Retry** | A fake raises a transient error N−1 times, then succeeds. Assert exactly N attempts |
| Source with retry | **No retry on permanent** | A permanent error (e.g. invalid symbol) is attempted exactly once |
| Source / job | **Exhaustion** | Once max attempts are exhausted, the job records `failed` with the error and doesn't raise |
| Job | **Resilience** | Symbols `[A, B, C]` where `B` raises: `A` and `C` are loaded, `B` is recorded `failed`, the job returns normally |
| Job | **Empty** | The source returns no bars: the run is recorded `empty` and no exception is raised |
| Config | **Fail fast** | A missing required or invalid variable raises a validation error naming the variable |
| Normalisation | **EL boundary** | Timestamps are UTC-aware, NaN → None, output rows = input rows minus all-NaN rows. No resampling, no filling |

## Commands

Run from `services/<service>/` inside the venv (see the `development` skill).

```bash
# 1. Lint & format
ruff check .
ruff format --check .
# 2. Types
mypy src
# 3. Unit tests (fast; run constantly)
pytest -m "not integration" -q
# 4. Integration tests (Docker must be running)
pytest -m integration -q

# Full gate: before ticking a milestone and before Done
ruff check . && ruff format --check . && mypy src \
  && pytest --cov=finstream_ingestor --cov-report=term-missing --cov-fail-under=85

# Repo-wide hooks (from repo root)
pre-commit run --all-files
```

If Docker isn't available, say so explicitly. Integration tests then count as **not run**, never as passed.

## Smoke test (Docker Compose)

Run this when runtime behaviour, the Dockerfile or Compose changes. Work from the repo root, using a `.env` that **the user** created from `.env.example`. Agents don't create or read `.env` (GR-5). Variables are expanded inside the `db` container, so the host shell never needs the secrets.

```bash
docker compose up -d --build
docker compose ps                                   # db healthy; ingestor running, then healthy
docker compose logs --tail=100 ingestor             # startup, schema init, first runs, no tracebacks

docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  SELECT symbol, bar_interval, count(*) AS bars, max(ts) AS latest
  FROM raw.market_prices GROUP BY 1, 2 ORDER BY 1, 2;"'

docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  SELECT job, symbol, status, rows_received, rows_upserted, left(error, 80) AS error
  FROM raw.ingestion_runs ORDER BY started_at DESC LIMIT 20;"'

docker compose restart ingestor                     # re-run over the same windows
# expected: failed runs have an understandable error; row counts grow only by newly published bars

docker compose down                                 # never add -v without user confirmation (GR-12)
```

## Reporting

Every run that backs a claim goes into the active plan's **Verification log**: the command, a trimmed but real output, and PASS / FAIL / SKIPPED (with reason). Never paraphrase a result you didn't see.

````markdown
### 2026-09-20 · M2 schema & upsert
- `pytest -m integration -q` → PASS
  ```
  7 passed in 14.21s
  ```
- `mypy src` → PASS: `Success: no issues found in 9 source files`
- Smoke test → SKIPPED: runtime behaviour unchanged in this slice
````
