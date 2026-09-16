# AGENTS.md: FinStream rulebook

This file is the **canonical source of rules** for every AI agent (Claude Code, Codex, Cursor, …) and every human working in this repository. Tool-specific files such as `CLAUDE.md` only import or point to it. If another file contradicts this one, this file wins (see [§7](#7-precedence-and-exceptions)).

## 1. Project snapshot

- **FinStream** is a microservice data platform for financial market data (gold, ETFs, stock indices).
- **FinStream Ingestor** (`services/ingestor`, the first service) is a long-running daemon. It **extracts** market data from Yahoo Finance (`yfinance`) and **loads** it, raw, into PostgreSQL + TimescaleDB. It does no transformation; downstream services own that.
- **Stack:** Python 3.12, APScheduler 3.x, tenacity, SQLAlchemy 2.0 Core, psycopg 3, pydantic-settings, PostgreSQL 16 + TimescaleDB, Docker Compose.
- **Status:** bootstrap. Docs and methodology are done. Code waits on [plan 0001](docs/plans/0001-ingestor-implementation.md).

## 2. Repo map

| Path | Contents |
|---|---|
| `AGENTS.md` | This rulebook |
| `CLAUDE.md` | Claude Code entry point (`@AGENTS.md` plus hook notes) |
| `.agents/skills/{research,development,test}/SKILL.md` | Phase skills (canonical location) |
| `.claude/skills` | Symlink → `../.agents/skills` |
| `.claude/settings.json`, `.claude/hooks/` | Claude Code rule enforcement |
| `.pre-commit-config.yaml` | Tool-agnostic enforcement for every agent and human |
| `docs/methodology.md` | Full workflow, gates, rationale, enforcement matrix |
| `docs/architecture.md`, `docs/data-model.md`, `docs/configuration.md` | System reference |
| `docs/adr/` | Architecture Decision Records |
| `docs/plans/` | Implementation plans, one per change |
| `docs/research/` | Research notes |
| `services/ingestor/` | Ingestor service (created by plan 0001) |

## 3. Golden rules

"MUST" / "MUST NOT" are absolute unless the user grants an exception ([§7](#7-precedence-and-exceptions)). Cite rules by ID, e.g. "GR-2". The rationale and enforcement for each rule are in [docs/methodology.md](docs/methodology.md#5-golden-rules-rationale-and-enforcement).

1. **GR-1 EL only.** The Ingestor MAY coerce types, normalise timestamps to UTC, rename columns and drop rows where every value is NaN. It MUST NOT resample, aggregate, fill gaps, compute returns or indicators, or convert currencies. See [ADR-0001](docs/adr/0001-ingestor-is-extract-load-only.md).
2. **GR-2 Idempotent writes.** Every write to a data table MUST be an upsert on the natural key. Running a job twice MUST NOT change the row count.
3. **GR-3 The daemon never dies because of a job.** External calls (APIs, DB) MUST go through tenacity retries. A job failure MUST be logged, recorded in `raw.ingestion_runs`, and MUST NOT propagate to the scheduler. A failure for one ticker MUST NOT stop the others.
4. **GR-4 Configuration comes from the environment only.** Tickers, URLs, schedules, intervals and secrets MUST NOT be hardcoded. Every new variable MUST be added to `.env.example` and [docs/configuration.md](docs/configuration.md) in the same change.
5. **GR-5 Secrets.** Agents MUST NOT read, create, edit or commit `.env` files, and MUST NOT log credentials or an unmasked `DATABASE_URL`.
6. **GR-6 Schema.** DDL MUST be idempotent (`IF NOT EXISTS`, `if_not_exists => TRUE`) and backward compatible. A breaking change needs an ADR and a plan. [docs/data-model.md](docs/data-model.md) MUST be updated in the same change.
7. **GR-7 Tests.** New behaviour MUST have tests. Automated tests MUST NOT reach the network, so Yahoo is always mocked. Upsert and schema logic MUST be tested against a real TimescaleDB container, never a mocked DB.
8. **GR-8 Dependencies.** Versions MUST be pinned exactly (`==`) in `requirements*.txt`. Every new dependency MUST be justified in a research note.
9. **GR-9 Verify, don't assume.** Library APIs and versions MUST be checked against current sources (context7, PyPI, official docs), not memory. Agents MUST NOT claim tests or lint pass without running them and showing the output.
10. **GR-10 Scope discipline.** Do only what the approved plan covers. Anything else you discover goes into the plan's "Follow-ups". Don't fix it silently.
11. **GR-11 Docs move with code.** README, `docs/`, ADRs and the plan MUST be updated in the same change as the code they describe.
12. **GR-12 Git hygiene.** Every commit MUST follow [Conventional Commits](https://www.conventionalcommits.org/) (see §8). Commit at meaningful units: one complete, verified slice, typically a milestone or a coherent change. Don't spend separate commits on trivial edits such as plan status bumps; fold them into the related commit. Use one branch per plan (e.g. `feat/0001-ingestor`), and do no feature work directly on `main`. Agents MUST NOT bypass hooks (`--no-verify`, `SKIP=`), force-push, `git reset --hard`, `git clean -f` or `docker compose down -v` without explicit user confirmation.

## 4. Workflow

Every change goes through five phases, and each phase has a skill with the detailed procedure. The full description is in [docs/methodology.md](docs/methodology.md).

| # | Phase | Skill | Output / gate |
|---|---|---|---|
| 1 | Research | [`research`](.agents/skills/research/SKILL.md) | `docs/research/YYYY-MM-DD-<topic>.md`; draft ADR if a decision was made |
| 2 | Plan | [plan template](docs/plans/TEMPLATE.md) | `docs/plans/NNNN-<slug>.md` in `Draft`. **Gate:** the user approves it → `Approved` |
| 3 | Implement | [`development`](.agents/skills/development/SKILL.md) | Small tested slices, with plan tasks ticked |
| 4 | Test | [`test`](.agents/skills/test/SKILL.md) | Real command output in the plan's Verification log |
| 5 | Review & document | [`development`](.agents/skills/development/SKILL.md#review--document-final-phase) | Definition of Done met; plan `Done` |

- **A plan is required** for any change that touches more than one source file, the DB schema, configuration, dependencies, Docker, or externally visible behaviour. Typo fixes and documentation-only fixes are exempt.
- **No code before an `Approved` plan.** If you're asked to build something that no approved plan covers, write or update the plan first and stop for approval.
- **Unsure which phase you're in?** If you'd be guessing about an API or version, you're in Research. If there's no approved plan, you're in Plan.

## 5. Commands

> These become available once plan 0001 is implemented. Run them from `services/ingestor/` inside its venv unless noted.

```bash
# quality
ruff check . && ruff format --check . && mypy src
# tests
pytest -m "not integration"                             # unit: fast, no Docker, no network
pytest -m integration                                   # integration: needs Docker (testcontainers)
pytest --cov=finstream_ingestor --cov-fail-under=85     # full suite + coverage gate
# stack (repo root; the user owns .env)
docker compose up -d --build && docker compose logs -f ingestor
# all hooks (repo root)
pre-commit run --all-files
```

## 6. Definition of Done

- [ ] `ruff check`, `ruff format --check` and `mypy src` are clean
- [ ] `pytest` is green, with coverage ≥ 85% on `src/`
- [ ] Idempotency and resilience tests exist for every new or changed writer/job
- [ ] `docker compose up` smoke test passed (whenever runtime behaviour, Docker or Compose changed)
- [ ] `.env.example`, `docs/`, ADRs and README reflect the change
- [ ] Plan tasks are ticked, the Verification log is filled in, and the status is `Done`

## 7. Precedence and exceptions

**Explicit user instruction > AGENTS.md golden rules > skill instructions > plan details.**

If a user instruction conflicts with a golden rule:
1. Name the rule ("this conflicts with GR-2") and explain the risk in one or two sentences.
2. Ask for explicit confirmation.
3. Once confirmed, proceed, and record the exception (rule, reason, date) in the active plan's "Exceptions" section. If there is no active plan, state it in your final summary.

If a hook blocks an action, don't work around it with an equivalent command. Explain the situation and let the user decide.

## 8. Conventions

- **Plans:** `docs/plans/NNNN-<slug>.md`, numbered sequentially with 4 digits, created from [TEMPLATE.md](docs/plans/TEMPLATE.md). Statuses: `Draft` → `Approved` → `In progress` → `Done` (or `Abandoned`). **Only the user approves a plan.**
- **ADRs:** `docs/adr/NNNN-<slug>.md` from [0000-template.md](docs/adr/0000-template.md). Statuses: `Proposed` → `Accepted` → `Superseded by ADR-NNNN` / `Deprecated`. **Accepted ADRs are immutable.** To change a decision, write a new ADR that supersedes the old one. The only edit allowed on an Accepted ADR is a one-line status change, and the hook enforces this.
- **Research notes:** `docs/research/YYYY-MM-DD-<topic>.md` from [TEMPLATE.md](docs/research/TEMPLATE.md).
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) are mandatory for every commit, and the `conventional-pre-commit` hook checks them.
  - **Format:** `<type>(<optional scope>): <subject>`.
  - **Types:** `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `style`, `build`, `ci`, `chore`, `revert`.
  - **Scopes:** e.g. `ingestor`, `db`, `skills`, `hooks`, `adr`, `plans`.
  - **Subject:** imperative, lower-case, no trailing period, ≤ 72 characters.
  - **Breaking changes:** `!` after the type/scope or a `BREAKING CHANGE:` footer.
  - **Contents:** one logical change per commit, including its code, tests and doc/plan updates. Stage only that change's files, and never commit red or unverified work.
  - **Example:** `feat(ingestor): add idempotent price upsert`.
- **Branches:** `feat/NNNN-<slug>`, `fix/NNNN-<slug>`, `docs/<slug>`.
- **Indexes:** keep [docs/README.md](docs/README.md) (ADR and plan tables) in sync (GR-11).
