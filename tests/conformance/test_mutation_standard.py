"""Mechanical enforcement of the R11 standard: *a test must prove it can fail.*

This is the "make it standard" requirement, turned into a test rather than a convention. It exists
because the standard was violated four times in three gate rounds:

1. **Round 1 (H1)** — four schema acceptance criteria were "met" by tests that could not fail: one
   had a literal ``pass`` body, one asserted ``len(tables) > 0``, one asserted a tautology.
2. **Round 2 (C-1)** — the guard written to close that hole was itself vacuous. It called Django's
   SQLite ``remove_constraint()``, which is ``_remake_table()`` — a rebuild from *current model
   state* — so the constraint was still in ``_meta.constraints`` and was written straight back out.
   The guard printed ``duplicate insert accepted`` and reported ``5 passed``.
3. **Round 2 (C-2)** — the FK ``on_delete`` audit checked ``deconstruct()`` for the presence of the
   key rather than for the value behind it, which is a tautology for every FK.
4. **Round 3 (H3-3)** — **this file was the loophole.** It enforced the standard with
   ``any(m in src for m in VERIFIED_MECHANISMS)`` — a *substring match over the guard's source
   text*. A ``test_mutation_*`` guard that mutated nothing and merely mentioned ``_meta`` in a
   comment satisfied it, because ``"_meta" in src`` was true. The mechanism was weaker than the
   standard it was supposed to enforce, which is the one failure mode R11 exists to prevent.

Round 3 (H3-3) is why this file is now **AST-based** and why it carries **negative self-tests**.
Enforcement that is itself unverified is not enforcement: :func:`test_the_standard_rejects_a_vacuous_guard`
runs deliberately-broken guards through the analyser below and requires each one to be rejected, so a
future simplification of this file cannot quietly reintroduce the substring hole.

How a guard is judged
----------------------
A ``test_mutation_*`` test must do two *different* things, both located by AST rather than by text:

**1. Mutate.** At least one mutating site:

* ``schema_mutation(...)`` — the verified DDL context manager (:mod:`tests.conformance._mutation`);
* one of the self-verifying helpers ``add_index`` / ``detach_constraint`` / ``detach_field_unique`` /
  ``replace_index``, each of which reads the DDL back and raises ``MUTATION DID NOT LAND``;
* ``cursor.execute(...)`` — raw DDL, which raises loudly if the statement does not apply;
* an **assignment to an attribute** (e.g. ``field.remote_field.on_delete = ...``,
  ``login.null = True``) — an in-memory model-metadata edit. Django's deletion collector and the
  field deconstruction both read this state at call time, so flipping it genuinely changes behaviour.

**2. Verify.** At least one verification site:

* ``assert_ddl_delta(...)`` / ``assert_changed()`` — the DDL read-back;
* an ``assert`` statement that **mentions the mutated attribute** — the metadata read-back, which is
  what proves the assignment actually took on the object the real assertion will read;
* for the raw-DDL mechanism, ``cursor.execute`` *is* the verification, since SQLite raises on a
  statement that does not apply.

Bare ``schema_editor(...)`` / ``add_field(...)`` and friends are rejected outright: on SQLite they
rebuild the table from current model state, so an unverified one is a silent no-op (round 2, C-1).

A violation here is a **test defect**, not a product defect, and it is reported as such.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from django.db import connection

import tests.conformance._mutation as mutation_module
import tests.conformance.test_schema_mutation_guards as guards

TESTS_DIR = Path(inspect.getfile(guards)).parent
THIS_FILE = Path(__file__).name

#: Schema-editing constructs that can **silently no-op**, because on SQLite they rebuild the table
#: from current model state rather than dropping anything. These must never appear unverified.
#: `cursor.execute` is deliberately absent: raw SQL raises loudly when it does not apply, so it is a
#: legitimate verified mechanism — flagging it would contradict the per-guard rule below.
SILENT_NOOP_CALLS = frozenset(
    {
        "schema_editor",
        "remove_constraint",
        "add_constraint",
        "alter_db_table",
        "add_field",
        "remove_field",
    }
)

#: Self-verifying mutation helpers. Each reads the DDL back and raises `MUTATION DID NOT LAND`, so
#: calling one *is* the verification — see `_mutation.py`.
SELF_VERIFYING_HELPERS = frozenset(
    {"add_index", "detach_constraint", "detach_field_unique", "replace_index"}
)

#: The DDL-verification calls. Present anywhere in a guard counts as a verification site.
DDL_VERIFIERS = frozenset({"assert_ddl_delta", "assert_changed"})

#: The verified DDL context manager.
DDL_MUTATION = "schema_mutation"


def _all_test_files() -> list[Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def _called_names(node: ast.AST) -> set[str]:
    """Every name invoked by a call inside ``node``: ``f()`` → ``f``, ``a.f()`` → ``f``."""
    return {
        call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
    }


def _assigns_to_attributes(node: ast.AST) -> set[str]:
    """Dotted names of every attribute **assigned** inside ``node``.

    Only ``ast.Attribute`` targets count. ``x = compute()`` is not a mutation — it rebinds a local
    name and changes no shared state, which is exactly the shape of a fake guard.

    Tuple targets are unpacked (``login.null, login.blank = True, True`` — guard 4 mutates two
    fields in one statement), as are starred targets.
    """
    out: set[str] = set()
    for stmt in ast.walk(node):
        targets: list[ast.expr] = []
        if isinstance(stmt, ast.Assign):
            targets = [t for t in stmt.targets if isinstance(t, ast.Attribute)]
            for t in stmt.targets:
                if isinstance(t, (ast.Tuple, ast.List)):
                    targets += [e for e in t.elts if isinstance(e, ast.Attribute)]
        elif isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Attribute):
                targets = [stmt.target]
        for target in targets:
            out.add(ast.unparse(target))
    return out


def _assert_statements(node: ast.AST) -> list[ast.Assert]:
    return [stmt for stmt in ast.walk(node) if isinstance(stmt, ast.Assert)]


def _mentions(statements: list[ast.AST], needle: str) -> bool:
    """True when any of ``statements`` mentions ``needle`` as a whole dotted name.

    Compares dotted *tails*, not raw substrings, so a guard cannot satisfy the read-back by writing
    the attribute name inside a string literal or a comment.
    """
    tail = needle.rsplit(".", 1)[-1]
    return any(
        any(isinstance(n, ast.Attribute) and n.attr == tail for n in ast.walk(stmt))
        for stmt in statements
    )


@dataclass
class GuardVerdict:
    """Why a guard passed or failed. Empty ``problems`` means it satisfies the standard."""

    mutates_via: list[str] = field(default_factory=list)
    verifies_via: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def analyse_guard(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    helpers: dict[str, ast.FunctionDef] | None = None,
) -> GuardVerdict:
    """Decide whether ``fn`` satisfies R11: it must *mutate* and it must *verify*.

    Pure function of the AST, which is what lets the negative self-tests below feed it deliberately
    broken guards and require a rejection. ``helpers`` are the module-level functions the guard may
    delegate to; their mutations count as the guard's, one level deep.
    """
    helpers = helpers or {}
    calls = _called_names(fn)
    assigns = _assigns_to_attributes(fn)
    asserts = _assert_statements(fn)
    verdict = GuardVerdict()

    # Fold in one level of delegation: if the guard calls a same-module helper, that helper's
    # mutations are the guard's. Depth 1 is deliberate — a chain of helpers is a sign the mechanism
    # has grown too indirect to audit by reading the guard.
    for name in calls & helpers.keys():
        helper = helpers[name]
        helper_assigns = _assigns_to_attributes(helper)
        if helper_assigns:
            assigns |= helper_assigns
            verdict.mutates_via.append(
                f"delegated to {name}() which assigns {sorted(helper_assigns)}"
            )

    # -- 1. mutation ---------------------------------------------------------------------
    if DDL_MUTATION in calls:
        verdict.mutates_via.append(f"{DDL_MUTATION}()")
    if calls & SELF_VERIFYING_HELPERS:
        helpers_used = sorted(calls & SELF_VERIFYING_HELPERS)
        verdict.mutates_via.append(f"self-verifying helper {helpers_used}")
        # Each helper reads the DDL back and raises `MUTATION DID NOT LAND` on a no-op, so calling
        # one satisfies the verification requirement by itself. Guards 3, 5, 6 and 11-15 rely on
        # exactly this: they call a helper and then assert a consequence, never a separate
        # `assert_ddl_delta`.
        verdict.verifies_via.append(
            f"{helpers_used} read the DDL back and raise if it did not move"
        )
    if "execute" in calls:
        verdict.mutates_via.append("cursor.execute() raw DDL")
    if assigns:
        verdict.mutates_via.append(f"attribute assignment to {sorted(assigns)}")

    if not verdict.mutates_via:
        verdict.problems.append(
            "declares itself a mutation guard but mutates nothing: no schema_mutation(), no "
            "self-verifying helper, no cursor.execute(), and no assignment to an attribute. It can "
            "only be asserting whatever the schema already does, which is the round-1 H1 defect "
            "verbatim."
        )
        return verdict

    # -- 2. verification ------------------------------------------------------------------
    if calls & DDL_VERIFIERS:
        verdict.verifies_via.append(f"DDL read-back {sorted(calls & DDL_VERIFIERS)}")
    if "execute" in calls:
        # Raw DDL is self-verifying: SQLite raises if the statement does not apply.
        verdict.verifies_via.append("cursor.execute() raises loudly if the DDL does not apply")
    if assigns:
        # The read-back may re-fetch the field (`Model._meta.get_field(x).attr`) rather than reuse
        # the local, and may sit inside the `with` block rather than beside the assignment. Match on
        # the leaf attribute name anywhere in the guard's asserts. Guards 2, 4 and 7-10 do this.
        all_asserts = list(asserts)
        for name in calls & helpers.keys():
            all_asserts += _assert_statements(helpers[name])
        read_back = sorted(a for a in assigns if _mentions(all_asserts, a))
        if read_back:
            verdict.verifies_via.append(f"assert reads back {read_back}")
        else:
            verdict.problems.append(
                f"mutates {sorted(assigns)} but no `assert` reads the mutated attribute back, so "
                "nothing proves the assignment took effect on the object the real assertion reads. "
                "Add `assert model._meta.get_field(...).<attr> is <value>` inside the mutation."
            )
    return verdict


def _module_helpers() -> dict[str, ast.FunctionDef]:
    """Module-level helpers in the guards file, by name.

    A guard may legitimately delegate its mutation to a context manager defined beside it —
    `_swapped_on_delete` is how guards 7-10 flip `remote_field.on_delete`. Those helpers are
    part of the guard's own evidence, so they are analysed too and their mutation sites count
    as the guard's. Without this, a correct guard looks like a vacuous one.
    """
    tree = ast.parse(Path(inspect.getfile(guards)).read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("test_")
    }


def _guard_functions() -> list[ast.FunctionDef]:
    """Every ``test_mutation_*`` function in the guards module, parsed from its own source."""
    tree = ast.parse(Path(inspect.getfile(guards)).read_text(encoding="utf-8"))
    return [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_mutation_")
    ]


# ---------------------------------------------------------------------------------------
# The standard, applied to the guards that exist.
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("fn", _guard_functions(), ids=lambda fn: fn.name, scope="module")
def test_every_mutation_guard_mutates_and_verifies(fn: ast.FunctionDef) -> None:
    """Each guard must both change something and prove the change happened."""
    verdict = analyse_guard(fn, _module_helpers())
    assert not verdict.problems, (
        f"{fn.name} does not satisfy the R11 standard:\n  "
        + "\n  ".join(verdict.problems)
        + f"\n  detected mutation via: {verdict.mutates_via or 'NOTHING'}"
    )
    assert verdict.verifies_via, (
        f"{fn.name} mutates via {verdict.mutates_via} but has no verification site."
    )


@pytest.mark.parametrize("path", _all_test_files(), ids=lambda p: p.name)
def test_no_unverified_schema_mutation_anywhere(path: Path) -> None:
    """No test may edit the schema outside the verified mechanism."""
    if path.name == THIS_FILE:
        return
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None))
        in SILENT_NOOP_CALLS
    ]
    assert not offenders, (
        f"{path.name} performs schema mutations at line(s) {sorted(set(offenders))} without going "
        f"through schema_mutation(). On SQLite, schema_editor operations rebuild the table from "
        f"current model state, so an unverified mutation can be a silent no-op that still reports a "
        f"verified failure mode. Wrap it in schema_mutation() and assert_ddl_delta()."
    )


# ---------------------------------------------------------------------------------------
# The standard, tested against guards built to break it (round-3 H3-3).
#
# These are the real content of this file. Without them, `analyse_guard` is just a predicate
# nobody has checked — and an unchecked predicate is how the substring loophole survived two
# gate rounds. Each snippet below is the *shape* of a defect that actually occurred, or of the
# exact bypass round 3 demonstrated.
# ---------------------------------------------------------------------------------------


def _analyse_source(source: str) -> GuardVerdict:
    """Compile ``source`` as a module and analyse its single ``test_mutation_*`` function."""
    tree = ast.parse(source)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_mutation_")
    )
    return analyse_guard(fn)


#: Each case is a guard that must be REJECTED, with the defect it stands for.
VACUOUS_GUARDS: tuple[tuple[str, str, str], ...] = (
    (
        "mentions _meta but mutates nothing",
        # The exact round-3 H3-3 bypass: the old `any(m in src ...)` check passed this because
        # "_meta" appears in the body. There is no mutation of any kind.
        """
def test_mutation_fake_mentions_meta_only():
    index = Case._meta.indexes
    print("mutating", index)
""",
        "mutates nothing",
    ),
    (
        "mutates nothing and asserts a tautology",
        """
def test_mutation_fake_tautology():
    assert len(Case._meta.indexes) > 0
""",
        "mutates nothing",
    ),
    (
        "assigns to a bare local name, which is not a mutation",
        """
def test_mutation_fake_local_rebind():
    mutated = list(Case._meta.indexes)
    assert mutated is not None
""",
        "mutates nothing",
    ),
    (
        "mutates an attribute but never reads it back",
        # The subtler half of the standard: the mutation may be real, but without a read-back the
        # guard could be assigning to a detached or stale object and never notice.
        """
def test_mutation_unverified_attribute_edit():
    field = Task._meta.get_field("case")
    field.remote_field.on_delete = None
    assert True
""",
        "no `assert` reads the mutated attribute back",
    ),
    (
        "satisfies the read-back by mentioning the name in a string literal",
        """
def test_mutation_readback_in_a_string():
    field = Task._meta.get_field("case")
    field.remote_field.on_delete = None
    assert "on_delete" in "field.remote_field.on_delete = None"
""",
        "no `assert` reads the mutated attribute back",
    ),
    (
        "satisfies the read-back by mentioning the name in a comment",
        """
def test_mutation_readback_in_a_comment():
    field = Task._meta.get_field("case")
    field.remote_field.on_delete = None
    # on_delete was changed here
    assert True
""",
        "no `assert` reads the mutated attribute back",
    ),
)


@pytest.mark.parametrize(
    ("description", "source", "expected_fragment"),
    VACUOUS_GUARDS,
    ids=[case[0] for case in VACUOUS_GUARDS],
)
def test_the_standard_rejects_a_vacuous_guard(
    description: str, source: str, expected_fragment: str
) -> None:
    """A guard built to break the standard must be rejected — this is the round-3 H3-3 regression.

    The old substring implementation passed the first, fourth, fifth and sixth cases above.
    """
    verdict = _analyse_source(source)
    assert verdict.problems, (
        f"the analyser accepted a guard that {description}. That is exactly how the round-3 "
        f"substring loophole let a vacuous guard into the suite: the enforcement was weaker than "
        f"the standard. Detected mutation via: {verdict.mutates_via}"
    )
    assert any(expected_fragment in problem for problem in verdict.problems), (
        f"guard that {description} was rejected, but not for the expected reason "
        f"{expected_fragment!r}: {verdict.problems}"
    )


#: Guards that must be ACCEPTED. Without these, a fix that rejects everything would also pass.
SOUND_GUARDS: tuple[tuple[str, str], ...] = (
    (
        "verified DDL mutation with a DDL read-back",
        """
def test_mutation_good_ddl():
    with schema_mutation("task") as mutation:
        mutation.detach_constraint(Task, "uniq_case_title")
        mutation.assert_ddl_delta(missing=["UNIQUE"])
""",
    ),
    (
        "self-verifying helper, which verifies internally",
        """
def test_mutation_good_helper():
    with schema_mutation("task") as mutation:
        mutation.replace_index(Task, original, mutated)
""",
    ),
    (
        "attribute mutation with an explicit read-back",
        """
def test_mutation_good_attribute():
    field = Task._meta.get_field("case")
    field.remote_field.on_delete = models.SET_NULL
    assert Task._meta.get_field("case").remote_field.on_delete is models.SET_NULL
""",
    ),
    (
        "raw DDL, which raises loudly if it does not apply",
        """
def test_mutation_good_raw_ddl():
    with connection.cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS task_created_at_idx")
""",
    ),
)


@pytest.mark.parametrize(
    ("description", "source"), SOUND_GUARDS, ids=[case[0] for case in SOUND_GUARDS]
)
def test_the_standard_accepts_a_sound_guard(description: str, source: str) -> None:
    """The analyser must not be satisfiable by rejecting everything — see the cases above."""
    verdict = _analyse_source(source)
    assert not verdict.problems, (
        f"the analyser rejected a legitimate guard that {description}: {verdict.problems}"
    )
    assert verdict.mutates_via and verdict.verifies_via, (
        f"guard that {description} was accepted but not credited with both a mutation and a "
        f"verification: mutates={verdict.mutates_via}, verifies={verdict.verifies_via}"
    )


# ---------------------------------------------------------------------------------------
# Guarding the guard's own machinery.
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_mutation_helper_still_verifies() -> None:
    """Guard the guard: `schema_mutation` must still *behave* as though a no-op is a failure.

    Checked by execution, not by searching its source for the sentinel string. An earlier version
    read `inspect.getsource(schema_mutation)` and asserted ``"MUTATION DID NOT LAND" in src`` —
    which is the same substring-over-source technique this file exists to forbid, used to protect the
    mechanism that forbids it. It would also have passed if the string appeared only in a comment.

    So this runs the real thing: a context manager over a table whose DDL cannot change, with no
    verification call. `schema_mutation` must raise. If someone makes the exit check permissive,
    this fails.
    """
    # SQLite refuses to open a schema editor while FK constraint checks are on, so they are toggled
    # around it — the same dance `_mutation.py`'s callers perform via the pytest-django fixtures.
    with connection.constraint_checks_disabled():
        with pytest.raises(AssertionError, match="MUTATION DID NOT LAND"):
            with mutation_module.schema_mutation("django_migrations"):
                pass  # no mutation, and deliberately no assert_ddl_delta()

        # Positive control: the same no-op mutation *is* accepted once a verification call is made,
        # proving the assertion above detects the missing verification rather than some unrelated
        # error. `assert_ddl_delta()` with no needles re-reads the DDL and marks the delta checked;
        # `assert_changed()` would be wrong here, since it demands the DDL actually differ.
        with mutation_module.schema_mutation("django_migrations") as mutation:
            mutation.assert_ddl_delta()

    assert mutation_module.Mutation.assert_ddl_delta, "assert_ddl_delta() is the per-test standard."


def test_the_standard_does_not_regress_to_substring_matching() -> None:
    """The analyser must not hold any name bound from a guard's source text.

    The round-3 H3-3 implementation was ``any(m in src for m in VERIFIED_MECHANISMS)``, where
    ``src`` came from ``inspect.getsource(fn)``. The loophole is not the ``in`` operator — this
    file legitimately writes ``"execute" in calls``, comparing against a set of *call names* — it
    is having a string of source text in hand at all while deciding.

    So the check is for the *variable*, not the operator: any binding whose right-hand side calls
    ``getsource`` is flagged wherever it appears in the module. The loop below covers every
    assignment in this file, including any added later.
    """
    src = Path(__file__).read_text(encoding="utf-8")

    def _binds_getsource(node: ast.AST) -> bool:
        return any(
            isinstance(inner, ast.Call) and getattr(inner.func, "attr", "") == "getsource"
            for inner in ast.walk(node)
        )

    offenders = [
        node.lineno
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Assign) and _binds_getsource(node.value)
    ]
    offenders += [
        node.lineno
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.AnnAssign)
        and node.value is not None
        and _binds_getsource(node.value)
    ]
    assert not offenders, (
        f"line(s) {sorted(set(offenders))} bind a name from inspect.getsource() in this module. "
        f"Round-3 H3-3 passed because the analyser held a guard's source text and searched it for "
        f"mechanism names — the same could then happen inside analyse_guard(). Analyse the AST "
        f"(see test_the_analyser_never_reads_a_guard_as_text)."
    )


def test_the_analyser_never_reads_a_guard_as_text() -> None:
    """The strongest form of the same rule: the decision path must not read source at all.

    ``analyse_guard`` takes an AST node and returns a verdict. If it also took source text, the
    substring loophole would be one refactor away. This fails if that signature changes.
    """
    params = inspect.signature(analyse_guard).parameters
    assert "fn" in params, "analyse_guard must take the parsed function node"
    assert not any(
        name in params and any(word in name for word in ("src", "source", "text"))
        for name in params
    ), (
        f"analyse_guard() gained a text parameter {sorted(params)} — the analyser works from the "
        "AST alone, never from a guard's source text (round-3 H3-3)."
    )


def test_guard_file_docstring_states_it_is_permanent() -> None:
    """The guards must not drift back into 'delete me after the evidence' framing."""
    doc = inspect.getdoc(guards) or ""
    assert "permanent" in doc.lower(), (
        "test_schema_mutation_guards.py must document that it is a permanent regression guard. "
        "Earlier it was described as temporary scaffolding to delete after capturing evidence, which "
        "would have discarded the only artifact proving the conformance suite is not vacuous."
    )
