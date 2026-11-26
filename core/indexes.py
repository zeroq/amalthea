"""Postgres-only index classes that degrade safely on other backends.

Django's SQLite schema editor rebuilds tables from model state during tests that
mutate constraints. A plain :class:`~django.contrib.postgres.indexes.GinIndex`
with an ``OpClass`` expression emits invalid SQLite DDL during those rebuilds,
so expression indexes use this subclass: it keeps the Postgres index in Django
state (and therefore in ``makemigrations --check``) but emits a harmless
comment on non-Postgres backends.
"""

from __future__ import annotations

from typing import Any

from django.contrib.postgres.indexes import GinIndex
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Model


class PostgresExpressionGinIndex(GinIndex):
    """A GIN expression index that is skipped entirely outside Postgres."""

    def create_sql(
        self,
        model: type[Model],
        schema_editor: BaseDatabaseSchemaEditor,
        using: str = "",
        **kwargs: Any,
    ) -> Any:
        if schema_editor.connection.vendor != "postgresql":
            return f"-- skipped postgres-only index {self.name}"
        return super().create_sql(model, schema_editor, using=using, **kwargs)

    def remove_sql(
        self,
        model: type[Model],
        schema_editor: BaseDatabaseSchemaEditor,
        **kwargs: Any,
    ) -> Any:
        if schema_editor.connection.vendor != "postgresql":
            return f"-- skipped postgres-only index {self.name}"
        return super().remove_sql(model, schema_editor, **kwargs)
