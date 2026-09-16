"""The query layer is read-only and parameter-bound."""

from __future__ import annotations

import re

import pytest

from finstream_dashboard import queries

WRITING_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE|GRANT|COPY)\b", re.IGNORECASE
)


@pytest.mark.parametrize("statement", queries.ALL_STATEMENTS, ids=lambda s: str(s)[:30])
def test_every_statement_only_reads(statement: object) -> None:
    """The dashboard must never write: ingestion owns the raw schema (ADR-0001)."""
    sql = str(statement)
    assert sql.strip().upper().startswith("SELECT") or sql.strip().startswith("\n")
    assert not WRITING_KEYWORDS.search(sql), f"writing statement in the dashboard: {sql[:80]}"


@pytest.mark.parametrize("statement", queries.ALL_STATEMENTS, ids=lambda s: str(s)[:30])
def test_statements_read_only_the_raw_schema(statement: object) -> None:
    sql = str(statement)
    for table in re.findall(r"FROM\s+([\w.]+)", sql, flags=re.IGNORECASE):
        assert table.startswith("raw."), f"unexpected table {table}"


def test_user_input_is_bound_not_interpolated() -> None:
    """A symbol comes from a widget, so it must arrive as a bind parameter."""
    sql = str(queries.PRICE_HISTORY_SQL)
    assert ":symbol" in sql and ":bar_interval" in sql
    assert "%s" not in sql and "format(" not in sql


def test_price_history_is_bounded() -> None:
    sql = str(queries.PRICE_HISTORY_SQL)
    assert "LIMIT :max_rows" in sql, "an unbounded query would not survive a growing table"
    assert "ORDER BY ts" in sql
