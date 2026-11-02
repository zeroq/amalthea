"""`GET /api/v1/search` — simple, org-scoped global search for integrations.

Unlike `POST /api/v1/query`, this endpoint has a fixed field set and a documented envelope. It
shares `query.search` with the UI, so the two surfaces cannot drift on matching or scoping.

Response contract: plan §7. Raw text only; no HTML and no UI highlighting.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from identity.audit import record_cross_org_read
from identity.models import Organisation
from query.search import (
    SEARCH_MAX_PAGE_SIZE,
    SEARCH_PAGE_SIZE,
    SearchKind,
    SearchSection,
    parse_query,
    search_sections,
)

_VALID_TYPES = {"all", "case", "alert", "observable"}


def _bad_request(message: str) -> Response:
    return Response(
        {"type": "BadRequest", "message": message},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _parse_int(name: str, raw: str, *, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{name}' must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"'{name}' must be between {minimum} and {maximum}")
    return value


def _audit_superadmin_search(request: Request, sections: list[SearchSection], user: Any) -> None:
    """One audit row per (kind, target org), mirroring `/api/v1/query`'s break-glass audit."""
    if getattr(user, "role", "") != "superAdmin":
        return
    actor_org = getattr(user, "org", None)
    if actor_org is None:
        return

    representatives: dict[tuple[str, str], str] = {}
    for section in sections:
        for hit in section.hits:
            if hit.org_id is None or hit.org_id == str(actor_org.id):
                continue
            representatives.setdefault((hit.kind, hit.org_id), hit.id)

    if not representatives:
        return

    orgs = Organisation.objects.in_bulk([org_id for _, org_id in representatives])
    for (kind, org_id), target_id in representatives.items():
        target_org = orgs.get(UUID(org_id))
        if target_org is None:
            continue
        record_cross_org_read(
            request,
            actor=user,
            actor_org=actor_org,
            target_org=target_org,
            target_type=kind.capitalize(),
            target_id=target_id,
            action="cross_org_read_search",
        )


@api_view(["GET"])
def search_view(request: Request) -> Response:
    """Search cases, alerts and observables visible to the caller."""
    raw_q = request.GET.get("q", "")
    search_type = request.GET.get("type", "all")

    if search_type not in _VALID_TYPES:
        return _bad_request(f"Unsupported search type '{search_type}'")

    query = parse_query(raw_q)
    kind: SearchKind | None = None if search_type == "all" else cast(SearchKind, search_type)

    page = 1
    per_page = SEARCH_PAGE_SIZE
    if kind is not None:
        try:
            page = _parse_int("page", request.GET.get("page", "1"), minimum=1, maximum=2**31 - 1)
            per_page = _parse_int(
                "per_page",
                request.GET.get("per_page", str(SEARCH_PAGE_SIZE)),
                minimum=1,
                maximum=SEARCH_MAX_PAGE_SIZE,
            )
        except ValueError as exc:
            return _bad_request(str(exc))

    result = search_sections(
        query,
        request.user,
        kind,
        page=page,
        per_page=per_page,
    )
    sections = result["sections"]
    total = result["total"]
    counts = result["counts"]

    envelope = {
        "query": query.raw,
        "type": search_type,
        "total": total,
        "counts": {
            "case": counts.get("case", 0),
            "alert": counts.get("alert", 0),
            "observable": counts.get("observable", 0),
        },
        "sections": [section.to_dict() for section in sections],
    }

    _audit_superadmin_search(request, sections, request.user)

    response = Response(envelope)
    response["X-Total"] = str(total)
    return response
