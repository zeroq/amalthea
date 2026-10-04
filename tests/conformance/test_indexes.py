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

from ._schema import indexes, is_prefix_of, own_models, own_tables, unique_keys

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
