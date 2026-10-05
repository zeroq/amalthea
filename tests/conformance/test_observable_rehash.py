"""Round-3 **H3-1**: a vocabulary change must not leave stored digests stale.

The defect this file closes was reproduced by execution before it was fixed. With one row present:

    create `/Tmp/A.bin` under the case-sensitive `file` type   -> digest of "/Tmp/A.bin"
    flip `file.is_case_sensitive = False`                       -> (nothing recomputed)
    create `/tmp/a.bin`                                         -> digest of "/tmp/a.bin"
    => `uniq_obs_dtype_hash` ADMITS BOTH. Two rows, one artifact.

The same stale-digest class as round-2 `H-7`, one level out: `H-7` was a backfill using the wrong
hash function, this is a *live* rule change that nothing observed. ADR-002 §D4 is what makes it
reachable — the observable vocabulary is analyst-extensible, so `is_case_sensitive` is a value an
analyst can legitimately edit, not a constant.

Two mechanisms are covered:

* `ObservableType.save()` — intercepts the flip and re-hashes the affected rows.
* `observables/migrations/0005_rehash_observable_data_hash.py` — repairs rows already stale.

And the refusal path: re-hashing can legitimately *collapse* two rows that were distinct, and that
is a decision for a human because observables carry case/alert links, IOC flags and TLP markings.
"""

from __future__ import annotations

import ast
from importlib import import_module
from pathlib import Path

import pytest
from django.db import IntegrityError, transaction

from observables import models as models_module
from observables import rehash as rehash_module
from observables.hashing import canonical_value_under, data_hash
from observables.models import Observable, ObservableType
from observables.rehash import RehashCollisionError, rehash_for_type

# S108 noqa: string literals stored as observable *data* to exercise digest normalization. They are
# never opened, read or written — `observables/hashing.py` operates on strings only, so there is no
# filesystem interaction to make unsafe. Bound as named constants to keep the assertions readable
# and to satisfy the lint once, in one place.
FOLDED_A = "/tmp/a.bin"  # noqa: S108
FOLDED_B = "/tmp/b.bin"  # noqa: S108
UPPER_A = "/Tmp/A.bin"
LOWER_A = "/tmp/a.bin"  # noqa: S108
REPORT_UPPER = "/Tmp/Report.PDF"
REPORT_LOWER = "/tmp/report.pdf"  # noqa: S108
VAR_B = "/var/tmp/b.bin"  # noqa: S108
VAR_C = "/var/tmp/c.bin"  # noqa: S108


@pytest.fixture
def case_sensitive_type() -> ObservableType:
    return ObservableType.objects.create(name="rehash-file", is_case_sensitive=True)


@pytest.fixture
def folded_type() -> ObservableType:
    return ObservableType.objects.create(name="rehash-folded", is_case_sensitive=False)


def _add(observable_type: ObservableType, value: str) -> Observable:
    return Observable.objects.create(data_type=observable_type, data=value, normalized_data=value)


# ---------------------------------------------------------------------------------------
# The defect, and its fix.
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_h3_1_flipping_the_case_rule_never_admits_a_duplicate(
    case_sensitive_type: ObservableType,
) -> None:
    """The exact reproduced defect: one row exists, the flag flips, the same artifact re-enters."""
    existing = _add(case_sensitive_type, UPPER_A)
    assert existing.data_hash == data_hash(UPPER_A), "precondition: hashed case-sensitively"

    case_sensitive_type.is_case_sensitive = False
    case_sensitive_type.save()

    # The stored digest now follows the new rule...
    existing.refresh_from_db()
    assert existing.data_hash == data_hash(LOWER_A), (
        "the pre-existing row's digest still describes the old case rule"
    )
    # ...so the same artifact can no longer be inserted a second time.
    with pytest.raises(IntegrityError), transaction.atomic():
        _add(case_sensitive_type, LOWER_A)


@pytest.mark.django_db
def test_h3_1_rehash_rewrites_every_row_of_the_type(
    case_sensitive_type: ObservableType,
) -> None:
    """All rows move together, not just the one that happens to be touched."""
    values = [UPPER_A, VAR_B, VAR_C]
    rows = [_add(case_sensitive_type, value) for value in values]
    case_sensitive_type.is_case_sensitive = False
    case_sensitive_type.save()

    for row, value in zip(rows, values, strict=True):
        row.refresh_from_db()
        assert row.data_hash == data_hash(value.casefold()), f"{value} kept its old-case digest"


@pytest.mark.django_db
def test_h3_1_flipping_does_not_touch_other_types(
    case_sensitive_type: ObservableType, folded_type: ObservableType
) -> None:
    """Re-hashing is scoped to the type whose rule changed."""
    mine = _add(case_sensitive_type, UPPER_A)
    # Already folded, so its digest is identical before and after the other type's flip.
    theirs = _add(folded_type, FOLDED_B)

    case_sensitive_type.is_case_sensitive = False
    case_sensitive_type.save()

    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert theirs.data_hash == data_hash(FOLDED_B), "an unrelated type was re-hashed"
    assert mine.data_hash == data_hash(LOWER_A)


@pytest.mark.django_db
def test_h3_1_saving_without_touching_the_flag_is_a_no_op(
    case_sensitive_type: ObservableType,
) -> None:
    """The common case must not rewrite digests — an unrelated edit is not a rule change."""
    row = _add(case_sensitive_type, UPPER_A)
    before = row.data_hash

    case_sensitive_type.name = "rehash-file-renamed"
    case_sensitive_type.save()

    row.refresh_from_db()
    assert row.data_hash == before == data_hash(UPPER_A)


@pytest.mark.django_db
def test_h3_1_inserting_a_type_never_triggers_a_rehash() -> None:
    """`previous is None` on INSERT: a brand-new type has no rows to re-hash."""
    fresh = ObservableType.objects.create(name="rehash-brand-new", is_case_sensitive=True)
    assert Observable.objects.filter(data_type=fresh).count() == 0
    fresh.is_case_sensitive = False
    fresh.save()
    assert ObservableType.objects.get(pk=fresh.pk).is_case_sensitive is False


# ---------------------------------------------------------------------------------------
# The refusal path: collapsing two real rows is a human decision.
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_h3_1_a_colliding_flip_is_refused_and_the_flag_is_unchanged(
    case_sensitive_type: ObservableType,
) -> None:
    """Two genuinely distinct rows must not be merged by a flag flip.

    Deleting or merging one would destroy forensic evidence: observables are the shared pivot of
    Module C and carry `case_observables` / `alert_observables` links, IOC flags and TLP markings.
    So the save raises, and — because the re-hash runs *after* `super().save()` — the flag itself
    is rolled back, leaving the vocabulary consistent with its data.
    """
    upper = _add(case_sensitive_type, UPPER_A)
    lower = _add(case_sensitive_type, LOWER_A)
    assert upper.data_hash != lower.data_hash, "precondition: distinct under the old rule"

    case_sensitive_type.is_case_sensitive = False
    with pytest.raises(RehashCollisionError) as excinfo:
        with transaction.atomic():
            case_sensitive_type.save()

    assert "rehash-file" in str(excinfo.value)
    # The message names the artifact *values*, not primary keys: an operator resolving this needs to
    # recognise "/Tmp/A.bin", not a UUID. See `test_rehash_collision_names_the_values`.
    assert UPPER_A in str(excinfo.value) and LOWER_A in str(excinfo.value)

    assert ObservableType.objects.get(pk=case_sensitive_type.pk).is_case_sensitive is True, (
        "the flag was left changed while its rows still describe the old rule"
    )
    upper.refresh_from_db()
    lower.refresh_from_db()
    assert upper.data_hash == data_hash(UPPER_A)
    assert lower.data_hash == data_hash(LOWER_A)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_h3_1_a_colliding_flip_rolls_back_the_flag_in_autocommit(
    case_sensitive_type: ObservableType,
) -> None:
    """The refusal must hold without the caller wrapping the save in a transaction.

    `transaction=True` gives real autocommit semantics rather than pytest-django's wrapping
    `atomic` block, which is what makes this distinct from the test above: `save()` writes the flag
    *before* re-hashing, so in autocommit the flag would be committed and only then would the
    collision be discovered — leaving a vocabulary that no longer matches its rows, which is the
    defect this whole change exists to close.
    """
    upper = _add(case_sensitive_type, UPPER_A)
    lower = _add(case_sensitive_type, LOWER_A)

    case_sensitive_type.is_case_sensitive = False
    with pytest.raises(RehashCollisionError):
        case_sensitive_type.save()

    assert ObservableType.objects.get(pk=case_sensitive_type.pk).is_case_sensitive is True, (
        "the flag was committed before the collision was detected"
    )
    upper.refresh_from_db()
    lower.refresh_from_db()
    assert upper.data_hash == data_hash(UPPER_A)
    assert lower.data_hash == data_hash(LOWER_A)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_h3_1_a_non_colliding_flip_succeeds(case_sensitive_type: ObservableType) -> None:
    """The refusal must be narrow: rows that stay distinct re-hash cleanly."""
    _add(case_sensitive_type, UPPER_A)
    _add(case_sensitive_type, VAR_B)

    case_sensitive_type.is_case_sensitive = False
    case_sensitive_type.save()

    assert ObservableType.objects.get(pk=case_sensitive_type.pk).is_case_sensitive is False


@pytest.mark.django_db
def test_h3_1_refusal_writes_nothing(case_sensitive_type: ObservableType) -> None:
    """A refused re-hash must not have partially written digests before raising."""
    rows = [_add(case_sensitive_type, UPPER_A), _add(case_sensitive_type, LOWER_A)]
    before = {r.pk: r.data_hash for r in rows}

    with pytest.raises(RehashCollisionError), transaction.atomic():
        rehash_for_type(case_sensitive_type, case_sensitive=False)

    for row in rows:
        row.refresh_from_db()
        assert row.data_hash == before[row.pk]


# ---------------------------------------------------------------------------------------
# The service directly.
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_rehash_for_type_reports_what_it_did(case_sensitive_type: ObservableType) -> None:
    """The result object is what an operator reads, so it must be accurate."""
    _add(case_sensitive_type, UPPER_A)
    _add(case_sensitive_type, VAR_B)

    result = rehash_for_type(case_sensitive_type, case_sensitive=False)
    assert result.observable_type_name == "rehash-file"
    assert result.scanned == 2
    assert result.updated == 2
    assert result.changed is True


@pytest.mark.django_db
def test_rehash_reports_no_change_when_already_correct(folded_type: ObservableType) -> None:
    """Idempotent: a second pass must not report work, and must not corrupt anything."""
    row = _add(folded_type, FOLDED_A)
    again = rehash_for_type(folded_type, case_sensitive=False)
    assert again.scanned == 1
    row.refresh_from_db()
    assert row.data_hash == data_hash(FOLDED_A)


@pytest.mark.django_db
def test_rehash_collision_names_the_values(case_sensitive_type: ObservableType) -> None:
    """An operator has to be able to tell *which* artifacts are in conflict."""
    _add(case_sensitive_type, REPORT_UPPER)
    _add(case_sensitive_type, REPORT_LOWER)

    with pytest.raises(RehashCollisionError) as excinfo:
        rehash_for_type(case_sensitive_type, case_sensitive=False)

    message = str(excinfo.value)
    assert REPORT_UPPER in message and REPORT_LOWER in message


# ---------------------------------------------------------------------------------------
# The data migration that repairs pre-existing staleness.
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_h3_1_the_data_migration_repairs_an_already_stale_row(
    case_sensitive_type: ObservableType,
) -> None:
    """Rows written *before* the fix are repaired by 0005, not left stale.

    The staleness is forced with `update()`, which bypasses `save()` and therefore the
    interception — the same gap `Observable.save()` already documents for `normalized_data`, and
    exactly how the real defect arose.

    The migration body is invoked directly rather than through `MigrationExecutor.migrate()`:
    pytest-django has already applied 0005 during setup, so an executor call would be a no-op and
    the test would pass without ever running the code it claims to cover.
    """
    row = _add(case_sensitive_type, UPPER_A)
    correct = data_hash(UPPER_A)
    # Force staleness the way the real defect did: the stored digest describes the *other* rule.
    Observable.objects.filter(pk=row.pk).update(data_hash=data_hash(LOWER_A))
    row.refresh_from_db()
    assert row.data_hash == data_hash(LOWER_A) != correct

    # `forwards` is data-only and never uses `schema_editor`; passing one would wrap the call
    # in an atomic block, so the expected RuntimeError below would poison the transaction and the
    # follow-up "nothing was written" queries would fail for the wrong reason.
    _migration_0005().forwards(_FakeApps(Observable, ObservableType), None)

    row.refresh_from_db()
    assert row.data_hash == correct, "the migration left the stale digest in place"


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_h3_1_the_data_migration_refuses_to_merge_rows(
    case_sensitive_type: ObservableType,
) -> None:
    """A migration has no business merging observables, so it raises rather than guessing.

    The collision is reached the way the real defect produced it: the flag is flipped with
    `update()`, bypassing the interception, so the rows disagree with their type's rule while
    staying distinct on disk. Under the new rule they would both canonicalise to "/tmp/a.bin".
    """
    upper = _add(case_sensitive_type, UPPER_A)
    lower = _add(case_sensitive_type, LOWER_A)
    assert upper.data_hash != lower.data_hash, "precondition: distinct on disk"

    ObservableType.objects.filter(pk=case_sensitive_type.pk).update(is_case_sensitive=False)

    with pytest.raises(RuntimeError, match="refuses to merge observables"):
        _migration_0005().forwards(_FakeApps(Observable, ObservableType), None)

    # Nothing was written.
    upper.refresh_from_db()
    lower.refresh_from_db()
    assert upper.data_hash == data_hash(UPPER_A)
    assert lower.data_hash == data_hash(LOWER_A)


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_h3_1_the_data_migration_is_a_no_op_on_a_healthy_database(
    folded_type: ObservableType,
) -> None:
    """The common path must cost no writes: only genuinely stale rows are touched."""
    row = _add(folded_type, FOLDED_A)
    correct = row.data_hash

    # `forwards` is data-only and never uses `schema_editor`; passing one would wrap the call
    # in an atomic block, so the expected RuntimeError below would poison the transaction and the
    # follow-up "nothing was written" queries would fail for the wrong reason.
    _migration_0005().forwards(_FakeApps(Observable, ObservableType), None)

    row.refresh_from_db()
    assert row.data_hash == correct


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_h3_1_the_data_migration_is_idempotent() -> None:
    """Re-running forwards converges rather than corrupting: the digest is derived state."""
    folded = ObservableType.objects.create(name="rehash-idem", is_case_sensitive=False)
    row = _add(folded, FOLDED_A)
    expected = data_hash(canonical_value_under(FOLDED_A, case_sensitive=False))
    assert row.data_hash == expected

    for _ in range(2):
        _migration_0005().forwards(_FakeApps(Observable, ObservableType), None)
        row.refresh_from_db()
        assert row.data_hash == expected


def _migration_0005() -> object:
    """Import the data migration by path; its module name starts with a digit."""
    return import_module("observables.migrations.0005_rehash_observable_data_hash")


# ---------------------------------------------------------------------------------------
# Source-level guards for the atomicity claims.
#
# Mutation-verified: removing the `with transaction.atomic()` wrapper from `rehash_for_type` leaves
# every behavioural test green (16 passed), because a sequential suite cannot observe a window
# between "no collisions" and the write. R11 applies to the claims in the docstrings too, so the
# guarantee is pinned statically instead — with a negative self-test below, so the detector itself
# cannot be vacuous.
# ---------------------------------------------------------------------------------------


def _atomic_blocks(func: ast.FunctionDef) -> list[ast.With]:
    """Every ``with ...atomic(...):`` block directly reachable in ``func``."""
    blocks: list[ast.With] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            expr = item.context_expr
            if not isinstance(expr, ast.Call):
                continue
            target = expr.func
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name == "atomic":
                blocks.append(node)
                break
    return blocks


def _find_function(source: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node  # type: ignore[return-value]
    raise AssertionError(f"no function named {name!r} found")


def _contains_call(node: ast.AST, fragment: str) -> bool:
    return any(
        fragment in ast.unparse(call) for call in ast.walk(node) if isinstance(call, ast.Call)
    )


def _model_save_source() -> str:
    return (Path(models_module.__file__)).read_text(encoding="utf-8")


def test_h3_1_rehash_scan_check_and_write_share_one_transaction() -> None:
    """`rehash_for_type` must keep the scan, the collision check and the writes in one atomic block.

    Separating the preflight would reopen the window this module closes: a concurrent insert landing
    between "no collisions" and the write is exactly the silent duplicate of H3-1.
    """
    source = Path(rehash_module.__file__).read_text(encoding="utf-8")
    blocks = _atomic_blocks(_find_function(source, "rehash_for_type"))

    assert len(blocks) == 1, f"expected exactly one atomic block, found {len(blocks)}"
    block = blocks[0]
    assert _contains_call(block, "values_list"), "the scan is outside the atomic block"
    assert _contains_call(block, "_collisions_under"), (
        "the collision check is outside the atomic block — a concurrent insert could slip in"
    )
    assert _contains_call(block, ".update("), "the digest writes are outside the atomic block"


def test_h3_1_observable_type_save_atomic_covers_flag_and_rehash() -> None:
    """The flag write and the re-hash must be in the *same* atomic block, not two separate ones.

    Two blocks would not help: `super().save()` would still commit in autocommit before the
    collision was discovered, which is the autocommit case
    `test_h3_1_a_colliding_flip_rolls_back_the_flag_in_autocommit` covers behaviourally.
    """
    save = _find_function(_model_save_source(), "save")
    blocks = [
        block
        for block in _atomic_blocks(save)
        if _contains_call(block, "super().save(") and _contains_call(block, "rehash_for_type(")
    ]

    assert len(blocks) == 1, (
        f"expected one atomic block covering both the flag write and the re-hash, found {len(blocks)}"
    )


def test_h3_1_the_atomicity_detector_can_actually_fail() -> None:
    """Negative control: the two guards above must be capable of failing.

    Without this, a detector that returned `True` unconditionally — or that silently stopped parsing
    — would report the atomicity claims as verified while checking nothing. This is the H3-3 lesson
    applied to H3-1's own guard.
    """
    without_atomic = "def f():\n    rows = list(qs)\n    return rows\n"
    with_atomic = "def f():\n    with transaction.atomic():\n        return list(qs)\n"

    assert _atomic_blocks(_find_function(without_atomic, "f")) == [], (
        "detector claims an atomic block where there is none"
    )
    assert len(_atomic_blocks(_find_function(with_atomic, "f"))) == 1, (
        "detector fails to see a real atomic block"
    )

    # And it must distinguish "atomic block present" from "the calls are inside it".
    split = (
        "def f():\n"
        "    with transaction.atomic():\n"
        "        super().save()\n"
        "    rehash_for_type(self)\n"
    )
    block = _atomic_blocks(_find_function(split, "f"))[0]
    assert not _contains_call(block, "rehash_for_type("), (
        "detector ignores block boundaries — a call outside the `with` would pass"
    )


class _FakeApps:
    """Minimal `apps` shim so the migration body can be called directly with real models."""

    def __init__(self, *models_: object) -> None:
        self._models = {m.__name__: m for m in models_}  # type: ignore[attr-defined]

    def get_model(self, _app_label: str, model_name: str) -> object:
        return self._models[model_name]
