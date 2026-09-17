# Plan 0005: catch up missed ingestion runs

- **Status:** Done <!-- Draft | Approved | In progress | Done | Abandoned. Only the user sets Approved. -->
- **Created:** 2026-09-17
- **Branch:** `main`
- **Related:** [ADR-0003](../adr/0003-apscheduler-and-tenacity.md), [plan 0001](0001-ingestor-implementation.md) M4, [architecture](../architecture.md), [configuration](../configuration.md)

## Context

Observed on the running stack on 2026-09-17: the newest stored bar was **20 hours old** while every recorded run said `success` and the service reported healthy.

The logs explain it. The host machine slept for about 18.5 hours — there is a matching gap in the heartbeat, which otherwise fires every minute. On wake APScheduler logged:

```
Run time of job "intraday 1h bars" was missed by 0:56:03
Run time of job "intraday 1h bars" was missed by 0:12:58
```

With `misfire_grace_time = 300`, a run that late is **skipped**, and `coalesce=True` had already collapsed the ~18 missed runs into one. So nothing was fetched until the next natural interval, up to an hour later.

This is a gap in the design, not a coding mistake. [ADR-0003](../adr/0003-apscheduler-and-tenacity.md) accepts an in-memory job store *because* "every job also runs at startup", which covers a **restart**. It does not cover a process that stays alive while the host is suspended — a laptop, exactly this machine.

## Goals

- After a suspend, a lost network, or any other long stall, ingestion catches up **immediately** rather than waiting for the next interval.
- Exactly one catch-up run, not one per missed interval; the overlapping lookback plus idempotent upserts (GR-2) make a single run sufficient.
- The behaviour is configurable, and the reason is documented where the next person will look.
- The heartbeat keeps its short grace: a missed beat is meaningless, and catching up on heartbeats would defeat the healthcheck's purpose.

## Non-goals

- A persistent job store (that remains ADR-0003's accepted trade-off).
- Waking the machine, or any change to the schedules themselves.

## Design

- **New setting `SCHEDULER_CATCH_UP_MISSED_RUNS`** (default `true`). When enabled, the ingestion jobs (`intraday`, `daily`, `backfill`) are registered with `misfire_grace_time=None`, APScheduler's "run however late it is". With `coalesce=True` already in place, a backlog still produces a single run.
- When disabled, those jobs fall back to `SCHEDULER_MISFIRE_GRACE_SECONDS`, the current behaviour.
- **The heartbeat is unchanged** and keeps `SCHEDULER_MISFIRE_GRACE_SECONDS`.
- `job_defaults` keeps `max_instances=1` and `coalesce=True`; only the per-job misfire grace changes.
- ADR-0003 is Accepted and therefore immutable, and this does not reverse its decision: it refines how the accepted trade-off behaves. The rationale is recorded here and in `architecture.md`.

## Tasks

- [x] **M1: Implement** — the setting, per-job misfire grace in `scheduler.py`, tests covering both modes and the heartbeat exception
- [x] **M2: Document** — `.env.example`, `docs/configuration.md`, the scheduling section of `architecture.md`
- [x] **M3: Verify** — gates green, stack restarted, CI green

## Test plan

| Type | Assertion |
|---|---|
| Default behaviour | With catch-up enabled, `intraday`, `daily` and `backfill` are registered with `misfire_grace_time=None` |
| Opt-out | With it disabled, they use `SCHEDULER_MISFIRE_GRACE_SECONDS` |
| Heartbeat exception | The heartbeat always keeps the configured grace, in both modes |
| Unchanged guarantees | `max_instances=1` and `coalesce=True` still hold, so a backlog cannot start several runs at once |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A catch-up run fires immediately after a long suspend and hits Yahoo with every symbol at once | Medium | Low | One coalesced run, symbols fetched sequentially, existing retry/backoff, and failures recorded per symbol |
| Data written for a window already stored | High | None | Upserts are idempotent (GR-2); unchanged rows are not rewritten |
| Someone wants the old behaviour | Low | Low | `SCHEDULER_CATCH_UP_MISSED_RUNS=false` restores it |

## Exceptions

- **Plan written and approved in one step**, as with plans 0002–0004: the maintainer asked for this fix directly on 2026-09-17.

## Follow-ups

- Surface staleness in the dashboard: flag a series whose newest bar is far older than its interval, so a stall is visible without reading logs.

## Definition of Done

- [x] `ruff check`, `ruff format --check`, `mypy src` clean
- [x] `pytest` green, coverage ≥ 85% on `src/`
- [x] `.env.example`, `docs/configuration.md`, `docs/architecture.md` updated
- [x] Stack restarted and healthy; CI green
- [x] Tasks ticked, Verification log filled, Status `Done`

## Verification log

### 2026-09-17 · catch-up implemented

**First, the immediate fix:** restarting the ingestor refreshed the stale data. The newest bar
moved from `2026-09-16 22:00` to `2026-09-17 18:00`, rows grew 1768 → 2052, and all 46 startup
runs succeeded. That confirmed the diagnosis: nothing was broken in the code, the schedule had
simply stopped catching up.

Gate, after the change:

- `ruff check`, `ruff format --check`, `mypy src` → PASS.
- `pytest --cov=finstream_ingestor --cov-fail-under=85` → **PASS: `115 passed`, coverage 96.02%**.
- `python3.11 -m compileall src tests` → PASS.

**Policy as the running container reports it** (built from its own environment, not from a test
fixture):

```
catch_up_missed_runs : True
ingestion grace      : None (run however late)
  intraday   grace=None
  daily      grace=None
  heartbeat  grace=from-defaults
```

Turning the flag off restores the old behaviour (`intraday grace=300`), which is covered by a test.

**A bug in my own test, not the code.** Two new tests failed at first with `AttributeError` on
`job.misfire_grace_time`. The implementation was already correct; the tests iterated over *every*
job, and a pending heartbeat carries no explicit grace because it inherits from `job_defaults`.
The lookups are now defensive, which is also how the heartbeat assertion was already written.

Stack after rebuild: all three services healthy, data lag under 10 minutes.

## Change log

- 2026-09-17: created and approved (maintainer request, after a 20-hour data staleness was traced to host suspend).
- 2026-09-17: implemented and closed; ingestion now catches up immediately after a stall.
