"""Case summary service for MCP Phase 2.

The sole direct-write MCP path: append one labelled, provenance-bound
`ai-summary` timeline event after strict validation, duplicate detection and
rate limiting. It never changes case state.
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateformat import format as date_format

from cases.ledger import append_timeline_event
from cases.models import Case, TimelineEvent
from ui.markdown import render_markdown

_TITLE = "AI-generated case summary"
_KIND = "ai-summary"
_LABEL = "ai-generated"
_HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_IMAGE = re.compile(r"!\[")


def _iso(value: Any) -> str:
    return date_format(value, "Y-m-d\\TH:i:s\\Z")


def _content_hash(case_id: str, sanitised: str) -> str:
    return hashlib.sha256(f"{case_id}\n{sanitised}".encode()).hexdigest()


def add_case_summary(
    *,
    case_id: str,
    summary_markdown: str,
    source_event_ids: list[str],
    idempotency_key: str | None = None,
    actor: Any = None,
    credential: Any = None,
) -> dict[str, Any]:
    """Append an `ai-summary` TimelineEvent to a case.

    Returns snake_case internals; the tool wrapper maps them to the public
    camelCase MCP contract. Duplicate content returns the existing event.
    """
    if credential is None or not getattr(credential, "id", None):
        raise ValidationError("An active MCP credential is required.", code="invalid_arguments")
    if idempotency_key is not None and (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 200
    ):
        raise ValidationError(
            "idempotency_key must be a non-empty string <= 200 chars.", code="invalid_arguments"
        )

    try:
        case = Case.objects.select_related("owner_org").get(
            pk=case_id, owner_org_id=credential.organisation_id
        )
    except Exception:
        raise ValidationError("Case not found.", code="not_found") from None

    if not isinstance(summary_markdown, str) or not 1 <= len(summary_markdown) <= 20_000:
        raise ValidationError(
            "summary_markdown must be between 1 and 20000 characters.",
            code="invalid_arguments",
        )
    if _HTML_TAG.search(summary_markdown) or _IMAGE.search(summary_markdown):
        raise ValidationError("Raw HTML and images are not allowed.", code="invalid_arguments")

    if not isinstance(source_event_ids, list) or not 1 <= len(source_event_ids) <= 100:
        raise ValidationError(
            "source_event_ids must contain 1-100 event IDs.", code="invalid_arguments"
        )
    normalised_event_ids: list[str] = []
    for raw_id in source_event_ids:
        if not isinstance(raw_id, (str,)) or not raw_id.strip():
            raise ValidationError("Invalid source event id.", code="invalid_arguments")
        normalised_event_ids.append(raw_id.strip())
    valid_ids = {
        str(event_id)
        for event_id in TimelineEvent.objects.filter(case=case).values_list("id", flat=True)
    }
    if any(event_id not in valid_ids for event_id in normalised_event_ids):
        raise ValidationError(
            "A source event does not belong to the target case.", code="invalid_arguments"
        )

    sanitised = render_markdown(summary_markdown)
    if not sanitised:
        raise ValidationError("summary_markdown could not be rendered.", code="invalid_arguments")
    content_hash = _content_hash(str(case.id), summary_markdown.strip())
    request_id = (
        idempotency_key.strip()
        if idempotency_key
        else hashlib.sha256(
            f"{credential.id}:{case.id}:{content_hash}:{','.join(sorted(normalised_event_ids))}".encode()
        ).hexdigest()[:32]
    )

    # Duplicate/idempotency checks run BEFORE quota counters are consumed.
    existing = (
        TimelineEvent.objects.filter(
            case=case,
            kind=_KIND,
        )
        .filter(metadata__contentHash=content_hash)
        .first()
    )
    if existing is None:
        existing = TimelineEvent.objects.filter(
            case=case, kind=_KIND, metadata__requestId=request_id
        ).first()
    if existing is not None:
        return {
            "case_id": str(case.id),
            "event_id": str(existing.id),
            "created_at": _iso(existing.date),
            "label": _LABEL,
            "requires_human_verification": True,
        }

    five_min_ago = timezone.now() - timedelta(minutes=5)
    if TimelineEvent.objects.filter(case=case, kind=_KIND, date__gte=five_min_ago).exists():
        raise ValidationError(
            "Rate limited: one summary per case per 5 minutes.", code="rate_limited"
        )

    daily_key = f"mcp:summary:cred:{credential.id}:day"
    cache.add(daily_key, 0, timeout=86_400)
    daily_count = int(cache.get(daily_key, 0))
    if daily_count >= 20:
        raise ValidationError(
            "Rate limited: 20 summaries per credential per day.", code="rate_limited"
        )
    cache.incr(daily_key)

    event = append_timeline_event(
        case=case,
        date=timezone.now(),
        title=_TITLE,
        kind=_KIND,
        description=summary_markdown.strip(),
        actor=credential.user if actor is None else actor,
        metadata={
            "generatedBy": "mcp",
            "credentialId": str(credential.id),
            "credential_name": getattr(credential, "name", ""),
            "requestId": request_id,
            "sourceEventIds": normalised_event_ids,
            "contentHash": content_hash,
            "label": _LABEL,
            "requiresHumanVerification": True,
        },
    )
    return {
        "case_id": str(case.id),
        "event_id": str(event.id),
        "created_at": _iso(event.date),
        "label": _LABEL,
        "requires_human_verification": True,
    }
