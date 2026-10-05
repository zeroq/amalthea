"""REVIEW-2026-10-03 **H3/H4** — bounded observable uniqueness and correct seeding.

**H3.** `Observable` was unique on `(data_type, normalized_data)`. `normalized_data` is an
unbounded `TextField`, so inside a btree index it hits Postgres's ~2704-byte tuple cap: on
prod, a long URL raised an integrity error *from the index*, at INSERT time, for a row that
was perfectly valid. Worse, on a non-`C` collation the comparison becomes locale-aware, so
for a case-sensitive type `C:\\Temp\\A.txt` and `c:\\temp\\a.txt` collided — silently
contradicting AC5.4. The unique key is now `(data_type, data_hash)`: a 64-character SHA-256
digest, collation-independent, bounded.

The original failure cannot be reproduced on SQLite, which has no btree tuple limit, so
these tests prove the *bound* (the indexed value is 64 characters no matter how long the
input is) and the *independence* (values that differ only in case coexist), and
`test_two_long_observables_coexist` shows two maximum-length inputs no longer collide.

**H4.** The seeded `hash` type was marked `is_case_sensitive=True`, inverting AC5.4 and
leaving no correctly-seeded case-sensitive type to test against.

Review items: H3 (unbounded index), H4 (inverted case sensitivity), round-2 H-7 (the 0003
backfill hashed the raw value instead of `canonical_value()`).
"""

from __future__ import annotations

import hashlib
import importlib

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader

from observables.hashing import canonical_value, data_hash
from observables.models import Observable, ObservableType

#: Comfortably past Postgres's 2704-byte btree tuple limit, and a realistic long URL.
LONG_VALUE = "https://cdn.example.com/very/long/path/" + "a" * 4000
CASE_SENSITIVE_PAIR = ("C:\\Temp\\A.txt", "c:\\temp\\a.txt")
#: Round-2 H-7. An uppercase hex hash for a type the runtime folds to lowercase — the exact
#: row the old backfill hashed wrong.
UPPERCASE_HASH = "D41D8CD98F00B204"


@pytest.fixture
def file_type() -> ObservableType:
    return ObservableType.objects.get_or_create(name="file", defaults={"is_case_sensitive": True})[
        0
    ]


@pytest.fixture
def hash_type() -> ObservableType:
    return ObservableType.objects.get_or_create(name="hash", defaults={"is_case_sensitive": False})[
        0
    ]


def test_the_digest_is_sha256_of_the_normalised_value() -> None:
    assert data_hash("1.1.1.1") == hashlib.sha256(b"1.1.1.1").hexdigest()
    assert len(data_hash("anything at all")) == 64


@pytest.mark.django_db
def test_data_hash_is_populated_and_bounded(file_type: ObservableType) -> None:
    long_observable = Observable.objects.create(
        data_type=file_type, data=LONG_VALUE, normalized_data=LONG_VALUE
    )
    long_observable.refresh_from_db()
    assert len(long_observable.data_hash) == 64
    assert long_observable.data_hash == data_hash(LONG_VALUE)
    assert len(long_observable.normalized_data) > 2704, "the payload really is oversized"


@pytest.mark.django_db
def test_two_long_observables_coexist(file_type: ObservableType) -> None:
    """H3's actual symptom: two valid long values collided on prod and one was rejected."""
    other = LONG_VALUE.replace("a" * 10, "a" * 10, 1) + "b"
    first = Observable.objects.create(
        data_type=file_type, data=LONG_VALUE, normalized_data=LONG_VALUE
    )
    second = Observable.objects.create(data_type=file_type, data=other, normalized_data=other)
    assert first.pk != second.pk
    assert Observable.objects.filter(data_type=file_type).count() == 2


@pytest.mark.django_db
def test_the_unique_key_is_the_hash_not_the_text(file_type: ObservableType) -> None:
    Observable.objects.create(
        data_type=file_type, data="C:\\Temp\\A.txt", normalized_data="C:\\Temp\\A.txt"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Observable.objects.create(
            data_type=file_type, data="C:\\Temp\\A.txt", normalized_data="C:\\Temp\\A.txt"
        )


@pytest.mark.django_db
def test_case_sensitive_values_that_differ_only_in_case_coexist(file_type: ObservableType) -> None:
    """AC5.4: a case-sensitive type must NOT normalise, so both spellings are distinct."""
    upper = Observable.objects.create(
        data_type=file_type, data=CASE_SENSITIVE_PAIR[0], normalized_data=CASE_SENSITIVE_PAIR[0]
    )
    lower = Observable.objects.create(
        data_type=file_type, data=CASE_SENSITIVE_PAIR[1], normalized_data=CASE_SENSITIVE_PAIR[1]
    )
    assert upper.normalized_data != lower.normalized_data
    assert upper.data_hash != lower.data_hash
    assert Observable.objects.filter(data_type=file_type).count() == 2


@pytest.mark.django_db
def test_hashes_normalise_case_insensitively(hash_type: ObservableType) -> None:
    """AC5.4: hex hashes are not case-sensitive, so both spellings are the same artifact."""
    Observable.objects.create(
        data_type=hash_type, data="D41D8CD98F00B204", normalized_data="d41d8cd98f00b204"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        Observable.objects.create(
            data_type=hash_type,
            data="D41D8CD98F00B204",
            normalized_data="D41D8CD98F00B204",
        )


@pytest.mark.django_db
def test_the_same_value_under_different_types_is_allowed(
    file_type: ObservableType, hash_type: ObservableType
) -> None:
    """`1.2.3.4` as a file name and as an IP are different observations."""
    Observable.objects.create(data_type=file_type, data="1.2.3.4", normalized_data="1.2.3.4")
    Observable.objects.create(data_type=hash_type, data="1.2.3.4", normalized_data="1.2.3.4")
    assert Observable.objects.count() == 2


@pytest.mark.django_db
def test_h4_hash_is_seeded_case_insensitive_and_file_is_not() -> None:
    """The seeded vocabulary must match AC5.4: only a file path is case-sensitive."""
    seeded = dict(ObservableType.objects.values_list("name", "is_case_sensitive"))
    assert seeded["hash"] is False, "H4: hex hashes are compared case-insensitively"
    assert seeded["file"] is True, "H4: a file path is the one genuinely case-sensitive type"
    assert not [name for name, flag in seeded.items() if flag == "hash"], seeded


# ---------------------------------------------------------------------------------------
# Round-2 H-7 (TODO 1.17) — the 0003 backfill must agree with the runtime.
#
# It used to hash raw `normalized_data` while `DataHashField` hashes
# `canonical_value(row)`. For a case-insensitive type the two disagree, so a migrated
# uppercase hash carried a digest the runtime would never produce — and
# `uniq_obs_dtype_hash`, the *only* constraint whose job is to prevent that, admitted a
# second row for the same artifact.
# ---------------------------------------------------------------------------------------


def h7_backfill():
    """The `RunPython` callable from migration 0003, imported by name."""
    return importlib.import_module(
        "observables.migrations.0003_observable_data_hash"
    ).backfill_data_hash


def test_h7_the_backfill_calls_the_runtime_canonicalisation() -> None:
    """Pin the *mechanism*, so the two implementations cannot drift apart again.

    A corrected copy of the formula would satisfy the behavioural test below today and
    diverge the next time `canonical_value()` changes. Requiring the migration to call the
    runtime's own function is what makes "they agree" a structural property rather than a
    coincidence, and it fails loudly on SQLite even though the bug is only *observable* on
    production data.
    """
    backfill = h7_backfill()
    referenced = backfill.__code__.co_names
    assert "canonical_value" in referenced, (
        f"the 0003 backfill must call observables.hashing.canonical_value(), got {referenced}"
    )
    assert "data_hash" in referenced, "and it must hash through the same helper"
    module = importlib.import_module(backfill.__module__)
    assert module.canonical_value is canonical_value
    assert module.data_hash is data_hash


@pytest.mark.django_db
def test_h7_a_backfilled_row_satisfies_the_same_invariant_as_a_runtime_row(
    hash_type: ObservableType,
) -> None:
    """The behavioural half: reproduce the bug, repair it, and prove the duplicate closes.

    Steps deliberately, because each one is a fact:
      1. write the digest the *old* backfill produced — it really does differ;
      2. show the runtime then admits a second row for the same artifact (the AC5.4 hole);
      3. re-run the fixed backfill — the row now carries the runtime's digest;
      4. the same duplicate is rejected, so the migrated row and a runtime row are now the
         same artifact as far as the constraint is concerned.
    """
    migrated = Observable.objects.create(
        data_type=hash_type, data=UPPERCASE_HASH, normalized_data=UPPERCASE_HASH
    )
    stale = hashlib.sha256(UPPERCASE_HASH.encode("utf-8")).hexdigest()
    Observable.objects.filter(pk=migrated.pk).update(data_hash=stale)
    assert Observable.objects.get(pk=migrated.pk).data_hash == stale
    assert stale != data_hash(canonical_value(Observable.objects.get(pk=migrated.pk))), (
        "if the old formula now agrees with the runtime, this test is not exercising H-7"
    )

    lowercase = UPPERCASE_HASH.lower()
    admitted = Observable.objects.create(
        data_type=hash_type, data=lowercase, normalized_data=lowercase
    )
    assert admitted.pk, "the stale digest let a duplicate through — that is the H-7 defect"
    Observable.objects.filter(pk=admitted.pk).delete()

    # A real `apps` registry from the migration graph, so the backfill runs against the
    # model shape the migration gave it rather than against the live one.
    h7_backfill()(MigrationLoader(connection).project_state().apps, None)

    repaired = Observable.objects.get(pk=migrated.pk)
    assert repaired.data_hash == data_hash(canonical_value(repaired)), (
        "the backfill still does not produce the runtime's digest"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        (
            Observable.objects.create(
                data_type=hash_type, data=lowercase, normalized_data=lowercase
            ),
            "a migrated row and a runtime row of one artifact must not both be admitted",
        )


@pytest.mark.django_db
def test_h7_the_backfill_is_case_sensitive_for_a_case_sensitive_type(
    file_type: ObservableType,
) -> None:
    """The other half of AC5.4: `file` must NOT be folded, by the migration either.

    Without this, a fix that simply case-folded everything would pass the test above while
    breaking the case-sensitive type.
    """
    path = CASE_SENSITIVE_PAIR[0]
    migrated = Observable.objects.create(data_type=file_type, data=path, normalized_data=path)
    stale_digest = "0" * 64
    Observable.objects.filter(pk=migrated.pk).update(data_hash=stale_digest)
    assert stale_digest != data_hash(canonical_value(Observable.objects.get(pk=migrated.pk)))

    h7_backfill()(MigrationLoader(connection).project_state().apps, None)

    repaired = Observable.objects.get(pk=migrated.pk)
    assert repaired.data_hash == data_hash(path), "a file path must be hashed as written"
    assert repaired.data_hash != data_hash(path.lower())


# --------------------------------------------------------------------------------------
# REVIEW-2026-10-04 round-2 **M-5** (TODO 1.19): `DataHashField` was skipped by
# `update_fields`, so the dedup key went stale.
#
# `DataHashField.pre_save` recomputes the digest on every write, which is what lets
# `normalized_data` be caller-writable at all. But `save(update_fields=[...])` writes only
# the columns it is handed, so the recomputed attribute never reached the row:
#
#   o.normalized_data = "/tmp/bbb.bin"
#   o.save(update_fields=["normalized_data"])
#   o.refresh_from_db().data_hash   # -> digest of "/tmp/aaa.bin"
#
# `data_hash` is the right half of `uniq_obs_dtype_hash(data_type, data_hash)`. A stale
# digest means the row no longer collides with the artifact it *used* to be, and no longer
# collides with the one it now *is* — the single constraint that prevents a duplicate
# forensic artifact from entering the graph twice. `QuerySet.update()` has the same hole
# and cannot be closed here; it never calls `save()` or `pre_save()`.
# --------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_m5_a_partial_save_recomputes_the_digest(file_type: ObservableType) -> None:
    # S108 noqa: these are string literals stored as observable *data* to exercise digest
    # recomputation; nothing is ever opened. Not temp-file usage.
    before, after = "/tmp/aaa.bin", "/tmp/bbb.bin"  # noqa: S108
    observable = Observable.objects.create(data_type=file_type, data=before, normalized_data=before)
    assert observable.data_hash == data_hash(before)

    observable.normalized_data = after
    observable.save(update_fields=["normalized_data"])
    observable.refresh_from_db()

    assert observable.data_hash == data_hash(after), (
        "save(update_fields=['normalized_data']) left data_hash at the previous digest. "
        "uniq_obs_dtype_hash is then keyed on a value that describes a different artifact."
    )


@pytest.mark.django_db
def test_m5_a_partial_save_of_data_type_recomputes_the_digest(
    file_type: ObservableType,
) -> None:
    """`data_type` alone changes the digest, because case-folding is a property of the type."""
    folded_type = ObservableType.objects.create(name="path-folded", is_case_sensitive=False)
    value = "C:\\Temp\\A.txt"

    observable = Observable.objects.create(data_type=folded_type, data=value, normalized_data=value)
    assert observable.data_hash == data_hash(value.lower())

    observable.data_type = file_type  # now case-sensitive: digest of the value as written
    observable.save(update_fields=["data_type"])
    observable.refresh_from_db()

    assert observable.data_hash == data_hash(value), (
        "moving a row to a case-sensitive type must re-derive the digest; canonical_value() "
        "stops case-folding and the stored hash no longer describes the row."
    )


@pytest.mark.django_db
def test_m5_an_unrelated_partial_save_leaves_the_digest_alone(
    file_type: ObservableType,
) -> None:
    """The widened `update_fields` must not become a blanket full-row write."""
    observable = Observable.objects.create(
        data_type=file_type,
        data="p",
        normalized_data="/tmp/p.bin",  # noqa: S108 — string data, not I/O
    )
    expected = observable.data_hash

    observable.message = "seen on host web-01"
    observable.save(update_fields=["message"])
    observable.refresh_from_db()

    assert observable.message == "seen on host web-01"
    assert observable.data_hash == expected


@pytest.mark.django_db
def test_m5_a_stale_digest_no_longer_admits_a_duplicate_artifact(
    file_type: ObservableType,
) -> None:
    """The consequence, not just the mechanism: the dedup guarantee must actually hold.

    The harm from a stale digest is not a false positive on the *old* value — a row that
    still claims the old digest keeps rejecting it, which is the safe direction. It is that
    the row the analyst actually edited becomes invisible: the row now holds `second` but
    is indexed under `first`, so a second copy of `second` is admitted alongside it and the
    graph ends up with two observables for one artifact — the exact outcome
    `uniq_obs_dtype_hash` exists to prevent.
    """
    # S108 noqa: string data, not a path used for I/O — see above.
    first, second = "/tmp/first.bin", "/tmp/second.bin"  # noqa: S108
    edited = Observable.objects.create(data_type=file_type, data=first, normalized_data=first)

    edited.normalized_data = second
    edited.save(update_fields=["normalized_data"])
    edited.refresh_from_db()

    with pytest.raises(IntegrityError), transaction.atomic():
        (
            Observable.objects.create(data_type=file_type, data=second, normalized_data=second),
            (
                "this row already describes /tmp/second.bin, so re-adding it must be rejected. "
                "It is admitted only if data_hash went stale on the partial save above, leaving "
                "the edited row indexed under its previous value."
            ),
        )
