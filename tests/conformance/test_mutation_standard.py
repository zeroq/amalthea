"""Mechanical enforcement of the R11 standard: *a test must prove it can fail.*

This is the "make it standard" requirement, turned into a test rather than a convention. It exists
because the standard was violated three times in two gate rounds:

1. **Round 1 (H1)** — four schema acceptance criteria were "met" by tests that could not fail: one
   had a literal ``pass`` body, one asserted ``len(tables) > 0``, one asserted a tautology.
2. **Round 2 (C-1)** — the guard written to close that hole was itself vacuous. It called Django's
   SQLite ``remove_constraint()``, which is ``_remake_table()`` — a rebuild from *current model
   state* — so the constraint was still in ``_meta.constraints`` and was written straight back out.
   The guard printed ``duplicate insert accepted`` and reported ``5 passed``.
3. **Round 2 (C-2)** — the FK ``on_delete`` audit checked ``deconstruct()`` for the presence of
   the key rather than for the value behind it. (The original write-up blamed Django for
   omitting ``on_delete`` on ``CASCADE``; that is not what happens. Measured on the pinned
   Django 5.2.17, ``ForeignKey.deconstruct()`` always emits ``on_delete``, so the
   ``"on_delete" in ...`` assertion was a tautology, not a detection — the three data-loss
   swaps it was meant to catch passed either way.)

In every case the defect was in the **evidence**, not the code under test. So the rule is enforced
here, by failing the build:

* Every test named ``test_mutation_*`` must apply its mutation through :func:`schema_mutation`
  (which verifies the DDL actually moved) or through raw DDL that raises loudly on failure.
* Any test performing a schema edit anywhere in the suite must do one of the above — no unverified
  ``schema_editor`` / ``ALTER TABLE`` / ``DROP INDEX`` calls anywhere.
* :func:`schema_mutation`'s own "did it land?" check must not be removed. It is the whole point.

A violation here is a **test defect**, not a product defect, and it is reported as such.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import tests.conformance._mutation as mutation_module
import tests.conformance.test_schema_mutation_guards as guards

TESTS_DIR = Path(inspect.getfile(guards)).parent

#: Schema-editing constructs that can **silently no-op**, because on SQLite they rebuild the table
#: from current model state rather than dropping anything. These must never appear unverified.
#: `cursor.execute` is deliberately absent: raw SQL raises loudly when it does not apply, so it is a
#: legitimate verified mechanism — flagging it would contradict the per-guard rule below.
SILENT_NOOP_CALLS = {
    "schema_editor",
    "remove_constraint",
    "add_constraint",
    "alter_db_table",
    "add_field",
    "remove_field",
}

#: Accepted ways a guard may apply a mutation. Model-metadata mutation (`_meta`, field flags) counts,
#: because the assertion reads that same state back — there is no separate DDL to drift from.
VERIFIED_MECHANISMS = ("schema_mutation(", "cursor.execute(", "_meta", "index_names(")


def _all_test_files() -> list[Path]:
    return sorted(p for p in TESTS_DIR.glob("test_*.py"))


def _source_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(n for n in dir(guards) if n.startswith("test_mutation_")))
def test_every_mutation_guard_verifies_its_own_mutation(name: str) -> None:
    """Each guard must prove its mutation landed, not merely assert a consequence."""
    fn = getattr(guards, name)
    src = inspect.getsource(fn)
    verifies = any(m in src for m in VERIFIED_MECHANISMS)
    assert verifies, (
        f"{name} mutates state without verifying the mutation happened. It must either go through "
        f"schema_mutation() — which reads the DDL back and raises if it never moved — or issue raw "
        f"DDL, which fails loudly if it does not apply. Otherwise it may report a demonstrated "
        f"failure mode for a schema it never actually changed (the round-2 C-1 defect)."
    )


@pytest.mark.parametrize("path", _all_test_files(), ids=lambda p: p.name)
def test_no_unverified_schema_mutation_anywhere(path: Path) -> None:
    """No test may edit the schema outside the verified mechanism."""
    if path.name == Path(__file__).name:
        return
    tree = ast.parse(_source_of(path))
    offenders: list[int] = []
    for node in ast.walk(tree):
        # schema_editor(...) / remove_constraint(...) style calls
        if isinstance(node, ast.Call):
            func = node.func
            attr = getattr(func, "attr", None) or getattr(func, "id", None)
            if attr in SILENT_NOOP_CALLS:
                offenders.append(node.lineno)
    assert not offenders, (
        f"{path.name} performs schema mutations at line(s) {sorted(set(offenders))} without going "
        f"through schema_mutation(). On SQLite, schema_editor operations rebuild the table from "
        f"current model state, so an unverified mutation can be a silent no-op that still reports a "
        f"verified failure mode. Wrap it in schema_mutation() and assert_ddl_delta()."
    )


def test_the_mutation_helper_still_verifies() -> None:
    """Guard the guard: `schema_mutation`'s own 'did it land?' check must survive edits."""
    src = inspect.getsource(mutation_module.schema_mutation)
    assert "MUTATION DID NOT LAND" in src, (
        "schema_mutation() no longer reports a no-op mutation. This function is the mechanism that "
        "makes the R11 standard enforceable; removing the check reintroduces the round-2 C-1 class "
        "of defect with nothing left to catch it."
    )
    assert mutation_module.Mutation.assert_ddl_delta, "assert_ddl_delta() is the per-test standard."


def test_guard_file_docstring_states_it_is_permanent() -> None:
    """The guards must not drift back into 'delete me after the evidence' framing."""
    doc = inspect.getdoc(guards) or ""
    assert "permanent" in doc.lower(), (
        "test_schema_mutation_guards.py must document that it is a permanent regression guard. "
        "Earlier it was described as temporary scaffolding to delete after capturing evidence, which "
        "would have discarded the only artifact proving the conformance suite is not vacuous."
    )
