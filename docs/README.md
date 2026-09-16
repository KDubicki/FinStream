# FinStream documentation

| Document | Purpose |
|---|---|
| [STATUS.md](STATUS.md) | Short summary of where the project stands right now |
| [methodology.md](methodology.md) | How work is done: phases, gates, golden-rule rationale, commit policy, enforcement layers |
| [architecture.md](architecture.md) | Platform and Ingestor architecture, EL boundary, scheduling, failure modes |
| [data-model.md](data-model.md) | `raw` schema DDL, keys, hypertable, upsert semantics |
| [configuration.md](configuration.md) | Environment variable reference (source of truth for `.env.example`) |
| [adr/](adr/) | Architecture Decision Records ([template](adr/0000-template.md)) |
| [plans/](plans/) | Implementation plans ([template](plans/TEMPLATE.md)) |
| [research/](research/) | Research notes ([template](research/TEMPLATE.md)) |

The rulebook for agents and humans is [AGENTS.md](../AGENTS.md).

## Architecture Decision Records

| ADR | Title | Status |
|---|---|---|
| [0001](adr/0001-ingestor-is-extract-load-only.md) | The FinStream Ingestor is Extract & Load only | Accepted |
| [0002](adr/0002-postgresql-timescaledb-raw-storage.md) | PostgreSQL + TimescaleDB for raw storage | Accepted |
| [0003](adr/0003-apscheduler-and-tenacity.md) | APScheduler 3.x for scheduling, tenacity for retries | Accepted |
| [0004](adr/0004-agent-methodology-and-enforcement.md) | Agent methodology with a single rulebook and layered enforcement | Accepted |
| [0005](adr/0005-serving-reads-raw-directly.md) | The dashboard reads `raw` directly, for now | Accepted |

## Plans

| Plan | Title | Status |
|---|---|---|
| [0001](plans/0001-ingestor-implementation.md) | FinStream Ingestor implementation | In progress |
| [0002](plans/0002-ci-pipeline.md) | CI pipeline | Done |
| [0003](plans/0003-dashboard-service.md) | Streamlit dashboard service | In progress |

## Research notes

| Note | Topic |
|---|---|
| [2026-09-16-yfinance-behaviour.md](research/2026-09-16-yfinance-behaviour.md) | R1: yfinance errors, config API, columns, timezones, history limits |
| [2026-09-16-apscheduler-tenacity.md](research/2026-09-16-apscheduler-tenacity.md) | R2: APScheduler 3.x cron/day-of-week, job defaults, tenacity retryers |
| [2026-09-16-timescaledb-testcontainers.md](research/2026-09-16-timescaledb-testcontainers.md) | R3: hypertable syntax, unique-constraint rule, integration-test container |
| [2026-09-16-dependency-pins.md](research/2026-09-16-dependency-pins.md) | R4: exact version pins, image tag, pre-commit revs |

Keep these tables in sync whenever an ADR, plan or research note is added or changes status (GR-11).
