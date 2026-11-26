"""Migration helpers for Postgres GIN indexes.

Django's generic ``models.Index(..., opclasses=["gin_jsonb_ops"])`` emits a btree
index with a GIN operator class, which Postgres rejects. These helpers keep the
migration state as a real ``GinIndex`` while making the database operation
Postgres-only: SQLite continues to migrate cleanly without a no-op index.
"""

from __future__ import annotations

import re
from typing import Any

from django.db import migrations

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPRESSION = re.compile(r"^[A-Za-z0-9_\"().: ]+$")


def _validate_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return value


def gin_index_operation(table: str, name: str, column: str) -> migrations.RunPython:
    """Return a Postgres-only ``RunPython`` operation that creates/drops a GIN index."""
    safe_table = _validate_identifier(table)
    safe_name = _validate_identifier(name)
    safe_column = _validate_identifier(column)

    def create(apps: Any, schema_editor: Any) -> None:
        if schema_editor.connection.vendor != "postgresql":
            return
        schema_editor.execute(
            f'CREATE INDEX IF NOT EXISTS "{safe_name}" '
            f'ON "{safe_table}" USING gin ("{safe_column}")'
        )

    def drop(apps: Any, schema_editor: Any) -> None:
        if schema_editor.connection.vendor != "postgresql":
            return
        schema_editor.execute(f'DROP INDEX IF EXISTS "{safe_name}"')

    return migrations.RunPython(create, drop)


def gin_expression_index_operation(
    table: str, name: str, expression: str, opclass: str
) -> migrations.RunPython:
    """Return a Postgres-only GIN expression index operation.

    ``expression`` is DDL text from a migration, not user input; the whitelist keeps a
    typo or copied statement from becoming an arbitrary SQL suffix.
    """
    safe_table = _validate_identifier(table)
    safe_name = _validate_identifier(name)
    safe_opclass = _validate_identifier(opclass)
    if not _EXPRESSION.fullmatch(expression):
        raise ValueError(f"unsafe expression: {expression!r}")

    def create(apps: Any, schema_editor: Any) -> None:
        if schema_editor.connection.vendor != "postgresql":
            return
        schema_editor.execute(
            f'CREATE INDEX IF NOT EXISTS "{safe_name}" ON "{safe_table}" '
            f"USING gin (({expression}) {safe_opclass})"
        )

    def drop(apps: Any, schema_editor: Any) -> None:
        if schema_editor.connection.vendor != "postgresql":
            return
        schema_editor.execute(f'DROP INDEX IF EXISTS "{safe_name}"')

    return migrations.RunPython(create, drop)
