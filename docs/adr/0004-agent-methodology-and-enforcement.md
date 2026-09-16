# ADR-0004: Agent methodology with a single rulebook and layered enforcement

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** FinStream owner
- **Related:** [AGENTS.md](../../AGENTS.md), [methodology](../methodology.md)

## Context

FinStream is developed largely with AI coding agents, and more than one tool may be used (Claude Code, Codex, Cursor, …). Without explicit structure, agents tend to:
- drift from conventions between sessions
- rely on stale memory of library APIs
- expand scope, or claim success without evidence
- occasionally take destructive or secret-exposing actions

Rules that exist only as prose are forgotten or rationalised away. Rules duplicated per tool drift apart.

## Decision

1. **One canonical rulebook: `AGENTS.md`.** It holds twelve numbered golden rules (GR-1 … GR-12), the workflow, the Definition of Done, precedence and conventions. `CLAUDE.md` only imports it (`@AGENTS.md`) and describes Claude-specific mechanics.
2. **Phase skills in a tool-neutral location:** `.agents/skills/{research,development,test}/SKILL.md`. `.claude/skills` is a symlink to it.
3. **A gated workflow:** Research → Plan (**user approval gate**) → Implement → Test → Review & Document. Artefacts are plans (`docs/plans`), ADRs (`docs/adr`, immutable once Accepted) and research notes (`docs/research`).
4. **Commit policy:** every commit is a Conventional Commit, and each verified slice is committed right away.
5. **Layered enforcement:**
   - **Claude Code hooks:** a SessionStart reminder; guards for `.env`/keys, Accepted ADRs, hook bypass, force-push and destructive git/docker/rm commands; ruff after every Python edit.
   - **pre-commit for every tool and human:** ruff, mypy, gitleaks, private-key and `.env` detection, symlink checks, Conventional Commit message validation.
   - **CI** is deferred until the first service has code.
6. **Precedence:** user instruction > golden rules > skills > plan. Conflicts with a golden rule require explicit confirmation and are recorded as exceptions.

## Alternatives considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Documentation only | Zero tooling | Nothing is checked; rules erode | Rejected |
| `CLAUDE.md` as canonical rulebook | Native to Claude Code | Locks rules to one tool | Rejected |
| Separate rule files per tool | Tailored per tool | Guaranteed drift | Rejected |
| Hooks + pre-commit + CI now | Strongest enforcement | CI has nothing to run until code exists | CI deferred (follow-up) |

## Consequences

### Positive
- Every agent and human follows the same rules. Plans, research notes, the verification log and Conventional Commits leave an audit trail.
- The most damaging mistakes (secret exposure, bypassed hooks, destroyed data) are blocked mechanically.

### Negative / risks
- The process adds overhead to small changes. This is mitigated by exempting doc/typo fixes from the plan requirement.
- Hooks inspect commands heuristically. They're defence in depth, not a sandbox, and a determined workaround is possible (GR-12 forbids it).
- pre-commit must be installed locally to be effective, until CI exists.
- Symlinked skills need `core.symlinks` support on Windows.

## Compliance

- The hooks (`.claude/hooks/*`) and `.pre-commit-config.yaml` are version-controlled and reviewed like code.
- Each plan's Definition of Done and the golden-rules review in the development skill.
- Changing the methodology requires a new ADR that supersedes this one.
