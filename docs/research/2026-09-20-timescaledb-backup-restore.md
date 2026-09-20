# Research: backing up and restoring the FinStream database

- **Date:** 2026-09-20
- **Author:** Claude Code (Opus 5) — reviewed by: *pending*
- **Related plan / item:** [plan 0007](../plans/0007-backup-staleness-and-docs-truth.md), backlog item R1
- **Status:** Final

## Question

How do we take a restorable backup of the `pgdata` volume's contents, and what is the exact
restore procedure for a TimescaleDB hypertable? Unblocks plan 0007 M1: without a verified restore
path, a backup script is a guess.

## Context

The named volume `pgdata` (`docker-compose.yml:10-11,43-44`) holds the only copy of every bar the
Ingestor has ever collected. Everything else in the repo is reproducible from git; this is not.
The repo currently has no backup and no restore procedure — `grep -rniE 'backup|restore|pg_dump'`
over `docs/`, `README.md`, `AGENTS.md`, `docker-compose.yml` and `.github/` returns no relevant hit
(audit note [01-current-state-and-backlog.md](2026-09-18-platform-evolution/01-current-state-and-backlog.md),
finding F1).

`raw.market_prices` is a **hypertable** (4 chunks as of today), which is why this needs checking
rather than assuming: TimescaleDB keeps its own catalog in `_timescaledb_catalog`, and a plain
`pg_dump` also dumps those internal tables.

## Findings

- **TimescaleDB's own documented restore path is
  `CREATE EXTENSION timescaledb CASCADE` → `timescaledb_pre_restore()` → `pg_restore` (without
  `--create`) → `timescaledb_post_restore()`.** Source: the extension's own test suite,
  `test/sql/pg_dump.sql` and `test/sql/utils/pg_dump_aux_restore.sh`, retrieved via context7 from
  `/timescale/timescaledb`, checked 2026-09-20.
- **`pg_dump --format=custom` warns about circular foreign keys on the TimescaleDB catalog.**
  Verified on the running stack, 2026-09-20 (full output in Evidence). The warning concerns
  `continuous_agg` — a catalog table this project does not use — and did not prevent a correct
  restore.
- **Both restore paths produce byte-identical data on this stack.** The official
  pre/post-restore path and a plain `pg_restore` into a freshly created database both yielded the
  same content checksum as the source database, with the hypertable and all 4 chunks intact, and
  the restored hypertable still accepted writes. Verified 2026-09-20.
- **The reason the plain path works here is image-specific, not a general property.**
  `timescale/timescaledb:2.30.0-pg16` installs the extension into `template1` (verified: `psql -d
  template1` reports `timescaledb 2.30.0`), so every `CREATE DATABASE` inherits it and the dump's
  `CREATE EXTENSION` is a no-op. On an image without that, the plain path would not be equivalent.
- **Versions used:** `pg_dump`/`psql`/`pg_restore` 16.15, PostgreSQL 16, TimescaleDB 2.30.0,
  image `timescale/timescaledb:2.30.0-pg16`. Checked 2026-09-20.
- **Credentials never have to leave the container.** `docker compose exec db sh -c '… "$POSTGRES_PASSWORD" …'`
  reads the password from the container's own environment, so it appears in no host process
  argument list, no shell history and no script (GR-5).

## Evidence

Dump of the live database (2,540 bars, 1,835 runs, 4 chunks):

```
$ docker compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' > test.dump
exit=0
-rw-r--r--  1 kdubicki  wheel   172K  test.dump

pg_dump: warning: there are circular foreign-key constraints on this table:
pg_dump: detail: continuous_agg
pg_dump: hint: You might not be able to restore the dump without using --disable-triggers …
```

Path A — plain restore into a fresh database:

```
$ psql -d postgres -c "CREATE DATABASE restore_probe;"
$ pg_restore -U … -d restore_probe --no-owner --no-privileges < test.dump
restore exit=0

restore_probe: timescaledb 2.30.0
bars 2540 | runs 1835 | chunks 4
INSERT 0 1          -- the restored hypertable still accepts writes
```

Path B — the officially documented path:

```
$ psql -d restore_official -c "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;" \
                           -c "SELECT timescaledb_pre_restore();"
NOTICE:  extension "timescaledb" already exists, skipping
 timescaledb_pre_restore
 t
$ pg_restore -U … -d restore_official --no-owner --no-privileges < test.dump
$ psql -d restore_official -c "SELECT timescaledb_post_restore();"

official-path checksum: 2fcf7d108aaddae4f89d0d4380d3c2bf
bars/runs/chunks: 2540/1835/4
```

Content checksum, source versus both restores — `md5(string_agg(source||symbol||bar_interval||ts||close ORDER BY …))`:

```
finstream:        2fcf7d108aaddae4f89d0d4380d3c2bf
restore_probe:    2fcf7d108aaddae4f89d0d4380d3c2bf
restore_official: 2fcf7d108aaddae4f89d0d4380d3c2bf
```

Both probe databases were dropped afterwards; the live database still reports 2,540 bars.

## Options

| Option | Pros | Cons |
|---|---|---|
| **A. `pg_dump --format=custom` of the database** | Portable across machines and volume layouts; selective restore possible; small (172 KB for 2.5k bars); runs while the stack is up | Restore of a hypertable needs the documented pre/post steps to be safe in the general case |
| B. Copy the `pgdata` volume (`tar` of the mount) | Simplest conceptually; captures everything | Only restorable onto the *same* PostgreSQL major and TimescaleDB version; must stop the database for consistency; much larger |
| C. `ts_dump.sh` (TimescaleDB's per-hypertable CSV script) | Version-independent, survives a TimescaleDB major upgrade | Per-hypertable, not whole-database; loses `ingestion_runs` unless scripted separately; more moving parts |
| D. Continuous archiving (WAL-E / pgBackRest) | Point-in-time recovery | Far beyond a single-maintainer laptop stack; new dependency and daemon |

## Recommendation

**Option A**, with the restore documented along TimescaleDB's official pre/post-restore path.

The dump is tiny, the stack stays up while it runs, and the procedure was verified end to end today
against the real image. The official path is what the runbook should say even though the plain path
also worked here, because the plain path's success depends on the extension already being present
in `template1` — an image detail, not a guarantee.

Option C is worth revisiting **if** a TimescaleDB major upgrade is ever planned, since a custom-format
dump is not guaranteed to restore across extension majors. That is a follow-up, not this plan.

## Risks & open questions

- **The dump is only as good as its last run.** A manual script means a backup exists only when
  someone remembers. Scheduling it (cron, or a Compose sidecar) is deliberately out of plan 0007's
  scope and recorded as a follow-up.
- **Restore across a TimescaleDB major version is UNVERIFIED.** Everything above was same-version
  (2.30.0 → 2.30.0). A restore into a different extension major was not tested and must not be
  assumed to work.
- **Encryption at rest is not addressed.** The dump contains no credentials, but it does contain
  all collected data; where it is stored is the operator's decision.
- The 172 KB figure is today's size at ~2.5k bars. It grows roughly linearly, so about 63k bars a
  year ≈ a few MB a year — not a capacity concern for a long time.

## Sources

1. TimescaleDB `test/sql/pg_dump.sql` and `test/sql/utils/pg_dump_aux_restore.sh` — the extension's
   own test suite, retrieved via context7 (`/timescale/timescaledb`), checked 2026-09-20.
2. TimescaleDB `scripts/ts_dump.sh` (option C), same source and date.
3. The running FinStream stack: `timescale/timescaledb:2.30.0-pg16`, PostgreSQL 16.15,
   TimescaleDB 2.30.0 — spike commands and output above, executed 2026-09-20.
