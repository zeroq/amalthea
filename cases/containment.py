"""Containment phase action capture service.

The General-tab phase dialog and any future API path share this one write path:
validate the selected taxonomy terms, replace the case's current action links
when they changed, move the case to ``containment`` / ``Contained``, and snapshot
a changed result as a case Page plus ledger/audit entries.

All writes happen in one transaction. Validation happens before the first write,
so an error never leaves a partially-replaced action set behind.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from uuid import UUID

from django.db import transaction
from django.db.models import Max, QuerySet
from django.utils import timezone

from cases.ledger import append_timeline_event
from cases.models import Case, CaseContainmentAction, CaseStatus, Page
from cases.update import apply_case_update
from core import taxonomy_read
from core.models import TaxonomyTerm
from core.serializers import page_json
from identity.models import AuditLog, User
from realtime.publisher import publish_case_event

CONTAINMENT_ACTION_OTHER_CODE = taxonomy_read.CONTAINMENT_ACTION_OTHER_CODE
OTHER_TEXT_MAX_LENGTH = 500


@dataclass
class ContainmentResult:
    """Result of a containment service invocation."""

    case: Case
    page: Page | None = None
    timeline_event_id: UUID | None = None
    error: str | None = None


def _resolve_contained_status() -> CaseStatus | None:
    """Return the visible ``Contained`` CaseStatus, or None if it is missing."""

    return (
        CaseStatus.objects.filter(value="Contained", hidden=False, stage="InProgress")
        .order_by("order", "value")
        .first()
    )


def _visible_containment_terms() -> QuerySet[TaxonomyTerm]:
    """Return visible taxonomy terms in the containment-actions category."""

    return (
        taxonomy_read._get_term_model()
        .objects.filter(
            category=taxonomy_read.CATEGORY_CONTAINMENT_ACTIONS,
            hidden=False,
        )
        .order_by("order", "code")
    )


def _dedupe_codes(codes: Iterable[str]) -> set[str]:
    """Normalize and deduplicate action codes without losing ordering semantics."""

    result: set[str] = set()
    for code in codes:
        normalized = (code or "").strip().lower()
        if normalized:
            result.add(normalized)
    return result


def _action_set_changed(
    case: Case,
    new_codes: set[str],
    other_selected: bool,
    other_text: str,
) -> bool:
    """Return whether the submitted action set differs from the stored one."""

    if case.phase != "containment":
        return True

    current_links = CaseContainmentAction.objects.filter(case=case)
    current_codes = set(current_links.values_list("term__code", flat=True))
    if new_codes != current_codes:
        return True

    current_other = (
        current_links.filter(term__code=CONTAINMENT_ACTION_OTHER_CODE)
        .values_list("other_text", flat=True)
        .first()
    )
    current_other_text = (current_other or "").strip()
    if other_selected != bool(current_other_text):
        return True
    return other_selected and other_text != current_other_text


def _page_content(
    codes: set[str],
    *,
    other_text: str,
    timestamp: datetime,
) -> str:
    """Build the Markdown snapshot. Only selected actions are listed."""

    lines = [
        "The case is in the Containment phase.",
        "",
        "Selected actions:",
    ]
    for code in sorted(codes):
        lines.append(f"- {taxonomy_read.label(taxonomy_read.CATEGORY_CONTAINMENT_ACTIONS, code)}")
    if other_text:
        lines.extend(["", f"Other: {other_text}"])
    lines.extend(["", f"Timestamp: {timestamp.strftime('%Y-%m-%d %H:%M')}"])
    return "\n".join(lines)


def apply_containment_actions(
    case: Case,
    *,
    actor: User,
    action_codes: Iterable[str],
    other_text: str = "",
) -> ContainmentResult:
    """Validate and apply containment actions to ``case``.

    Returns a result with ``error`` set on failure. Validation is complete before
    the transaction opens, so a rejected request makes no writes. On a changed
    success the status, through-relation, Page, ledger entry and audit row commit
    together; an identical save does not create a second snapshot.
    """

    contained_status = _resolve_contained_status()
    if contained_status is None:
        return ContainmentResult(
            case=case,
            error="The Contained case status is missing or hidden; cannot enter containment.",
        )

    codes = _dedupe_codes(action_codes)
    if not codes:
        return ContainmentResult(
            case=case,
            error="At least one containment action is required.",
        )

    terms_by_code = {term.code: term for term in _visible_containment_terms()}
    unknown_codes = sorted(codes - terms_by_code.keys())
    if unknown_codes:
        return ContainmentResult(
            case=case,
            error=f"Unknown or hidden containment action: {', '.join(unknown_codes)}.",
        )

    other_selected = CONTAINMENT_ACTION_OTHER_CODE in codes
    normalized_other = (other_text or "").strip()
    if other_selected:
        if not normalized_other:
            return ContainmentResult(
                case=case,
                error="The 'Other' action requires a description.",
            )
        if len(normalized_other) > OTHER_TEXT_MAX_LENGTH:
            return ContainmentResult(
                case=case,
                error=(f"Other detail must be {OTHER_TEXT_MAX_LENGTH} characters or fewer."),
            )
    else:
        # The field is only meaningful for the Other action; ignore it entirely.
        normalized_other = ""

    if case.status.stage == "Closed":
        return ContainmentResult(
            case=case,
            error="Closed cases cannot enter containment. Reopen the case first.",
        )

    currently_in_containment = case.phase == "containment"
    action_set_changed = _action_set_changed(case, codes, other_selected, normalized_other)
    create_page = not currently_in_containment or action_set_changed
    now = timezone.localtime()
    page: Page | None = None
    timeline_event_id: UUID | None = None

    with transaction.atomic():
        if action_set_changed:
            CaseContainmentAction.objects.filter(case=case).delete()
            CaseContainmentAction.objects.bulk_create(
                [
                    CaseContainmentAction(
                        case=case,
                        term=terms_by_code[code],
                        other_text=(
                            normalized_other if code == CONTAINMENT_ACTION_OTHER_CODE else ""
                        ),
                        created_by=actor,
                    )
                    for code in sorted(codes)
                ]
            )

        apply_case_update(
            case,
            {"phase": "containment", "status": contained_status.value},
            actor=actor,
        )

        if create_page:
            max_order = case.pages.aggregate(max_order=Max("order"))["max_order"] or 0
            title_prefix = (
                "Containment actions updated" if currently_in_containment else "Containment actions"
            )
            page = Page.objects.create(
                case=case,
                title=f"{title_prefix} — {now.strftime('%Y-%m-%d %H:%M')}",
                content=_page_content(
                    codes,
                    other_text=normalized_other if other_selected else "",
                    timestamp=now,
                ),
                category="containment",
                created_by=actor,
                order=max_order + 1,
            )

            timeline_event = append_timeline_event(
                case,
                title="Containment actions applied",
                kind="containment-actions",
                actor=actor,
                metadata={
                    "codes": sorted(codes),
                    "other": normalized_other if other_selected else "",
                    "page_id": str(page.id),
                },
            )
            timeline_event_id = timeline_event.id

            transaction.on_commit(
                partial(
                    publish_case_event,
                    str(case.id),
                    "page",
                    {"event": page_json(page)},
                )
            )

        if page is not None:
            AuditLog.objects.create(
                actor=actor,
                actor_org_id=getattr(actor, "org_id", None),
                target_org_id=case.owner_org_id,
                action="case.containment_actions",
                target_type="Case",
                target_id=str(case.id),
                details={
                    "codes": sorted(codes),
                    "other": normalized_other if other_selected else "",
                    "page_id": str(page.id),
                },
            )

    return ContainmentResult(
        case=case,
        page=page,
        timeline_event_id=timeline_event_id,
        error=None,
    )
