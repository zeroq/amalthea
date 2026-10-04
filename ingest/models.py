from __future__ import annotations

from django.db import models

from core.enums import SEVERITY_CHOICES, SEVERITY_MAX, SEVERITY_MIN, in_range
from core.models import TimeStampedModel, UUIDModel


class IngestionSource(UUIDModel, TimeStampedModel):
    """A configured webhook feed: `{slug}` in the URL, plus its mapping rules.

    `mapping_config` holds the JSON-path rules the Phase 4 mapping engine reads, and
    (REVIEW C4) the rule that produces each alert's `correlation_key`.
    """

    slug = models.CharField(max_length=100, unique=True)
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
        ]

    def __str__(self) -> str:
        return self.slug
