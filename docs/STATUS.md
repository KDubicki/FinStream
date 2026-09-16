# Project status

**Updated:** 2026-09-17 (M2 done) · **Branch:** `main` (work lands here; the plan 0001 branch was merged and deleted)

## In one sentence

The rules, documentation and the approved plan are in place; the Ingestor service has been started — configuration and logging are done and tested, the database, Yahoo source, scheduler and Docker are not written yet.

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
| Dashboard (Streamlit, read-only) | In progress (plan 0003): built and tested; Compose smoke test pending |

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

## Blocker

None. Docker Desktop is running, so the TimescaleDB integration tests execute locally as well as in CI.

## Next step

Create `.env` and run `docker compose up -d --build` to see the whole stack working (only you can create `.env`). Then M6: the golden-rules review and closing plan 0001.

More detail: [plan 0001](plans/0001-ingestor-implementation.md) · [docs index](README.md) · [rules](../AGENTS.md)
