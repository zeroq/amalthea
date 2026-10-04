from __future__ import annotations

from django.db import models
from django.utils import timezone

from core.enums import (
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
    in_values,
)
from core.models import TimeStampedModel, UUIDModel
from identity.models import Organisation, User
from ingest.models import IngestionSource


class AlertStatus(UUIDModel, TimeStampedModel):
    value = models.CharField(max_length=64, unique=True)
    stage = models.CharField(max_length=32, choices=[(s, s) for s in ALERT_STAGES])
    order = models.IntegerField(default=0)
    description = models.TextField(blank=True)
    hidden = models.BooleanField(default=False)

    class Meta:
        db_table = "alert_status"
        constraints = [
            in_values("alert_status_stage_valid", "stage", ALERT_STAGES),
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
    # NOT NULL by design: the unique constraint below is unconditional, so the pipeline
    # must ALWAYS populate a ref. When the mapping yields none it synthesises
    # "sha256:<digest of the canonicalised payload>" and records the substitution in
    # ingestion_warnings — see ingest/references.py (REVIEW C3).
    source_ref = models.CharField(max_length=255)
    external_link = models.CharField(max_length=500, blank=True)
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
    tags = models.ManyToManyField("cases.Tag", blank=True, related_name="alerts")
    flag = models.BooleanField(default=False)
    tlp = models.SmallIntegerField(default=2, choices=list(TLP_CHOICES))
    pap = models.SmallIntegerField(default=2, choices=list(PAP_CHOICES))
    summary = models.TextField(blank=True)
    assignee = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_alerts"
    )
    raw_payload = models.JSONField(blank=True, default=dict)
    # REVIEW C4: the correlation hot path had no column to index — AC5.2's query
    # compiled to JSON extraction over `raw_payload` plus a temp b-tree. This is a
    # namespaced, portable, indexable key (e.g. "dest_ip:10.0.0.5") written by the
    # IngestionSource's mapping rule; AGENTS.md §4.2's "same destination IP within
    # 10 minutes" becomes an index seek on (correlation_key, date).
    correlation_key = models.CharField(max_length=256, blank=True, default="")
    # REVIEW M2: was `source_id`, which generated the column `source_id_id`. Renamed
    # (plan §13 #12); the column is now `ingestion_source_id`. See the class docstring
    # for why `source` and `ingestion_source` are deliberately not the same value.
    ingestion_source = models.ForeignKey(
        IngestionSource, on_delete=models.SET_NULL, null=True, blank=True, related_name="alerts"
    )
    case = models.ForeignKey(
        "cases.Case", on_delete=models.SET_NULL, null=True, blank=True, related_name="alerts"
    )
    follow = models.BooleanField(default=False)
    ingestion_warnings = models.JSONField(blank=True, default=dict)
    owner_org = models.ForeignKey(
        Organisation, on_delete=models.SET_NULL, null=True, blank=True, related_name="alerts"
    )

    class Meta:
        db_table = "alert"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "type", "source_ref"], name="uniq_alert_source_type_ref"
            ),
            in_range("alert_severity_range", "severity", SEVERITY_MIN, SEVERITY_MAX),
            in_range("alert_tlp_range", "tlp", TLP_MIN, TLP_MAX),
            in_range("alert_pap_range", "pap", PAP_MIN, PAP_MAX),
        ]
        indexes = [
            models.Index(fields=["status", "-date"], name="alert_status_date_idx"),
            models.Index(fields=["correlation_key", "date"], name="alert_corrkey_date_idx"),
            models.Index(fields=["-created_at"], name="alert_created_at_idx"),
        ]

    def __str__(self) -> str:
        return self.title


class AlertCustomFieldValue(UUIDModel, TimeStampedModel):
    # db_index=False on both FKs: the unique constraint below is a left prefix for each.
    alert = models.ForeignKey(
        Alert, on_delete=models.CASCADE, related_name="custom_field_values", db_index=False
    )
    custom_field = models.ForeignKey(
        "cases.CustomField", on_delete=models.CASCADE, related_name="alert_values", db_index=False
    )
    order = models.IntegerField(default=0)
    value = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "alert_custom_field_value"
        constraints = [
            # One value per (alert, custom_field): a retried write must not silently
            # duplicate rows (REVIEW M4).
            models.UniqueConstraint(
                fields=["alert", "custom_field"], name="uniq_alert_custom_field"
            ),
        ]

    def __str__(self) -> str:
        return f"AlertCFV({self.alert_id})"


class AlertObservable(UUIDModel, TimeStampedModel):
    # db_index=False on `alert`: left prefix of uniq_alert_observable below.
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
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_alert_observables",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "alert_observable"
        indexes = [
            # Reverse direction: `observable -> alerts`, same rationale as M5 on cases.
            models.Index(fields=["observable", "alert"], name="alertobs_obs_alert_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["alert", "observable"], name="uniq_alert_observable"),
        ]

    def __str__(self) -> str:
        return f"AlertObs({self.alert_id}:{self.observable_id})"
