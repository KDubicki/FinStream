## What and why

<!-- One paragraph: what changes and which problem it solves. -->

**Plan:** <!-- docs/plans/NNNN-....md, or "n/a: typo/docs-only fix" -->

## Definition of Done

<!-- AGENTS.md §6. Tick what applies; mark anything else "n/a" with a reason. -->

- [ ] `ruff check`, `ruff format --check` and `mypy src` are clean
- [ ] `pytest` is green, coverage ≥ 85% on `src/`
- [ ] Idempotency and resilience tests exist for new or changed writers/jobs
- [ ] `docker compose up` smoke test passed (runtime behaviour, Docker or Compose changed)
- [ ] `.env.example`, `docs/`, ADRs and README updated in this change
- [ ] Plan tasks ticked and the Verification log filled in with real output

## Golden rules

- [ ] No transformation logic added to the Ingestor (GR-1)
- [ ] Writes stay idempotent, jobs still cannot crash the daemon (GR-2, GR-3)
- [ ] New configuration is env-driven and documented (GR-4)
- [ ] No secrets, no `.env` file in the diff (GR-5)
- [ ] Dependency changes are pinned exactly and justified in a research note (GR-8)

## Exceptions

<!-- Golden-rule exceptions the maintainer approved: rule, reason, date. "None" if none. -->

None.
