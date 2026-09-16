# ADR-0003: APScheduler 3.x for scheduling, tenacity for retries

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** FinStream owner
- **Related:** [architecture](../architecture.md), [plan 0001](../plans/0001-ingestor-implementation.md) (R2), AGENTS.md GR-3

> Accepted on 2026-09-16 after [research R2](../research/2026-09-16-apscheduler-tenacity.md) confirmed every API detail below, including the day-of-week numbering mismatch.

## Context

The Ingestor runs continuously in one container and triggers jobs:
- at fixed intervals (intraday)
- on a cron schedule after market close (daily)
- once at startup (backfill)

Jobs must never overlap themselves. A backlog of missed runs should collapse into one run, and triggers need to be timezone-aware. Every external call (Yahoo, DB) can fail transiently and must be retried with backoff, without crashing the process.

State of the ecosystem, checked on PyPI on 2026-09-15:
- **APScheduler:** latest stable is 3.11.3 (released 2026-06-28). The 4.0 line is still pre-release (4.0.0a6).
- **tenacity:** latest stable is 9.1.4.

## Decision

We will use:

- **APScheduler 3.11.x** with an in-process `BlockingScheduler` and the in-memory job store.
  - Triggers: `IntervalTrigger` (intraday) and `CronTrigger.from_crontab` (daily; day-of-week written as **names** such as `mon-fri`), plus one-shot jobs at startup.
  - Job defaults: `max_instances=1`, `coalesce=True`, `misfire_grace_time` from config.
  - Listeners for `EVENT_JOB_ERROR` / `EVENT_JOB_MISSED` as defence in depth.
- **tenacity 9.x** for retries.
  - `Retrying` objects built from settings: `stop_after_attempt`, `wait_exponential_jitter`, `retry_if_exception_type` for transient errors only, `before_sleep_log`, `reraise=True`.
  - Building from settings (rather than hardcoded decorators) keeps the policy configurable (GR-4) and lets tests inject zero waits.

## Alternatives considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| APScheduler 3.11.x (chosen) | Mature; cron + interval triggers; timezones; coalesce/misfire/max_instances | In-memory store loses schedule state on restart; single instance only | **Accepted** |
| APScheduler 4.x | Modern async design, persistent data stores | Pre-release (alpha), API still changing | Rejected for now; revisit when stable |
| `schedule` library | Very simple | No cron expressions, no timezone/misfire/coalesce handling | Rejected |
| System cron / supercronic in the container | Familiar | Process-per-run, no in-process retries/state, harder logging and health | Rejected |
| Celery beat, Airflow, Prefect, Dagster | Distributed, UI, persistence | Broker/webserver/metadata DB: heavy for one EL daemon | Rejected for now |
| Hand-rolled retry loops | No dependency | Reinventing backoff/jitter; harder to test consistently | Rejected (tenacity) |

## Consequences

### Positive
- A single-process daemon with no extra infrastructure.
- Well-defined overlap and misfire semantics, and retry behaviour that is testable and configurable.

### Negative / risks
- **Schedule state isn't persisted.** This is acceptable because every job also runs at startup, and overlapping lookbacks plus idempotent upserts heal gaps.
- **Single replica only.** Two replicas would fetch everything twice. Data stays correct thanks to GR-2, but API quota is wasted. Scaling out would need a persistent job store or a DB advisory lock (a future ADR).
- **Day-of-week numbering:** verified in R2. The APScheduler 3.x docs state that "APScheduler treats 0 as Monday while the original crontab treats it as Sunday", and `CronTrigger.from_crontab` keeps APScheduler's numbering, so `1-5` would mean Tue-Sat. Day names are therefore mandatory in `DAILY_CRON`, and config validation rejects a numeric day-of-week field.
- **Future migration:** moving to APScheduler 4.x once it's stable will need a plan.

## Compliance

- **GR-3** in AGENTS.md.
- Retry, exhaustion and resilience tests (test skill).
- Scheduler wiring unit test asserts `max_instances=1` and `coalesce=True` for every job.
