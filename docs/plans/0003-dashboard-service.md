# Plan 0003: Streamlit dashboard service

- **Status:** In progress <!-- Draft | Approved | In progress | Done | Abandoned. Only the user sets Approved. -->
- **Created:** 2026-09-17
- **Branch:** `main` (single-maintainer repo, AGENTS.md GR-12)
- **Related:** [ADR-0001](../adr/0001-ingestor-is-extract-load-only.md), [ADR-0002](../adr/0002-postgresql-timescaledb-raw-storage.md), [architecture](../architecture.md), [data model](../data-model.md)

## Context

The Ingestor collects raw bars, but nothing shows them. The maintainer asked for a Streamlit app to look at the data.

A dashboard is a **serving** concern, and [ADR-0001](../adr/0001-ingestor-is-extract-load-only.md) keeps the Ingestor to Extract & Load, so this is a **separate service** (`services/dashboard`) that reads `raw` and never writes to it. It does not replace plan 0001 M4 (jobs, scheduler, main): without that, nothing would fetch data for the dashboard to show.

## Goals

- A Streamlit app that reads `raw.market_prices` and `raw.ingestion_runs` **read-only**.
- Pick a symbol, a bar interval and a date range; see the price series and volume.
- An ingestion-health view: recent runs, their status, rows written, errors — so a failing symbol is visible.
- Runs via `docker compose up` next to the database, on its own port.
- Sensible behaviour before any data exists: an empty state that explains what to do, not a stack trace.

## Non-goals

- Writing to the database, or any transformation beyond what a chart needs for display. Derived series, indicators and aggregates belong to a processing service (ADR-0001).
- Authentication, multi-user features, deployment beyond local Compose.
- Replacing plan 0001 M4.

## Design

### Layout
```
services/dashboard/
├── Dockerfile
├── requirements.txt / requirements-dev.txt     # exact pins (GR-8)
├── pyproject.toml                              # ruff, mypy, pytest config
├── src/finstream_dashboard/
│   ├── config.py        # Settings: DATABASE_URL, default symbols, refresh interval
│   ├── queries.py       # every SQL statement, parameter-bound, returning DataFrames
│   └── app.py           # Streamlit UI only: layout, widgets, charts
└── tests/{unit,integration}/
```

### Boundaries
- `queries.py` holds all SQL and is independently testable; `app.py` holds no SQL.
- The app only ever issues `SELECT`s. A follow-up may give it a database role that cannot write.
- Query results are cached with `st.cache_data(ttl=…)` so a page interaction does not re-query the database every rerun.

### Views
1. **Prices** — symbol and interval pickers, date range, a price chart (close and adjusted close), a volume chart, and a table of the most recent bars.
2. **Coverage** — per symbol and interval: number of bars, first and last timestamp, so gaps are obvious.
3. **Ingestion health** — the latest rows from `raw.ingestion_runs`: status, counts, error text, plus a per-status count for the last 24 hours.

### Configuration
Reuses `DATABASE_URL` from the same `.env` (GR-4). New variables (`DASHBOARD_PORT`, `DASHBOARD_CACHE_TTL_SECONDS`, `DASHBOARD_DEFAULT_INTERVAL`) are documented in `docs/configuration.md` and added to `.env.example` in the same change.

### Compose
A `dashboard` service built from `services/dashboard`, `depends_on: db (service_healthy)`, published on `127.0.0.1:${DASHBOARD_PORT}` only.

## Tasks

- [x] **M1: Queries and configuration** — `config.py`, `queries.py`, unit tests for statement construction, integration tests against the TimescaleDB container with seeded rows (reusing the plan 0001 fixtures)
- [x] **M2: The app** — `app.py` with the three views, empty states, and caching; a test that imports the module and renders its query layer headlessly
- [ ] **M3: Packaging and docs** — Dockerfile ✅, Compose service ✅, `.env.example` ✅, `docs/configuration.md` ✅, architecture and README ✅, [ADR-0005](../adr/0005-serving-reads-raw-directly.md) ✅. **Open:** the Compose smoke test, which needs a `.env` that only the maintainer can create (GR-5)

## Test plan

| Mandatory type (test skill) | Applies here |
|---|---|
| Fail fast | Config validation: a missing `DATABASE_URL` stops the app with a clear message |
| EL boundary | n/a for a read-only consumer, but queries must not write: a test asserts every statement is a SELECT |
| Idempotency / resilience | n/a: the dashboard writes nothing |
| Integration | Queries run against real TimescaleDB with seeded bars, including the empty-database case |
| Smoke | `docker compose up dashboard` serves the page and shows the seeded data |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Dashboard drifts into doing transformations | Medium | Medium | All SQL in `queries.py`, reviewed against GR-1; aggregation limited to what a chart needs |
| Heavy queries on a growing table | Medium | Low | Date-range filters, `LIMIT`, and cached results |
| Streamlit reruns hammer the database | Medium | Medium | `st.cache_data` with a TTL from configuration |
| An empty database looks broken | High | Low | Explicit empty states pointing at the Ingestor |

## Exceptions

- **Plan written and approved in one step**, as with plan 0002: the maintainer asked for the dashboard directly on 2026-09-17.

## Follow-ups

- A read-only database role for the dashboard.
- Moving the dashboard onto a processing/serving schema once one exists, superseding ADR-0005.
- Auto-refresh, and alerting on consecutive failed runs.

## Definition of Done

- [ ] `ruff check`, `ruff format --check`, `mypy src` clean for the new service
- [ ] `pytest` green, coverage ≥ 85% on `src/`
- [ ] `docker compose up` smoke test passed, with the page reachable and showing data
- [ ] `.env.example`, `docs/`, ADR-0005 and README updated
- [ ] All tasks ticked, Verification log filled in, Status `Done`

## Verification log

### 2026-09-17 · M1 + M2

Run from `services/dashboard` in its own venv (Python 3.12.13):

- `ruff check .` and `ruff format --check .` → PASS (after fixing four `ARG005` findings in the UI tests).
- `mypy src` → PASS: `Success: no issues found in 4 source files`.
- `pytest --cov=finstream_dashboard --cov-fail-under=85` → **PASS: `31 passed`, coverage 90.18%**.
- Integration tests (6) → PASS against `timescale/timescaledb:2.30.0-pg16`, including the empty-database case, range filtering, `LIMIT`, newest-first ordering and the failed-run views.
- `python3.11 -m compileall src tests` → PASS.
- Import smoke with `DATABASE_URL` unset → PASS: the module imports without constructing `Settings`.

**Two bugs found while getting the gate green, both in the new code:**

1. `@st.cache_data(ttl=_ttl())` built `Settings` at **import time**, so importing the app without `DATABASE_URL` raised a validation error. The TTL now comes from the environment directly, and `Settings` is only constructed inside the cached resource.
2. `start, end = st.date_input(...)` unpacked blindly. Streamlit returns a **one-element** tuple while the user is still choosing the second date, so the page would have crashed mid-interaction. The view now guards the selection and asks for an end date instead.

Also fixed: two test modules shared the basename `test_queries.py`, which pytest cannot collect without packages; they now have distinct names.

**CI was extended in the same change**: `test`, `integration` and `docker-build` now run as a matrix over `[ingestor, dashboard]`, so the new service is gated exactly like the first one. Without that it would have been invisible to CI.

**Not yet verified:** `docker compose up dashboard`. It needs a `.env` with `POSTGRES_PASSWORD`, which agents must not create (GR-5), so this is the maintainer's step.

## Change log

- 2026-09-17: created and approved (maintainer request).
