# Project status

**Updated:** 2026-09-17 · **Branch:** `main` (work lands here)

## In one sentence

The platform runs end to end: the Ingestor collects 23 instruments from Yahoo on a schedule into TimescaleDB, and a read-only Streamlit dashboard shows them. Only the final review (plan 0001 M6) is left.

## What already works

| Area | State |
|---|---|
| Rulebook (`AGENTS.md`), methodology, ADRs | Done. 12 golden rules, 4 ADRs, phase workflow |
| Agent skills (research / development / test) | Done, shared by every agent tool |
| Rule enforcement (Claude Code hooks + pre-commit) | Done and tested: 60 hook cases pass, pre-commit is green |
| CI (GitHub Actions) | Done (plan 0002): lint, types, tests, secret scan, weekly audit. First runs green |
| Documentation (architecture, data model, configuration) | Done, as a design spec |
| Plan 0001 + research notes R1–R4 | Done. Every open technical question is answered |
| Service: config + logging | Done |
| Service: database layer (schema, idempotent upsert, run records) | Done |
| Service: Yahoo source (fetch, classify, retry) | Done |
| Service: jobs, scheduler, entrypoint, healthcheck | Done. 107 tests, 95.95% coverage |
| Dashboard (Streamlit, read-only) | Done (plans 0003, 0004). Named instruments grouped by asset class, charts that follow the data |
| Instrument coverage | 23 symbols across metals, energy, indices, ETFs, FX, crypto and rates. 50 series stored, all runs successful |

## What doesn't exist yet

Nothing essential. M6 remains: the final golden-rules review and closing plan 0001. **The stack is runnable**: `docker compose up` starts the database, the ingestor and the dashboard.

## Plan 0001 progress

| Milestone | State |
|---|---|
| M0 research | ✅ done |
| M1 scaffold, config, logging | ✅ done |
| M2 schema + idempotent upsert | ✅ done |
| M3 Yahoo source + retries | ✅ done |
| M4 jobs, scheduler, main | ✅ done |
| M5 Docker + Compose | ✅ done |
| M6 review + docs | ⬜ next |

## Key decisions already made

- The Ingestor only fetches and stores raw data. Transformations belong to later services (ADR-0001).
- PostgreSQL 16 + TimescaleDB, primary key `(source, symbol, bar_interval, ts)`, writes are upserts, so a job can be re-run safely (ADR-0002).
- APScheduler 3.11 + tenacity. `DAILY_CRON` must use day names, because APScheduler counts `0` as Monday while classic cron counts it as Sunday (ADR-0003).
- Versions are pinned exactly. pandas 3.0.5 with yfinance 1.7.0 is verified to work.

## Running locally

`docker compose up -d --build` starts all three services. On this machine the database is published on **`127.0.0.1:5433`**, not the default 5432, because another project already uses that port; `POSTGRES_PORT` in `.env` controls it. The dashboard is at <http://127.0.0.1:8501>.

## Blocker

None.

## Next step

M6: the golden-rules review over the whole diff, a docs pass, and closing plan 0001.

More detail: [plan 0001](plans/0001-ingestor-implementation.md) · [docs index](README.md) · [rules](../AGENTS.md)
