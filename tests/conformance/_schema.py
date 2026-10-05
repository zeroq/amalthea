"""Database-level schema introspection helpers shared by the conformance tests.

Every function here reads the **live** database, not `Model._meta`. The previous
generation of these tests read `_meta` and therefore asserted that Django's *in-memory
idea* of the schema matched itself: a migration that was never applied, or a field that
existed only in the model, both passed. These helpers exist so a test fails when the
database and the models disagree, which is the entire point of the Phase 3 schema gate.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.apps import apps
from django.db import connection

#: Apps Amalthea owns. Django's own apps are excluded: their schema is not ours to
#: constrain, and `auth_permission` legitimately has auto-increment integer PKs.
OWN_APPS = frozenset({"alerts", "automation", "cases", "identity", "ingest", "observables"})


def own_labels() -> list[str]:
    """App labels whose models Amalthea owns, sorted for stable reporting."""
    labels = {cfg.label for cfg in apps.get_app_configs() if cfg.name in OWN_APPS}
    return sorted(labels)


def own_models() -> list[tuple[str, type]]:
    """`(app_label, model)` for every model Amalthea owns, sorted."""
    out: list[tuple[str, type]] = []
    for label in own_labels():
        for model in apps.get_app_config(label).get_models():
            out.append((label, model))
    return sorted(out, key=lambda pair: pair[1]._meta.db_table)


def table_of(model: type) -> str:
    return model._meta.db_table


def own_tables() -> set[str]:
    """Tables that exist in the database and belong to a model Amalthea owns.

    Django's own tables (`django_*`, `auth_*`, `taggit_*`) are excluded so the audit only
    covers schema this project is responsible for.
    """
    present = set(connection.introspection.table_names())
    ours = {table_of(model) for _, model in own_models()}
    return {t for t in present if t in ours}


def indexes(table: str) -> dict[str, tuple[str, ...]]:
    """`{index_name: columns}` for real indexes, excluding the primary key.

    Only entries whose `index` flag is set qualify. On SQLite a `unique=True` column or
    multi-column constraint is stored as a table-level UNIQUE constraint rather than a
    standalone CREATE INDEX, and Django reports it with `index=False`; those are still
    backed by an index (SQLite auto-creates one), so they are included separately by
    `unique_keys`.
    """
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return {
        name: tuple(spec["columns"])
        for name, spec in constraints.items()
        if spec["index"] and not spec["primary_key"]
    }


def unique_keys(table: str) -> set[tuple[str, ...]]:
    """Every UNIQUE column set on `table`, primary key excluded."""
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return {
        tuple(spec["columns"])
        for spec in constraints.values()
        if spec["unique"] and not spec["primary_key"]
    }


def foreign_keys(table: str) -> set[tuple[str, ...]]:
    """Every foreign-key column set on `table`."""
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return {tuple(spec["columns"]) for spec in constraints.values() if spec["foreign_key"]}


def check_names(table: str) -> set[str]:
    """Names of CHECK constraints the database reports for `table`.

    SQLite keeps CHECK constraints in the table DDL and Django recovers their names by
    parsing it, so anonymous constraints surface as `__unnamed_constraint_N__`.
    """
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return {name for name, spec in constraints.items() if spec.get("check")}


def index_ddl(table: str, name: str) -> str:
    """The raw ``CREATE INDEX`` statement for one index, or `""` if it is absent.

    Django's introspection reports a partial index's columns but **not** its predicate, on
    either engine — `get_constraints()` has no `condition` key for SQLite, and Postgres's
    predicate is not surfaced by the Django backend at all. So anything that must assert on
    `WHERE status = 'Pending'` (REVIEW-2026-10-04 **H-1**) has to read the catalog directly,
    the same reason `table_ddl` exists in `_mutation`.
    """
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE tablename = %s AND indexname = %s",
                [table, name],
            )
        else:
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = %s "
                "AND name = %s",
                [table, name],
            )
        row = cursor.fetchone()
    return (row[0] or "") if row else ""


def partial_index_predicate(table: str, name: str) -> str:
    """The ``WHERE ...`` tail of a partial index's DDL, lower-cased. `""` if not partial.

    Both engines spell the clause the same way, so no cross-engine normalisation is needed
    beyond case: SQLite emits ``... ("created_at") WHERE "status" = 'Pending'`` and Postgres
    ``... (created_at) WHERE (status::text = 'Pending'::text)``. Assertions therefore check
    *what the predicate mentions*, not its exact text.
    """
    ddl = index_ddl(table, name).lower()
    marker = ddl.rfind(" where ")
    return ddl[marker + len(" where ") :] if marker != -1 else ""


def columns(table: str) -> dict[str, str]:
    """`{column_name: declared SQL type}` as stored by the database."""
    with connection.cursor() as cursor:
        return {
            info.name: info.type_code.upper()
            for info in connection.introspection.get_table_description(cursor, table)
        }


def is_prefix_of(short: Iterable[str], long: Iterable[str]) -> bool:
    """True when `short` is a leading subsequence of `long`."""
    short, long = list(short), list(long)
    return len(short) <= len(long) and long[: len(short)] == short
