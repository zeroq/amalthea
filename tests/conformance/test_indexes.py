"""REVIEW-2026-10-03 **H1/M1** — index audit against the live database.

The previous version asserted `len(connection.introspection.table_names()) > 0`, which
holds for an empty database and therefore could not fail.

These tests assert the two things PLAN §6.3 actually promises:

**Required indexes exist.** Every index the plan names, the review's hot paths depend on,
or that a remediation added is present as a real index in the database, with the exact
columns and in the exact column order (order matters: a `(status_id, date)` index cannot
serve an `(date, status_id)` lookup).

**No redundant index survives.** REVIEW M1 — an index that duplicates an implicit FK index,
duplicates a `unique=True` index, or is entirely left-prefix-covered by a wider index is
pure write amplification and bloats the table. The removed ones are named in the failure
message so a regression is self-explanatory.

Review items: H1 (vacuous test), M1 (redundant indexes), C1 (case number), C2 (pending-run
dispatch), C4 (correlation key), M5 (observable fan-out), M9 (timeline keyset).
"""

from __future__ import annotations

import pytest
from django.db import connection

from ._schema import (
    index_ddl,
    indexes,
    is_prefix_of,
    own_models,
    own_tables,
    partial_index_predicate,
    unique_keys,
)

#: `table -> {index name: columns}`. Names, not just columns, are asserted: an index that
#: silently changes shape is a schema change nobody asked for.
REQUIRED_INDEXES: dict[str, dict[str, tuple[str, ...]]] = {
    # PLAN §6.3: alert queue `(status, -date)` and the alert-source lookup for AC2.x.
    "alert": {
        "alert_status_date_idx": ("status_id", "date"),
        # REVIEW C4: AC5.2 correlates on a namespaced key within a 10-minute window.
        "alert_corrkey_date_idx": ("correlation_key", "date"),
        "alert_created_at_idx": ("created_at",),
    },
    "case_record": {
        # PLAN §6.3: case queue `(status, -start_date)`; REVIEW L5 makes start_date NOT
        # NULL because NULLs sort first on Postgres.
        "case_status_start_idx": ("status_id", "start_date"),
        "case_severity_idx": ("severity",),
        "case_created_at_idx": ("created_at",),
    },
    # REVIEW M9: trailing id makes keyset pagination deterministic for equal timestamps.
    "timeline_event": {"timeline_case_date_idx": ("case_id", "date", "id")},
    # REVIEW M5: Module C's observable -> cases fan-out.
    "case_observable": {"caseobs_obs_case_idx": ("observable_id", "case_id")},
    "alert_observable": {"alertobs_obs_alert_idx": ("observable_id", "alert_id")},
    # REVIEW C2: dispatch reconciliation, the pending-run sweep and trigger resolution.
    "automation_run": {
        "ar_case_started_idx": ("case_id", "started_at"),
        "ar_celery_task_idx": ("celery_task_id",),
        "ar_pending_idx": ("created_at",),
    },
    "playbook": {"playbook_trigger_idx": ("trigger_event", "is_active")},
    "observable": {"obs_created_at_idx": ("created_at",)},
    "task": {"task_created_at_idx": ("created_at",)},
}

#: REVIEW M1 — indexes deleted in this remediation. Kept as an explicit list so that
#: re-introducing one is a named failure rather than a silent regression, and so the
#: report can point at what was removed.
REMOVED_INDEXES: dict[str, tuple[str, ...]] = {
    # Duplicated the implicit FK index Django already creates for `Alert.case_id`.
    "alert": ("alert_case_idx",),
    # Duplicated the `number` unique index (which is itself an index).
    "case_record": ("case_number_idx",),
    # Duplicated the implicit FK index for `Case.assignee`.
    "case_record_removed_assignee": ("case_assignee_idx",),
    # Duplicated the implicit FK index for `Task.case`.
    "task": ("task_case_idx",),
}

#: REVIEW L-2 — `table -> (column, (column, ...))`: the join tables' *left* FK must have no
#: dedicated index, because the table's own `UNIQUE(left, tag)` covers it as a left prefix.
#: Asserted by column rather than by name because the auto-generated index name embeds a hash
#: of the table name, and a glob loose enough to catch it also catches the unique composite.
JOIN_TABLE_LEFT_COLUMN: dict[str, tuple[str, tuple[str, ...]]] = {
    "alert_tags": ("alert_id", ("alert_id", "tag_id")),
    "case_record_tags": ("case_id", ("case_id", "tag_id")),
    "observable_tags": ("observable_id", ("observable_id", "tag_id")),
}


@pytest.mark.django_db
def test_index_audit_is_not_vacuous() -> None:
    """Guards the guard: pin the scope of this audit.

    The audit reads live indexes. If `_schema.own_tables()` regressed to an empty set the
    removal test below would pass while checking nothing, so assert the real population.
    """
    tables = own_tables()
    assert len(tables) >= 18, f"expected Amalthea's own tables, found {sorted(tables)}"
    assert {t for _, model in own_models() for t in [model._meta.db_table]} == tables


@pytest.mark.django_db
@pytest.mark.parametrize(("table", "expected"), sorted(REQUIRED_INDEXES.items()))
def test_required_indexes_exist_with_the_expected_columns(
    table: str, expected: dict[str, tuple[str, ...]]
) -> None:
    present = indexes(table)
    wrong = {
        name: (columns, present.get(name))
        for name, columns in expected.items()
        if present.get(name) != columns
    }
    assert not wrong, f"{table}: expected/missing index columns {wrong}"


@pytest.mark.django_db
def test_removed_indexes_stay_removed() -> None:
    """REVIEW M1: these indexes duplicated another index or the PK/unique index."""
    present = {table: indexes(table) for table in own_tables()}
    resurrected = [
        f"{table}.{name}"
        for table, names in REMOVED_INDEXES.items()
        for name in names
        if name in present.get(table, {})
    ]
    assert not resurrected, f"REVIEW M1 regressed; these indexes are redundant again: {resurrected}"


@pytest.mark.django_db
@pytest.mark.parametrize("table", sorted(JOIN_TABLE_LEFT_COLUMN))
def test_l2_the_join_table_left_column_has_no_dedicated_index(table: str) -> None:
    """The M2M join tables pruned their left FK index (REVIEW L-2) — and kept the right one.

    Django's auto-created through models gave the left FK an index the table's own
    `UNIQUE(left, tag)` already covers. Because an implicit model has no declaration to
    edit, this was invisible to every index audit until the join models were declared
    explicitly. Three things have to hold, and the test pins all three so the fix cannot
    over-correct into a sequential scan:

    1. no index exists on exactly `(left)`;
    2. the `UNIQUE(left, tag)` composite is still there to serve the lookup;
    3. `tag` still has its own index — `(left, tag)` does **not** cover `(tag)`, so pruning
       it would have been the same class of bug in the opposite direction.
    """
    left, composite = JOIN_TABLE_LEFT_COLUMN[table]
    right = composite[1]
    present = indexes(table)

    dedicated = sorted(name for name, cols in present.items() if cols == (left,))
    assert not dedicated, (
        f"{table}: {dedicated} indexes {left} alone, but UNIQUE{composite} already serves that "
        f"lookup as a left prefix (REVIEW M-1/L-2). It costs write amplification for nothing."
    )
    assert composite in present.values() or composite in unique_keys(table), (
        f"{table}: UNIQUE{composite} is missing, so dropping the {left} index would leave "
        f"WHERE {left} = ? as a sequential scan."
    )
    assert (right,) in present.values(), (
        f"{table}: no index on {right}. It is the RIGHT column of UNIQUE{composite}, so the "
        f"composite cannot serve WHERE {right} = ? — 'every case with this tag' would scan."
    )


@pytest.mark.django_db
def test_no_index_is_left_prefix_covered_by_a_wider_one() -> None:
    """REVIEW M1: `(case_id)` is redundant once `(case_id, started_at)` exists.

    On both engines the wider index serves the narrower lookup, so the narrower one only
    costs INSERT/UPDATE time and disk. Unique constraints are excluded from the comparison
    in the other direction because a UNIQUE index cannot serve a non-unique lookup.
    """
    redundant: list[str] = []
    for table in sorted(own_tables()):
        found = indexes(table)
        names = sorted(found)
        for narrow_name in names:
            for wide_name in names:
                if narrow_name == wide_name:
                    continue
                narrow, wide = found[narrow_name], found[wide_name]
                if len(wide) > len(narrow) and is_prefix_of(narrow, wide):
                    redundant.append(f"{table}.{narrow_name}{narrow} covered by {wide_name}{wide}")
    assert not redundant, "redundant (prefix-covered) indexes: " + "; ".join(redundant)


def _declared_columns(model: type, entry: object) -> tuple[str, ...]:
    """Columns a `Meta.indexes` entry covers, as plain column names.

    `Index(fields=["-date"])` records the descending ordering as a `-` prefix on the field
    name; SQLite and Postgres both index that column, so the prefix is stripped.
    """
    return tuple(
        model._meta.get_field(name.removeprefix("-")).column  # type: ignore[union-attr]
        for name in entry.fields  # type: ignore[attr-defined]
    )


def _covering_columns(model: type) -> list[tuple[str, ...]]:
    """Column tuples on `model`'s table that can serve a single-column lookup.

    Built from the **declared** schema — `Meta.indexes`, UNIQUE constraints, `unique=True`
    columns and the other FKs' implicit indexes — because that is where a `db_index=False`
    justification can be wrong. Read from the live database as well (below), because a
    declared cover that was never migrated is still a sequential scan.

    UNIQUE constraints are included deliberately. Django's SQLite introspection reports them
    with `index=False`, so an earlier version of this file compared prefixes against an
    empty name set for every table whose only wide index was a constraint (round-2 **L-3**) —
    which is exactly where the two needed indexes had been dropped.
    """
    out: list[tuple[str, ...]] = [_declared_columns(model, entry) for entry in model._meta.indexes]
    for constraint in model._meta.constraints:
        names = getattr(constraint, "fields", None)
        if names:
            out.append(tuple(model._meta.get_field(name).column for name in names))
    # `unique_together` is a tuple *of* tuples. Iterating it directly (the earlier version of
    # this line) hands `get_field()` a whole tuple like ('case', 'tag') instead of a field
    # name, so it raised FieldDoesNotExist on any model that used it. Nothing used it until
    # the `*TagLink` join models were declared (REVIEW L-2), which is why the bug stayed
    # latent: the line was unreachable, not correct.
    for names in model._meta.unique_together:
        out.append(tuple(model._meta.get_field(name).column for name in names))
    out += [
        (f.column,)
        for f in model._meta.fields
        if (f.unique or (f.many_to_one and f.db_index)) and f.column
    ]
    return sorted((tuple(cols) for cols in out), key=len, reverse=True)  # type: ignore[arg-type]


def _live_columns(table: str) -> list[tuple[str, ...]]:
    """Column tuples the database actually has an index for, UNIQUE constraints included."""
    live = list(indexes(table).values()) + list(unique_keys(table))
    return sorted(live, key=len, reverse=True)


@pytest.mark.django_db
def test_every_unindexed_fk_is_a_left_prefix_of_a_composite_index() -> None:
    """REVIEW-2026-10-04 round-2 **H-5** (TODO 1.15): re-derive the whole `M1` prune.

    `ForeignKey` sets `db_index=True` by default. REVIEW M1 set it to `False` on 13 columns
    on the justification that a composite already covers the lookup — a claim that is only
    true for a **left** prefix, and that was checked by eye. Two of the thirteen were
    `custom_field`, the **right-hand** column of `uniq_{case,alert}_custom_field`, so
    `WHERE custom_field_id = ?` was a sequential scan and the suite was green.

    This is the mechanical version of the claim: for every FK declared `db_index=False`,
    some **declared** index whose columns *start* with that column must exist, and it must
    also exist in the live database. Both halves are load-bearing — the declaration is where
    the reasoning happens, the database is where the query actually runs.
    """
    unjustified: list[str] = []
    unmigrated: list[str] = []
    for _, model in own_models():
        declared = _covering_columns(model)
        live = _live_columns(model._meta.db_table)
        for field in model._meta.fields:
            if not field.many_to_one or field.db_index or not field.column:
                continue
            if not any(is_prefix_of((field.column,), cols) for cols in declared):
                unjustified.append(
                    f"{model._meta.db_table}.{field.column} ({model.__name__}.{field.name})"
                )
            elif not any(is_prefix_of((field.column,), cols) for cols in live):
                unmigrated.append(f"{model._meta.db_table}.{field.column}")
    assert not unjustified, (
        "FK(s) with db_index=False whose column is not the LEFT prefix of any declared index "
        "or UNIQUE constraint — the lookup `WHERE <column> = ?` falls back to a sequential "
        f"scan: {unjustified}"
    )
    assert not unmigrated, (
        f"FK(s) justified by a declared left prefix that the database does not have: {unmigrated}"
    )


@pytest.mark.django_db
def test_the_custom_field_fk_indexes_are_present() -> None:
    """The two H-5 regressions named individually, so restoring either is a named failure."""
    from alerts.models import AlertCustomFieldValue
    from cases.models import CaseCustomFieldValue

    for model in (CaseCustomFieldValue, AlertCustomFieldValue):
        field = model._meta.get_field("custom_field")
        assert field.db_index, (
            f"{model.__name__}.custom_field lost its index: it is the right-hand column of "
            "the unique constraint, not a left prefix (H-5)"
        )
        assert any(
            columns == (field.column,) for columns in indexes(model._meta.db_table).values()
        ), f"{model._meta.db_table} has no index on {field.column} in the database"


@pytest.mark.django_db
def test_no_hand_written_index_duplicates_an_implicit_fk_index() -> None:
    """Django indexes every FK with `db_index=True`; a same-column `Meta.indexes` entry
    is pure duplication (REVIEW M1).

    A single-column index on an FK column in the database is always the implicit one — it
    carries Django's generated name — so this is checked against the model metadata, which
    is where a duplicate can actually be authored.
    """
    offenders: list[str] = []
    for _, model in own_models():
        fk_cols = {
            f.column for f in model._meta.fields if f.many_to_one and f.column and f.db_index
        }
        for entry in model._meta.indexes:
            cols = _declared_columns(model, entry)
            if len(cols) == 1 and cols[0] in fk_cols:
                offenders.append(f"{model._meta.db_table}.{entry.name}{cols}")
    assert not offenders, f"Meta.indexes entries duplicating an implicit FK index: {offenders}"


@pytest.mark.django_db
def test_every_declared_index_exists_in_the_database() -> None:
    """Catch a `Meta.indexes` entry that was never migrated.

    This is the assertion the original `len(tables) > 0` was supposed to make: the models
    and the database must agree about indexes, in both directions.
    """
    missing: list[str] = []
    for _, model in own_models():
        present = set(indexes(model._meta.db_table).values())
        for entry in model._meta.indexes:
            cols = _declared_columns(model, entry)
            if cols not in present:
                missing.append(f"{model._meta.db_table}.{entry.name}{cols}")
    assert not missing, f"declared index(es) absent from the database: {missing}"


@pytest.mark.django_db
def test_unique_constraints_are_backed_by_indexes() -> None:
    """Every UNIQUE key the models declare must be enforced in the database."""
    problems: list[str] = []
    for _, model in own_models():
        table = model._meta.db_table
        declared = {
            tuple(model._meta.get_field(name).column for name in c.fields)
            for c in model._meta.constraints
            if getattr(c, "fields", None)
        }
        actual = unique_keys(table)
        missing = {cols for cols in declared if cols not in actual}
        if missing:
            problems.append(f"{table}: UNIQUE {sorted(missing)} missing from the database")
    assert not problems, "unique constraints not enforced: " + "; ".join(problems)


# ---------------------------------------------------------------------------------------
# REVIEW-2026-10-04 round-2 H-1 — the *partial* predicate on `ar_pending_idx`.
#
# Removing `condition=Q(status="Pending")` and regenerating the migration left the suite
# green, because nothing in it looked at the predicate: Django's introspection reports the
# index's columns and stops. The whole value of C2 is that the index holds Pending rows
# only, so this is the assertion that value depends on.
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_pending_run_index_predicate_is_still_partial() -> None:
    """Read the catalog: the index must carry a `WHERE status = 'Pending'` predicate.

    Checked on the *declared* condition and on the *live* DDL, because either alone has a
    gap. The declaration can be correct while the migration was never applied; the DDL can
    be correct while the declaration has been edited for the next migration.
    """
    from django.db import models

    from automation.models import AutomationRun

    declared = next((i for i in AutomationRun._meta.indexes if i.name == "ar_pending_idx"), None)
    assert declared is not None, "ar_pending_idx is not declared on AutomationRun"
    assert declared.condition == models.Q(status="Pending"), (
        f"the partial predicate changed: {declared.condition!r}. Without it the dispatch beat "
        "scans and sorts every run, not just the pending ones."
    )

    predicate = partial_index_predicate("automation_run", "ar_pending_idx")
    assert predicate, (
        "ar_pending_idx has no WHERE clause in the database — "
        f"DDL: {index_ddl('automation_run', 'ar_pending_idx')!r}"
    )
    assert "status" in predicate and "pending" in predicate, predicate


@pytest.mark.django_db
@pytest.mark.skipif(
    connection.vendor != "sqlite",
    reason="plan shape is asserted against SQLite's EXPLAIN QUERY PLAN; on Postgres the "
    "predicate proof is the catalog read in test_the_pending_run_index_predicate_is_still_partial "
    "plus the ANALYZE run in the round-2 review's verification SQL (TODO 3.1)",
)
def test_the_pending_index_serves_only_pending_runs() -> None:
    """The behavioural half: Pending uses the index, any other status does not.

    The plan *is* the predicate's observable effect, and it is the same evidence the review
    recorded. If the predicate widens, the Success query starts using the index too and this
    fails.
    """

    def plan(sql: str) -> str:
        with connection.cursor() as cursor:
            rows = cursor.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
        return " | ".join(row[-1] for row in rows)

    pending = plan("SELECT id FROM automation_run WHERE status = 'Pending' ORDER BY created_at")
    other = plan("SELECT id FROM automation_run WHERE status = 'Success' ORDER BY created_at")
    assert "ar_pending_idx" in pending, pending
    assert "TEMP B-TREE" not in pending, f"the pending sweep must not sort: {pending}"
    assert "ar_pending_idx" not in other, (
        f"a non-Pending status must not be answered from a Pending-only index: {other}"
    )
