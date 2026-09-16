# Project status

**Updated:** 2026-09-17 · **Branch:** `main` (work lands here; the plan 0001 branch was merged and deleted)

## In one sentence

The rules, documentation and the approved plan are in place; the Ingestor service has been started — configuration and logging are done and tested, the database, Yahoo source, scheduler and Docker are not written yet.

## What already works

| Area | State |
|---|---|
| Rulebook (`AGENTS.md`), methodology, ADRs | Done. 12 golden rules, 4 ADRs, phase workflow |
| Agent skills (research / development / test) | Done, shared by every agent tool |
| Rule enforcement (Claude Code hooks + pre-commit) | Done and tested: 60 hook cases pass, pre-commit is green |
| CI (GitHub Actions) | Added (plan 0002): lint, types, tests, secret scan, weekly audit. First run pending |
| Documentation (architecture, data model, configuration) | Done, as a design spec |
| Plan 0001 + research notes R1–R4 | Done. Every open technical question is answered |
| Service: config + logging | Done. 33 tests, 100% coverage on `src/` |

## What doesn't exist yet

`schema.py` and `db.py` (tables + upsert) · `sources/yahoo.py` (data fetching) · `jobs.py` and `scheduler.py` · `Dockerfile` and `docker-compose.yml`. **No data is being collected yet.**

## Plan 0001 progress

| Milestone | State |
|---|---|
| M0 research | ✅ done |
| M1 scaffold, config, logging | ✅ done |
| M2 schema + idempotent upsert | ⬜ next, **blocked** |
| M3 Yahoo source + retries | ⬜ |
| M4 jobs, scheduler, main | ⬜ |
| M5 Docker + Compose | ⬜ |
| M6 review + docs | ⬜ |

## Key decisions already made

- The Ingestor only fetches and stores raw data. Transformations belong to later services (ADR-0001).
- PostgreSQL 16 + TimescaleDB, primary key `(source, symbol, bar_interval, ts)`, writes are upserts, so a job can be re-run safely (ADR-0002).
- APScheduler 3.11 + tenacity. `DAILY_CRON` must use day names, because APScheduler counts `0` as Monday while classic cron counts it as Sunday (ADR-0003).
- Versions are pinned exactly. pandas 3.0.5 with yfinance 1.7.0 is verified to work.

## Blocker

**Docker isn't running.** M2's tests require a real TimescaleDB in a container. Start Docker Desktop, and M2 can proceed.

## Next step

Start Docker → implement M2 (schema + upsert + integration tests for idempotency).

More detail: [plan 0001](plans/0001-ingestor-implementation.md) · [docs index](README.md) · [rules](../AGENTS.md)
