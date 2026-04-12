"""Validated PostgreSQL schema/table identifiers for dynamic SQL."""

from __future__ import annotations

import re

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$", re.IGNORECASE)


def assert_pg_identifier(name: str, *, kind: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Invalid PostgreSQL {kind} identifier: {name!r}")
    return name


def qualified_table(*, schema: str, table: str) -> str:
    """Return `"schema"."table"` for safe interpolation into SQL text."""
    s = assert_pg_identifier(schema, kind="schema")
    t = assert_pg_identifier(table, kind="table")
    return f'"{s}"."{t}"'


def dotted_name(*, schema: str, table: str) -> str:
    """Return `schema.table` for API strings (e.g. nutrition_source)."""
    s = assert_pg_identifier(schema, kind="schema")
    t = assert_pg_identifier(table, kind="table")
    return f"{s}.{t}"
