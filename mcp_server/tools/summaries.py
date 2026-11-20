"""MCP Phase 2 post_case_summary tool.

The only direct-write MCP tool. It appends a labelled `ai-summary` TimelineEvent
after scope, schema, provenance, sanitisation and rate-limit checks.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

from mcp_server.auth import Principal
from mcp_server.models import McpCredential
from mcp_server.schemas import tool_result
from mcp_server.services.summaries import add_case_summary
from mcp_server.tools.base import ToolFailureError, run_write

_ALLOWED_ARGUMENTS = {"case_id", "summary_markdown", "source_event_ids", "idempotency_key"}


def post_case_summary(*, arguments: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Post an AI-generated summary as a labelled, append-only timeline event."""
    unknown = set(arguments or {}) - _ALLOWED_ARGUMENTS
    if unknown:
        raise ToolFailureError(
            "invalid_arguments", message=f"Unknown fields: {', '.join(sorted(unknown))}"
        )

    case_id = arguments.get("case_id")
    summary_markdown = arguments.get("summary_markdown")
    source_event_ids = arguments.get("source_event_ids")

    if not case_id:
        raise ToolFailureError("invalid_arguments", message="case_id is required")
    if not summary_markdown:
        raise ToolFailureError("invalid_arguments", message="summary_markdown is required")
    if not isinstance(source_event_ids, list) or not 1 <= len(source_event_ids) <= 100:
        raise ToolFailureError(
            "invalid_arguments", message="source_event_ids must be a list of 1-100 event IDs"
        )

    if principal.credential_id is None:
        raise ToolFailureError("not_found", status="not_found", message="Credential not found.")

    credential = (
        McpCredential.objects.select_related("user", "organisation")
        .filter(pk=principal.credential_id, organisation_id=principal.organisation_id)
        .first()
    )
    if credential is None or not credential.is_active:
        raise ToolFailureError("not_found", status="not_found", message="Credential not found.")

    result = _call_summary_service(
        case_id=str(case_id),
        summary_markdown=summary_markdown,
        source_event_ids=source_event_ids,
        idempotency_key=arguments.get("idempotency_key"),
        actor=credential.user,
        credential=credential,
    )
    return {
        "caseId": result["case_id"],
        "eventId": result["event_id"],
        "createdAt": result["created_at"],
        "label": result["label"],
        "requiresHumanVerification": result["requires_human_verification"],
    }


def _call_summary_service(**kwargs: Any) -> dict[str, Any]:
    """Translate service-level ValidationError codes into stable tool failures."""
    try:
        return add_case_summary(**kwargs)
    except ValidationError as exc:
        code = getattr(exc, "code", None) or "invalid_arguments"
        if code not in ("invalid_arguments", "not_found", "rate_limited"):
            code = "invalid_arguments"
        raise ToolFailureError(code, message="Summary request rejected.") from exc


def register(server: Any) -> None:
    """Register the one direct-write MCP tool (labelled, append-only summaries)."""

    @server.tool(
        name="post_case_summary",
        description="Append a labelled AI summary to a visible case timeline; no case state change.",
    )
    async def post_case_summary_tool(
        case_id: str,
        summary_markdown: str,
        source_event_ids: list[str],
        idempotency_key: str | None = None,
    ) -> Any:
        """Post an AI-generated case summary with provenance and rate limits."""
        arguments: dict[str, Any] = {
            "case_id": case_id,
            "summary_markdown": summary_markdown,
            "source_event_ids": source_event_ids,
        }
        if idempotency_key is not None:
            arguments["idempotency_key"] = idempotency_key
        data = await run_write(
            "post_case_summary",
            arguments,
            post_case_summary,
            scope="mcp:summary",
            target_type="case",
            target_id=case_id,
        )
        return tool_result(data, total=1, summary="AI summary appended")
