#!/usr/bin/env python3
"""SessionStart hook: inject a short rules reminder and the active plans into the agent's context.

Claude Code adds the stdout of a SessionStart hook to the session context. The hook runs on
startup, resume, /clear and compaction, so the rules survive context resets.
Must stay compatible with the macOS system python3 (3.9): stdlib only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

STATUS = re.compile(r"^\W*status\W*([A-Za-z][A-Za-z ]*)", re.IGNORECASE | re.MULTILINE)
PLAN_FILE = re.compile(r"^\d{4}-.+\.md$")
ACTIVE_STATUSES = {"approved", "in progress"}


def read_plans(root: Path) -> list[tuple[str, str]]:
    plans_dir = root / "docs" / "plans"
    if not plans_dir.is_dir():
        return []
    plans: list[tuple[str, str]] = []
    for path in sorted(plans_dir.iterdir()):
        if not PLAN_FILE.match(path.name):
            continue
        try:
            match = STATUS.search(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        plans.append((path.name, match.group(1).strip() if match else "unknown"))
    return plans


def main() -> None:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    plans = read_plans(root)
    active = [
        f"{name} ({status})"
        for name, status in plans
        if status.lower() in ACTIVE_STATUSES
    ]
    drafts = [name for name, status in plans if status.lower() == "draft"]

    lines = [
        "FinStream rules reminder (.claude/hooks/session_start.py):",
        "- AGENTS.md is the rulebook. Golden rules GR-1..GR-12 are mandatory; cite them by ID.",
        (
            "- Workflow: Research -> Plan -> Implement -> Test -> Review & Document"
            " (skills: research, development, test)."
        ),
        (
            "- No code without an Approved plan in docs/plans/. Never read or edit .env files."
            " Never bypass or work around hooks."
        ),
        "- Commit every verified slice right away, always as a Conventional Commit (GR-12).",
        "- Never claim tests or lint pass without running them and showing real output (GR-9).",
        "- Active plans: " + (", ".join(active) if active else "none"),
    ]
    if drafts:
        lines.append("- Draft plans awaiting user approval: " + ", ".join(drafts))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
