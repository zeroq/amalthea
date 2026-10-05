from __future__ import annotations

from typing import Any

from django.db import models, transaction

from core.enums import (
    PAP_CHOICES,
    PAP_MAX,
    PAP_MIN,
    TLP_CHOICES,
    TLP_MAX,
    TLP_MIN,
    in_range,
)
from core.models import TimeStampedModel, UUIDModel

from .hashing import DataHashField


class ObservableType(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    is_attachment = models.BooleanField(default=False)
    # Drives normalization: lowercase unless the type is case-sensitive. Only `file` is
    # seeded case-sensitive (REVIEW H4); `hash` must NOT be, or AC5.4 ("case hashes
    # normalize to lowercase") is inverted.
    is_case_sensitive = models.BooleanField(default=False)

    class Meta:
        db_table = "observable_type"

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Re-hash this type's observables when the case rule changes (round-3 **H3-1**).

        `is_case_sensitive` is an input to `Observable.data_hash`, and ADR-002 §D4 makes the
        vocabulary analyst-extensible. Nothing else observed a change to it: `Observable.save()`
        recomputes when the *row's* `normalized_data` or `data_type` changes, but a row that is not
        itself saved keeps the digest computed under the old rule. That left the unique constraint
        blind — flipping `file` to case-insensitive and re-inserting the same path admitted a
        duplicate, because the pre-existing row's digest still described the old spelling.

        So the flip is intercepted here and the affected rows are re-hashed. It happens *after*
        `super().save()` so that a refusal (two rows that would collapse into one artifact) leaves
        the flag itself unchanged — the analyst gets an error naming the collision, not a vocabulary
        that silently stopped matching its data. Both writes share one `atomic` block so that
        "unchanged" is true in autocommit too, not only inside a caller's transaction.

        Only the flag is intercepted. `name` changes are cosmetic, and `is_attachment` feeds no
        derived state.
        """
        from .rehash import rehash_for_type

        # Read the persisted value before writing, so this is a no-op on INSERT and on a save that
        # does not touch the flag (the overwhelming majority).
        previous: bool | None = None
        if not self._state.adding:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("is_case_sensitive", flat=True)
                .first()
            )
        needs_rehash = previous is not None and previous != self.is_case_sensitive
        if not needs_rehash:
            super().save(*args, **kwargs)
            return

        # One transaction for the flag and the rows it describes. Without this, autocommit would
        # persist the new flag and only *then* discover a collision, leaving a vocabulary that no
        # longer matches its data — the exact defect this hook exists to prevent.
        with transaction.atomic():
            super().save(*args, **kwargs)
            rehash_for_type(self, case_sensitive=self.is_case_sensitive)


class Observable(UUIDModel, TimeStampedModel):
    """A globally deduplicated forensic artifact (AGENTS.md Module C).

    Unique on ``(data_type, data_hash)``, not ``(data_type, normalized_data)`` — see
    `observables.hashing` for the two Postgres-only failure modes that motivates it.
    `normalized_data` stays on the row (it is what analysts read and what enrichment
    compares against); only the *indexed* form is the digest.
    """

    # db_index=False: `data_type` is the left prefix of uniq_obs_dtype_hash below (M1).
    data_type = models.ForeignKey(
        ObservableType, on_delete=models.PROTECT, related_name="observables", db_index=False
    )
    data = models.TextField()
    normalized_data = models.TextField()
    data_hash = DataHashField()
    tags = models.ManyToManyField(
        "cases.Tag", blank=True, related_name="observables", through="ObservableTagLink"
    )
    ioc = models.BooleanField(default=False)
    sighted = models.BooleanField(default=False)
    sighted_at = models.DateTimeField(null=True, blank=True)
    ignore_similarity = models.BooleanField(default=False)
    message = models.TextField(blank=True)
    tlp = models.SmallIntegerField(default=2, choices=list(TLP_CHOICES))
    pap = models.SmallIntegerField(default=2, choices=list(PAP_CHOICES))
    enrichment_data = models.JSONField(blank=True, default=dict)
    external = models.BooleanField(default=False)

    class Meta:
        db_table = "observable"
        constraints = [
            models.UniqueConstraint(fields=["data_type", "data_hash"], name="uniq_obs_dtype_hash"),
            in_range("observable_tlp_range", "tlp", TLP_MIN, TLP_MAX),
            in_range("observable_pap_range", "pap", PAP_MIN, PAP_MAX),
        ]
        indexes = [
            models.Index(fields=["-created_at"], name="obs_created_at_idx"),
        ]

    def __str__(self) -> str:
        return self.data

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep `data_hash` in step when the caller saves only some columns.

        `DataHashField.pre_save` recomputes the digest on every write, which is what makes
        it safe for `normalized_data` to be caller-writable. But a save carrying
        `update_fields` writes *only* those columns, so `o.save(update_fields=[
        "normalized_data"])` left `data_hash` pointing at the previous value — and
        `data_hash` is the right half of `uniq_obs_dtype_hash(data_type, data_hash)`, the
        only constraint standing between a case file and a duplicate artifact. That made
        the stale row collide with, or silently miss, a genuinely new artifact. Adding the
        column here is what makes the field's promise hold for partial saves too.

        `data_type` counts as a trigger as well, because `canonical_value()` case-folds
        according to `data_type.is_case_sensitive`; moving a row between a case-sensitive
        and a case-insensitive type changes its hash with `normalized_data` untouched.

        Not covered, and not coverable here: `QuerySet.update()` never runs `pre_save` or
        `save()`. Bulk-patching `normalized_data` must go through a save loop or recompute
        the digest in the same expression.
        """
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            touched = {f if isinstance(f, str) else f.name for f in update_fields}
            if touched & {"normalized_data", "data_type"} and "data_hash" not in touched:
                kwargs["update_fields"] = [*update_fields, "data_hash"]
        super().save(*args, **kwargs)


class ObservableTagLink(models.Model):
    """The join row behind `Observable.tags`. See `cases.CaseTagLink` for why it is declared.

    `observable` is the left prefix of `UNIQUE (observable, tag)`, so its implicit index is
    redundant (REVIEW 2026-10-04 **L-2**); `tag` keeps its index for the reverse lookup.
    """

    observable = models.ForeignKey(
        Observable, on_delete=models.CASCADE, related_name="tag_links", db_index=False
    )
    tag = models.ForeignKey("cases.Tag", on_delete=models.CASCADE, related_name="observable_links")

    class Meta:
        db_table = "observable_tags"
        unique_together = (("observable", "tag"),)

    def __str__(self) -> str:
        return f"{self.observable_id}:{self.tag_id}"
