#!/usr/bin/env python3
"""PreToolUse guard for the Claude Code Bash tool.

Enforces (see AGENTS.md):
  * GR-5:  no reading, writing, copying, deleting or staging .env files from the shell, and no
           `docker compose config` without --quiet (it prints interpolated secrets).
  * GR-12: no hook bypass (--no-verify, `git commit -n`, SKIP=, core.hooksPath), no force-push,
           `git reset --hard`, `git clean -f`, `docker compose down -v` or volume removal, and no
           `rm -rf` outside build caches and temp dirs.

This is defence in depth, not a sandbox: it inspects command text heuristically. Heredoc bodies
are ignored, so file contents written with `cat <<EOF` never trigger it.
Exit 0 allows the call; exit 2 blocks it and shows stderr to the agent.
Must stay compatible with the macOS system python3 (3.9): stdlib only.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from typing import NoReturn

HEREDOC_START = re.compile(r"(?<!<)<<-?(?!<)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHORT_FLAGS = re.compile(r"^-[A-Za-z]+$")
ENV_FILE = re.compile(r"(^|/)\.env(\.[A-Za-z0-9_.-]+)?$")
SEPARATOR_CHARS = set(";&|()")

WRAPPERS = {"sudo", "command", "time", "nohup", "exec", "env", "xargs"}
ENV_TOUCHING_PROGRAMS = {
    "cat",
    "less",
    "more",
    "head",
    "tail",
    "bat",
    "nl",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "awk",
    "sed",
    "cut",
    "sort",
    "uniq",
    "xxd",
    "od",
    "hexdump",
    "strings",
    "base64",
    "source",
    ".",
    "cp",
    "mv",
    "rm",
    "ln",
    "tee",
    "touch",
    "vi",
    "vim",
    "nano",
    "code",
    "open",
    "diff",
    "scp",
}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<"}
HOOK_BYPASS_ENV = {"SKIP", "PRE_COMMIT_ALLOW_NO_CONFIG"}
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
COMPOSE_OPTIONS_WITH_VALUE = {
    "-f",
    "--file",
    "-p",
    "--project-name",
    "--profile",
    "--env-file",
    "--project-directory",
    "--ansi",
    "--progress",
    "--parallel",
}
SAFE_RM_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
    ".coverage",
    "coverage.xml",
    "build",
    "dist",
    ".venv",
    "venv",
}
SAFE_RM_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "$TMPDIR/", "${TMPDIR}/")


def block(rule: str, reason: str) -> NoReturn:
    print(
        f"Blocked by .claude/hooks/guard_bash.py ({rule}): {reason}\n"
        "Do not look for a workaround. If this is genuinely needed, explain why to the user and "
        "ask them to run it themselves (they can type `! <command>`).",
        file=sys.stderr,
    )
    sys.exit(2)


def strip_heredocs(command: str) -> str:
    """Drop heredoc bodies so file contents are not parsed as commands."""
    kept: list[str] = []
    terminator: str | None = None
    for line in command.split("\n"):
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            continue
        kept.append(line)
        match = HEREDOC_START.search(line)
        if match:
            terminator = match.group(2)
    return "\n".join(kept)


def split_segments(command: str) -> list[list[str]]:
    """Tokenise a command line and split it into simple commands on ; & | ( ) and newlines."""
    text = strip_heredocs(command).replace("\\\n", " ").replace("\n", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a crude split
        tokens = text.split()
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and set(token) <= SEPARATOR_CHARS:
            segments.append([])
        elif token:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def strip_prefix(words: list[str]) -> tuple[set[str], list[str]]:
    """Remove leading VAR=value assignments and wrappers (sudo, env, ...)."""
    assigned: set[str] = set()
    index = 0
    while index < len(words):
        word = words[index]
        if ASSIGNMENT.match(word):
            assigned.add(word.split("=", 1)[0])
        elif word in WRAPPERS:
            while index + 1 < len(words) and words[index + 1].startswith("-"):
                index += 1
        else:
            break
        index += 1
    return assigned, words[index:]


def short_flags(args: list[str]) -> set[str]:
    return {char for arg in args if SHORT_FLAGS.match(arg) for char in arg[1:]}


def first_positional(
    args: list[str], options_with_value: set[str]
) -> tuple[str, list[str]]:
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in options_with_value:
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            return arg, args[index + 1 :]
    return "", []


def is_env_token(token: str) -> bool:
    return bool(ENV_FILE.search(token)) and not token.endswith(".env.example")


def check_env_access(program: str, words: list[str]) -> None:
    if not any(is_env_token(word) for word in words):
        return
    redirected = any(
        word in REDIRECTS and index + 1 < len(words) and is_env_token(words[index + 1])
        for index, word in enumerate(words)
    )
    if program in ENV_TOUCHING_PROGRAMS or redirected:
        block(
            "GR-5", "shell access to .env files is forbidden; they may contain secrets."
        )


def check_git(assigned: set[str], args: list[str]) -> None:
    if assigned & HOOK_BYPASS_ENV:
        block(
            "GR-12", "skipping pre-commit hooks via environment variables is forbidden."
        )
    for index, arg in enumerate(args):
        if (
            arg == "-c"
            and index + 1 < len(args)
            and args[index + 1].lower().startswith("core.hookspath")
        ):
            block("GR-12", "overriding core.hooksPath bypasses the repository hooks.")
    if "--no-verify" in args:
        block("GR-12", "--no-verify bypasses pre-commit and commit-msg hooks.")

    subcommand, rest = first_positional(args, GIT_OPTIONS_WITH_VALUE)
    flags = short_flags(rest)
    if subcommand == "commit" and "n" in flags:
        block("GR-12", "`git commit -n` bypasses hooks.")
    if subcommand == "add" and any(is_env_token(arg) for arg in rest):
        block("GR-5", ".env files must never be staged.")
    if subcommand == "push" and (
        "f" in flags
        or "--mirror" in rest
        or any(arg.startswith("--force") for arg in rest)
        or any(arg.startswith("+") for arg in rest)
    ):
        block("GR-12", "force-pushing rewrites shared history.")
    if subcommand == "reset" and "--hard" in rest:
        block("GR-12", "`git reset --hard` irreversibly discards work.")
    if subcommand == "clean" and ("f" in flags or "--force" in rest):
        block(
            "GR-12",
            "`git clean -f` irreversibly deletes untracked files (including local .env).",
        )


def check_docker(program: str, args: list[str]) -> None:
    compose_args: list[str] | None = None
    if program == "docker-compose":
        compose_args = args
    elif "compose" in args:
        compose_args = args[args.index("compose") + 1 :]

    if compose_args is not None:
        subcommand, rest = first_positional(compose_args, COMPOSE_OPTIONS_WITH_VALUE)
        if subcommand == "down" and ("--volumes" in rest or "v" in short_flags(rest)):
            block("GR-12", "`docker compose down -v` deletes the database volume.")
        if subcommand == "config" and not {"-q", "--quiet"} & set(rest):
            block(
                "GR-5",
                "`docker compose config` prints interpolated secrets; use --quiet to validate.",
            )
        return

    if program == "docker":
        subcommand, rest = first_positional(args, set())
        if subcommand == "volume" and rest[:1] and rest[0] in {"rm", "prune"}:
            block("GR-12", "removing Docker volumes deletes database data.")
        if subcommand == "system" and "prune" in rest and "--volumes" in rest:
            block("GR-12", "`docker system prune --volumes` deletes database data.")


def is_safe_rm_target(target: str) -> bool:
    if ".." in target.split("/"):
        return False
    if target.startswith(SAFE_RM_PREFIXES):
        return True
    name = target.rstrip("/").split("/")[-1]
    return name in SAFE_RM_NAMES or name.endswith(".egg-info")


def check_rm(args: list[str]) -> None:
    flags = short_flags(args)
    recursive = bool(flags & {"r", "R"}) or "--recursive" in args
    forced = "f" in flags or "--force" in args
    if not (recursive and forced):
        return
    for target in (arg for arg in args if not arg.startswith("-")):
        if not is_safe_rm_target(target):
            block(
                "GR-12",
                f"`rm -rf {target}` is only allowed for build caches/virtualenvs or temp dirs.",
            )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)  # never break the session on unexpected input

    command = (payload.get("tool_input") or {}).get("command") or ""
    for segment in split_segments(command):
        assigned, words = strip_prefix(segment)
        if not words:
            continue
        program = words[0].rsplit("/", 1)[-1]
        args = words[1:]
        check_env_access(program, words)
        if program == "git":
            check_git(assigned, args)
        elif program in {"docker", "docker-compose"}:
            check_docker(program, args)
        elif program == "rm":
            check_rm(args)
    sys.exit(0)


if __name__ == "__main__":
    main()
