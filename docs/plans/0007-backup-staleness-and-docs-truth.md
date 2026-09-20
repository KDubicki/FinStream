# Plan 0007: backup and restore, staleness detection, and documentation that tells the truth

- **Status:** In progress <!-- Draft | Approved | In progress | Done | Abandoned. Only the user sets Approved. -->
- **Created:** 2026-09-20
- **Branch:** `feat/0007-backup-staleness-docs-truth`
- **Related:** [audit note](../research/2026-09-18-platform-evolution/01-current-state-and-backlog.md) (R1, R2, R21, R22, R23), [backup research](../research/2026-09-20-timescaledb-backup-restore.md), [plan 0005](0005-catch-up-missed-runs.md), [ADR-0005](../adr/0005-serving-reads-raw-directly.md)

## Context

The 2026-09-18 platform audit ranked a 33-item backlog and recommended three things be done before
anything ambitious, because each protects work already done. The maintainer picked exactly that
trio on 2026-09-20.

1. **There is no backup** (audit F1). The `pgdata` volume holds the only copy of every bar ever
   collected. Everything else in this repo is reproducible from git; the collected history is not.
   That `docker compose down -v` is blocked by a hook shows the volume is understood to be
   precious — but a blocked command is not a recovery path.
2. **Nothing detects "collection quietly stopped"** (audit F2). `docs/architecture.md:138` records
   the incident in the project's own words: a suspended laptop left data 20 hours stale *while
   every run still reported `success`*. Plan 0005 fixed the cause; its follow-up — make staleness
   visible — is still open. The healthcheck only proves the scheduler thread ticks, which is
   precisely the signal that stayed green throughout.
3. **The documentation tells agents the code does not exist** (audit F9, F10). `AGENTS.md:10` still
   says *"Status: bootstrap … Code waits on plan 0001"*, the repo map omits `services/dashboard`,
   three reference docs say "Status: design", `configuration.md` embeds a stale copy of
   `.env.example` while declaring itself the source of truth, and six places promise "plan 0002"
   for additional data sources when 0002 is the CI pipeline. AGENTS.md is the first file every
   agent reads.

## Goals

- A one-command backup of the database, and a **restore procedure that has actually been run**,
  not merely written down.
- A stall visible on the dashboard without reading logs: a page-level banner when collection has
  stopped everywhere, and a per-series flag for one instrument falling behind its peers.
- Documentation whose status lines, repo map, command list and configuration reference match the
  repository as it is on 2026-09-20.

## Non-goals

- **Scheduling the backup.** M1 delivers a script and a runbook; cron, a sidecar or an offsite copy
  are follow-ups. A backup that exists only when someone runs it is still infinitely better than none.
- Point-in-time recovery, WAL archiving, encryption at rest.
- Trading calendars. Staleness is judged by comparing series with their peers, not against market
  hours; an honest heuristic, stated as one (see *Design → Why peers, not clocks*).
- Rewriting the rulebook. M3 corrects statements of **fact**; no golden rule, workflow or gate
  changes (see *Exceptions*).
- The remaining 28 backlog items, including the `plan 0002` renumbering's deeper cause — that six
  documents hardcode a plan number at all.

## Design

### M1 — Backup and restore

`scripts/backup_db.sh`, plus `docs/runbooks/restore.md`.

The research note settles the mechanics: a `pg_dump --format=custom` of the database, restored
along TimescaleDB's documented `timescaledb_pre_restore()` / `timescaledb_post_restore()` path.
Both were run end to end against the live stack on 2026-09-20 and produced a content checksum
identical to the source, with the hypertable and all four chunks intact.

```
docker compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" …'
```

The password is read **inside** the container from its own environment, so it never appears in a
host process's argument list, in shell history or in the script (GR-5). The script reads no `.env`.

- Output: `backups/finstream-YYYYMMDD-HHMMSS.dump` by default, overridable by argument.
  `backups/` is added to `.gitignore` — a dump is data, not source.
- The script fails loudly: `set -euo pipefail`, a check that the stack is up, and a non-zero exit
  if the dump is empty or `pg_dump` fails.
- It prints the dump's size and the row counts it captured, so the operator sees what was saved.

### M2 — Staleness detection

New `services/dashboard/src/finstream_dashboard/freshness.py`, pure functions over the existing
`coverage` query. No new SQL, no new environment variables (the thresholds are constants, beside
`DEFAULT_WINDOW_DAYS` and `MAX_COMPARE_SYMBOLS`).

**Why peers, not clocks.** A calendar-based rule ("a 1h series older than 2h is stale") is wrong
for every instrument that does not trade around the clock: an index is legitimately 65 hours stale
between Friday's close and Monday's open, so any threshold loose enough to avoid false alarms is
too loose to catch a real stall. Comparing a series against *the freshest series of the same
interval* needs no calendar and is exactly the signal that failed in the 20-hour incident, where
**everything** fell behind together.

Two signals, both derived from `coverage`:

| Signal | Rule | Answers |
|---|---|---|
| Overall | age of the newest bar anywhere > `GLOBAL_STALE_AFTER` (12h) | "has collection stopped?" |
| Per series | `last_ts` further behind its interval's freshest series than `BEHIND_TOLERANCE` (6h for `1h`, 2d otherwise) | "is one instrument stuck?" |

Surfacing:

- A banner at the top of `main()`, above the tabs, so a stall is visible on whichever tab is open.
- A `freshness` column on the Coverage table, and the overall age as a metric on Ingestion health.

### M3 — Documentation that matches the repository

Corrections only, each one a statement of fact that is currently false:

| File | Correction |
|---|---|
| `AGENTS.md` §1 | "Status: bootstrap … waits on plan 0001" → what actually runs |
| `AGENTS.md` §2 | Repo map gains `services/dashboard`, `scripts/`, `docs/STATUS.md`, `docs/runbooks/` |
| `AGENTS.md` §5 | Drop "become available once plan 0001 is implemented"; add the dashboard's commands |
| `docs/architecture.md`, `docs/data-model.md`, `docs/configuration.md` | "Status: design" → current |
| `docs/configuration.md` | Delete the embedded stale copy of `.env.example` (missing `POSTGRES_PORT`, `SCHEDULER_CATCH_UP_MISSED_RUNS` and every `DASHBOARD_*`), and point at the real file |
| `docs/STATUS.md` | Resolve the M6 contradiction; real test counts; plans 0006 and 0007 |
| 6 locations (audit F10) | "plan 0002" as the home of additional data sources → "a future plan", since 0002 is CI and 0006 is now the comparison work |

The `plan 0002` references are fixed by **removing the number**, not by substituting another one:
hardcoding a second number would drift exactly as the first did.

## Research items

- [x] R1: TimescaleDB backup and restore mechanics → [2026-09-20-timescaledb-backup-restore.md](../research/2026-09-20-timescaledb-backup-restore.md)

## Tasks

- [x] **M1: Backup and restore** — `scripts/backup_db.sh`, `docs/runbooks/restore.md`, `.gitignore`
      entry; a real dump taken and a real restore performed into a scratch database, with output in
      the Verification log
- [ ] **M2: Staleness** — `freshness.py`, banner, Coverage column, health metric, unit tests
- [ ] **M3: Documentation** — the corrections above, with real numbers from re-run gates

## Test plan

| Type | Applies |
|---|---|
| Backup round trip | Dump the live database, restore into a scratch database, compare a content checksum, confirm the hypertable's chunks survive and still accept writes, drop the scratch database |
| Script failure modes | Non-zero exit when the stack is down; refuses to write an empty dump |
| Freshness logic | Fresh data flags nothing; a whole-stack stall raises the overall flag; one series behind its peers is flagged while its peers are not; a single-series database never flags itself; an empty coverage frame is handled |
| Interval isolation | A `1h` series is compared against `1h` peers, never against `1d` |
| Render guards | Banner and Coverage column render in bare mode, fresh and stale |
| Regression | Existing 97 dashboard tests and the ingestor suite stay green |
| Docs | `configuration.md` no longer contradicts `.env.example`; every corrected claim re-checked against the repo |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The restore procedure is written but never executed, so it fails when needed | Medium | **High** | M1 is not done until a restore has actually run; the checksum comparison is in the Verification log |
| A peer-relative staleness rule false-alarms on an instrument that genuinely trades less | Medium | Low | Generous tolerances, the flag is informational, and the rule is documented as a heuristic |
| Editing AGENTS.md is read as a rulebook change requiring an ADR | Medium | Medium | Only statements of fact change; no rule text is touched. Recorded under *Exceptions* and flagged to the maintainer |
| The backup script leaks the database password | Low | **High** | The password is expanded inside the container, never passed as an argument; gitleaks and the `.env` guard hook both run on commit |
| Editing a closed plan (0001) to fix the `plan 0002` reference | Low | Low | Plans are a record, not immutable (only Accepted ADRs are); the change log records it |

## Exceptions

**Editing `AGENTS.md` without a superseding ADR.** `docs/methodology.md:61` requires a plan *and an
ADR superseding ADR-0004* for "Methodology or rulebook" changes, while `AGENTS.md` §4 exempts
documentation-only fixes. The audit flagged the tension as its open question Q1.

Reading taken here: **M3 changes no rule.** It corrects a status line, a repo map, a command list
and a configuration reference — statements of fact about the repository, all of them currently
false. The golden rules, the workflow, the gates and the precedence order are untouched, so
ADR-0004 still describes the methodology accurately and has nothing to supersede. Recorded so the
maintainer can overrule it; if overruled, M3 stops and an ADR is written first.

## Follow-ups

<Out-of-scope findings discovered during the work (GR-10).>

- Schedule the backup (cron entry, Compose sidecar, or a CI job against a dev stack) and keep an
  offsite copy. M1 only makes a backup *possible*.
- Restoring across a TimescaleDB **major** version is unverified; revisit before any extension
  upgrade, when `ts_dump.sh`-style CSV export may be the safer route.
- Six documents hardcode a plan number for work that is not planned yet. M3 removes the numbers;
  the underlying habit is worth a convention.

## Definition of Done

- [ ] `ruff check`, `ruff format --check`, `mypy src` clean
- [ ] `pytest` green, coverage ≥ 85% on `src/`
- [ ] Idempotency and resilience tests exist for new or changed writers/jobs — *n/a: no writer or
      job changes; the backup script only reads*
- [ ] `docker compose up` smoke test passed, with a real backup **and a real restore** recorded
- [ ] `docs/`, README and `.env.example` reflect the change
- [ ] All tasks ticked, Verification log filled in, Status `Done`

## Verification log

<Command → trimmed real output → PASS / FAIL / SKIPPED (reason). Newest entries last.>

### 2026-09-20 · M1 backup and a restore that was actually performed

`scripts/backup_db.sh` + `docs/runbooks/restore.md`, with `backups/` git-ignored.

Real backup of the live stack:

```
$ ./scripts/backup_db.sh
Backing up the FinStream database
  contents: 2540 bars, 1835 ingestion runs
  written:  .../backups/finstream-20260920-200302.dump (176K)
```

**The restore was executed, not just documented**, running the runbook's blocks verbatim against
that dump:

```
step 1  CREATE DATABASE / CREATE EXTENSION / timescaledb_pre_restore()  -> t
step 2  pg_restore --no-owner --no-privileges                           -> exit 0
step 3  timescaledb_post_restore()                                      -> t

finstream:          2540 bars, 4 chunks
finstream_restored: 2540 bars, 4 chunks

content checksum
finstream:          2fcf7d108aaddae4f89d0d4380d3c2bf
finstream_restored: 2fcf7d108aaddae4f89d0d4380d3c2bf
```

Identical. The scratch database was dropped afterwards and the live database is untouched.

Failure modes, exercised with a stubbed `docker` on `PATH` so the running stack was never disturbed:

```
STUB_MODE=down       -> "the 'db' service is not accepting connections"   exit=1  leftover files: 0
STUB_MODE=emptydump  -> "pg_dump produced an empty file"                  exit=1  leftover files: 0
STUB_MODE=faildump   -> "pg_dump returned a non-zero status"              exit=1  leftover files: 0
```

The dump goes to a temporary name and is moved into place only after a non-empty check, so a
truncated file never sits at the final path looking like a good backup.

PASS.

Two things found while doing this:

- **`$$` in SQL passed through `sh -c` expands to the shell's PID.** The first version printed
  `... || 12668 bars ...` and failed. Dollar-quoting cannot survive that hop; the script now selects
  two plain columns and formats them in bash.
- **`pg_dump` emits a circular-foreign-key warning about `continuous_agg`**, a TimescaleDB catalog
  table this project does not use. It reads alarmingly ("You might not be able to restore"), but the
  restore above reproduced the data exactly. The runbook says so explicitly and tells the operator
  not to reach for `--disable-triggers`.

## Change log

- 2026-09-20: created; scope chosen by the maintainer from the audit's recommended trio, approved
  and started in one step, as with plans 0002–0004.
