# Configuration

All configuration comes from environment variables (GR-4). Locally they're loaded from a `.env` file at the repo root, which Docker Compose passes to the containers.

- **This document is the source of truth.** `.env.example` must list every variable below with a safe placeholder or default, in the same order.
- **`.env` is never committed and never read or edited by agents** (GR-5). The user creates it: `cp .env.example .env`.
- **Validation happens at startup** (pydantic-settings). An invalid or missing required value stops the service with an error naming the variable.

> **Status: current.** A new or changed variable must update this file and `.env.example` in the same change (GR-4).

## Database

| Variable | Required | Default | Used by | Description / validation |
|---|---|---|---|---|
| `POSTGRES_USER` | no | `finstream` | db, smoke test | Database superuser created by the DB container |
| `POSTGRES_PASSWORD` | **yes** | — | db | Password for `POSTGRES_USER`. Compose refuses to start without it |
| `POSTGRES_DB` | no | `finstream` | db, smoke test | Database name |
| `POSTGRES_PORT` | no | `5432` | db | Host port Compose publishes on `127.0.0.1` |
| `DATABASE_URL` | **yes** | — | ingestor | SQLAlchemy URL with the psycopg 3 driver: `postgresql+psycopg://<user>:<password>@db:5432/<db>`. Special characters in the password must be URL-encoded. Stored as a secret and never logged unmasked |
| `TIMESCALEDB_ENABLED` | no | `true` | ingestor | `true`: create the `timescaledb` extension and the hypertable. `false`: plain PostgreSQL tables |

## Source: Yahoo Finance

| Variable | Required | Default | Description / validation |
|---|---|---|---|
| `YAHOO_SYMBOLS` | no | see the table below | Comma-separated Yahoo symbols. Whitespace is trimmed, duplicates removed, must not be empty. More symbols means more calls per cycle, so trim the list if Yahoo starts rate limiting |

Default symbol set (every symbol verified against Yahoo on 2026-09-17, plan 0004):

| Class | Symbols |
|---|---|
| Metals | `GC=F` gold futures, `SI=F` silver futures, `GLD` gold ETF, `SLV` silver ETF |
| Energy | `CL=F` WTI crude, `NG=F` natural gas |
| Indices | `^GSPC` S&P 500, `^IXIC` Nasdaq Composite, `^DJI` Dow Jones, `^RUT` Russell 2000, `^VIX` volatility index, `^FTSE` FTSE 100, `^STOXX50E` Euro Stoxx 50 |
| ETFs | `SPY` S&P 500, `QQQ` Nasdaq 100, `IWM` Russell 2000, `TLT` 20+ year Treasuries |
| FX | `EURUSD=X`, `USDPLN=X`, `DX-Y.NYB` dollar index |
| Crypto | `BTC-USD`, `ETH-USD` |
| Rates | `^TNX` US 10-year Treasury yield |

`^WIG20` is **not** included: Yahoo reports it delisted and it returns no prices. `WIG20.WA` does
work and can be added to `YAHOO_SYMBOLS` if you want the Warsaw index.

Several indices (`^GDAXI`, `^FCHI`, `^N225`, `^HSI`) return a NaN close for the most recent
intraday bar; they are stored faithfully and the dashboard falls back to the last valid close.

## Schedules

| Variable | Required | Default | Description / validation |
|---|---|---|---|
| `SCHEDULER_TIMEZONE` | no | `UTC` | IANA timezone for cron triggers. UTC avoids DST surprises |
| `SCHEDULER_MISFIRE_GRACE_SECONDS` | no | `300` | How late a trigger may still run (APScheduler `misfire_grace_time`). Positive integer. Applies to the heartbeat, and to ingestion when catch-up is off |
| `SCHEDULER_CATCH_UP_MISSED_RUNS` | no | `true` | `true`: ingestion jobs run however late they are, so a suspended host or a lost network catches up immediately instead of waiting for the next interval. `coalesce` still collapses a backlog into one run, and upserts make the repeat harmless. `false` restores the grace period above ([plan 0005](plans/0005-catch-up-missed-runs.md)) |
| `INTRADAY_ENABLED` | no | `true` | Enable the intraday job |
| `INTRADAY_INTERVAL` | no | `1h` | Bar interval requested from Yahoo. Must be a supported yfinance interval |
| `INTRADAY_EVERY_MINUTES` | no | `60` | How often the intraday job runs. Positive integer |
| `INTRADAY_LOOKBACK` | no | `5d` | Period fetched on each run (yfinance period string). Should overlap previous runs |
| `DAILY_ENABLED` | no | `true` | Enable the daily job |
| `DAILY_CRON` | no | `30 22 * * mon-fri` | 5-field crontab in `SCHEDULER_TIMEZONE`. The default is 22:30 UTC on weekdays, after the US close in both EST and EDT. **Use day names** (`mon-fri`), not numbers, because APScheduler's day-of-week numbering can differ from classic cron (verified in plan 0001 R2) |
| `DAILY_LOOKBACK` | no | `10d` | Period of `1d` bars fetched on each daily run |
| `BACKFILL_ON_START` | no | `false` | Run a one-off `1d` backfill at startup |
| `BACKFILL_PERIOD` | no | `2y` | Backfill period (yfinance period string, e.g. `1y`, `5y`, `max`) |

> Yahoo limits how far back intraday intervals go (roughly the last 730 days for `1h`), which is **UNVERIFIED** until plan 0001 R1. Backfill therefore uses daily bars only.

## Retries

| Variable | Required | Default | Description / validation |
|---|---|---|---|
| `RETRY_MAX_ATTEMPTS` | no | `5` | Max attempts per API call or DB operation (including the first). Integer ≥ 1 |
| `RETRY_MAX_WAIT_SECONDS` | no | `60` | Upper bound of the exponential backoff with jitter between attempts. Integer ≥ 0 |

## Dashboard (`services/dashboard`)

The dashboard reuses `DATABASE_URL` and only ever reads ([ADR-0005](adr/0005-serving-reads-raw-directly.md)).

| Variable | Required | Default | Description / validation |
|---|---|---|---|
| `DASHBOARD_PORT` | no | `8501` | Host port Compose publishes on `127.0.0.1` |
| `DASHBOARD_CACHE_TTL_SECONDS` | no | `60` | How long query results are cached, so moving a widget does not re-query the database. Integer ≥ 0 |
| `DASHBOARD_DEFAULT_INTERVAL` | no | `1d` | Bar interval selected when the page loads |
| `DASHBOARD_MAX_ROWS` | no | `5000` | Upper bound on rows pulled into one chart. Integer ≥ 100 |

## Health & logging

| Variable | Required | Default | Description / validation |
|---|---|---|---|
| `HEARTBEAT_FILE` | no | `/tmp/finstream-ingestor.heartbeat` | File touched by the heartbeat job and checked by the Docker `HEALTHCHECK` |
| `HEARTBEAT_EVERY_SECONDS` | no | `60` | Heartbeat period. The healthcheck treats the service as unhealthy if the file is older than 3× this value |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | no | `json` | `json` (default, for containers) or `text` (local debugging) |

## The real `.env.example`

This document describes each variable; [`.env.example`](../.env.example) at the repository root is
the file you copy. It is deliberately **not** reproduced here — an embedded copy drifted silently
once already, going stale on `POSTGRES_PORT`, `SCHEDULER_CATCH_UP_MISSED_RUNS` and every
`DASHBOARD_*` variable while this page still called itself the source of truth.

```bash
cp .env.example .env    # then edit it; agents never read or write .env (GR-5)
```
