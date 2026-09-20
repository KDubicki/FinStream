# Research: FinStream current state and improvement backlog

- **Date:** 2026-09-18
- **Author:** Claude Code (Opus 5), multi-agent audit; reviewed by: *pending*
- **Related plan / item:** none yet — this note is the input for plans 0006+
- **Status:** Draft <!-- Draft | Final -->

## Question

What can be improved in FinStream as it stands on 2026-09-18, and in which order, given the
maintainer's stated direction: **more data sources** and **an event-driven platform** that other
services can react to? Unblocks: the next plans (0006+) and the two companion notes in this folder.

## Context

All five plans are `Done`. The stack runs end to end: the Ingestor collects 23 configured symbols
from Yahoo into `raw.market_prices`, and a read-only Streamlit dashboard reads `raw` directly
([ADR-0005](../../adr/0005-serving-reads-raw-directly.md)). `docs/STATUS.md:57` says "Nothing is
outstanding", which is true at plan level and misleading at platform level: the plans' own
Follow-up sections carry a dozen admitted gaps, harvested verbatim in the appendix below.

This note is a **repo audit**, not a design. It records what exists, what the repo itself already
admits is missing, and what a fresh read of the code exposes. Design work lives in
[02-data-sources.md](02-data-sources.md) and
[03-event-driven-platform.md](03-event-driven-platform.md); the sequencing lives in
[04-roadmap.md](04-roadmap.md).

**Method.** Three agents worked in parallel (sources, event-driven, audit). Every file:line claim
below was produced by reading the file; the load-bearing ones were re-verified in a second pass by
the orchestrating session on 2026-09-18 — those are marked ✅ in the Evidence section. Nothing here
comes from memory (GR-9).

## Findings

### What is genuinely in good shape

- **The rulebook is enforced, not just written.** 12 golden rules, 5 ADRs, hooks plus pre-commit
  plus CI. Checked 2026-09-18: `ruff check`, `ruff format --check`, `mypy src` and
  `pytest --collect-only` are clean in both services; **zero** TODO/FIXME/XXX/HACK in the repo;
  5 `type: ignore` and 3 `noqa` in production source, each narrow and commented.
- **Test depth is real.** 167 tests (115 ingestor, 52 dashboard), 26 of them integration tests
  against a real `timescale/timescaledb:2.30.0-pg16` container, plus an autouse socket guard that
  blocks non-local hosts (GR-7 holds).
- **The EL boundary holds.** `sources/base.py:59-72` keeps jobs source-agnostic; the dashboard's
  read-only property is asserted by a test over `ALL_STATEMENTS`
  (`services/dashboard/tests/unit/test_queries_sql.py:11-21`).
- **The abstraction for a second source already exists.** `PriceSource` is a Protocol; only the
  *wiring* is single-source (see F12).

### F1 — There is no backup, and no restore procedure ✅

`grep -rniE 'backup|restore|pg_dump'` over `docs/`, `README.md`, `AGENTS.md`, `docker-compose.yml`
and `.github/` returns no relevant hit. The named volume `pgdata`
(`docker-compose.yml:10-11,43-44`) holds the only copy of every bar ever collected, on a laptop the
docs describe being suspended for 20 hours. That `docker compose down -v` is blocked by a hook
(`.claude/hooks/guard_bash.py`) shows the volume is understood to be precious — but a blocked
command is not a recovery path. **Highest-impact gap in the repo**: everything else is
reproducible, the collected history is not.

### F2 — Nothing detects "collection quietly stopped" ✅

`docs/architecture.md:138` records it in the project's own words: *"Observed 2026-09-17: before
this, a suspended laptop left data 20 hours stale while every run still reported `success`."*
Plan 0005 fixed the *cause*; its single follow-up (`docs/plans/0005-catch-up-missed-runs.md:72`) —
surface staleness — is still open. `healthcheck.py:29-33` only proves the scheduler thread ticks,
which is exactly the signal that stayed green during the 20-hour stall.

### F3 — Cross-job concurrency is unbounded and undocumented ✅

`scheduler.py:122,134,151` register intraday, daily and backfill with `next_run_time=now`, and
APScheduler's default executor is `ThreadPoolExecutor(max_workers=10)`
(verified in the pinned 3.11.3: `apscheduler/executors/pool.py:47`). `max_instances=1`
(`scheduler.py:50`) stops a job overlapping *itself only* — `docs/architecture.md:137` states that
narrower guarantee correctly. At startup, daily and backfill therefore write `bar_interval='1d'`
rows for the same symbols concurrently. Idempotent upserts (GR-2) make this safe for *data*, but it
doubles the Yahoo request rate at the worst moment (startup) and produces two `ingestion_runs` rows
per symbol.

**Verified non-issue, recorded so nobody "fixes" it:** the shared `Retrying` objects are
thread-safe — tenacity 9.1.4 keeps per-attempt state in `threading.local()`
(`tenacity/__init__.py:237`). ✅

### F4 — The dashboard's queries cannot use an index ✅

`schema.py:41` gives `market_prices` exactly one index: the primary key, led by `source`. The only
other `sa.Index` calls in the file are for `ingestion_runs` (`schema.py:65-66`). The dashboard
filters and groups *without* `source` (`queries.py:20-29` `GROUP BY symbol, bar_interval` over the
whole table; `queries.py:31-40` `WHERE symbol = … AND bar_interval = …`), and PostgreSQL 16 has no
index skip-scan. The coverage aggregate is a full-table scan on every page load
(`app.py:190`). Harmless at ~2k rows; it degrades continuously and silently.

### F5 — A rate-limit episode costs about 1.5 hours per pass

`sources/yahoo.py:79-85` retries per symbol (5 attempts, backoff to 60 s) with no circuit breaker.
Multiplied by 23 symbols (`config.py:45-53`), one Yahoo rate-limit episode stalls a whole pass while
`max_instances=1` blocks the next. The repo already flags this as a live Medium risk
(`docs/plans/0001-ingestor-implementation.md:312`, `docs/plans/0004-…:80`). A second source makes
it worse, not better, unless a breaker lands first.

### F6 — `running` rows are never reclaimed

`db.py:158-184` inserts a `running` row before the fetch; a killed process leaves it forever.
`docs/data-model.md:95` acknowledges this ("a row left in `running` means the process died
mid-run") and nothing acts on it, so the dashboard's health tile (`app.py:166-172`) shows a
permanent phantom.

### F7 — The dashboard is a second-class citizen in tooling ✅

- `pip-audit` covers only the ingestor (`.github/workflows/security.yml:36-38`).
- The mypy pre-commit hook is scoped `files: ^services/ingestor/src/`
  (`.pre-commit-config.yaml:42`), and CI's only mypy *is* that hook — so the Definition of Done's
  `mypy src` is **unenforced** for the dashboard.
- Dependabot has no `/services/dashboard` entry and no `docker` ecosystem, with a comment saying
  docker arrives "together with the Dockerfile" (`.github/dependabot.yml:36-37`) — both Dockerfiles
  now exist.
- The dashboard connects with the ingestor's superuser `DATABASE_URL`
  (`docker-compose.yml:24,34`); read-only is enforced by a regex over statement strings, not by the
  database. Plan 0003's follow-up already asks for a read-only role.

### F8 — Risky paths without tests

From `coverage --show-missing`: `db.py:51-56` (`create_db_engine`) — lines 53-55 are the **only**
place a database URL is logged, so the GR-5 masking has no test at all; `db.py:80-85`
(`wait_for_db`); `scheduler.py:76-83` (the GR-3 defence-in-depth listener). Separately,
`services/dashboard/tests/integration/conftest.py:18-50` hand-copies the `raw` DDL and has already
diverged from `schema.py` (no CHECK constraint, no indexes, no hypertable), so an additive schema
change passes the dashboard suite even when it would break the dashboard.

### F9 — Documentation drift in the files agents read first ✅

`AGENTS.md:10` still says *"Status: bootstrap … Code waits on plan 0001"*; `AGENTS.md:27` and the
repo map omit `services/dashboard`; `AGENTS.md:64` says the commands "become available once plan
0001 is implemented". `architecture.md:3`, `data-model.md:3` and `configuration.md:9` all say
"Status: design". `STATUS.md:29` says M6 remains while `:7` and `:41` say it is done, and `:22`
quotes 107 tests / 95.95% against today's 115 / 96.02%. `configuration.md:92-127` embeds a stale
copy of `.env.example` (missing `POSTGRES_PORT`, `SCHEDULER_CATCH_UP_MISSED_RUNS`, every
`DASHBOARD_*`) while `configuration.md:5` declares itself the source of truth — the real
`.env.example` is correct, the doc is not.

This matters more than ordinary drift: **AGENTS.md is the first file every agent reads**, and it
currently tells them the code does not exist yet.

### F10 — "Plan 0002" means two different things ✅

Six places promise plan 0002 for *additional data sources*, but 0002 is the CI pipeline:
`architecture.md:21,141,156`, `data-model.md:58`, `plans/0001-…:28,311`, `STATUS.md:60`. The next
free number is **0006**, and "more sources" is the stated next direction — so the collision is about
to be walked into.

### F11 — What the data model cannot express

| Missing | Evidence | Consequence |
|---|---|---|
| Instrument metadata | exchange tz/currency/name live in a **UI** module, `dashboard/instruments.py:39-75`, while `data-model.md:61` tells downstream to derive trading dates "using the exchange timezone" — stored nowhere | Downstream cannot resolve a session date; a second consumer re-invents the table |
| Corporate actions | `yahoo.py:129` fetches with `actions=False` | An `adj_close` revision can be observed but never explained |
| Cross-source identity | key is `(source, symbol, …)`; nothing maps `GC=F`(yahoo) ↔ the same metal elsewhere | Two sources produce two unrelated series |
| Non-OHLCV series | `^TNX` (a yield) is already squeezed into an OHLCV row | Macro/FX/rate series have no honest home |
| **Lineage** | no `run_id` column on `market_prices` (`schema.py:22-42`) | You cannot say which run wrote or revised a bar — and an event consumer cannot be told *which rows* changed (see F12 and note 03) |
| Retention | `data-model.md:11` "The Ingestor never deletes data rows" | ~575 `ingestion_runs` rows/day ≈ 210k/yr vs ~63k `market_prices` rows/yr: **the operational log outgrows the data about 3:1** |

### F12 — Both stated directions are blocked on wiring, not on abstractions

`PriceSource` is source-agnostic (`sources/base.py:59-72`), but `main.py:56-60` hardcodes
`YahooSource`, `scheduler.py:86-92` takes a single `source`, `config.py:45` has a single
`yahoo_symbols`, and `sources/__init__.py` is empty. Nothing emits events; `jobs.py:105-112` (after
a successful upsert, before `finish_run`) is the natural hook, and `db.py:138-147` already uses
`RETURNING` so it knows exactly **which** bars changed — that return value is currently reduced to a
count (`db.py:149-153`). That is the single most valuable fact in this note for the event-driven
work: *the information an event needs already exists and is thrown away.*

## Evidence

Re-verified by the orchestrating session on 2026-09-18 (marked ✅ above):

- Backup: `grep -rniE 'backup|restore|pg_dump' docs README.md AGENTS.md docker-compose.yml .github`
  → only unrelated hits (`restore-keys:` in CI cache, "restores it" in plan 0005).
- Indexes: `grep -n "Index" services/ingestor/src/finstream_ingestor/schema.py` → lines 65 and 66
  only, both on `ingestion_runs`; `market_prices` ends at `sa.PrimaryKeyConstraint(...)`
  (`schema.py:41`).
- APScheduler: `apscheduler/executors/pool.py:47` → `def __init__(self, max_workers=10, …)` in the
  pinned 3.11.3.
- tenacity: `tenacity/__init__.py:237` → `self._local = threading.local()` in the pinned 9.1.4.
- Dependabot/mypy scope/doc drift: quoted line ranges read directly, 2026-09-18.

Test and coverage figures are the ones the repo itself last recorded
(`docs/plans/0005-catch-up-missed-runs.md`: `115 passed`, 96.02%;
`docs/plans/0004-…`: dashboard `52 passed`, 90.94%). The suites were **collected**, not executed, in
this session — no test-pass claim is made here (GR-9).

## Options

The backlog below is the option space. Each row: impact, effort, the rule or documented follow-up it
serves, and whether the project's own rules (AGENTS.md §4) require a plan.

| # | Item | Impact | Effort | Rule / origin | Plan? |
|---|---|---|---|---|---|
| R1 | Back up `pgdata`; document restore | **High** | M | new; GR-11 runbook | Yes |
| R2 | Freshness/staleness detection (F2) | **High** | M | plan 0005 follow-up; GR-3 | Yes |
| R3 | Index `(symbol, bar_interval, ts DESC)` (F4) | Med | S | GR-6 additive | Yes |
| R4 | Startup sweep of stale `running` rows (F6) | Med | S | GR-3 | Yes |
| R5 | Bound cross-job concurrency (F3) | Med | S | GR-3, GR-11 | Yes |
| R6 | Circuit-break rate-limit episodes (F5) | Med | M | GR-3, GR-4 | Yes |
| R7 | Least-privilege `finstream_ro` role (F7) | Med | M | plan 0003 follow-up | Yes |
| R8 | pip-audit + mypy for the dashboard in CI (F7) | Med | S | GR-7, GR-8, DoD | Yes |
| R9 | Complete Dependabot: dashboard pip + docker (F7) | Med | S | GR-8; plan 0001 follow-up | Yes |
| R10 | `raw.instruments` metadata table (F11) | **High** | M | plan 0001 + 0004 follow-ups | Yes |
| R11 | Emit ingestion events (note 03) | **High** | M | stated direction; GR-6 | Yes + ADR |
| R12 | Lineage: `ingestion_run_id` on bars (F11) | Med | M | GR-6 additive | Yes |
| R13 | Second source + per-source config (note 02) | **High** | L | plan 0001 follow-up | Yes + ADR |
| R14 | `raw.corporate_actions` | Med | M | research note 2026-09-16-yfinance:83 | Yes |
| R15 | Retention/compression, `ingestion_runs` first (F11) | Med | M | plan 0001 follow-up | Yes + ADR |
| R16 | Metrics + alerting on consecutive failures | Med | M | plans 0001, 0003 follow-ups | Yes |
| R17 | Test `create_db_engine` / `wait_for_db` / listener (F8) | Med | S | GR-5, GR-7 | No |
| R18 | Stop hand-copying `raw` DDL in the dashboard fixture (F8) | Med | M | GR-6, GR-7 | Yes |
| R19 | Container hardening; digest-pin base images | Med | M | GR-8 | Yes |
| R20 | Image scanning + SBOM in CI | Med | S | plan 0002 follow-up | Yes |
| R21 | Fix stale status banners, 11 locations (F9) | Med | S | **GR-11** | No (docs-only) |
| R22 | Renumber "additional sources" → plan 0006 (F10) | Low | S | GR-11 | No |
| R23 | Remove the stale `.env.example` copy in configuration.md (F9) | Med | S | GR-4, GR-11 | No |
| R24 | Makefile wrapping AGENTS.md §5 per service | Med | S | GR-11 | No |
| R25 | Version the hook test-suite; wire into CI | Med | M | plan 0001 follow-up; **GR-7** | Yes |
| R26 | Compose smoke test in CI | Med | M | DoD | Yes |
| R27 | Drop the obsolete Dockerfile probe; fix dashboard cache key | Low | S | GR-11 | Yes |
| R28 | Branch protection on `main` | Low | S | plan 0002 follow-up | No (setting) |
| R29 | Route the dashboard cache TTL through `Settings` | Low | S | GR-4 | No |
| R30 | Type `_record_failure(run_id: UUID \| None)`, drop the ignore | Low | S | — | No |
| R31 | Document the multi-batch upsert transaction boundary | Low | S | GR-11 | No |
| R32 | Add `run_id` to the success log line | Low | S | GR-11 | No |
| R33 | Explicit connect/statement timeouts on the engine | Low | S | GR-3 | No¹ |

¹ Becomes plan-worthy if it introduces a new environment variable (GR-4).

## Recommendation

**Do three things before anything ambitious**, because each protects work already done:

1. **R1 backup + documented restore.** Everything else in this repo is reproducible from git; the
   collected history is not.
2. **R2 staleness detection.** The platform has already lost 20 hours to a failure that every
   existing signal reported as healthy.
3. **R21 + R23 + R22 (a docs-only batch, no plan needed).** AGENTS.md currently tells every agent
   that the code does not exist, and `configuration.md` contradicts `.env.example`. Fix this
   *before* writing plan 0006, because that plan will be written by an agent reading those files.

**Then, for the stated direction, in this order:** `raw.instruments` (R10) → lineage (R12) → events
(R11) → second source (R13). The ordering is load-bearing and argued in
[04-roadmap.md](04-roadmap.md): identity before a second source is far cheaper than retrofitting it
across two sources' rows, and lineage before events is what lets an event say *which* rows changed
instead of just "something happened".

## Risks & open questions

- **Q1.** `docs/methodology.md:61` appears to require a plan plus a superseding ADR for rulebook
  changes, while `AGENTS.md:58` exempts documentation-only fixes. A stale status line is clearly the
  latter; adding dashboard commands to AGENTS.md §5 is arguably the former. **Needs the maintainer's
  reading before AGENTS.md is edited.**
- **Q2.** R15 (retention) contradicts `data-model.md:11` ("The Ingestor never deletes data rows") and
  therefore needs an ADR, not just a plan — even though it would only ever delete from the
  operational log.
- **Q3.** Coverage and test counts here are the repo's last recorded numbers. Re-run the gates before
  quoting them in a plan.
- The backlog is deliberately wider than any one plan. Items R28–R33 are cheap enough to fold into
  whichever plan touches the same file (GR-10: don't fix them silently — record them).

## Sources

1. The FinStream repository at commit `9efd54e`, read 2026-09-18. All file:line references above.
2. `apscheduler` 3.11.3, `tenacity` 9.1.4 — installed source in `services/ingestor/.venv`, read
   2026-09-18.
3. Companion notes: [02-data-sources.md](02-data-sources.md),
   [03-event-driven-platform.md](03-event-driven-platform.md), [04-roadmap.md](04-roadmap.md).

## Appendix A — every open item the repo already admits, verbatim

**plan 0001 Follow-ups** (`docs/plans/0001-ingestor-implementation.md:325-333`)
> - Plan 0002: additional free sources (e.g. Alpha Vantage, Twelve Data, FRED) through `PriceSource`.
> - ~~CI (GitHub Actions)…~~ Covered by plan 0002.
> - Alembic once a non-additive schema change is needed.
> - Metrics endpoint (Prometheus) and alerting on consecutive failed runs.
> - TimescaleDB compression and retention policies.
> - Upgrading to PostgreSQL 17/18 … ADR-0002 fixes PostgreSQL 16, so this needs a new ADR that supersedes it.
> - Dependency audit and update policy (e.g. `pip-audit`).
> - Instrument metadata (`raw.instruments`: exchange timezone, currency, asset class).
> - Version the Claude Code hook test-suite in the repo … so the guards themselves are covered by GR-7.

**plan 0002 Follow-ups** (`docs/plans/0002-ci-pipeline.md:92-94`)
> - Branch protection on `main` …
> - SBOM and image signing when images start being published.
> - A coverage report published as a job summary or to an external service.

**plan 0003 Follow-ups** (`docs/plans/0003-dashboard-service.md:91-93`)
> - A read-only database role for the dashboard.
> - Moving the dashboard onto a processing/serving schema once one exists, superseding ADR-0005.
> - Auto-refresh, and alerting on consecutive failed runs.

**plan 0004 Follow-ups** (`docs/plans/0004-dashboard-readability-and-coverage.md:89-90`)
> - `raw.instruments` table so names and exchange timezones live with the data.
> - Symbol comparison on one chart, and candlesticks.

**plan 0005 Follow-ups** (`docs/plans/0005-catch-up-missed-runs.md:72`)
> - Surface staleness in the dashboard: flag a series whose newest bar is far older than its interval, so a stall is visible without reading logs.
