# Plan NNNN: <title>

- **Status:** Draft <!-- Draft | Approved | In progress | Done | Abandoned. Only the user sets Approved. -->
- **Created:** YYYY-MM-DD
- **Branch:** feat/NNNN-<slug>
- **Related:** <ADRs, research notes, docs>

> Copy to `docs/plans/NNNN-<slug>.md`. No implementation starts before the user sets `Status: Approved` (AGENTS.md §4).

## Context

<The problem, why now, and the intended outcome.>

## Goals

- ...

## Non-goals

- ...

## Design

<Approach, interfaces, data model changes (→ docs/data-model.md), configuration changes (→ docs/configuration.md), decisions (→ ADRs).>

## Research items

- [ ] R1: <question> → <link to note once done>

## Tasks

Each milestone is one coherent, testable slice, committed as a single Conventional Commit together with its tests and doc updates.

- [ ] **M1: <milestone>**
  - [ ] <task>

## Test plan

<Which mandatory test types from the test skill apply, where they live, and whether a smoke test is needed.>

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| | | | |

## Exceptions

<Golden-rule exceptions explicitly granted by the user: rule, reason, date. "None" if none.>

None.

## Follow-ups

<Out-of-scope findings discovered during the work (GR-10).>

## Definition of Done

- [ ] `ruff check`, `ruff format --check`, `mypy src` clean
- [ ] `pytest` green, coverage ≥ 85% on `src/`
- [ ] Idempotency and resilience tests exist for new or changed writers/jobs
- [ ] `docker compose up` smoke test passed (if runtime behaviour, Docker or Compose changed)
- [ ] `.env.example`, `docs/`, ADRs and README updated
- [ ] All tasks ticked, Verification log filled in, Status `Done`

## Verification log

<Command → trimmed real output → PASS / FAIL / SKIPPED (reason). Newest entries last.>

## Change log

- YYYY-MM-DD: created
