"""Re-deriving `Observable.data_hash` when the *vocabulary* changes (REVIEW round-3 **H3-1**).

The defect
----------
`ObservableType.is_case_sensitive` decides whether `canonical_value()` case-folds a value before
hashing it, so it is an input to `Observable.data_hash`. `Observable.save()` recomputes the digest
when the row's own `normalized_data` or `data_type` changes, and `DataHashField.pre_save` covers
every insert path. But **nothing observed a change to the flag itself**, and ADR-002 §D4 makes the
vocabulary analyst-extensible. So an analyst who reclassified `file` as case-insensitive left
every stored digest describing the *previous* rule:

    create `/Tmp/A.bin` under case-sensitive `file`   -> digest of "/Tmp/A.bin"
    flip `file.is_case_sensitive = False`             -> nothing recomputes
    create `/tmp/a.bin`                               -> digest of "/tmp/a.bin"
    => uniq_obs_dtype_hash ADMITS BOTH. Two rows, one artifact.

Verified by execution, not by reading: `tests/conformance/test_observable_rehash.py`.

Why the collision cannot be resolved automatically
-------------------------------------------------
Re-hashing can make two rows that were legitimately distinct become identical — that is the *point*
of flipping to case-insensitive. Deleting or merging one would destroy forensic evidence, and
`Observable` rows are the shared pivot of Module C: they carry `case_observables` and
`alert_observables` links, IOC flags, TLP markings and enrichment data. Silently collapsing them
would either drop those links or reassign an analyst's TLP marking to a different artifact. This is
a decision for a human, so :func:`rehash_for_type` **refuses** and reports.

The same reasoning applies to the data migration in `0005_rehash_observable_data_hash`: it runs on
the pre-fix state, where the bug has not yet been exercised in anger, so there is nothing to
resolve — but it deliberately refuses rather than guessing if it ever finds a collision.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from django.db import transaction

from .hashing import canonical_value_under, data_hash
from .models import Observable, ObservableType


class RehashCollisionError(Exception):
    """Raised when re-hashing a type would collapse two existing observables into one.

    Carries the colliding groups so an operator can resolve them deliberately. Never raised after a
    partial write: the check runs inside the same transaction as the update, so a refusal leaves the
    database untouched.
    """

    def __init__(self, observable_type_name: str, groups: Sequence[Sequence[str]]) -> None:
        self.observable_type_name = observable_type_name
        self.groups = [tuple(sorted(group)) for group in groups]
        detail = "; ".join(" == ".join(repr(value) for value in group) for group in self.groups)
        super().__init__(
            f"re-hashing observable type {observable_type_name!r} would collapse "
            f"{sum(len(g) for g in self.groups)} existing observables into "
            f"{len(self.groups)} artifact(s): {detail}. These rows are distinct today and are "
            f"linked from cases and alerts, so merging them is a judgement call: resolve them "
            f"deliberately, or pick a case rule that keeps them distinct."
        )


@dataclass
class RehashResult:
    """What a re-hash actually did. `updated` counts rows written; `collisions` is normally empty."""

    observable_type_name: str
    case_sensitive: bool
    updated: int = 0
    scanned: int = 0
    collisions: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.updated > 0


def _collisions_under(
    rows: Sequence[tuple[str, str]], *, case_sensitive: bool
) -> list[tuple[str, ...]]:
    """Groups of ``(pk, normalized_data)`` whose new digests would coincide.

    ``pk`` is stringified so the result is a plain, loggable, JSON-friendly structure; these are
    surfaced to an operator and must not hold model instances.
    """
    by_digest: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for pk, normalized in rows:
        digest = data_hash(canonical_value_under(normalized, case_sensitive=case_sensitive))
        by_digest.setdefault(digest, []).append(pk)
        labels[pk] = normalized
    return [tuple(sorted(labels[pk] for pk in pks)) for pks in by_digest.values() if len(pks) > 1]


def rehash_for_type(
    observable_type: ObservableType,
    *,
    case_sensitive: bool,
    using: str | None = None,
) -> RehashResult:
    """Recompute `data_hash` for every `Observable` of ``observable_type`` under a new case rule.

    Returns a :class:`RehashResult`, or raises :class:`RehashCollisionError` **before writing anything**
    if the new rule would merge existing rows. The scan, the collision check and the writes run in
    one transaction, so the refusal cannot leave a half-re-hashed vocabulary behind.

    Bypassing this: ``QuerySet.update()`` and ``bulk_create()`` do not run ``save()``, so a caller
    that flips the flag with ``.update()`` gets no re-hash. That is the same documented gap
    `Observable.save()` already carries for `normalized_data`; the flag belongs to the vocabulary
    and is changed through the admin or this function, not through a queryset patch.
    """
    name = str(getattr(observable_type, "name", observable_type))
    result = RehashResult(observable_type_name=name, case_sensitive=case_sensitive)

    observable_model: type[Observable] = Observable
    # The read, the collision check and the writes share one transaction. Splitting the preflight out
    # would leave a window in which a concurrent insert lands between "no collisions" and the write,
    # which is the same silent-duplicate this module exists to prevent. On PostgreSQL the re-hash
    # additionally needs `select_for_update` on the type row to be fully serialised; SQLite's
    # write lock covers this path in the test suite.
    with transaction.atomic(using=using):
        rows = list(
            observable_model.objects.using(using)
            .filter(data_type=observable_type)
            .values_list("pk", "normalized_data")
        )
        result.scanned = len(rows)

        collisions = _collisions_under(
            [(str(pk), normalized) for pk, normalized in rows],
            case_sensitive=case_sensitive,
        )
        if collisions:
            result.collisions = collisions
            raise RehashCollisionError(name, collisions)

        for pk, normalized in rows:
            digest = data_hash(canonical_value_under(normalized, case_sensitive=case_sensitive))
            # `update()` is correct here and deliberate: the digest is derived state we have just
            # computed explicitly, and going through `save()` would re-read the *old* flag from the
            # cached `data_type` relation, undoing the very change being applied.
            observable_model.objects.using(using).filter(pk=pk).update(data_hash=digest)
            result.updated += 1
    return result
