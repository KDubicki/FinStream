# Research: making FinStream event-driven

- **Date:** 2026-09-18
- **Author:** Claude Code (Opus 5), research agent; reviewed by: *pending*
- **Related plan / item:** none yet — input to the "ingestion events" plan and to a **new ADR**
  ("Events are published through a transactional outbox in `raw`")
- **Status:** Draft <!-- Draft | Final -->

> **Question:** how should downstream services (processing, analytics, alerting, future serving
> APIs) learn about newly ingested data without polling `raw.*`?
>
> Research only — no production code, configuration or schema was changed. Every factual claim
> carries a URL and the date it was checked (all **2026-09-18**). Anything not confirmed against a
> primary source is tagged **UNVERIFIED**.
>
> **Measurements.** The RAM/image figures in §5 were taken on this machine (Docker Engine 29.4.0,
> Docker Desktop VM with 7.75 GiB RAM / 10 CPUs, `linux/arm64`) on 2026-09-18: `docker run -d …`,
> 45 s idle with zero traffic, `docker stats --no-stream`. Raw output is kept beside this note in
> [broker-measurements.txt](broker-measurements.txt). All images were removed afterwards. These are
> single-sample idle measurements on one machine, not a benchmark — they are here to separate
> "invisible" from "heavier than the database", which is the only distinction the decision needs.
>
> **Independent re-verification.** The orchestrating session re-fetched the two load-bearing
> PostgreSQL 16 quotes (`INSERT … RETURNING` returning only rows actually inserted/updated, and
> `NOTIFY`'s 8000-byte payload limit with its claim-check advice) and the PyPI records for
> `nats-py` 2.16.0, `cloudevents` 2.2.0, `redis` 8.1.0, `confluent-kafka` 2.15.1 and
> `psycopg` 3.3.5 on 2026-09-18. All matched exactly.
>
> Companion notes: [01 current state](01-current-state-and-backlog.md) ·
> [02 data sources](02-data-sources.md) · [04 roadmap](04-roadmap.md)

---

## 1. Executive recommendation (the short version)

**Do not add a broker yet. Add a transactional outbox inside the database you already run, and ring a `LISTEN`/`NOTIFY` doorbell on top of it.**

The single most important finding is that FinStream's existing upsert already computes exactly the event stream you want, for free:

```sql
ON CONFLICT (...) DO UPDATE SET ... WHERE (t.open, ...) IS DISTINCT FROM (EXCLUDED.open, ...)
RETURNING market_prices.ts
```

PostgreSQL documents that for `INSERT … ON CONFLICT DO UPDATE … RETURNING`, *"Only rows that were successfully inserted or updated will be returned. For example, if a row was locked but not updated because an `ON CONFLICT DO UPDATE ... WHERE` clause `condition` was not satisfied, the row will not be returned."* ([PostgreSQL 16, `INSERT`](https://www.postgresql.org/docs/16/sql-insert.html), checked 2026-09-18). So the rows returned by `db.upsert_bars` **are** the set of bars that genuinely changed. A re-run that changes nothing returns zero rows and must therefore emit zero events. Idempotent ingestion (GR-2) and a non-noisy event stream are the same property.

Staged path (details in §11):

| Stage | What lands | New containers | New pinned deps | Trigger to move on |
|---|---|---|---|---|
| **1** | `raw.ingestion_events` outbox written in the *same transaction* as the upsert + `pg_notify` on commit | **0** | **0** | — |
| **2** | A consumer library/service: `LISTEN` for latency, `SELECT … FOR UPDATE SKIP LOCKED` (or per-consumer cursor) for durability and catch-up; dashboard live refresh | 0–1 (the processing service you were going to build anyway) | 0 | — |
| **3** | NATS + JetStream, fed by an outbox relay; outbox stays as the source of truth | 1 (6.9 MiB image, **3.77 MiB RAM idle, measured**) | `nats-py` | A non-Python consumer, fan-out beyond one DB, backpressure, or replay needs that the DB can't serve |

Runner-up brokers if stage 3 ever needs different semantics: Redis Streams (12.22 MiB idle, weakest durability defaults) and Redpanda (**587.2 MiB idle, measured** — too heavy to sit next to TimescaleDB on a laptop).

---

## 2. What FinStream looks like today (baseline facts, from the repo)

| Fact | Source in repo |
|---|---|
| One TimescaleDB container, one ingestor daemon, one read-only Streamlit dashboard | `docker-compose.yml` |
| DB image `timescale/timescaledb:2.30.0-pg16` → PostgreSQL 16 | `docker-compose.yml` |
| `DATABASE_URL=postgresql+psycopg://…` → **psycopg 3 is already a dependency** | `.env.example`, `services/ingestor/requirements.txt` (`psycopg[binary]==3.3.5`) |
| Upsert uses `ON CONFLICT DO UPDATE … WHERE … IS DISTINCT FROM … RETURNING ts`, counted via `len(conn.execute(...).all())` | `services/ingestor/src/finstream_ingestor/db.py:upsert_bars` |
| Each 1000-row batch runs in its **own** `engine.begin()` transaction; `start_run` / `finish_run` are separate transactions | `db.py` |
| `raw.ingestion_runs` is already an append-only event log, and the repo already argues why that does not violate GR-2 | `docs/data-model.md` §3 |
| The job boundary swallows every exception so the scheduler survives (GR-3) | `services/ingestor/src/finstream_ingestor/jobs.py` |
| The dashboard reads `raw` directly and is expected to be superseded when a processing layer arrives | `docs/adr/0005-serving-reads-raw-directly.md` |

Two consequences for this research:

1. **psycopg 3 is already pinned**, so `LISTEN`/`NOTIFY` costs zero new dependencies (GR-8 friction: none).
2. **Writing an outbox row atomically with the bars requires touching `upsert_bars`'s transaction scope** — a small, local change, but a change, so a plan is required (AGENTS.md §4).

---

## 3. Option family A — PostgreSQL-native, no broker

### 3.1 `LISTEN` / `NOTIFY`

All quotes from [PostgreSQL 16 `NOTIFY`](https://www.postgresql.org/docs/16/sql-notify.html) and [`LISTEN`](https://www.postgresql.org/docs/16/sql-listen.html), checked 2026-09-18.

| Property | What the docs actually say |
|---|---|
| **Payload limit** | *"In the default configuration it must be shorter than 8000 bytes."* — the 8000-byte figure in the brief is **confirmed**, and it is a *byte* limit on the payload string. |
| **The docs themselves recommend the claim-check pattern** | *"(If binary data or large amounts of information need to be communicated, it's best to put it in a database table and send the key of the record.)"* |
| **Transactional** | *"if a `NOTIFY` is executed inside a transaction, the notify events are not delivered until and unless the transaction is committed."* And: *"if the transaction is aborted, all the commands within it have had no effect, including `NOTIFY`."* → **atomic with the data write, for free.** |
| **Delivery to listeners only** | *"all the sessions currently listening on that notification channel are notified"*. A session that is not listening at commit time never sees the event. Registrations are per-session and *"a session's listen registrations are automatically cleared when the session ends."* → **at-most-once; a reconnect loses everything sent while disconnected.** |
| **Duplicate folding** | *"If the same channel name is signaled multiple times with identical payload strings within the same transaction, only one instance of the notification event is delivered"*; distinct payloads are always distinct; *"notifications from different transactions will never get folded into one notification."* |
| **Ordering** | *"NOTIFY guarantees that notifications from the same transaction get delivered in the order they were sent."* |
| **Queue** | *"There is a queue that holds notifications that have been sent but not yet processed by all listening sessions. If this queue becomes full, transactions calling `NOTIFY` will fail at commit. The queue is quite large (8GB in a standard installation)…"* — and a listener that sits inside a long transaction blocks cleanup: *"Once the queue is half full you will see warnings in the log file…"*. `pg_notification_queue_usage()` reports the fill fraction. |
| **No 2PC** | *"A transaction that has executed `NOTIFY` cannot be prepared for two-phase commit."* |
| **Dynamic payloads** | `pg_notify(text, text)` — *"The function is much easier to use than the `NOTIFY` command if you need to work with non-constant channel names and payloads."* |

**psycopg 3 API** ([psycopg 3 — Asynchronous notifications](https://www.psycopg.org/psycopg3/docs/advanced/async.html), checked 2026-09-18; installed version `psycopg[binary]==3.3.5`, [PyPI](https://pypi.org/pypi/psycopg/json), released 2026-08-31):

- *"you should keep the connection in `autocommit` mode if you wish to receive or send notifications in a timely manner."*
- `Connection.notifies()` returns a generator; *"The generator can be stopped using its `close()` method, or using the parameters `timeout` or `stop_after` to receive notifications only for a certain time or up to a certain number."*
- Notifications arrive as `Notify(channel=…, payload=…, pid=…)`.
- Important fix: *"Before Psycopg 3.2.4, notification received between calling `LISTEN` and starting the generator were lost."* We are on 3.3.5, so we are past that.
- `AsyncConnection.notifies()` is the async equivalent. **UNVERIFIED** in the page I fetched, but implied by the page's async framing.

**Verdict.** Perfect as a *doorbell*, useless as a *log*. At-most-once, no persistence, no replay, nothing while a consumer is down or restarting. Never make it the only transport.

### 3.2 Outbox / change-log table polled with `FOR UPDATE SKIP LOCKED`

[PostgreSQL 16 `SELECT` — locking clauses](https://www.postgresql.org/docs/16/sql-select.html), checked 2026-09-18:

- *"With `SKIP LOCKED`, any selected rows that cannot be immediately locked are skipped."*
- *"Skipping locked rows provides an inconsistent view of the data, so this is not suitable for general purpose work, but can be used to avoid lock contention with multiple consumers accessing a queue-like table."*

That is a direct endorsement of the queue use case, with an explicit caveat that you must not use it for general reads.

This is exactly the mechanism the mature Python "Postgres as a queue" libraries use. [PgQueuer](https://github.com/janbjorge/pgqueuer) (checked 2026-09-18, [PyPI `pgqueuer` 1.4.0](https://pypi.org/pypi/pgqueuer/json), released 2026-09-14, requires Python ≥ 3.10): *"LISTEN/NOTIFY wakes workers the moment a job lands (with a polling fallback)"* and `FOR UPDATE SKIP LOCKED` so jobs are *"never double-processed"*; it supports asyncpg and psycopg, and its pitch is *"If you already run PostgreSQL, it can do double duty as your job queue. That means one fewer service to operate."* [Procrastinate](https://pypi.org/pypi/procrastinate/json) 3.9.0, released 2026-06-20, is the other well-known option.

I am **not** recommending adopting either library (they are *task* queues; we want a *fan-out event log*), but they are strong evidence that the NOTIFY-doorbell + SKIP-LOCKED-truth combination is a well-trodden production pattern rather than a homegrown invention.

**Two consumption shapes, pick deliberately:**

| Shape | Mechanism | Fan-out | Replay | Notes |
|---|---|---|---|---|
| **Work queue** | `SELECT … WHERE processed_at IS NULL … FOR UPDATE SKIP LOCKED LIMIT n`, then mark/delete | One logical consumer (competing workers) | No (rows are consumed) | Simple, self-trimming |
| **Log + per-consumer cursor** | `event_id bigint` + `consumer_offsets(consumer, last_event_id)`; each consumer reads forward | N independent consumers | Yes, while rows are retained | This is what we want for a platform |

**Gap to design around (important):** `bigserial` ids are handed out *before* commit, so a transaction with a lower id can become visible *after* one with a higher id. A cursor that advances to `max(event_id)` can therefore skip events. Documented mitigations use snapshot functions — `pg_current_snapshot() → pg_snapshot`, *"Returns a current snapshot, a data structure showing which transaction IDs are now in-progress"*, with `pg_snapshot_xmin()` and `pg_visible_in_snapshot()` ([PostgreSQL 16, System Information Functions](https://www.postgresql.org/docs/16/functions-info.html), checked 2026-09-18) — i.e. store `xmin` alongside the id and only advance the cursor past ids whose transactions are certainly committed, or simply read with a short watermark lag. At FinStream's write rate (a handful of symbols, one writer, batches of a few dozen rows) the practical exposure is tiny, but it must be written down, not discovered later.

### 3.3 Logical replication / `pgoutput` / `wal2json` / Debezium

| Requirement | Evidence |
|---|---|
| `wal_level = logical` | *"`logical` adds information necessary to support logical decoding… This parameter can only be set at server start."* and *"Using a level of `logical` will increase the WAL volume"* ([PG16 WAL config](https://www.postgresql.org/docs/16/runtime-config-wal.html), checked 2026-09-18). **Requires a DB restart and a config change to the pinned Timescale image.** |
| Replication slots are dangerous if neglected | *"Replication slots persist across crashes and know nothing about the state of their consumer(s). They will prevent removal of required resources even when there is no connection using them. This consumes storage because neither required WAL nor required rows from the system catalogs can be removed by `VACUUM` as long as they are required by a replication slot. In extreme cases this could cause the database to shut down to prevent transaction ID wraparound… So if a slot is no longer required it should be dropped."* ([PG16 Logical Decoding](https://www.postgresql.org/docs/16/logicaldecoding-explanation.html), checked 2026-09-18) |
| Slot guarantees | *"A logical slot will emit each change just once in normal operation"*; slots are *"crash-safe"* and *"persist independently of the connection using them"* (same page). |
| Debezium needs a JVM stack | Debezium runs either as a Kafka Connect connector or as standalone Debezium Server; *"Both require Java runtime"* ([Debezium 3.6 PostgreSQL connector](https://debezium.io/documentation/reference/stable/connectors/postgresql.html), checked 2026-09-18). `pgoutput` is *"The standard logical decoding output plug-in in PostgreSQL 10+."* |
| **TimescaleDB makes CDC worse, not better** | *"By default, the connector captures changes from each chunk table, and streams the changes to the individual topics that correspond to each chunk."* A dedicated SMT is needed to reassemble them into one logical topic, and *"The TimescaleDB SMT does not apply any special processing to compression functions. Compressed chunks are forwarded unchanged… Typically, messages with compressed chunks are dropped"* ([Debezium 3.6 TimescaleDB SMT](https://debezium.io/documentation/reference/stable/transformations/timescaledb.html), checked 2026-09-18). FinStream's roadmap explicitly lists TimescaleDB compression as a follow-up (`docs/architecture.md` §4) — adopting CDC now would put those two on a collision course. |

Debezium *does* have a first-class [Outbox Event Router SMT](https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html) (Debezium 3.6, checked 2026-09-18) expecting columns `id`, `aggregatetype`, `aggregateid`, `type`, `payload`, designed so applications *"write to a local outbox table within their transaction"* rather than dual-writing. **This is the single best reason to shape the stage-1 outbox table with Debezium-compatible column names now** — it makes stage 3 a configuration exercise rather than a migration, if we ever want CDC.

**Verdict.** Wrong tool at this scale. It buys "capture everything that ever changes" at the price of a WAL config change, a slot that can silently fill the disk, a JVM sidecar, and a documented conflict with Timescale compression. Revisit only if FinStream ever needs to stream the whole `raw` schema to a foreign system.

### 3.4 TimescaleDB-specific hooks — **verified, and the answer is no**

I checked this rather than assuming:

- **Continuous aggregates emit nothing.** [`refresh_continuous_aggregate()`](https://www.tigerdata.com/docs/api/latest/continuous-aggregates/refresh_continuous_aggregate) (checked 2026-09-18) *"Refresh all buckets of a continuous aggregate in the refresh window"*; since 2.28.0 it *"processes the refresh window incrementally in batches by default… Each batch runs in its own transaction"*. **The documentation makes no mention of any notification or event emitted on refresh.**
- **You cannot even put a trigger on a continuous aggregate.** [TimescaleDB — Triggers](https://www.tigerdata.com/docs/use-timescale/latest/schema-management/triggers) (checked 2026-09-18) states outright: *"Triggers are not supported on continuous aggregates"*, plus *"`ROW`-level triggers with transition tables are not supported on hypertables"* and *"`DELETE` triggers with transition tables are not supported"*.
- **Triggers on a hypertable do work and do propagate:** *"When you create, alter, drop, enable, or disable a trigger on a hypertable — including via `ALTER TABLE … ENABLE/DISABLE TRIGGER` with the `ALL`, `USER`, `ALWAYS`, and `REPLICA` variants — TimescaleDB propagates the change to every chunk."* (same page).
- **The only Timescale-native "hook" is its job scheduler.** `add_job()` registers a PL/pgSQL procedure with a `schedule_interval` ([TigerData — `add_job()`](https://docs.tigerdata.com/api/latest/jobs-automation/add_job/), checked 2026-09-18 via search index; exact API text **partially UNVERIFIED** — the page was reached through search results, not fetched directly). A user-defined action calling `pg_notify()` after a refresh is possible, but it is a thing *we* would build, not a thing TimescaleDB gives us.

**Conclusion:** TimescaleDB adds **no** notification capability. Everything event-driven must be built on plain PostgreSQL mechanisms — which is convenient, because it also keeps working in `TIMESCALEDB_ENABLED=false` mode.

A row-level `AFTER INSERT OR UPDATE` trigger on `raw.market_prices` calling `pg_notify` *would* work and would require zero application changes. I **do not** recommend it: it hides the event contract in DDL where the ingestor's tests don't see it, it fires per row (a 1000-row batch becomes 1000 notifications unless deduplicated), and it puts business meaning in the database rather than in `jobs.py`. The application-level outbox is explicit, testable with the existing pytest + testcontainers setup (GR-7), and reviewable.

### 3.5 Postgres-native comparison

| | LISTEN/NOTIFY alone | Outbox + SKIP LOCKED | Outbox + NOTIFY doorbell | Logical replication / Debezium | Timescale hooks |
|---|---|---|---|---|---|
| New containers | 0 | 0 | 0 | 1–2 (JVM) | 0 |
| New pinned deps | 0 | 0 | 0 | 0 Python / lots of Java | 0 |
| Atomic with the data write | Yes (commit-gated) | Yes | Yes | Yes (reads the WAL) | n/a |
| Survives consumer downtime | **No** | Yes | Yes | Yes (slot retains WAL) | n/a |
| Replay for a late joiner | No | Yes (while retained) | Yes | Yes (from slot / topic) | n/a |
| Latency | ~ms | poll interval | ~ms | ~ms | n/a |
| Payload ceiling | **< 8000 bytes** | unlimited (a table row) | unlimited (thin notify, fat row) | unlimited | n/a |
| Ops risk | queue fills at 8 GB | table growth → needs retention | both, both bounded | **slot can fill the disk / force shutdown** | n/a |
| Fits GR-3 | Yes | Yes | Yes | Yes | n/a |
| **Verdict** | doorbell only | good | **recommended** | premature | **nothing on offer** |

---

## 4. The transactional outbox, precisely

Canonical statement of the pattern ([microservices.io — Transactional outbox](https://microservices.io/patterns/data/transactional-outbox.html), checked 2026-09-18):

- Problem: *"How to atomically update the database and send messages to a message broker?"*
- Solution: *"The service that sends the message to first store the message in the database as part of the transaction that updates the business entities. A separate process then sends the messages to the message broker."*
- Benefits: *"2PC is not used"*; *"Messages are guaranteed to be sent if and only if the database transaction commits"*; *"Messages are sent to the message broker in the order they were sent by the application"*.
- Drawbacks: *"Potentially error prone since the developer might forget to publish the message/event after updating the database"*, and the relay may publish duplicates, so consumers must be idempotent.
- Relays: the *Polling publisher* pattern (our stage 1/2) or the *Transaction log tailing* pattern (Debezium, §3.3).

### 4.1 Why this matters *specifically* for GR-2 and GR-3

**GR-2 — "Every write to a data table MUST be an upsert on the natural key. Running a job twice MUST NOT change the row count."**

The lookback windows deliberately overlap (`docs/architecture.md` §2.4), so the ingestor re-fetches the same bars many times a day. A naive "publish an event for every bar I fetched" design would emit a torrent of events that say nothing changed. The `WHERE … IS DISTINCT FROM` guard plus `RETURNING` already filters that down to real changes, at the database, for free. **The outbox row must be derived from the `RETURNING` result, not from the input `bars` list.** Doing it the other way round turns the overlap-heals-gaps design into an event-storm generator.

**GR-3 — "The daemon never dies because of a job. External calls (APIs, DB) MUST go through tenacity retries… A job failure MUST be logged, recorded in `raw.ingestion_runs`, and MUST NOT propagate to the scheduler."**

Publishing directly to a broker from inside `jobs.ingest` would add a *second* external system to the hot path. Even wrapped in `try/except` (the job boundary already catches everything) you get the dual-write problem: the DB commits, the broker publish fails, the event is lost forever and nothing records that it was lost. The outbox removes the extra external call entirely — the "publish" is a row in the same transaction, protected by the same `OperationalError` retryer that already guards `upsert_bars`. The relay that talks to a broker lives **outside** the scheduler process (or in a separate job whose failure only delays delivery). **This is the decisive architectural argument for the outbox over direct publishing, and it is a golden-rule argument, not a taste argument.**

### 4.2 Exact PostgreSQL semantics of `INSERT … ON CONFLICT DO UPDATE … WHERE … RETURNING`

All from [PostgreSQL 16 `INSERT`](https://www.postgresql.org/docs/16/sql-insert.html), checked 2026-09-18:

1. **The `WHERE` on `DO UPDATE` is evaluated last and still locks:** *"An expression that returns a value of type `boolean`. Only rows for which this expression returns `true` will be updated, although all rows will be locked when the `ON CONFLICT DO UPDATE` action is taken. Note that condition is evaluated last, after a conflict has been identified as a candidate to update."*
   → Unchanged bars are **locked but not written**. No dead tuple, no bloat, `updated_at` untouched. Cost is a row lock for the duration of the transaction, which for a single-writer ingestor is free.
2. **`RETURNING` returns exactly the rows that changed:** *"Only rows that were successfully inserted or updated will be returned. For example, if a row was locked but not updated because an `ON CONFLICT DO UPDATE ... WHERE` clause condition was not satisfied, the row will not be returned."*
   → `len(conn.execute(statement).all())` in `db.upsert_bars` is already a correct change count, and the returned rows are already a correct change *set*. This is the whole event stream.
3. **Atomicity:** *"ON CONFLICT DO UPDATE guarantees an atomic INSERT or UPDATE outcome; provided there is no independent error, one of those two outcomes is guaranteed, even under high concurrency."*

### 4.3 Can `RETURNING` tell inserted from updated from unchanged?

- **Unchanged** — yes, trivially and by documented behaviour: unchanged rows are simply absent from `RETURNING` (quote 2 above). ✅
- **Inserted vs updated** — PostgreSQL 16 gives you **no documented, first-class way**. Three candidate techniques, honestly rated:

| Technique | Verdict | Evidence |
|---|---|---|
| `RETURNING (xmax = 0) AS inserted` | **UNVERIFIED — do not rely on it.** The system-column docs say `xmax` is *"The identity (transaction ID) of the deleting transaction, or zero for an undeleted row version. It is possible for this column to be nonzero in a visible row version."* Nothing in the `INSERT` or system-column documentation states or promises that `ON CONFLICT DO UPDATE` leaves a non-zero `xmax` on the returned tuple. It is folklore that happens to work today. | [PG16 System Columns](https://www.postgresql.org/docs/16/ddl-system-columns.html), checked 2026-09-18 |
| `RETURNING (ingested_at = updated_at) AS inserted` | **Works, on documented semantics.** `now()` returns the transaction start time and *"their values do not change during the transaction… the intent is to allow a single transaction to have a consistent notion of the 'current' time, so that multiple modifications within the same transaction bear the same time stamp."* On insert both columns take the same `now()` default; on update only `updated_at` is set to `now()`, and `ingested_at` retains an earlier transaction's timestamp. **Caveat:** if the same natural key were inserted and then updated inside *one* transaction (two batches of the same `engine.begin()`), the test would misreport. The current code gives each batch its own transaction, so this is safe today, but the invariant must be written down. | [PG16 Date/Time Functions](https://www.postgresql.org/docs/16/functions-datetime.html), checked 2026-09-18 |
| `MERGE … RETURNING merge_action(), …` | **The real answer — but needs PostgreSQL 17.** *"The optional `RETURNING` clause causes `MERGE` to compute and return value(s) based on each row inserted, updated, or deleted. Any expression using the source or target table's columns, or the `merge_action()` function can be computed."* FinStream is pinned to `pg16`. | [PG17 `MERGE`](https://www.postgresql.org/docs/17/sql-merge.html), checked 2026-09-18 |

**Recommendation:** do not model `bars.inserted` and `bars.revised` as separate events in stage 1. Emit one `bars.upserted`, and if a consumer later needs the distinction, add a `revised boolean` field derived from `(ingested_at = updated_at)` — cheap, documented, and reversible. Do **not** ship the `xmax` trick into a repo whose GR-9 says *"Library APIs and versions MUST be checked against current sources… not memory."*

### 4.4 The one code change stage 1 requires

`upsert_bars` currently opens `engine.begin()` per batch and returns an `int`. For the outbox to be atomic with the data, the event row must be inserted **inside that same `with engine.begin()` block**, from the `RETURNING` result. Sketch of the shape (not code to commit — no approved plan exists):

- `upsert_bars` returns the changed keys (or a small summary per `(source, symbol, bar_interval)`) instead of only a count, and writes `raw.ingestion_events` + `pg_notify` in the same transaction; or
- an `emit` callback is passed in, keeping `db.py` free of policy.

Either way this is a multi-file change touching the schema, so **AGENTS.md §4 requires a plan and approval before any code.**

---

## 5. Option family B — brokers. Measured footprint

**All RAM figures below are measured on this machine on 2026-09-18** (method in the header). "Image (pull)" is the compressed `linux/amd64` size from the Docker Hub registry API `full_size` on 2026-09-18; "Image (disk)" is the unpacked `linux/arm64` size reported by `docker images` here. Both are given because the two differ by 3–4×, and the compose footprint people actually feel is the unpacked one.

| Broker | Image (pull, amd64) | Image (disk, arm64) | **RAM idle, measured** | CPU idle | Version measured |
|---|---|---|---|---|---|
| **NATS + JetStream** `nats:2-alpine -js` | 10.9 MiB (`nats:2-alpine`); 6.9 MiB for `nats:latest` | 40.6 MB | **3.77 MiB** | 0.10 % | nats-server v2.15.0 |
| **Redis** `redis:8-alpine` | 37.2 MiB | 160 MB | **12.22 MiB** | 0.47 % | redis 8.10.1 |
| **RabbitMQ** `rabbitmq:4-alpine` | 80.6 MiB (108.7 MiB for the Debian tag) | 261 MB | **95.64 MiB** | 0.32 % | RabbitMQ 4.3.6 |
| **Redpanda** `redpandadata/redpanda:latest`, `--mode dev-container --smp 1 --overprovisioned` | 123.5 MiB | 531 MB | **587.2 MiB** | 0.49 % | latest, 2026-09-17 |
| **Apache Kafka (KRaft)** `apache/kafka:latest`, single-node combined | 227.8 MiB | 690 MB | **289.9 MiB** | 3.03 % | latest, 2026-06-24 |
| *(reference)* `timescale/timescaledb:2.30.0-pg16` | 555.5 MiB | — | already running | — | pinned in compose |

Registry sizes from `https://hub.docker.com/v2/repositories/<repo>/tags/<tag>` (`full_size`, amd64), checked 2026-09-18.

Independent corroboration of the heavy end:
- **Redpanda documents a floor of 2 GB RAM per core** for self-hosted brokers ([Redpanda — Requirements and Recommendations](https://docs.redpanda.com/current/deploy/deployment-option/self-hosted/manual/production/requirements/), reached via the docs search index on 2026-09-18; exact sentence **UNVERIFIED** — the direct page fetch 404'd and I am relying on the indexed summary). My measurement of 587 MiB was in explicitly-deprivileged `dev-container` mode; production posture is far larger.
- **Kafka's default broker heap is `-Xmx1G -Xms1G`** — literally `export KAFKA_HEAP_OPTS="-Xmx1G -Xms1G"` in [`bin/kafka-server-start.sh`](https://raw.githubusercontent.com/apache/kafka/trunk/bin/kafka-server-start.sh), checked 2026-09-18. The 289.9 MiB I measured is the JVM at rest inside that reservation; it grows.
- **Kafka 4.0 (18 March 2025) is the first release with no ZooKeeper at all:** *"Apache Kafka 4.0 is a significant milestone, marking the first major release to operate entirely without Apache ZooKeeper®"*, and *"Kafka Clients and Kafka Streams require Java 11, while Kafka Brokers, Connect and Tools now require Java 17"* ([AK 4.0.0 release announcement](https://kafka.apache.org/blog/2025/03/18/apache-kafka-4.0.0-release-announcement/), checked 2026-09-18). Latest supported release is **4.3.1, released 25 June 2026** ([Kafka downloads](https://kafka.apache.org/community/downloads/), checked 2026-09-18). So the "you need ZooKeeper too" objection is dead — but the JVM one is not.

### 5.1 Semantics and Python clients

Every version and release date in this table was read from `https://pypi.org/pypi/<pkg>/json` on **2026-09-18**. Nothing here is from memory.

| Broker | Persistence / replay | Delivery semantics | Ordering | Python client (PyPI) | Latest version | Released | `requires_python` | API style |
|---|---|---|---|---|---|---|---|---|
| **NATS JetStream** | File or memory storage; retention `Limits` / `Interest` / `WorkQueue`; consumers can start *"from the beginning of the stream, from the latest message, from a specific sequence number, or from a specific time"* | *"If a message isn't acknowledged in time, the server redelivers it, which is what gives you at-least-once delivery."* Exactly-once is assembled from `Nats-Msg-Id` dedup (default duplicate window **2m0s**) + double-ack | per stream/subject | `nats-py` | **2.16.0** | 2026-09-16 | ≥3.7 (classifiers list 3.12) | **asyncio only** — *"An asyncio Python client for NATS"*; *"Starting v2.0.0 series, the client now has JetStream support"* |
| **Redis Streams** | Append-only log in RAM, persisted via RDB/AOF. **Measured default in `redis:8-alpine`: `save 3600 1 300 100 60 10000`, `appendonly no`** — i.e. snapshot-only, up to an hour of loss on a hard kill unless you change it | at-least-once via the Pending Entries List; `XACK` to ack, `XAUTOCLAIM` to recover a dead consumer | per stream | `redis` | **8.1.0** | 2026-07-30 | ≥3.10 (3.12 listed) | sync + asyncio in one package |
| **RabbitMQ streams** | *"a persistent replicated data structure"*, *"an append-only log of messages that can be repeatedly read until they expire"*, offsets via `x-stream-offset` (`first`/`last`/offset/timestamp), retention by `max-age` / `max-length-bytes` | at-least-once + consumer acks | per stream | `pika` / `aio-pika` | **1.4.4** / **10.0.1** | 2026-08-06 / 2026-07-09 | ≥3.7 / ≥3.11,<4 | sync / asyncio |
| **RabbitMQ quorum queues** | Raft; *"persisted on disk regardless of the message delivery mode"*; never keeps message bodies in memory; *"1MB for every 30000 messages"* index | at-least-once | per queue | as above | — | — | — | — |
| **Kafka / Redpanda** | Partitioned log, retention by time/size, arbitrary offset seek | *"At most once / At least once / Exactly once"*; exactly-once needs the idempotent producer + transactions: *"the Kafka producer also supports an idempotent delivery option which guarantees that resending will not result in duplicate entries in the log"* | **per partition only** — *"Events with the same event key… are written to the same partition, and Kafka guarantees that any consumer of a given topic-partition will always read that partition's events in exactly the same order as they were written"* | `confluent-kafka` / `aiokafka` / `kafka-python` / `quixstreams` | **2.15.1** / **0.14.0** / **3.0.11** / **3.26.0** | 2026-09-10 / 2026-04-29 / 2026-08-16 / 2026-09-14 | ≥3.8 / ≥3.10 / ≥3.8 / ≥3.9,<4 | `confluent-kafka` is a librdkafka binding — **its sync-only nature is UNVERIFIED by a primary source**; `aiokafka` is asyncio by name and classifier |

Sources for the semantics quotes, all checked 2026-09-18:
[NATS JetStream concepts](https://docs.nats.io/nats-concepts/jetstream) (docs v2.15) ·
[NATS Streams](https://docs.nats.io/nats-concepts/jetstream/streams) ·
[Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/) ·
[RabbitMQ Streams](https://www.rabbitmq.com/docs/streams) (docs v4.3) ·
[RabbitMQ Quorum Queues](https://www.rabbitmq.com/docs/quorum-queues) (docs v4.3) ·
[Kafka 4.3 design — Message Delivery Semantics](https://raw.githubusercontent.com/apache/kafka/4.3/docs/design/design.md) ·
[Kafka 4.3 introduction](https://raw.githubusercontent.com/apache/kafka/4.3/docs/getting-started/introduction.md).

### 5.2 Single-node fitness

- **RabbitMQ quorum queues are pointless on one node.** The docs are explicit that *"it is highly recommended for the quorum queue group size to be an odd number"*, and a single-node group gives fault tolerance *"0"* and partition tolerance *"not applicable"*. You would pay 96 MiB and an Erlang runtime for none of the safety the feature exists to provide. RabbitMQ *streams* on one node are more defensible, but there is a wire-format catch: *"Streams internally store their messages as AMQP 1.0 encoded data… headers with complex values such as arrays or tables… will not be converted"* when read through an AMQP 0-9-1 client.
- **Redpanda's honest selling point is Kafka-compatibility without the JVM**, plus a **built-in schema registry**: *"The Schema Registry is built directly into the Redpanda binary. It runs out of the box with Redpanda's default configuration, and it requires no new binaries to install and no new services to deploy or maintain"*, supporting *"Avro, Protobuf, and JSON serialization formats"* ([Redpanda Schema Registry overview](https://docs.redpanda.com/current/manage/schema-reg/schema-reg-overview/), docs v26.2, checked 2026-09-18). That is genuinely attractive — but 587 MiB idle next to a 555 MiB TimescaleDB image on a single laptop is not a hobby-scale footprint.
- **NATS is the only one whose cost is invisible.** 3.77 MiB idle is less than the rounding error on the ingestor's own Python process. If a broker is ever added, this is the one that does not change the character of the deployment.

---

## 6. Event schema and contract

### 6.1 CloudEvents

- **Current released spec version: v1.0.2**; the `main` branch carries *"CloudEvents - Version 1.0.3-wip"* ([cloudevents/spec](https://github.com/cloudevents/spec), [spec.md](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md), checked 2026-09-18). CNCF graduation: Jan 25, 2024.
- **REQUIRED context attributes:** `id` (*"Producers MUST ensure that `source` + `id` is unique for each distinct event"*), `source` (*"MUST be a non-empty URI-reference"*), `specversion`, `type` (*"SHOULD be prefixed with a reverse-DNS name"*).
- **OPTIONAL:** `datacontenttype`, `dataschema` (*"Identifies the schema that `data` adheres to"*), `subject`, `time` (RFC 3339).
- **Python SDK:** [`cloudevents` 2.2.0](https://pypi.org/pypi/cloudevents/json), released **2026-06-11**, `requires_python >=3.10`, classifiers list 3.12. ✅ compatible.

**Recommendation:** adopt the CloudEvents *attribute names* in the outbox table from day one (`event_id`→`id`, `source`, `type`, `time`, `subject`, `data`), but **do not add the `cloudevents` dependency in stage 1**. The names are the valuable part — they cost nothing and make any future HTTP/NATS/Kafka binding mechanical. `source + id` uniqueness maps naturally onto a `UNIQUE(source, event_id)` constraint, which is also the consumer's dedup key.

### 6.2 JSON Schema vs Avro vs Protobuf

| | JSON Schema | Avro | Protobuf |
|---|---|---|---|
| Payload readable in `psql` | **Yes** — the outbox row is inspectable with a `SELECT` | No | No |
| Needs a registry to decode | No | Effectively yes | No (needs the `.proto`) |
| Python lib (PyPI, checked 2026-09-18) | `jsonschema` **4.26.0**, released 2026-01-07, ≥3.10 | `fastavro` **1.12.2**, released 2026-04-24, ≥3.9 | `protobuf` **7.36.2**, released 2026-09-17, ≥3.10 |
| Fits a `jsonb` outbox column | Perfectly | Awkward (bytes) | Awkward (bytes) |
| Compile step | None | Schema files | `protoc` codegen |

**Recommendation: JSON in a `jsonb` column, with a JSON Schema file under `docs/` as the contract, and no validation dependency until a second producer exists.** Avro/Protobuf earn their keep when bandwidth and cross-language codegen matter; FinStream has one language and a handful of events per minute. Adding `fastavro` or `protobuf` now would need a research-note justification under GR-8 that I do not think can honestly be written.

**Schema registry:** not warranted at stage 1 or 2. When/if it is: Redpanda's is built in (quote in §5.2); Confluent Schema Registry and Apicurio are separate services. The valuable idea to borrow *now* is the compatibility discipline, not the server.

### 6.3 Versioning practice

Confluent's compatibility taxonomy is the standard vocabulary ([Schema Evolution and Compatibility](https://docs.confluent.io/platform/current/schema-registry/fundamentals/schema-evolution.html), Confluent Platform 8.3, checked 2026-09-18): `BACKWARD` (*"consumers using new schema can read data written with old schema (add optional fields, remove fields)"*), `FORWARD`, `FULL` (*"add/remove optional fields only"*), their `_TRANSITIVE` variants, and `NONE`. **Default is `BACKWARD`.** And: *"the ability to delete a field and keep the schema compatible requires that the field was either specified as optional or provided a default value in the original version."*

Concrete rules for FinStream:

1. Version in the `type`, not in a field: `finstream.bars.upserted.v1`. A breaking change becomes `…v2` published **alongside** `v1` until consumers move.
2. Only additive, optional changes within a version (that is `BACKWARD` compatibility). Consumers must ignore unknown fields.
3. The schema lives in the repo next to `docs/data-model.md` and changes in the same commit as the code (GR-11).
4. `dataschema` on the event points at that file/URL.

### 6.4 Thin event vs fat event — the claim-check question

The claim-check pattern: *"Store a large message payload in an external data store and send only a reference token, called a claim check, through a messaging system… The messaging system never sees or stores the payload."* Primary use cases are *"message sizes surpass the limits of your messaging system"* and *"large messages are straining the messaging system"* ([Azure Architecture Center — Claim-Check pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/claim-check), ms.date 2024-05-01, checked 2026-09-18).

For FinStream the trade-off resolves decisively toward **thin**:

| | Thin event (keys + range) | Fat event (full bar payloads) |
|---|---|---|
| Fits under NOTIFY's 8000-byte payload | Yes, comfortably | No — one day of 1m bars blows it instantly |
| Consistent with `ADR-0005` (the DB is the source of truth) | Yes | Creates a second source of truth |
| Survives a schema change to `raw.market_prices` | Yes (consumer re-reads current columns) | No — every event carries a frozen copy of the old shape |
| Risk of GR-1 drift (transformation sneaking into the event) | Low — there is nothing to transform | **Higher** — a fat payload invites "just add a computed field" |
| Consumer must query the DB | Yes (one indexed range scan on the PK) | No |
| Replay after `adj_close` revision | Reads *current* values — correct | Replays *stale* values — wrong |

The last row is the killer for a market-data platform. Yahoo revises `adj_close` after corporate actions (`docs/data-model.md` §4), so a fat event is a photograph of a number that is explicitly allowed to change. **And PostgreSQL's own NOTIFY documentation gives the same advice:** *"If binary data or large amounts of information need to be communicated, it's best to put it in a database table and send the key of the record."*

**Decision: thin events.** Carry the natural key plus the changed time range and a count; the consumer reads `raw.market_prices` for the values. That is the claim-check pattern with the "external data store" being the database that was already the source of truth.

---

## 7. Delivery semantics in practice

| Question | Answer for FinStream |
|---|---|
| **At-least-once vs exactly-once** | Take at-least-once and make consumers idempotent. Kafka's own docs warn: *"Many systems claim to provide 'exactly-once' delivery semantics, but it is important to read the fine print, because sometimes these claims are misleading (i.e. they don't translate to the case where consumers or producers can fail…)"* ([Kafka 4.3 design](https://raw.githubusercontent.com/apache/kafka/4.3/docs/design/design.md), checked 2026-09-18). JetStream's exactly-once is likewise *assembled* from dedup + double-ack, not free. |
| **What makes a FinStream consumer idempotent** | The same thing that makes the ingestor idempotent: a natural key. `(source, symbol, bar_interval, ts)` for data, `(source, event_id)` for events. A consumer that upserts on the natural key can process an event twice with no effect — which is GR-2 propagating one layer downstream. This is a genuinely elegant fit and worth stating in the ADR. |
| **Ordering per key** | Postgres outbox: `NOTIFY guarantees that notifications from the same transaction get delivered in the order they were sent`, and a cursor read `ORDER BY event_id` gives total order per reader. Kafka/Redpanda: ordering exists **only within a partition**, so a key must hash to one partition. JetStream: ordered per stream/subject. For FinStream the natural partition key is `(source, symbol, bar_interval)`. |
| **Replay for a late-joining consumer** | **The database remains the source of truth — do not do event sourcing.** A new consumer bootstraps by `SELECT`ing `raw.market_prices` (a full or windowed scan), then starts following events from the current cursor. The event log is a *change notification* log, not the system of record. This keeps the ADR-0001 layering intact, makes the outbox table safely trimmable, and means an event-log outage degrades latency rather than correctness. Event sourcing would mean the event log *is* the truth — a much bigger commitment with no payoff here. |
| **Outbox retention** | Trim aggressively (e.g. delete events older than the slowest consumer's cursor, with a floor of N days). The table is append-only, so an unbounded outbox is a slow-motion disk leak — the same class of bug as an un-dropped replication slot, but easier to fix. |

---

## 8. Does going event-driven conflict with any golden rule?

**GR-1 — EL only.** Quoted: *"The Ingestor MAY coerce types, normalise timestamps to UTC, rename columns and drop rows where every value is NaN. It MUST NOT resample, aggregate, fill gaps, compute returns or indicators, or convert currencies."*

**No conflict, with one guardrail.** Emitting `{source, symbol, bar_interval, ts_min, ts_max, row_count}` is bookkeeping about *what was loaded*, in exactly the same category as `raw.ingestion_runs.rows_upserted`, which the repo already sanctions. `min(ts)`/`max(ts)`/`count(*)` over the rows *this statement wrote* is metadata about the write, not an aggregation of market data — no bar is resampled, combined or derived. The guardrail: **the event payload must never contain a derived market-data value.** The moment someone adds `pct_change` or `last_close` to the event, GR-1 is breached. A thin-event contract (§6.4) makes that breach structurally hard, because there is nowhere to put such a field. Worth an explicit line in the ADR and a test that asserts the event payload keys are a fixed allow-list.

**GR-3 — the daemon never dies because of a job.** Quoted: *"External calls (APIs, DB) MUST go through tenacity retries. A job failure MUST be logged, recorded in `raw.ingestion_runs`, and MUST NOT propagate to the scheduler. A failure for one ticker MUST NOT stop the others."*

**No conflict with the outbox; a real conflict with direct broker publishing.** The outbox introduces no new external call inside `jobs.ingest` — the event is a row in the transaction that was already happening, covered by the existing `db_retryer`. Publishing straight to NATS/Redis/Kafka from the job would add a second failure domain to the hot path and create the dual-write hole described in §4.1. **If stage 3 ever happens, the relay must run outside the scheduler's job boundary** (separate process, or an APScheduler job whose failure only delays delivery and is recorded), so that a broker outage degrades to "events are late" rather than "ingestion fails".

**Other rules that bind this work:**

| Rule | Obligation |
|---|---|
| **GR-2** | Events derive from `RETURNING`, so re-runs emit nothing. The outbox table is append-only with a generated id — the same argument `docs/data-model.md` §3 already makes for `ingestion_runs` applies verbatim, and should be reused rather than re-invented. |
| **GR-4** | Every new setting (`EVENTS_ENABLED`, `EVENTS_NOTIFY_CHANNEL`, `EVENTS_RETENTION_DAYS`, later `NATS_URL`) goes into `.env.example` **and** `docs/configuration.md` in the same change. |
| **GR-6** | `raw.ingestion_events` is an additive change: `CREATE TABLE IF NOT EXISTS`, idempotent, backward compatible, `docs/data-model.md` updated in the same change. No breaking change to `raw.market_prices` is needed. |
| **GR-7** | Upsert-derived event emission must be tested against a real TimescaleDB container (it is upsert + schema logic, explicitly named in GR-7). Needs: an idempotency test (second identical run emits **zero** events), a revision test (a changed `adj_close` emits exactly one), and a NOTIFY-delivery integration test. |
| **GR-8** | Stage 1 and 2 add **zero** dependencies. Stage 3 adds `nats-py` and needs a research note justifying it. |
| **GR-9** | This note is that verification. Nothing here is from memory; the PyPI versions were fetched programmatically. |
| **GR-10** | The outbox is the scope. Building the processing service, the alerting service, or the broker in the same change is scope creep — those are separate plans. |
| **GR-11** | `docs/architecture.md` §1 (the layer table and the mermaid flow), `docs/data-model.md`, `README.md` all change with the code. |
| **ADR-0005** | Unaffected at stage 1/2 — the dashboard keeps reading `raw` and merely *learns when to refresh*. That is a genuine, visible win from stage 1 with no ADR churn. |

**Required new ADR:** "Events are published through a transactional outbox in `raw`, and the database stays the source of truth." It should record the rejected alternatives (direct broker publish, DB triggers, Debezium/CDC, LISTEN-only) with the evidence above.

---

## 9. Proposed event catalogue

Naming: `finstream.<noun>.<past-tense-verb>.<version>`, reverse-DNS-ish, as CloudEvents recommends (`type` *"SHOULD be prefixed with a reverse-DNS name"*). `subject` carries the natural key so a broker can route/partition on it without parsing `data`.

| `type` | Producer | `subject` | `data` keys | Consumers | Emitted when |
|---|---|---|---|---|---|
| `finstream.bars.upserted.v1` | ingestor | `yahoo/SPY/1d` | `source`, `symbol`, `bar_interval`, `ts_min`, `ts_max`, `row_count`, `run_id`, `job` | processing service, dashboard (live refresh), alerting | The upsert's `RETURNING` yielded ≥ 1 row. **Never emitted for a no-op re-run.** One event per `(source, symbol, bar_interval)` per transaction, not per bar. |
| `finstream.ingestion_run.finished.v1` | ingestor | `yahoo/SPY/1d` | `run_id`, `job`, `source`, `symbol`, `bar_interval`, `status` (`success`/`empty`), `rows_received`, `rows_upserted`, `started_at`, `finished_at` | ops dashboard, health evaluator | `finish_run` with a non-failed status |
| `finstream.ingestion_run.failed.v1` | ingestor | `yahoo/SPY/1d` | as above plus `error` (already trimmed to `MAX_ERROR_LENGTH`, **must stay credential-free per GR-5**) | alerting, health evaluator | `finish_run(status="failed")` |
| `finstream.symbol.first_seen.v1` | ingestor | `yahoo/GC=F/1d` | `source`, `symbol`, `bar_interval`, `first_ts` | processing (backfill trigger), dashboard (instrument list) | First-ever insert for a natural-key prefix. Detection needs a `NOT EXISTS` check or an `instruments` registry; **the `xmax` insert/update trick is not a sound basis for this** (§4.3). Lowest-value event — defer to stage 2+. |
| `finstream.source.degraded.v1` / `.recovered.v1` | **a consumer, not the ingestor** | `yahoo` | `source`, `consecutive_failures`, `window`, `since` | alerting | N consecutive failed runs. This is a stateful judgement over the run-event stream; putting it in the ingestor would add policy to a daemon whose job is EL. Keeping it in a consumer is the first real demonstration that the event bus is worth having. |

Channel/subject layout that works unchanged from `pg_notify` to JetStream subjects to Kafka topics:

- Postgres channel: a single `finstream_events` channel carrying `{"type": …, "subject": …, "id": …}` (< 8000 bytes, trivially). One channel keeps listener management simple; consumers filter on `type`.
- JetStream subjects later: `finstream.bars.upserted.yahoo.SPY.1d` — subject-level filtering per consumer, no payload parsing.
- Kafka later: topic `finstream.bars.upserted`, key `yahoo|SPY|1d` → one partition per instrument-interval, giving the per-key ordering Kafka guarantees.

Proposed outbox table shape (CloudEvents-named, Debezium-outbox-compatible enough to migrate later — **design sketch, not approved**):

```
raw.ingestion_events(
  event_id     BIGSERIAL PRIMARY KEY,   -- cursor (see the commit-order caveat, §3.2)
  event_uuid   UUID NOT NULL,           -- CloudEvents `id`; UNIQUE with source
  type         TEXT NOT NULL,           -- CloudEvents `type`
  source       TEXT NOT NULL,           -- CloudEvents `source`
  subject      TEXT,                    -- CloudEvents `subject` = natural key
  occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),   -- CloudEvents `time`
  data         JSONB NOT NULL,
  UNIQUE (source, event_uuid)
)
```

---

## 10. The smallest change that delivers real value, and the upgrade path

### Stage 1 — outbox + doorbell, inside the database you already run

**Zero new containers, zero new dependencies, one new table, one new env var group.**

1. `raw.ingestion_events` (idempotent DDL, GR-6), documented in `docs/data-model.md`.
2. `upsert_bars` writes one event row per `(source, symbol, bar_interval)` **inside the same transaction** as the bars, built from the `RETURNING` rows. Nothing changed → nothing emitted.
3. `finish_run` writes `ingestion_run.finished` / `.failed` in its own transaction.
4. `pg_notify('finstream_events', <thin json>)` in the same transactions — delivered only on commit, by documented behaviour.
5. Tests (GR-7): idempotent re-run emits zero events (real TimescaleDB container); a revised `adj_close` emits exactly one; a listening session receives the notify; a rolled-back transaction emits nothing.

**Value delivered immediately, with no consumer written yet:** the outbox is a queryable, exact answer to "what actually changed and when", which today requires comparing `updated_at` across the whole table. That alone is worth the change.

**First consumer, ~30 lines:** the dashboard opens a second psycopg connection in autocommit, `LISTEN finstream_events`, and refreshes on notify instead of on a timer. Visible, demo-able, and it touches no ADR.

### Stage 2 — a real consumer, with durability

Add `finstream_events` consumption to the processing service when it is built (superseding ADR-0005 as that ADR already anticipates):

- `LISTEN` for latency **plus** a poll loop as the safety net (exactly PgQueuer's design), because NOTIFY is at-most-once.
- Either per-consumer cursor (fan-out + replay) or `FOR UPDATE SKIP LOCKED` claim (work queue) — chosen explicitly, with the `bigserial` commit-order caveat from §3.2 written into the plan.
- Consumers are idempotent by natural key, so the DB tolerates redelivery by construction.
- Add outbox retention.

**Exit criteria that would justify stage 3 — write these into the ADR so the decision is pre-made:**
1. A consumer that is not Python, or not in this repo.
2. More than ~3 independent consumers, where per-consumer cursor bookkeeping in SQL stops being pleasant.
3. Consumers that need backpressure or independent scaling.
4. Retention/replay requirements that conflict with keeping the outbox small.
5. Event rates where per-event DB round-trips are measurably a problem. *(At FinStream's current volume — a handful of symbols on an hourly/daily cadence — this is nowhere near.)*

### Stage 3 — NATS + JetStream, if and only if an exit criterion fires

- Keep the outbox. Add a **relay** — a separate process (not inside the scheduler's job boundary, per GR-3) that reads unpublished outbox rows and publishes to JetStream with `Nats-Msg-Id: <event_uuid>`, so JetStream's documented dedup (default 2m window) absorbs relay retries.
- Consumers move from SQL cursors to JetStream consumers; the event contract does not change, because it was CloudEvents-shaped from day one.
- Cost, measured: 6.9 MiB image, **3.77 MiB RAM idle** — one new pinned dep (`nats-py==2.16.0`, released 2026-09-16, asyncio-only), one research note (GR-8), one ADR.
- If Kafka-compatibility is ever a hard requirement instead, Redpanda is the JVM-free route and bundles a schema registry — but budget **~590 MiB idle** (measured, in its most deprivileged mode) and re-check whether a single laptop is still the target.

**What I would explicitly not do:** adopt Debezium/logical replication (§3.3), put the event contract in a DB trigger (§3.4), publish directly to a broker from `jobs.ingest` (§4.1, GR-3), or make the event log the source of truth (§7).

---

## 11. Open questions for the plan phase

1. **Transaction scope of `upsert_bars`.** Today each 1000-row batch is its own transaction. Does one event per batch, or one event per symbol-fetch (requiring the whole fetch in one transaction), better match consumer expectations? Lookbacks are small, so a single transaction per symbol is probably fine — but it changes the current batching contract and must be decided, not drifted into.
2. **Cursor vs claim.** Fan-out with per-consumer cursors, or SKIP-LOCKED work queue? This determines whether `bars.upserted` can have two independent consumers without a broker.
3. **`bigserial` commit-order skew** (§3.2) — pick a mitigation (watermark lag vs `pg_snapshot`-based) and test it.
4. **Retention policy** for `raw.ingestion_events`, and whether it should be a hypertable too.
5. **Does the dashboard get a second DB connection?** Streamlit's execution model plus a long-lived `LISTEN` connection needs a look — **UNVERIFIED**, not researched here.
6. **`symbol.first_seen` detection** without the `xmax` trick.

---

## 12. Source index

Every URL below was fetched and read on **2026-09-18**.

| Claim area | Source | Version / date shown |
|---|---|---|
| NOTIFY payload limit, transactional delivery, folding, 8 GB queue, `pg_notify`, claim-check advice | https://www.postgresql.org/docs/16/sql-notify.html | PostgreSQL 16 |
| LISTEN scope, per-session registration, cleared at session end | https://www.postgresql.org/docs/16/sql-listen.html | PostgreSQL 16 |
| `ON CONFLICT DO UPDATE … WHERE` + `RETURNING` semantics, atomicity | https://www.postgresql.org/docs/16/sql-insert.html | PostgreSQL 16 |
| `FOR UPDATE … SKIP LOCKED` semantics and queue caveat | https://www.postgresql.org/docs/16/sql-select.html | PostgreSQL 16 |
| `xmax` definition (and why the insert/update trick is folklore) | https://www.postgresql.org/docs/16/ddl-system-columns.html | PostgreSQL 16 |
| `now()` = transaction start, stable within a transaction | https://www.postgresql.org/docs/16/functions-datetime.html | PostgreSQL 16 |
| `pg_current_snapshot()`, `pg_snapshot_xmin()`, `pg_visible_in_snapshot()` | https://www.postgresql.org/docs/16/functions-info.html | PostgreSQL 16 |
| `MERGE … RETURNING merge_action()` | https://www.postgresql.org/docs/17/sql-merge.html | PostgreSQL 17 |
| `wal_level = logical`, restart required, WAL volume | https://www.postgresql.org/docs/16/runtime-config-wal.html | PostgreSQL 16 |
| Replication-slot bloat caution, once-in-normal-operation, crash-safety | https://www.postgresql.org/docs/16/logicaldecoding-explanation.html | PostgreSQL 16 |
| psycopg 3 notifications API, autocommit, 3.2.4 fix | https://www.psycopg.org/psycopg3/docs/advanced/async.html | psycopg 3 (installed 3.3.5) |
| psycopg version/date | https://pypi.org/pypi/psycopg/json | 3.3.5, 2026-08-31 |
| Debezium Postgres connector: wal_level, pgoutput, slots, JVM | https://debezium.io/documentation/reference/stable/connectors/postgresql.html | Debezium 3.6 |
| Debezium TimescaleDB SMT: per-chunk topics, compression limitation | https://debezium.io/documentation/reference/stable/transformations/timescaledb.html | Debezium 3.6 |
| Debezium Outbox Event Router columns and rationale | https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html | Debezium 3.6 |
| TimescaleDB triggers: propagation, "not supported on continuous aggregates" | https://www.tigerdata.com/docs/use-timescale/latest/schema-management/triggers | TigerData docs, latest |
| `refresh_continuous_aggregate()`: no notification documented | https://www.tigerdata.com/docs/api/latest/continuous-aggregates/refresh_continuous_aggregate | since 1.3.0; batching since 2.28.0 |
| `add_job()` user-defined actions (**partially UNVERIFIED** — via search index) | https://docs.tigerdata.com/api/latest/jobs-automation/add_job/ | TigerData docs, latest |
| Transactional outbox pattern | https://microservices.io/patterns/data/transactional-outbox.html | — |
| Claim-check pattern | https://learn.microsoft.com/en-us/azure/architecture/patterns/claim-check | ms.date 2024-05-01 |
| CloudEvents spec + attributes | https://github.com/cloudevents/spec , https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md | Latest release v1.0.2; main = 1.0.3-wip |
| NATS JetStream concepts, at-least-once | https://docs.nats.io/nats-concepts/jetstream | NATS docs 2.15 |
| JetStream streams: retention, storage, `Nats-Msg-Id`, 2m0s duplicate window | https://docs.nats.io/nats-concepts/jetstream/streams | NATS docs 2.15 |
| nats-py: asyncio-only, JetStream since 2.0.0 | https://github.com/nats-io/nats.py | — |
| Redis Streams: consumer groups, PEL, XACK, XAUTOCLAIM, MAXLEN | https://redis.io/docs/latest/develop/data-types/streams/ | Redis docs (5.0+; 8.2 notes) |
| RabbitMQ streams: append-only log, offsets, retention, AMQP 0-9-1 caveat | https://www.rabbitmq.com/docs/streams | RabbitMQ docs 4.3 |
| RabbitMQ quorum queues: Raft, disk-always, odd group size, single node = 0 tolerance | https://www.rabbitmq.com/docs/quorum-queues | RabbitMQ docs 4.3 |
| Kafka delivery semantics, idempotent producer, "read the fine print" | https://raw.githubusercontent.com/apache/kafka/4.3/docs/design/design.md | Kafka 4.3 |
| Kafka per-partition ordering | https://raw.githubusercontent.com/apache/kafka/4.3/docs/getting-started/introduction.md | Kafka 4.3 |
| Kafka default broker heap `-Xmx1G -Xms1G` | https://raw.githubusercontent.com/apache/kafka/trunk/bin/kafka-server-start.sh | trunk |
| Kafka 4.0 removes ZooKeeper; Java 17 for brokers | https://kafka.apache.org/blog/2025/03/18/apache-kafka-4.0.0-release-announcement/ | 4.0.0, 2025-03-18 |
| Latest Kafka release 4.3.1 | https://kafka.apache.org/community/downloads/ | 4.3.1, 2026-06-25 |
| Redpanda built-in schema registry (Avro/Protobuf/JSON) | https://docs.redpanda.com/current/manage/schema-reg/schema-reg-overview/ | Redpanda docs 26.2 |
| Redpanda 2 GB/core minimum (**UNVERIFIED** — indexed summary, direct fetch 404'd) | https://docs.redpanda.com/current/deploy/deployment-option/self-hosted/manual/production/requirements/ | current |
| Confluent compatibility types, default BACKWARD | https://docs.confluent.io/platform/current/schema-registry/fundamentals/schema-evolution.html | Confluent Platform 8.3 |
| PgQueuer: LISTEN/NOTIFY + SKIP LOCKED | https://github.com/janbjorge/pgqueuer | 1.4.0, 2026-09-14 |
| Docker image compressed sizes (amd64 `full_size`) | https://hub.docker.com/v2/repositories/{library/nats,library/redis,library/rabbitmq,apache/kafka,redpandadata/redpanda,timescale/timescaledb}/tags/{tag} | queried 2026-09-18 |
| PyPI versions/dates for nats-py, redis, pika, aio-pika, aiokafka, confluent-kafka, kafka-python, quixstreams, cloudevents, fastavro, jsonschema, protobuf, faststream, pgqueuer, procrastinate | https://pypi.org/pypi/&lt;pkg&gt;/json | fetched 2026-09-18 |
| **Idle RAM / image sizes** | own measurement, `broker-measurements.txt` (beside this note) | 2026-09-18, Docker 29.4.0, linux/arm64 |
