from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.enums import (
    ALERT_STAGE_CHOICES,
    ALERT_STAGES,
    PAP_CHOICES,
    PAP_MAX,
    PAP_MIN,
    SEVERITY_CHOICES,
    SEVERITY_MAX,
    SEVERITY_MIN,
    TLP_CHOICES,
    TLP_MAX,
    TLP_MIN,
    in_range,
)
from core.models import TimeStampedModel, UUIDModel
from identity.models import Organisation, User
from ingest.models import IngestionSource


class AlertStatus(UUIDModel, TimeStampedModel):
    value = models.CharField(max_length=64, unique=True)
    # `choices` must be (value, label) pairs or a mapping — Django's own system check rejects a flat
    # iterable of strings. The pairs are *derived* from ALERT_STAGES rather than restated, because
    # `compat/enums.py` derives the TheHive labels from that same tuple; a hand-written copy here
    # could drift and silently accept a stage the wire contract never emits. The label is the value
    # itself: human-readable TheHive wording is applied at the wire boundary, not in the domain.
    stage = models.CharField(max_length=32, choices=ALERT_STAGE_CHOICES)
    order = models.IntegerField(default=0)
    description = models.TextField(blank=True)
    hidden = models.BooleanField(default=False)

    class Meta:
        db_table = "alert_status"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stage__in=ALERT_STAGES), name="alert_status_stage_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.value


class Alert(UUIDModel, TimeStampedModel):
    """One alert ingested from a source, or received over the TheHive-compatible API.

    `source` (wire string) vs `ingestion_source` (FK) — REVIEW-2026-10-03 **M3**:

        These two are **intentionally independent and must never be derived from one
        another.** `source` is the value TheHive's clients sent us; it is untrusted,
        caller-supplied text and is preserved verbatim so that `GET /api/v1/alert/{id}`
        round-trips exactly what the sender sent. `ingestion_source` is *our* record of
        which configured `IngestionSource` (webhook slug + secret + mapping config)
        accepted the alert, and is set only by the ingest pipeline. A caller that POSTs
        `source: "splunk"` to our own alert endpoint cannot create or claim an
        `IngestionSource` by doing so, and an ingested alert whose wire `source` happens
        to name some other tool is not silently relabelled. Treat a mismatch between the
        two as data, not as a bug to be reconciled.
    """

    type = models.CharField(max_length=100)
    source = models.CharField(max_length=100)
    source_ref = models.CharField(max_length=255)
    external_link = models.CharField(blank=True, max_length=500)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    severity = models.SmallIntegerField(default=2, choices=list(SEVERITY_CHOICES))
    # db_index=False: `status` is the left prefix of alert_status_date_idx (REVIEW M1).
    status = models.ForeignKey(
        AlertStatus, on_delete=models.PROTECT, related_name="alerts", db_index=False
    )
    # NOT NULL with a default: on Postgres `ORDER BY date DESC` returns NULLs first, so
    # an undated alert would top the triage queue (REVIEW L5).
    date = models.DateTimeField(default=timezone.now)
    tags = models.ManyToManyField(
        "cases.Tag", blank=True, related_name="alerts", through="AlertTagLink"
    )
    flag = models.BooleanField(default=False)
    tlp = models.SmallIntegerField(default=2, choices=list(TLP_CHOICES))
    pap = models.SmallIntegerField(default=2, choices=list(PAP_CHOICES))
    summary = models.TextField(blank=True)
    assignee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_alerts",
    )
    raw_payload = models.JSONField(blank=True, default=dict)
    correlation_key = models.CharField(max_length=256, blank=True, default="")
    ingestion_source = models.ForeignKey(
        IngestionSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alerts",
    )
    case = models.ForeignKey(
        "cases.Case",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alerts",
    )
    follow = models.BooleanField(default=False)
    ingestion_warnings = models.JSONField(blank=True, default=dict)
    owner_org = models.ForeignKey(
        Organisation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alerts",
    )

    class Meta:
        db_table = "alert"
        indexes = [
            models.Index(fields=["status", "-date"], name="alert_status_date_idx"),
            models.Index(fields=["correlation_key", "date"], name="alert_corrkey_date_idx"),
            models.Index(fields=["-created_at"], name="alert_created_at_idx"),
            # GIN index on raw_payload for efficient JSON querying (Postgres only)
            models.Index(
                fields=["raw_payload"],
                name="alert_raw_payload_gin",
                condition=None,
                opclasses=["gin_jsonb_ops"],
            ),
            # GIN index on ingestion_warnings for efficient JSON querying (Postgres only)
            models.Index(
                fields=["ingestion_warnings"],
                name="alert_ingestion_warnings_gin",
                condition=None,
                opclasses=["gin_jsonb_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "type", "source_ref"], name="uniq_alert_source_type_ref"
            ),
            in_range("alert_severity_range", "severity", SEVERITY_MIN, SEVERITY_MAX),
            in_range("alert_tlp_range", "tlp", TLP_MIN, TLP_MAX),
            in_range("alert_pap_range", "pap", PAP_MIN, PAP_MAX),
        ]

    def __str__(self) -> str:
        return self.title


class AlertCustomFieldValue(UUIDModel, TimeStampedModel):
    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name="custom_field_values")
    # REVIEW-2026-10-04 H-5: `db_index` was pruned here on the (wrong) theory that
    # `uniq_alert_custom_field(alert_id, custom_field_id)` covers this column. It does not:
    # that composite's LEFT prefix is `alert_id`. `WHERE custom_field_id = ?` — the reverse
    # "every value of this custom field" query — was a sequential scan until the implicit
    # index was restored by `alerts/migrations/0005_restore_custom_field_fk_indexes`.
    custom_field = models.ForeignKey(
        "cases.CustomField", on_delete=models.CASCADE, related_name="alert_values"
    )
    order = models.IntegerField(default=0)
    value = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "alert_custom_field_value"
        constraints = [
            models.UniqueConstraint(
                fields=["alert", "custom_field"], name="uniq_alert_custom_field"
            ),
        ]

    def __str__(self) -> str:
        return f"AlertCFV({self.alert_id})"


class AlertObservable(UUIDModel):
    """Join row linking an alert to a globally-deduped observable.

    Append-only: a link is created or deleted, never edited, so the row carries only
    ``created_at``. It declares that field directly rather than inheriting
    ``TimeStampedModel``, whose ``updated_at`` would be a column nothing ever writes — and
    whose ``created_at`` this model used to shadow (REVIEW-2026-10-03 **M7**).
    """

    # Both FKs are left-prefix-covered, so both implicit single-column indexes are redundant
    # (REVIEW-2026-10-04 **L-2**). Mirrors `cases.CaseObservable`, which is the same shape:
    # `alert` is the left prefix of `UNIQUE (alert, observable)`, and `observable` is the left
    # prefix of `alertobs_obs_alert_idx (observable, alert)`. Both reverse lookups are therefore
    # still served, by a wider index, at the cost of one fewer index to write on every row.
    # Restoring these was the over-correction half of H-5: the prune was wrong for
    # `*_custom_field_value`, but right here.
    alert = models.ForeignKey(
        Alert, on_delete=models.CASCADE, related_name="alert_observables", db_index=False
    )
    observable = models.ForeignKey(
        "observables.Observable",
        on_delete=models.CASCADE,
        related_name="alert_observables",
        db_index=False,
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_alert_observables",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "alert_observable"
        indexes = [
            # The reverse direction. AGENTS.md Module C's fan-out (`observable -> alerts`)
            # must not depend on an implicit index surviving a refactor (REVIEW M5).
            models.Index(fields=["observable", "alert"], name="alertobs_obs_alert_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["alert", "observable"], name="uniq_alert_observable"),
        ]

    def __str__(self) -> str:
        return f"AlertObs({self.alert_id}:{self.observable_id})"


class AlertTagLink(models.Model):
    """The join row behind `Alert.tags`. See `cases.CaseTagLink` for why it is declared.

    `alert` is the left prefix of the table's own `UNIQUE (alert, tag)`, so its implicit
    single-column index is redundant (REVIEW-2026-10-04 **L-2**); `tag` keeps its index
    because the reverse lookup is not covered.
    """

    alert = models.ForeignKey(
        Alert, on_delete=models.CASCADE, related_name="tag_links", db_index=False
    )
    tag = models.ForeignKey("cases.Tag", on_delete=models.CASCADE, related_name="alert_links")

    class Meta:
        db_table = "alert_tags"
        unique_together = (("alert", "tag"),)

    def __str__(self) -> str:
        return f"{self.alert_id}:{self.tag_id}"
