from __future__ import annotations

from typing import Any

from django.db import models, router
from django.db.models import Q
from django.utils import timezone

from core.enums import (
    CASE_STAGES,
    CUSTOM_FIELD_TYPES,
    PAP_CHOICES,
    PAP_MAX,
    PAP_MIN,
    SEVERITY_CHOICES,
    SEVERITY_MAX,
    SEVERITY_MIN,
    TASK_STATUS_CHOICES,
    TLP_CHOICES,
    TLP_MAX,
    TLP_MIN,
    in_range,
    in_values,
)
from core.models import TimeStampedModel, UUIDModel
from identity.models import Organisation, User

from .numbering import (
    ALLOCATED_MARKER,
    AllocatedNumberField,
    AllocatingQuerySet,
    allocate_case_number,
)


class CaseStatus(UUIDModel, TimeStampedModel):
    value = models.CharField(max_length=64, unique=True)
    stage = models.CharField(max_length=32, choices=[(s, s) for s in CASE_STAGES])
    order = models.IntegerField(default=0)
    description = models.TextField(blank=True)
    hidden = models.BooleanField(default=False)

    class Meta:
        db_table = "case_status"
        constraints = [
            in_values("case_status_stage_valid", "stage", CASE_STAGES),
        ]

    def __str__(self) -> str:
        return self.value


class Tag(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    colour = models.CharField(max_length=20, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "tag"

    def __str__(self) -> str:
        return self.name


class CustomField(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    group = models.CharField(max_length=100, blank=True)
    type = models.CharField(max_length=50, choices=CUSTOM_FIELD_TYPES)
    options = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "custom_field"
        constraints = [
            in_values("custom_field_type_valid", "type", tuple(t for t, _ in CUSTOM_FIELD_TYPES)),
        ]

    def __str__(self) -> str:
        return self.name


class Case(UUIDModel, TimeStampedModel):
    """A security case. `number` is the human reference and is allocated, not supplied.

    `db_table` is `case_record`, not `case`: `case` is a reserved SQL word and Phase 8's
    query engine writes raw SQL (REVIEW-2026-10-03 M6). Recorded as a plan deviation.
    """

    # REVIEW-2026-10-03 C1: allocated from the Postgres sequence (or MAX()+1 on SQLite)
    # by AllocatedNumberField.pre_save, so `bulk_create()` cannot bypass it either.
    number = AllocatedNumberField(unique=True, editable=False)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    severity = models.SmallIntegerField(default=2, choices=list(SEVERITY_CHOICES))
    # db_index=False: the implicit FK index would duplicate case_status_start_idx,
    # which has `status` as its left prefix (REVIEW M1).
    status = models.ForeignKey(
        CaseStatus,
        on_delete=models.PROTECT,
        related_name="cases",
        db_index=False,
    )
    assignee = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_cases"
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="cases", through="CaseTagLink")
    flag = models.BooleanField(default=False)
    tlp = models.SmallIntegerField(default=2, choices=list(TLP_CHOICES))
    pap = models.SmallIntegerField(default=2, choices=list(PAP_CHOICES))
    summary = models.TextField(blank=True)
    # NOT NULL with a default: on Postgres `ORDER BY start_date DESC` returns NULLs
    # FIRST, so a nullable start_date put undated cases at the top of the queue (L5).
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)
    closed_date = models.DateTimeField(null=True, blank=True)
    owner_org = models.ForeignKey(
        Organisation, on_delete=models.SET_NULL, null=True, blank=True, related_name="cases"
    )
    ingestion_warnings = models.JSONField(blank=True, default=dict)

    objects = AllocatingQuerySet.as_manager()

    class Meta:
        db_table = "case_record"
        indexes = [
            models.Index(fields=["status", "-start_date"], name="case_status_start_idx"),
            models.Index(fields=["-severity"], name="case_severity_idx"),
            models.Index(fields=["-created_at"], name="case_created_at_idx"),
        ]
        constraints = [
            in_range("case_severity_range", "severity", SEVERITY_MIN, SEVERITY_MAX),
            in_range("case_tlp_range", "tlp", TLP_MIN, TLP_MAX),
            in_range("case_pap_range", "pap", PAP_MIN, PAP_MAX),
        ]

    def __str__(self) -> str:
        return f"Case({self.number}): {self.title}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # `number` is NOT NULL in the database but is `None` on the Python object until
        # it is allocated: `IntegerField.empty_strings_allowed` is False, so there is no
        # `""` sentinel and this guard genuinely fires. django-stubs types the attribute
        # as `int`, which makes a type checker believe the branch below is dead; the
        # explicit `int | None` annotation is what makes the guard type-check *correctly*
        # without touching the logic (REVIEW C1, mypy `unreachable`).
        number: int | None = self.number
        if number is None:
            self.number = allocate_case_number(using=router.db_for_write(type(self), instance=self))
            # Tell the field's `pre_save` this number came from the sequence, so it does
            # not issue a redundant `setval` on every single case write (REVIEW-2026-10-04
            # H-6). A number that arrived *with* the caller is deliberately not marked:
            # that is the case the sequence must be advanced for.
            setattr(self, ALLOCATED_MARKER, True)
        self.stamp_closed_date()
        super().save(*args, **kwargs)

    def stamp_closed_date(self) -> None:
        """Set `closed_date` on the transition into the Closed stage (REVIEW L4).

        TheHive's `closedDate` is the ledger's "how long did this take" anchor, so it is
        stamped once, on entry, and never clobbered afterwards. Reopening a case is a
        Phase 5 concern that may clear it explicitly.
        """
        if self.status_id and self.closed_date is None and self.status.stage == "Closed":
            self.closed_date = timezone.now()


class Task(UUIDModel, TimeStampedModel):
    # No case->task composite index exists, so the implicit FK index is kept (M1).
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    group = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, default="Waiting", choices=list(TASK_STATUS_CHOICES))
    flag = models.BooleanField(default=False)
    assignee = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_tasks"
    )
    order = models.IntegerField(default=0)
    due_date = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    mandatory = models.BooleanField(default=False)

    class Meta:
        db_table = "task"
        indexes = [
            models.Index(fields=["-created_at"], name="task_created_at_idx"),
        ]
        constraints = [
            in_values("task_status_valid", "status", tuple(s for s, _ in TASK_STATUS_CHOICES)),
        ]

    def __str__(self) -> str:
        return self.title


class TimelineEvent(UUIDModel, TimeStampedModel):
    # db_index=False: `case` is the left prefix of timeline_case_date_idx (M1).
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, related_name="timeline_events", db_index=False
    )
    date = models.DateTimeField()
    end_date = models.DateTimeField(null=True, blank=True)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=50, default="comment")
    actor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="timeline_events"
    )
    metadata = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "timeline_event"
        indexes = [
            # Trailing `-id` makes keyset pagination deterministic when automation events
            # share microsecond precision (REVIEW M9).
            models.Index(fields=["case", "-date", "-id"], name="timeline_case_date_idx"),
        ]

    def __str__(self) -> str:
        return self.title


class CaseCustomFieldValue(UUIDModel, TimeStampedModel):
    # REVIEW-2026-10-03 M1: `case` keeps db_index=False because uniq_case_custom_field
    # below has it as its **left** prefix, so the composite serves `WHERE case_id = ?`.
    # `custom_field` does NOT: it is the **right-hand** column of that constraint, so the
    # composite cannot serve `WHERE custom_field_id = ?` — a lookup the plan's custom-field
    # API needs ("every case carrying this field"). Dropping its implicit FK index turned
    # that query into a sequential scan (round-2 H-5), so the index stays.
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, related_name="custom_field_values", db_index=False
    )
    custom_field = models.ForeignKey(
        CustomField,
        on_delete=models.CASCADE,
        related_name="case_values",
    )
    order = models.IntegerField(default=0)
    value = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "case_custom_field_value"
        constraints = [
            # One value per (case, custom_field): a retried write must not silently
            # duplicate rows (REVIEW M4).
            models.UniqueConstraint(fields=["case", "custom_field"], name="uniq_case_custom_field"),
        ]

    def __str__(self) -> str:
        return f"CaseCFV({self.case_id})"


class CaseObservable(UUIDModel):
    """Join row linking a case to a globally-deduped observable.

    Append-only: a link is created or deleted, never edited, so the row carries only
    ``created_at``. It declares that field directly rather than inheriting
    ``TimeStampedModel``, whose ``updated_at`` would be a column nothing ever writes — and
    whose ``created_at`` this model used to shadow (REVIEW-2026-10-03 **M7**).
    """

    # db_index=False on `case`: left prefix of uniq_case_observable below.
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, related_name="case_observables", db_index=False
    )
    observable = models.ForeignKey(
        "observables.Observable",
        on_delete=models.CASCADE,
        related_name="case_observables",
        db_index=False,
    )
    added_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="added_case_observables",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "case_observable"
        indexes = [
            # The reverse direction. AGENTS.md Module C's fan-out (`observable -> cases`)
            # must not depend on an implicit index surviving a refactor (REVIEW M5).
            models.Index(fields=["observable", "case"], name="caseobs_obs_case_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["case", "observable"], name="uniq_case_observable"),
        ]

    def __str__(self) -> str:
        return f"CaseObs({self.case_id}:{self.observable_id})"


class CaseTagLink(models.Model):
    """The join row behind `Case.tags`. Declared explicitly so its indexes are controlled.

    Django's implicit M2M table would give `case` a single-column index that is already
    left-prefix-covered by the table's own `UNIQUE (case, tag)` — redundant write amplification on
    a hot table (REVIEW-2026-10-04 **L-2**). `tag` keeps its index because the reverse lookup
    ("every case carrying this tag") is not covered by the composite.
    """

    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, related_name="tag_links", db_index=False
    )
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="case_links")

    class Meta:
        db_table = "case_record_tags"
        unique_together = (("case", "tag"),)

    def __str__(self) -> str:
        return f"{self.case_id}:{self.tag_id}"


class Comment(UUIDModel, TimeStampedModel):
    """A first-class comment on a case or an alert (T2 P2, plan §6-Q1).

    TheHive 5 exposes comments as their own entity (`POST /case/{id}/comment`,
    `GET/POST /alert/{id}/comment`, `PATCH|DELETE /comment/{id}`) rather than as a view over the
    case ledger, so this is its own row rather than a `TimelineEvent` projection: a comment's
    `_id` is stable across a note being edited, while a ledger entry is append-only.

    Exactly one of `case`/`alert` is set — a comment belongs to one parent and the CHECK below
    makes "no parent" and "two parents" unrepresentable rather than a convention.
    """

    # db_index=False on both parents: each is the left prefix of its own composite index below.
    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="comments",
        db_index=False,
    )
    alert = models.ForeignKey(
        "alerts.Alert",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="comments",
        db_index=False,
    )
    message = models.TextField()
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="comments"
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="edited_comments"
    )

    class Meta:
        db_table = "comment"
        indexes = [
            models.Index(fields=["case", "-created_at", "-id"], name="comment_case_created_idx"),
            models.Index(fields=["alert", "-created_at", "-id"], name="comment_alert_created_idx"),
        ]
        constraints = [
            # Exactly one parent. TheHive's comment is case- or alert-scoped; a row with both would
            # be reachable from either and render twice in a case flow.
            models.CheckConstraint(
                condition=(
                    Q(case__isnull=False, alert__isnull=True)
                    | Q(case__isnull=True, alert__isnull=False)
                ),
                name="comment_one_parent",
            ),
        ]

    def __str__(self) -> str:
        parent = f"case={self.case_id}" if self.case_id else f"alert={self.alert_id}"
        return f"Comment({parent})"


class Page(UUIDModel, TimeStampedModel):
    """A TheHive case page: Markdown content attached to a case (plan §6-P2, spike §5).

    TheHive 5 exposes `POST /case/{caseId}/page`, `GET|PATCH|DELETE /case/{caseId}/page/{pageId}`.
    The stored shape follows the recorded `OutputPage`: `title`, `content` (Markdown), `order`
    and `category`. It is deliberately not a `TimelineEvent` projection — a page is mutable state,
    not an append-only ledger entry.
    """

    # db_index=False: `case` is the left prefix of `page_case_order_idx`.
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="pages", db_index=False)
    title = models.CharField(max_length=500)
    content = models.TextField(blank=True)
    order = models.IntegerField(default=0)
    category = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="pages"
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="edited_pages"
    )

    class Meta:
        db_table = "page"
        indexes = [
            models.Index(fields=["case", "order", "created_at"], name="page_case_order_idx"),
        ]

    def __str__(self) -> str:
        return f"Page({self.case_id}): {self.title}"


class Share(UUIDModel, TimeStampedModel):
    """A case shared with another organisation (plan §6-P2).

    TheHive shares are **case-only** (`/api/v1/case/{caseId}/shares`); alerts have no share route,
    so the plan's speculative "alert" half is dropped (recorded in `deviations.md`). Access is
    **default-deny** and this model can only ever *add* access for one organisation — it never
    widens the owner organisation's scope.

    `permissions` is a JSON bag holding `{"read": true, "write": <bool>}`. A missing/absent
    `write` key means read-only, so a share created without an explicit write grant can read but
    never mutate; that is the failure mode AC6.1-P2-b exists to prove.
    """

    # db_index=False: `case` is the left prefix of uniq_share_case_org.
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="shares", db_index=False)
    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="case_shares"
    )
    permissions = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_shares"
    )

    class Meta:
        db_table = "share"
        constraints = [
            models.UniqueConstraint(fields=["case", "organisation"], name="uniq_share_case_org"),
        ]

    @property
    def can_write(self) -> bool:
        return bool(self.permissions.get("write"))

    def __str__(self) -> str:
        return f"Share({self.case_id} -> {self.organisation_id})"


class CaseTemplate(UUIDModel, TimeStampedModel):
    """A reusable case blueprint (T2 P4; TheHive `OutputCaseTemplate`).

    Deliberately holds its defaults as **JSON definitions**, not as linked rows: a template is
    copied into a case, never shared with it, so a task default or a custom-field default is
    data the template owns outright. Modelling them as FK collections would make editing a
    template retroactively rewrite history on every case it ever seeded.

    `tags` is a list of names (not `Tag` rows) for the same reason — applying a template creates
    the tags it needs on the case.
    """

    name = models.CharField(max_length=100, unique=True)
    display_name = models.CharField(max_length=200, blank=True)
    title_prefix = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    severity = models.SmallIntegerField(default=2, choices=list(SEVERITY_CHOICES))
    tlp = models.SmallIntegerField(default=2, choices=list(TLP_CHOICES))
    pap = models.SmallIntegerField(default=2, choices=list(PAP_CHOICES))
    flag = models.BooleanField(default=False)
    summary = models.TextField(blank=True)
    # `[{"title", "group", "description", "order", "mandatory"}]`.
    tasks = models.JSONField(default=list, blank=True)
    # `[{"name", "value"}]` against `CustomField.name`.
    custom_fields = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "case_template"
        constraints = [
            in_range("case_template_severity_range", "severity", SEVERITY_MIN, SEVERITY_MAX),
            in_range("case_template_tlp_range", "tlp", TLP_MIN, TLP_MAX),
            in_range("case_template_pap_range", "pap", PAP_MIN, PAP_MAX),
        ]

    def __str__(self) -> str:
        return self.name


class Attachment(UUIDModel, TimeStampedModel):
    """A file attached to a case (T2 P3; TheHive `OutputAttachment`).

    The blob on disk is named **opaquely** (`<prefix>/<uuid4hex><ext>`) and never after the
    client's filename: `name` is untrusted display data, and a path built from it is the traversal
    sink AC6.1-P3-a exists to catch. `sha256` is recorded at write time so a download can be proven
    to return the same bytes (AC6.1-P3-b) and a future dedupe can key on content, not name.
    """

    # db_index=False: `case` is the left prefix of attach_case_created_idx.
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, related_name="attachments", db_index=False
    )
    # The *original* filename, for display and Content-Disposition only — never used as a path.
    name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=128)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    # The storage-relative path returned by `default_storage.save`; opaque, generated server-side.
    path = models.CharField(max_length=255)
    external = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="uploaded_attachments"
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="edited_attachments"
    )

    class Meta:
        db_table = "attachment"
        indexes = [
            models.Index(fields=["case", "-created_at", "-id"], name="attach_case_created_idx"),
            models.Index(fields=["sha256"], name="attach_sha256_idx"),
        ]

    def __str__(self) -> str:
        return f"Attachment({self.case_id}): {self.name}"
