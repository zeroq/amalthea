from __future__ import annotations

import uuid

from django.db import models


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TaxonomyTerm(UUIDModel, TimeStampedModel):
    """Editable compiled-enum terms (Phase 2). Global, not org-scoped."""

    CATEGORY_CHOICES = (
        ("case_scope", "Case Scope"),
        ("case_phase", "Case Phase"),
        ("case_type", "Case Type"),
        ("data_classification", "Data Classification"),
        ("case_verdict", "Case Verdict"),
    )

    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES)
    code = models.CharField(max_length=64)
    label = models.CharField(max_length=128)
    order = models.IntegerField(default=0)
    hidden = models.BooleanField(default=False)
    protected = models.BooleanField(default=False)
    is_closing = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "code"], name="taxonomy_term_category_code_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=["category", "order"], name="taxonomy_term_cat_order_idx"),
        ]
        ordering = ["category", "order", "code"]

    def __str__(self) -> str:
        return f"{self.category}:{self.code}={self.label}"
