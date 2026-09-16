# Plan 0002: CI pipeline

- **Status:** In progress <!-- Draft | Approved | In progress | Done | Abandoned. Only the user sets Approved. -->
- **Created:** 2026-09-17
- **Branch:** `main` (see Exceptions)
- **Related:** [plan 0001](0001-ingestor-implementation.md) follow-ups, [methodology §8](../methodology.md), AGENTS.md GR-7, GR-8, GR-12

## Context

Every rule in this repository is currently checked only on the developer's own machine: Claude Code hooks and pre-commit. Both can be absent (a fresh clone without `pre-commit install`) or skipped. Plan 0001 listed CI as a follow-up, and the user asked for it directly. CI closes the gap: the same gates run on a clean machine for every push, and their result is visible in the repository.

## Goals

- Every push and pull request runs the full gate on a clean machine: pre-commit (ruff, mypy, gitleaks, hygiene), unit tests, and the TimescaleDB integration tests.
- Commit messages in a pull request are verified against Conventional Commits (GR-12).
- Supply-chain hygiene: actions pinned by commit SHA, least-privilege tokens, pinned runner images, and automated dependency updates.
- Fast feedback: dependency caching, superseded runs cancelled, per-job timeouts.
- The container image is built as soon as a Dockerfile exists (plan 0001 M5), without breaking CI before then.

## Non-goals

- Deployment, releases, publishing images to a registry.
- External coverage services, self-hosted runners, SBOM and artifact signing (Follow-ups).
- Replacing local pre-commit. CI is the second line of defence, not the first.

## Design

### `.github/workflows/ci.yml` — on push to `main` and on pull requests

| Job | Does | Notes |
|---|---|---|
| `quality` | `pre-commit run --all-files` | One source of truth for lint, format, types and secret scanning: the same config developers run |
| `commit-messages` | Validates every commit subject in the PR range against Conventional Commits | Pull requests only; local commit-msg hooks can be missing on a fresh clone |
| `test` | `pytest -m "not integration"` on Python 3.11 and 3.12 | Matrix proves the 3.11 compatibility the development skill claims |
| `integration` | `pytest -m integration` plus the coverage gate on Python 3.12 | GitHub's Ubuntu runners provide a Docker daemon, which testcontainers needs (GR-7) |
| `docker-build` | Builds the ingestor image | Guarded by `hashFiles(...)`, so it is skipped until plan 0001 M5 adds the Dockerfile |

### `.github/workflows/security.yml` — weekly plus manual

- `pip-audit` against the pinned requirements.
- `gitleaks` over the full history, not just the diff.
- Scheduled rather than blocking: a new advisory is news, not a reason to block an unrelated pull request (GR-8).

### Hardening conventions (apply to every workflow)

- `permissions: contents: read` at workflow level, raised per job only where needed.
- Third-party actions pinned to a **commit SHA** with the version in a trailing comment.
- Pinned runner image (`ubuntu-24.04`), never `ubuntu-latest`.
- `concurrency` per ref with `cancel-in-progress: true`.
- `timeout-minutes` on every job.
- `persist-credentials: false` on checkout.
- Caching keyed on `requirements*.txt` and `.pre-commit-config.yaml`.

### `.github/dependabot.yml`

Weekly updates for GitHub Actions, pip (`services/ingestor`) and Docker, grouped, with Conventional Commit prefixes (`ci`, `build`). Dependabot proposes; a human still applies GR-8 (pin exactly, justify in a research note).

### `.github/pull_request_template.md`

The Definition of Done as a checklist, plus a link to the plan and a place to record rule exceptions.

## Tasks

- [x] **M1: Workflows and repository files** — `ci.yml`, `security.yml`, `dependabot.yml`, `pull_request_template.md`
- [x] **M2: Documentation** — methodology enforcement table gains the CI row; docs index and plan 0001 follow-up updated; README notes the CI gate
- [ ] **M3: Verification** — YAML parses ✅; the CI commands reproduce locally ✅; the first real run on GitHub is green ⬜ (pending the push)

## Test plan

CI configuration cannot be unit-tested, so verification is:
1. Every workflow file parses as YAML and its structure is asserted (jobs, `permissions`, `timeout-minutes`, SHA-pinned actions).
2. Each command a job runs is executed locally first and must pass.
3. The first real run on GitHub is inspected. **Only a green run counts as done** (GR-9).

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Integration tests behave differently on the runner than locally | Medium | Medium | Same pinned TimescaleDB image; testcontainers waits for readiness itself |
| A moving action tag introduces unreviewed code | Low | High | SHA pinning plus Dependabot updates |
| Docker build job fails before a Dockerfile exists | Medium | Low | `hashFiles()` guard skips the job |
| Advisory in `pip-audit` blocks unrelated work | Medium | Low | Scheduled workflow, not a PR gate |
| CI duplicates local pre-commit and slows feedback | Low | Low | Caching plus cancel-in-progress |

## Exceptions

- **Plan written and approved in one step.** AGENTS.md §4 requires user approval before implementation. The user asked for the CI directly on 2026-09-17, which is that approval; this plan records the design rather than gating it.
- **Work happens on `main`.** GR-12 asks for one branch per plan. The user asked to continue on `main` for this single-maintainer repository, so GR-12 is updated accordingly in M2 rather than being silently broken.

## Follow-ups

- Branch protection on `main`: require the CI checks, once the first run is green (a repository setting, not a file).
- SBOM and image signing when images start being published.
- A coverage report published as a job summary or to an external service.

## Definition of Done

- [ ] Workflows parse and are structurally asserted
- [ ] Every CI command passes locally first
- [ ] The first run on GitHub is green
- [ ] Docs updated: methodology, docs index, plan 0001 follow-up, README
- [ ] All tasks ticked, Verification log filled in, Status `Done`

## Verification log

### 2026-09-17 · M1 + M2

- **Workflow structure** (`uv run --with pyyaml python check_workflows.py`) → PASS: both files parse; workflow-level `permissions: contents: read`; every job pins `ubuntu-24.04` and sets `timeout-minutes`; every action is SHA-pinned with its version in a comment; every checkout sets `persist-credentials: false`.
- **Action pins resolved from upstream tags** (`git ls-remote --tags`): checkout v7.0.1 `3d3c42e5…`, setup-python v7.0.0 `5fda3b95…`, cache v6.1.0 `55cc8345…`, upload-artifact v7.0.1 `043fb46d…`.
- **`ghcr.io/gitleaks/gitleaks:v8.30.1` exists** → GHCR manifest request returned `HTTP 200` (OCI image index); available tags include `v8.30.0`, `v8.30.1`.
- **`quality` job reproduced locally** (`pre-commit run --all-files`) → PASS.
- **`test` job reproduced locally** (`pytest -m "not integration" -q`) → PASS: `33 passed`.
- **`commit-messages` job logic simulated** over the last 6 commits → PASS: 6/6 subjects match the Conventional Commits pattern.
- **`docker-build` guard** → the probe reports `exists=false`, so the build step is skipped until plan 0001 M5 adds the Dockerfile, exactly as designed.
- **Not yet verified:** the first real run on GitHub, and the `integration` job (no Docker daemon on this machine, so it has never run locally either).

## Change log

- 2026-09-17: created and approved (user request).
