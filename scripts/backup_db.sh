#!/usr/bin/env bash
#
# Back up the FinStream database to a custom-format dump.
#
# The pgdata volume holds the only copy of every bar ever collected: everything else in this
# repository is reproducible from git, that history is not.
#
# Usage:  scripts/backup_db.sh [output-directory]
# Default output directory: <repo>/backups (git-ignored).
#
# The database password is expanded *inside* the container from its own environment, so it never
# reaches this script, a host process's argument list, or shell history (AGENTS.md GR-5). This
# script reads no .env file.
#
# Restoring a dump is documented in docs/runbooks/restore.md, and the mechanics were verified in
# docs/research/2026-09-20-timescaledb-backup-restore.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-${REPO_ROOT}/backups}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
TARGET="${OUT_DIR}/finstream-${STAMP}.dump"

die() {
    echo "backup failed: $*" >&2
    exit 1
}

command -v docker >/dev/null 2>&1 || die "docker is not on PATH"
cd "${REPO_ROOT}"

# A dump from a database that is not accepting connections would be silently useless.
docker compose exec -T db pg_isready -q >/dev/null 2>&1 ||
    die "the 'db' service is not accepting connections; start it with 'docker compose up -d db'"

mkdir -p "${OUT_DIR}"

echo "Backing up the FinStream database"
# Two plain columns rather than string concatenation in SQL: `$$` inside the container's shell
# would expand to its PID, not to a dollar-quoted string.
counts="$(docker compose exec -T db sh -c '
    PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAF" " -c \
        "SELECT (SELECT count(*) FROM raw.market_prices), (SELECT count(*) FROM raw.ingestion_runs);"
')" || die "could not read row counts; is the raw schema present?"
read -r bars runs <<<"${counts}"
echo "  contents: ${bars} bars, ${runs} ingestion runs"

# Dump to a temporary file first: a truncated file at the final name would look like a good backup.
tmp="$(mktemp "${OUT_DIR}/.finstream-${STAMP}.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT

docker compose exec -T db sh -c '
    PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        --format=custom --no-owner --no-privileges
' >"${tmp}" || die "pg_dump returned a non-zero status"

[ -s "${tmp}" ] || die "pg_dump produced an empty file"

mv "${tmp}" "${TARGET}"
trap - EXIT

size="$(du -h "${TARGET}" | cut -f1)"
echo "  written:  ${TARGET} (${size})"
echo
echo "Restore instructions: docs/runbooks/restore.md"
