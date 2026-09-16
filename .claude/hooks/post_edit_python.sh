#!/usr/bin/env bash
# PostToolUse hook: format and lint an edited Python file with ruff.
# Exit 2 feeds remaining lint errors back to the agent. No-op for non-.py files or when ruff
# is not available (neither on PATH nor in the service virtualenv).
set -euo pipefail

file_path="$(python3 -c '
import json, sys
try:
    payload = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
print((payload.get("tool_input") or {}).get("file_path") or "")
')"

[[ "$file_path" == *.py && -f "$file_path" ]] || exit 0

project_dir="${CLAUDE_PROJECT_DIR:-.}"
if command -v ruff >/dev/null 2>&1; then
  ruff_bin="ruff"
elif [[ -x "$project_dir/services/ingestor/.venv/bin/ruff" ]]; then
  ruff_bin="$project_dir/services/ingestor/.venv/bin/ruff"
else
  exit 0
fi

"$ruff_bin" format --quiet "$file_path" >/dev/null 2>&1 || true
if ! output="$("$ruff_bin" check --fix --quiet "$file_path" 2>&1)"; then
  {
    echo "ruff reported issues in $file_path that could not be fixed automatically. Fix them before continuing:"
    echo "$output"
  } >&2
  exit 2
fi
exit 0
