# FinStream documentation

| Document | Purpose |
|---|---|
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
| [0003](adr/0003-apscheduler-and-tenacity.md) | APScheduler 3.x for scheduling, tenacity for retries | Proposed (pending plan 0001 R2) |
| [0004](adr/0004-agent-methodology-and-enforcement.md) | Agent methodology with a single rulebook and layered enforcement | Accepted |

## Plans

| Plan | Title | Status |
|---|---|---|
| [0001](plans/0001-ingestor-implementation.md) | FinStream Ingestor implementation | In progress |

## Research notes

*None yet. Plan 0001 M0 produces the first ones.*

Keep these tables in sync whenever an ADR, plan or research note is added or changes status (GR-11).
