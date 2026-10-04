from __future__ import annotations

from django.db import models

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
    tags = models.ManyToManyField("cases.Tag", blank=True, related_name="observables")
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
