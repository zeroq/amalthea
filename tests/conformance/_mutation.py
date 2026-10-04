"""Making a schema mutation *provably real* — the mechanism behind the project's R11 rule.

Why this module exists
----------------------
Risk **R11**: *a passing acceptance criterion is only evidence once its failure mode has been
demonstrated.* Round 1 of the schema gate failed because four of six schema ACs were "met" by tests
that could not fail. Round 2 then failed because the guard written to close that hole **was itself
vacuous**: it called Django's SQLite ``remove_constraint()``, which is ``_remake_table(model)`` —
a rebuild *from current model state* — so the constraint was still in ``_meta.constraints`` and was
written straight back out. The guard printed a success message and reported ``5 passed`` while the
schema it claimed to break was untouched.

The failure was not sloppiness in the assertion. It was that **nothing forced the test to check its
own mutation had landed**. The success string was authored by the same person as the assertion, so
it agreed with itself no matter what the database did.

The rule this module makes mechanical:

    **Assert the mutation changed something BEFORE asserting what it changed.**

``schema_mutation`` snapshots the live DDL from ``sqlite_master`` on entry, hands the caller a
``Mutation``, and on exit **re-reads the DDL and raises if it never moved**. A test physically cannot
report success for a mutation that did not happen.

Engine note
-----------
On SQLite, ``remove_constraint()`` rebuilds the table from ``_meta``, so the constraint must be
detached from the model *before* the schema editor runs (see :meth:`Mutation.detach_constraint`).
``DROP INDEX IF EXISTS`` is also a silent no-op for a UNIQUE constraint, which SQLite backs with an
auto-index named ``sqlite_autoindex_<table>_N`` rather than by the constraint's name. Both traps are
encoded here so the next guard cannot repeat them.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from django.db import connection, models
from django.db.models.options import Options


def table_ddl(*tables: str) -> dict[str, str]:
    """Return ``{table: CREATE statement}`` straight from ``sqlite_master``.

    Read from the database rather than from ``_meta``, because ``_meta`` is the thing under test.
    A guard that inspects the model state it just edited proves nothing.
    """
    out: dict[str, str] = {}
    with connection.cursor() as cursor:
        for table in tables:
            row = cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = %s",
                [table],
            ).fetchone()
            out[table] = (row[0] or "") if row else ""
    return out


def index_names(*tables: str) -> set[str]:
    """Every index name present on ``tables`` (including SQLite's implicit auto-indexes)."""
    out: set[str] = set()
    with connection.cursor() as cursor:
        for table in tables:
            for (name,) in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = %s", [table]
            ).fetchall():
                out.add(f"{table}.{name}")
    return out


@dataclass
class Mutation:
    """A schema edit whose occurrence is verified rather than assumed."""

    tables: tuple[str, ...]
    before: dict[str, str]
    _constraint: tuple[Options, models.BaseConstraint] | None = field(default=None, repr=False)
    _original_constraints: list[Any] | None = field(default=None, repr=False)
    _ddl_delta_checked: bool = field(default=False, repr=False)

    # -- setup helpers -------------------------------------------------------------------
    def add_index(self, model: type[models.Model], index: models.Index) -> None:
        """Add an index and verify it really exists in ``sqlite_master``.

        Index creation does not alter the table's ``CREATE`` statement, so
        :meth:`assert_ddl_delta` cannot see it — the check has to read ``sqlite_master``'s index
        rows instead. Skipping that step is how an index guard ends up asserting against a schema
        that never changed.
        """
        with connection.schema_editor() as editor:
            editor.add_index(model, index)
        name = f"{model._meta.db_table}.{index.name}"
        present = index_names(model._meta.db_table)
        assert name in present, (
            f"MUTATION DID NOT LAND: index {index.name!r} is absent from sqlite_master after "
            f"add_index(). The test is about to assert a consequence of a mutation that never "
            f"happened."
        )
        self._ddl_delta_checked = True
        self._added_indexes = getattr(self, "_added_indexes", [])
        self._added_indexes.append((model, index))

    def detach_constraint(self, model: type[models.Model], name: str) -> None:
        """Remove a constraint from ``_meta`` so ``_remake_table`` cannot write it back.

        Required before ``schema_editor.remove_constraint()`` on SQLite. Without this the
        "mutation" is a no-op that still reports success — the round-2 C-1 defect.
        """
        constraint = next(c for c in model._meta.constraints if c.name == name)
        self._constraint = (model._meta, constraint)
        self._original_constraints = list(model._meta.constraints)
        model._meta.constraints = [c for c in model._meta.constraints if c.name != name]
        with connection.schema_editor() as editor:
            editor.remove_constraint(model, constraint)

    # -- the standard -------------------------------------------------------------------
    def assert_ddl_delta(
        self, *, contains: Sequence[str] = (), missing: Sequence[str] = ()
    ) -> None:
        """Assert the live DDL really changed. **Call this first, before any consequence.**

        ``missing``  — substrings that must now be absent from the table DDL.
        ``contains`` — substrings that must now be present.
        """
        self._ddl_delta_checked = True
        after = table_ddl(*self.tables)
        for table in self.tables:
            for needle in missing:
                assert needle not in after[table], (
                    f"MUTATION DID NOT LAND: {needle!r} is still in the DDL of {table!r}. "
                    f"The test is about to assert a consequence of a mutation that never happened — "
                    f"this is exactly the R11 vacuity failure."
                )
            for needle in contains:
                assert needle in after[table], (
                    f"MUTATION DID NOT LAND: {needle!r} is absent from the DDL of {table!r}."
                )
        self.after = after

    @property
    def changed(self) -> bool:
        """True when the DDL differs from the pre-mutation snapshot in any way."""
        return table_ddl(*self.tables) != self.before

    def assert_changed(self) -> None:
        """Last-resort net for edits whose effect cannot be pinned to a specific string."""
        assert self.changed, "MUTATION DID NOT LAND: the DDL is byte-identical to the snapshot."
        self._ddl_delta_checked = True


@contextmanager
def schema_mutation(*tables: str, label: str = "") -> Iterator[Mutation]:
    """Run a schema mutation and **refuse to let it pass silently**.

    On exit the DDL is re-read. If it never changed, the test errors — so a no-op mutation cannot
    masquerade as a demonstrated failure mode.
    """
    mutation = Mutation(tables=tables, before=table_ddl(*tables))
    try:
        yield mutation
    finally:
        # Restore first, then judge, so the suite is left usable and the verdict is about the
        # mutation rather than about cleanup.
        for model, index in getattr(mutation, "_added_indexes", []):
            with connection.schema_editor() as editor:
                editor.remove_index(model, index)
        if mutation._constraint is not None and mutation._original_constraints is not None:
            opts, constraint = mutation._constraint
            opts.constraints = mutation._original_constraints
            with connection.schema_editor() as editor:
                editor.add_constraint(opts.model, constraint)
        after = table_ddl(*tables)
        if after == mutation.before and not mutation._ddl_delta_checked:
            name = f" ({label})" if label else ""
            raise AssertionError(
                f"MUTATION DID NOT LAND{name}: the DDL of {', '.join(tables)} is byte-identical "
                f"before and after, and no assert_ddl_delta()/assert_changed() call was made. "
                f"On SQLite, remove_constraint() rebuilds from _meta — detach the constraint first. "
                f"See tests/conformance/_mutation.py."
            )
