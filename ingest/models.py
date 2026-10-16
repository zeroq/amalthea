from __future__ import annotations

from django.db import models

from core.enums import SEVERITY_CHOICES, SEVERITY_MAX, SEVERITY_MIN, in_range
from core.models import TimeStampedModel, UUIDModel


class IngestionSource(UUIDModel, TimeStampedModel):
    """A configured webhook feed: its mapping rules, owned by one `Organisation`.

    `mapping_config` holds the JSON-path rules the mapping engine reads
    (`ingest/mapping.py`), and (REVIEW C4) the rule that produces each alert's `correlation_key`.

    **The source is org-owned** (ADR-009, reversing ADR-008 §4). It carries the mapping rules, and
    rules are a tenant's opinion — "our CrowdStrike calls `severity_name` Critical" — so the object
    that holds them belongs to the tenant that wrote them. `slug` is therefore unique *per
    organisation*, not globally: two tenants may each configure a `crowdstrike-edr` with different
    rules, and neither can see or edit the other's.

    Consequences worth stating once, because the alternative is a cross-tenant write:

    * `IngestionToken.organisation` must equal `source.organisation` (enforced in
      `IngestionToken.save`; a cross-table CHECK constraint cannot express it).
    * The receiver resolves *both* the feed and the tenant from one token, so the invariant above is
      what makes "token ⇒ source ⇒ org" unambiguous (`ingest/views.py`).
    """

    organisation = models.ForeignKey(
        "identity.Organisation",
        on_delete=models.PROTECT,
        related_name="ingestion_sources",
    )
    slug = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    mapping_config = models.JSONField(blank=True, default=dict)
    default_severity = models.SmallIntegerField(default=2, choices=list(SEVERITY_CHOICES))
    correlation_enabled = models.BooleanField(default=True)
    webhook_secret_hash = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "ingestion_source"
        constraints = [
            in_range(
                "ingestion_source_severity_range",
                "default_severity",
                SEVERITY_MIN,
                SEVERITY_MAX,
            ),
            # Replaces the old global `slug` uniqueness (ADR-009). Also the index the token form's
            # "reuse an existing profile" lookup rides on, so no separate index is warranted.
            models.UniqueConstraint(
                fields=["organisation", "slug"],
                name="ingestion_source_org_slug_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.slug
