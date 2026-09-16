# CLAUDE.md

@AGENTS.md

## Claude Code specifics

- **AGENTS.md is the rulebook.** This file only adds Claude Code details. Never add rules here.
- **Skills:** `.claude/skills` is a symlink to `.agents/skills` (`research`, `development`, `test`). Use the skill that matches the current workflow phase.
- **Hooks** (`.claude/settings.json`, scripts in `.claude/hooks/`) enforce part of the rulebook mechanically:
  - `SessionStart`: prints a rules reminder and lists active plans.
  - `PreToolUse` file guard: blocks access to `.env*` (except `.env.example`) and keys/certificates (GR-5). It also blocks edits to Accepted ADRs, except a one-line status change to superseded/deprecated.
  - `PreToolUse` Bash guard: blocks `--no-verify`/`SKIP=`, force-push, `git reset --hard`, `git clean -f`, `docker compose down -v`, `docker compose config`, touching `.env` from the shell, and `rm -rf` outside build caches/temp dirs (GR-5, GR-12).
  - `PostToolUse`: runs `ruff format` + `ruff check --fix` on edited `.py` files and reports remaining errors.
- **If a hook blocks you, don't work around it** with an alternative command that has the same effect. Explain the block to the user and ask (AGENTS.md §7).
- Use the context7 MCP server for library documentation (GR-9).
