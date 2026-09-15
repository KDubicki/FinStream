#!/usr/bin/env python3
"""PreToolUse guard for Claude Code file tools (Read, Edit, Write, MultiEdit, NotebookEdit).

Enforces (see AGENTS.md):
  * GR-5: no access to .env files (except .env.example) or private keys/certificates.
  * ADR immutability: an Accepted ADR may only receive a one-line status change to
    "Superseded by ADR-NNNN" or "Deprecated".

Protocol: the hook payload (JSON) arrives on stdin. Exit 0 allows the call; exit 2 blocks it
and shows stderr to the agent.
Must stay compatible with the macOS system python3 (3.9): stdlib only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks")
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
ADR_ACCEPTED = re.compile(r"^\W*status\W*accepted\b", re.IGNORECASE | re.MULTILINE)
STATUS_LINE = re.compile(r"^\W*status\W", re.IGNORECASE)
RETIRED_STATUS_LINE = re.compile(
    r"^\W*status\W*(superseded|deprecated)\b", re.IGNORECASE
)


def block(reason: str) -> NoReturn:
    print(f"Blocked by .claude/hooks/guard_files.py: {reason}", file=sys.stderr)
    sys.exit(2)


def is_env_file(path: Path) -> bool:
    name = path.name
    return name != ".env.example" and (name == ".env" or name.startswith(".env."))


def is_retiring_status_edit(tool_input: dict[str, Any]) -> bool:
    old_lines = str(tool_input.get("old_string", "")).strip().splitlines()
    new_lines = str(tool_input.get("new_string", "")).strip().splitlines()
    return (
        len(old_lines) == 1
        and len(new_lines) == 1
        and STATUS_LINE.match(old_lines[0]) is not None
        and RETIRED_STATUS_LINE.match(new_lines[0]) is not None
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)  # never break the session on unexpected input

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw_path:
        sys.exit(0)

    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(payload.get("cwd") or ".") / path

    if is_env_file(path):
        block(
            f"'{path.name}' may contain secrets (AGENTS.md GR-5). Document variables in "
            ".env.example and ask the user for any value you need."
        )
    if path.suffix.lower() in SECRET_SUFFIXES:
        block(
            f"'{path.name}' looks like a private key or certificate (AGENTS.md GR-5)."
        )

    if (
        tool in WRITE_TOOLS
        and path.parent.name == "adr"
        and path.suffix == ".md"
        and path.is_file()
    ):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            sys.exit(0)
        if ADR_ACCEPTED.search(content) and not (
            tool == "Edit" and is_retiring_status_edit(tool_input)
        ):
            block(
                f"'{path.name}' is an Accepted ADR and is immutable (AGENTS.md section 8). "
                "Write a new ADR that supersedes it. The only edit allowed here is a one-line "
                "status change to 'Superseded by ADR-NNNN' or 'Deprecated'."
            )
    sys.exit(0)


if __name__ == "__main__":
    main()
