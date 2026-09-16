---
name: research
description: Research procedure for the FinStream repo. Use BEFORE choosing, adding or upgrading a library, API, Docker image or pre-commit hook. Also use when the behaviour of yfinance/Yahoo Finance, TimescaleDB/PostgreSQL, APScheduler, tenacity, SQLAlchemy, psycopg or testcontainers is unknown or version-dependent; when pinning versions; when evaluating other free market-data APIs; or when a plan has an open research item (R1, R2, …). Produces a cited, versioned note in docs/research/ and, if a decision was made, a draft ADR. Writes no production code.
---

# Research skill

Research turns an open question into a **cited, versioned, reviewable note**, so that plans and code rest on verified facts rather than memory (AGENTS.md GR-9).

## When to use

- A plan lists a research item (e.g. plan 0001, M0: R1–R4).
- You're about to add or upgrade a dependency. GR-8 requires a research note.
- You're unsure how an external API or library behaves: errors, rate limits, timezones, return shapes, deprecations, version differences.
- A technical decision is needed that will become an ADR.

Don't use it when the repo's docs already answer the question. Check that first (step 2).

## Procedure

1. **Frame the question.** Write one or two precise sentences, plus the decision the answer unblocks.
   *Example:* "How does `yfinance` signal failure in the current stable release (exception or empty DataFrame), and which exception indicates rate limiting? Unblocks: error classification and retry policy in plan 0001 M3."
2. **Check what we already know.** Search `docs/research/`, `docs/adr/`, `docs/plans/` and `docs/*.md`. If an existing note answers the question and its versions still match, link that note and stop.
3. **Consult primary sources, in this order:**
   1. **context7** (`resolve-library-id` → `query-docs`) for library documentation.
   2. **PyPI JSON** (`https://pypi.org/pypi/<package>/json`) for the latest stable version, release date and `requires_python`. Don't choose pre-releases unless a plan explicitly says so.
   3. **Official docs, changelogs, release notes, GitHub source/issues** for behaviour the docs don't cover.
   4. **Docker Hub tags** for images (e.g. `timescale/timescaledb`).

   Blog posts and Q&A sites are secondary. Use them to find primary evidence, but never cite them as the source of truth.
4. **Spike if needed (optional).** Throwaway experiments go **outside the repo** (the session scratch directory or `/tmp`), never into `services/`. Spikes MAY use the network, unlike automated tests. Record the exact command, the versions used and a trimmed output.
5. **Write the note.** Copy [docs/research/TEMPLATE.md](../../../docs/research/TEMPLATE.md) to `docs/research/YYYY-MM-DD-<topic>.md` and fill in every section.
   - Every finding needs a source link, the version it applies to and the date you checked it.
   - Mark anything not confirmed by a primary source as **UNVERIFIED**.
6. **Close the loop.**
   - If a decision was made, draft an ADR from [docs/adr/0000-template.md](../../../docs/adr/0000-template.md) with `Status: Proposed` and link the note. To replace an Accepted ADR, write a new one that supersedes it.
   - Update the originating plan: tick the research item, link the note, and adjust the design if the findings require it. A material design change sends the plan back to the user for approval.
   - Move open questions into the plan's "Risks" or "Follow-ups".
   - Report to the user: the question, the answer in one paragraph, the recommendation, and links.

## Rules

- MUST NOT write or modify production code, tests, Docker or configuration files during research.
- MUST cite a source (URL plus version or date) for every factual claim. Memory is not a source.
- MUST record versions and the date checked, because library behaviour drifts (yfinance especially).
- MUST NOT put secrets, API keys or `.env` contents into notes (GR-5).
- SHOULD keep notes short: findings as bullets, evidence as links plus minimal excerpts.

## Output checklist

- [ ] `docs/research/YYYY-MM-DD-<topic>.md` exists, with every template section filled in
- [ ] Every finding has a source, a version and a date checked
- [ ] The recommendation is explicit, or explicitly "no decision yet" with what's missing
- [ ] An ADR is drafted if a decision was made
- [ ] The originating plan is updated and links to the note
