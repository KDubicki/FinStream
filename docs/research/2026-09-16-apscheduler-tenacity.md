# Research: APScheduler and tenacity (plan 0001, R2)

- **Date:** 2026-09-16
- **Author:** Claude Opus 5 (Claude Code), for review by the FinStream owner
- **Related plan / item:** [plan 0001](../plans/0001-ingestor-implementation.md), R2; [ADR-0003](../adr/0003-apscheduler-and-tenacity.md)
- **Status:** Final

## Question

Is APScheduler 3.x still the right choice, how does `CronTrigger.from_crontab` interpret day-of-week, what are the defaults for `misfire_grace_time` / `coalesce` / `max_instances`, and how should a tenacity retryer be built so tests can run without real waiting? This decides whether ADR-0003 is accepted.

## Findings

### Versions (PyPI, checked 2026-09-16)
- **APScheduler 3.11.3**, released 2026-06-28, `requires_python >=3.8`. The 4.0 line is still pre-release (latest `4.0.0a6`), so 3.x remains the only stable choice.
- **tenacity 9.1.4**, released 2026-02-07, `requires_python >=3.10`.

### Cron day-of-week: a real trap, and it confirms our design
The APScheduler 3.x documentation states:

> "Due to a historical mistake, there is a mismatch between weekday numbers, as APScheduler treats 0 as Monday while the original crontab treats it as Sunday."

- `day_of_week` accepts `0-6` **or** names: `mon,tue,wed,thu,fri,sat,sun`. The first weekday is always Monday.
- `CronTrigger.from_crontab(expr, timezone=None)` takes the classic five-field expression but **keeps APScheduler's numbering**, so `1-5` means Tue–Sat, not Mon–Fri.
- Fixed in APScheduler 4.x, but not in 3.x.

Source: <https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html>

### Job behaviour
- `max_instances`: "by default, only one instance of each job is allowed to be run at the same time", which matches what we want.
- `coalesce`: when several runs are queued, the job triggers only once, and no misfire events fire for the skipped ones.
- `misfire_grace_time`: how late a run may still start, checked per missed run.
- All three can be set globally through `job_defaults` on the scheduler.
- Listeners: `scheduler.add_listener(handler, EVENT_JOB_ERROR | EVENT_JOB_MISSED)`.
- `BlockingScheduler.start()` blocks. `shutdown()` waits for running jobs, `shutdown(wait=False)` returns immediately.

Source: <https://apscheduler.readthedocs.io/en/3.x/userguide.html>

### tenacity
- `Retrying` is configurable as an object: `stop`, `wait`, `retry`, `before`, `after`, `before_sleep`, `reraise`, and it exposes `statistics`.
- Tests can neutralise waiting by patching the wait strategy (`mock.patch.object(wrapped.retry, "wait", wait_none())`) or, for decorated functions, `fn.retry_with(...)` / `retry_with(enabled=False)`.

Source: <https://github.com/jd/tenacity> (`_autodocs/api-reference/retrying-class.md`, `doc/source/index.rst`)

## Recommendation

1. **Accept [ADR-0003](../adr/0003-apscheduler-and-tenacity.md) unchanged.** APScheduler 3.11.3 with `BlockingScheduler`, plus tenacity 9.1.4, is confirmed as the right choice.
2. **Keep `DAILY_CRON` using day names** (`30 22 * * mon-fri`). The numbering mismatch is documented and real.
3. **Add config validation (M1):** reject a `DAILY_CRON` whose day-of-week field is numeric, with an error that explains the mismatch and asks for `mon-fri`. A unit test asserts the trigger fires on Monday–Friday.
4. **Job defaults:** `job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": SCHEDULER_MISFIRE_GRACE_SECONDS}`.
5. **Retries:** build a `Retrying` object from `Settings` in the source layer. Tests pass `RETRY_MAX_WAIT_SECONDS=0` or patch the wait strategy with `wait_none()`, so no test ever sleeps.

## Risks & open questions

- APScheduler 4.x will eventually need a migration, and it also fixes the day-of-week numbering. That's a follow-up, not part of plan 0001.
- The in-memory job store loses schedule state on restart. This is accepted in ADR-0003, since every job also runs at startup and lookbacks overlap.

## Sources

1. <https://pypi.org/pypi/APScheduler/json>, <https://pypi.org/pypi/tenacity/json> (checked 2026-09-16)
2. <https://apscheduler.readthedocs.io/en/3.x/modules/triggers/cron.html>
3. <https://apscheduler.readthedocs.io/en/3.x/userguide.html>
4. <https://github.com/jd/tenacity>
