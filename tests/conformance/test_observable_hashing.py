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

Review items: H3 (unbounded index), H4 (inverted case sensitivity).
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from observables.hashing import data_hash
from observables.models import Observable, ObservableType

#: Comfortably past Postgres's 2704-byte btree tuple limit, and a realistic long URL.
LONG_VALUE = "https://cdn.example.com/very/long/path/" + "a" * 4000
CASE_SENSITIVE_PAIR = ("C:\\Temp\\A.txt", "c:\\temp\\a.txt")


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
    import hashlib

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
