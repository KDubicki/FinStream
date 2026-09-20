# Runbook: restore the FinStream database from a backup

**What this protects.** The `pgdata` volume holds the only copy of every bar the Ingestor has ever
collected. Source, configuration and images are reproducible from git; that history is not.

The mechanics below were **executed end to end** against `timescale/timescaledb:2.30.0-pg16` on
2026-09-20, not written from memory — see
[the research note](../research/2026-09-20-timescaledb-backup-restore.md) for the full evidence and
[plan 0007](../plans/0007-backup-staleness-and-docs-truth.md) for the verification log.

## Take a backup

```bash
scripts/backup_db.sh                 # writes backups/finstream-<UTC timestamp>.dump
scripts/backup_db.sh /path/to/dir    # or somewhere else
```

The stack can stay up. The script prints what it captured and where it landed:

```
Backing up the FinStream database
  contents: 2540 bars, 1835 ingestion runs
  written:  .../backups/finstream-20260920-200302.dump (176K)
```

`backups/` is git-ignored: a dump is data, not source. The database password never leaves the
container — it is expanded inside it from its own environment (GR-5).

### The warning you will see, and why it is not a problem

```
pg_dump: warning: there are circular foreign-key constraints on this table:
pg_dump: detail: continuous_agg
```

`continuous_agg` is a TimescaleDB catalog table, and this project uses no continuous aggregates.
A restore from a dump carrying this warning was verified to reproduce the data **byte for byte**,
with the hypertable and all its chunks intact. Do not add `--disable-triggers` to work around it.

## Restore

> Restoring **replaces** the target database. Restore into a scratch database first and compare,
> unless you are recovering from an actual loss.

Pick a target name. `finstream_restored` keeps the live database untouched; use the real database
name only when you mean to overwrite it.

```bash
TARGET=finstream_restored
DUMP=backups/finstream-20260920-200302.dump      # the dump you want back
```

**1. Create the database and prepare the extension.**

```bash
docker compose exec -T db sh -c "
  PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -U \"\$POSTGRES_USER\" -d postgres \
    -c 'CREATE DATABASE ${TARGET};'
  PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -U \"\$POSTGRES_USER\" -d ${TARGET} \
    -c 'CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;' \
    -c 'SELECT timescaledb_pre_restore();'
"
```

`timescaledb_pre_restore()` puts the extension into restoring mode so `pg_restore` can write its
catalog rows. This is TimescaleDB's own documented path; follow it even though a restore into a
brand-new database happens to work without it on this image (the image ships the extension in
`template1`, which is an image detail, not a guarantee).

**2. Restore the dump.**

```bash
docker compose exec -T db sh -c "
  PGPASSWORD=\"\$POSTGRES_PASSWORD\" pg_restore -U \"\$POSTGRES_USER\" -d ${TARGET} \
    --no-owner --no-privileges
" < "${DUMP}"
```

Do not pass `--create`: the database already exists and the extension is already prepared.

**3. Leave restoring mode.** This step is not optional — the extension stays degraded until it runs.

```bash
docker compose exec -T db sh -c "
  PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -U \"\$POSTGRES_USER\" -d ${TARGET} \
    -c 'SELECT timescaledb_post_restore();'
"
```

## Verify the restore

Never trust an exit code alone; compare content.

```bash
docker compose exec -T db sh -c "
  for D in \"\$POSTGRES_DB\" ${TARGET}; do
    echo -n \"\$D: \"
    PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -U \"\$POSTGRES_USER\" -d \"\$D\" -tAc \
      \"SELECT count(*) || ' bars, ' ||
               (SELECT count(*) FROM timescaledb_information.chunks
                WHERE hypertable_name = 'market_prices') || ' chunks'
         FROM raw.market_prices;\"
  done
"
```

Both lines must agree. For a stronger check, compare a content checksum of every bar:

```sql
SELECT md5(string_agg(t, E'\n' ORDER BY t))
FROM (SELECT source || symbol || bar_interval || ts::text || coalesce(close::text, '-') AS t
      FROM raw.market_prices) s;
```

## Put a restored database back into service

The services read `DATABASE_URL` from `.env`, which agents must not touch (GR-5) — so this step is
the maintainer's:

1. Stop the writers: `docker compose stop ingestor dashboard`.
2. Either point `DATABASE_URL` at the restored database, or rename: drop the damaged database and
   `ALTER DATABASE finstream_restored RENAME TO finstream;`.
3. `docker compose up -d ingestor dashboard`, then confirm on the dashboard's **Ingestion health**
   tab that new runs succeed and the freshness banner clears.

The Ingestor will simply carry on: every write is an idempotent upsert on the natural key (GR-2),
so re-collecting a period that the backup already contains changes no row count.

## Clean up a scratch restore

```bash
docker compose exec -T db sh -c "
  PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -U \"\$POSTGRES_USER\" -d postgres \
    -c 'DROP DATABASE IF EXISTS ${TARGET};'
"
```

## Limits

- **Same-version only.** Restoring across a TimescaleDB *major* version is unverified. Check before
  any extension upgrade; a CSV-style export may be the safer route then.
- **No point-in-time recovery.** You get back the state at the moment of the dump, nothing finer.
- **Backups are manual.** Nothing schedules `backup_db.sh` yet; a backup exists only when someone
  runs it.
- **The dump is unencrypted.** It holds no credentials, but it does hold all collected data.
