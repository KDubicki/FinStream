---
name: development
description: Implementation procedure and code conventions for the FinStream repo (Python 3.12 microservices, starting with the FinStream Ingestor). Use when writing or changing code, Dockerfiles, docker-compose, requirements, configuration (.env.example) or the DB schema, i.e. when executing an Approved plan from docs/plans/. Also covers the final Review & Document phase (golden-rules review, Definition of Done, closing a plan). For open questions about libraries/APIs use the research skill; for what and how to test use the test skill.
---

# Development skill

## Preconditions (check before touching code)

1. **An `Approved` or `In progress` plan covers this work.** Look in `docs/plans/`. If none exists or it's still `Draft`, stop. Write or update the plan from [TEMPLATE.md](../../../docs/plans/TEMPLATE.md) and ask the user to approve it. Only typo and documentation-only fixes are exempt (AGENTS.md §4).
2. **You've read** [AGENTS.md](../../../AGENTS.md), the plan, and the docs the plan touches: [architecture](../../../docs/architecture.md), [data model](../../../docs/data-model.md), [configuration](../../../docs/configuration.md), and the relevant ADRs.
3. **Research items blocking your slice are resolved.** If not, run the `research` skill first.

## Procedure

1. **Start.** Switch to the plan's branch: `git switch feat/NNNN-<slug>`, or `git switch -c …` the first time. If the plan is `Approved`, set it to `In progress`.
2. **Pick the smallest slice.** Take the next unticked task. A slice is one coherent, testable change, usually one milestone or sub-task.
3. **Write tests with or before the code.** Follow the `test` skill: mandatory test types, no network, real DB for DB logic.
4. **Implement** following the conventions below. Stay inside the plan's scope (GR-10). Add anything else you notice to the plan's "Follow-ups".
5. **Run the test loop** from the `test` skill (lint → types → unit → integration). Fix until green, then paste the real output into the plan's Verification log.
6. **Update docs in the same change** (GR-11):
   - new or changed env var → `.env.example` + `docs/configuration.md` (GR-4)
   - schema change → `docs/data-model.md` (GR-6)
   - behaviour or architecture change → `docs/architecture.md`, `README.md`
   - new technical decision → ADR (via `research`)
7. **Tick the task** in the plan.
8. **Commit the slice right away** as a Conventional Commit (AGENTS.md GR-12, §8), e.g. `feat(ingestor): add idempotent upsert for market prices`. Stage only the files that belong to the slice, and never commit red or unverified work. Never bypass hooks. If pre-commit fails, fix the cause and commit again.
9. **Repeat** from step 2. When every task is ticked, run [Review & Document](#review--document-final-phase).

## Code conventions (Python services)

### Language & tooling
- Python 3.12, and the code must stay 3.11-compatible. Full type hints. `mypy --strict` clean on `src/`.
- ruff handles formatting and linting. Its config lives in the service's `pyproject.toml`.
- `src/` layout with the package named `finstream_<service>`. Entrypoint: `python -m finstream_<service>.main`. `pyproject.toml` holds tool configuration only (`pythonpath = ["src"]` for pytest).

### Configuration (GR-4)
- One `Settings` class (pydantic-settings) in `config.py`. Instantiate it once in `main.py` and pass it down explicitly. No import-time settings globals.
- Validate eagerly (lists, cron expressions, positive integers, lookback formats) so that bad config fails at startup with a message that names the variable.
- Use `SecretStr` for secrets. Never log them. Log DB URLs masked, e.g. `engine.url.render_as_string(hide_password=True)`.

### Module boundaries
| Module | May | Must not |
|---|---|---|
| `sources/*` | Call external APIs, classify errors, retry, normalise into `PriceBar` | Import `db`; transform data (GR-1) |
| `db.py`, `schema.py` | SQLAlchemy **Core** (no ORM), idempotent DDL, upserts, run records | Call external APIs |
| `jobs.py` | Orchestrate source → db for each symbol, record runs | Let exceptions escape (GR-3) |
| `scheduler.py`, `main.py` | Wiring, triggers, lifecycle, signals | Contain business logic |

### EL boundary (GR-1)
- **Allowed** in a source's normalisation: renaming columns, casting types, converting timestamps to UTC-aware `datetime`, `NaN` → `None`, dropping rows where every value is `NaN`, adding bookkeeping fields (`source`).
- **Anything else is a transformation** and belongs downstream. If you're tempted, stop and ask.

### Database (GR-2, GR-6)
- Writes to data tables use `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(...)` on the natural key. Plain `INSERT` into data tables is forbidden.
- DDL is idempotent (`CREATE … IF NOT EXISTS`, `create_hypertable(…, if_not_exists => TRUE)`) and runs at startup.
- Timestamps are `TIMESTAMPTZ`, always UTC. Python datetimes are always timezone-aware.

### Error handling & retries (GR-3)

```python
# sources: retry transient failures only; build the retryer from settings so tests can inject zero waits
retryer = Retrying(
    retry=retry_if_exception_type((TransientSourceError, RateLimitedError)),
    stop=stop_after_attempt(settings.retry_max_attempts),
    wait=wait_exponential_jitter(initial=1, max=settings.retry_max_wait_seconds),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

# jobs: isolate every symbol and never raise into the scheduler
for symbol in symbols:
    run_id = start_run(engine, job=job, symbol=symbol, ...)
    try:
        bars = source.fetch(symbol, interval=interval, period=lookback)
        affected = upsert_bars(engine, bars)
        finish_run(engine, run_id, status="success" if bars else "empty",
                   rows_received=len(bars), rows_upserted=affected)
    except Exception as exc:  # noqa: BLE001 - deliberate job boundary (GR-3)
        logger.exception("ingest failed", extra={"job": job, "symbol": symbol, "run_id": str(run_id)})
        finish_run(engine, run_id, status="failed", error=repr(exc))
```

- Catch broad `Exception` **only** at the job boundary. Everywhere else, catch specific exceptions.
- Permanent errors (e.g. an invalid symbol) aren't retried. They're recorded as `failed`.

### Logging
- Use stdlib `logging` with a JSON formatter configured once in `logging_setup.py`, and `logger = logging.getLogger(__name__)`.
- Put context in `extra={"job": …, "symbol": …, "run_id": …}`. Use constant message strings, not f-strings.
- No `print()`.
- Levels: `INFO` for job start and finish with counts, `WARNING` for retries and empty results, `ERROR`/`exception` for failed runs.

### Dependencies (GR-8)
- Pin exactly (`==`). Runtime dependencies go in `requirements.txt`. Dev and test tools go in `requirements-dev.txt`, which starts with `-r requirements.txt`.
- Every new dependency needs a research note. Keep the list minimal.

### Docker
- Multi-stage build, slim base image, non-root user, and no secrets in the image or build args. Configuration arrives via environment at runtime.
- Provide a `HEALTHCHECK` and handle SIGTERM gracefully.

### Local environment

```bash
cd services/<service>
python3.12 -m venv .venv && source .venv/bin/activate   # system python3 on macOS may be < 3.11
pip install -r requirements-dev.txt
```

## Review & Document (final phase)

Before marking a plan `Done`:

1. **Golden-rules review.** Re-read GR-1 to GR-12 and check the full diff against each one (`git diff main...HEAD`). Fix every violation, or record the user-approved exception in the plan.
2. **Full gate.** Run the "Full gate" from the `test` skill and paste the output into the Verification log. Run the smoke test too if runtime behaviour, Docker or Compose changed.
3. **Definition of Done.** Tick every item in the plan's DoD list. Anything that doesn't apply gets `n/a` plus the reason.
4. **Docs match reality.** Check the README status and quickstart, `docs/architecture.md`, `docs/data-model.md`, and that `docs/configuration.md` agrees with `.env.example`. Check ADR statuses and the tables in `docs/README.md`.
5. **Close the plan.** Set `Status: Done`, add a one-line summary to its Change log, and commit (`docs(plans): mark plan NNNN done`).
6. **Report to the user:** what changed, the verification evidence (real output), follow-ups, and any exceptions granted.
