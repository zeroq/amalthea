"""Case template default constants and rendering helpers.

This module owns all constants and logic related to the initial alert payload token
rendering in case templates. No database models — pure functions and constants.
"""

from __future__ import annotations

import json
import re
from typing import Any

from alerts.models import Alert
from cases.models import Case

# Token that template authors place in task descriptions
SOURCE_ALERT_PAYLOAD_TOKEN = "{{ alert.raw_payload }}"  # noqa: S105

# Invisible sentinel injected alongside the rendered payload.
# A Markdown link-reference definition renders nothing but is preserved in the Markdown source.
# Used by the task-detail view to decide whether to offer the "View full alert payload" modal.
SOURCE_ALERT_PAYLOAD_SENTINEL = "[//]: # (amalthea:source-alert-payload)"

# Visible truncation marker shown when the 64 KiB cap is hit
SOURCE_ALERT_PAYLOAD_TRUNCATION_MARKER = (
    "\n\n> **Note:** The alert payload was truncated at 64 KiB. "
    "Use the **View full alert payload** button to see the complete JSON."
)

# 64 KiB UTF-8 byte cap for the rendered payload in the task description
MAX_SOURCE_ALERT_PAYLOAD_BYTES = 64 * 1024

# 8 MiB display safety cap for the full-payload modal.
# Prevents browser OOM on extremely large payloads while still allowing normal
# ingested alerts (typically < 1 MiB) to display completely.
MAX_FULL_PAYLOAD_BYTES = 8 * 1024 * 1024

# Default task definition for new templates (deep-copied, not shared)
DEFAULT_TEMPLATE_TASKS: list[dict[str, Any]] = [
    {
        "title": "Triage/Analysis",
        "group": "Triage",
        "description": (
            "## Initial alert payload\n\n"
            "The raw payload from the first alert linked to this case.\n\n"
            "{{ alert.raw_payload }}"
        ),
        "order": 0,
        "mandatory": False,
    }
]

# Default custom field definition for new templates (deep-copied, not shared)
DEFAULT_TEMPLATE_CUSTOM_FIELDS: list[dict[str, Any]] = [
    {"name": "Example Field", "type": "string", "value": "example"}
]


def earliest_alert_for_case(case: Case) -> Alert | None:
    """Return the earliest alert linked to `case` within the same organisation.

    Ordering: by `date` (ascending), then `created_at` (ascending), then `id` (ascending).
    Returns `None` if the case has no linked alerts from the same organisation.
    """
    return (
        case.alerts.filter(owner_org_id=case.owner_org_id)
        .order_by("date", "created_at", "id")
        .first()
    )


def has_source_payload_sentinel(description: str) -> bool:
    """Return True iff `description` contains the sentinel as a standalone line.

    The sentinel is a Markdown link-reference definition `[//]: # (amalthea:source-alert-payload)`.
    This checks for an exact line match (after stripping) to avoid false positives from
    inline mentions or quoted text.
    """
    if not description:
        return False
    sentinel_stripped = SOURCE_ALERT_PAYLOAD_SENTINEL.strip()
    for line in description.splitlines():
        if line.strip() == sentinel_stripped:
            return True
    return False


def _truncate_utf8_bytes(text: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate `text` to at most `max_bytes` UTF-8 bytes without splitting a multi-byte character.

    Returns a tuple of (truncated_text, was_truncated).
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    # Truncate at byte boundary, then decode with 'ignore' to drop any partial trailing char
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, True


def _build_dynamic_fence(payload: str) -> tuple[str, str]:
    """Build a fenced code block with a backtick fence longer than any backtick run in `payload`.

    Returns (opening_fence, closing_fence) where both are the same string of backticks + "json"
    that is guaranteed not to appear inside `payload`. This preserves payload backticks exactly
    without HTML entity escaping.
    """
    # Find the longest run of backticks in the payload
    max_backticks = 0
    for match in re.finditer(r"`+", payload):
        max_backticks = max(max_backticks, len(match.group()))
    # Use one more than the maximum found (minimum 3)
    fence_length = max(3, max_backticks + 1)
    fence = "`" * fence_length + "json"
    return fence, "`" * fence_length


def render_source_alert_payload(description: str, case: Case) -> str:
    """Replace `{{ alert.raw_payload }}` token in `description` with the earliest alert's payload.

    Behavior:
    - If the token is absent, returns `description` byte-for-byte unchanged.
    - If the token is present and an alert exists:
        * Pretty-prints the alert's `raw_payload` as JSON with `ensure_ascii=False`,
          `indent=2`, `sort_keys=True`, `default=str`.
        * Caps the **JSON payload itself** at 64 KiB UTF-8 bytes (without splitting multi-byte chars).
        * Wraps the (possibly truncated) JSON in a dynamically sized fence that is always closed.
        * Appends the visible truncation marker only when truncation occurred (outside the fence).
        * Replaces **every** occurrence of the token with a block-level fenced payload
          surrounded by newlines.
        * Appends the invisible sentinel (Markdown link-reference) exactly once at the end
          of the whole description, on its own line, only if not already present.
    - If the token is present but no alert exists:
        * Returns a readable fallback description (no sentinel, no truncation marker).
        * The fallback explains that no alert is linked yet.

    No `mark_safe`, no template `|safe`, and no exception for `{}` payloads.
    """
    if SOURCE_ALERT_PAYLOAD_TOKEN not in description:
        return description

    alert = earliest_alert_for_case(case)

    if alert is None:
        # No alert linked yet — readable fallback, no sentinel
        fallback = description.replace(
            SOURCE_ALERT_PAYLOAD_TOKEN,
            "_(No alert linked to this case yet. The payload will appear here when a template "
            "is applied to a case with at least one alert.)_",
        )
        return fallback

    # Pretty-print the raw_payload as JSON
    try:
        payload_json = json.dumps(
            alert.raw_payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError):
        # If serialization fails (shouldn't happen with default=str), show a safe fallback
        payload_json = "_(Unable to serialize alert payload)_"

    # Cap the JSON payload itself at 64 KiB UTF-8 bytes
    capped_json, was_truncated = _truncate_utf8_bytes(payload_json, MAX_SOURCE_ALERT_PAYLOAD_BYTES)

    # Build a dynamic fence that won't be broken by backticks in the payload
    open_fence, close_fence = _build_dynamic_fence(capped_json)

    # Wrap in a fenced code block — the trailing blank line is required so text that
    # followed the inline token cannot end up on the closing-fence line (P0 regression).
    fenced_payload = f"\n\n{open_fence}\n{capped_json}\n{close_fence}\n\n"

    # Append truncation marker only if truncated (outside the fence)
    if was_truncated:
        fenced_payload += SOURCE_ALERT_PAYLOAD_TRUNCATION_MARKER

    # Replace every occurrence of the token with the fenced payload
    result = description.replace(SOURCE_ALERT_PAYLOAD_TOKEN, fenced_payload)

    # Append the sentinel exactly once at the end, on its own line, if not already present.
    # The blank line matters: a Markdown link-reference definition cannot interrupt a
    # paragraph, so it would otherwise render as visible text after trailing prose.
    if not has_source_payload_sentinel(result):
        result = result.rstrip() + "\n\n" + SOURCE_ALERT_PAYLOAD_SENTINEL

    return result


def format_full_payload(alert: Alert) -> str:
    """Return the complete alert payload for the modal, with a display safety cap.

    Behavior:
    - Serializes compact JSON first; if compact exceeds `MAX_FULL_PAYLOAD_BYTES`,
      returns a clear "payload too large to display" message (not JSON).
    - Otherwise pretty-prints unless a conservative 2x size estimate already exceeds the cap;
      if pretty still exceeds the cap, returns the compact JSON instead (still complete,
      just not pretty).
    - Catches (TypeError, ValueError, RecursionError, MemoryError) and returns a safe message.
    - No sentinel, no truncation marker, no fence.
    """
    # First serialize compact JSON to check size
    try:
        compact_json = json.dumps(
            alert.raw_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError, RecursionError, MemoryError):
        return "_(Unable to serialize alert payload)_"

    # If compact JSON exceeds the display cap, refuse to render
    compact_bytes = len(compact_json.encode("utf-8"))
    if compact_bytes > MAX_FULL_PAYLOAD_BYTES:
        return "_(Payload too large to display; exceeds 8 MiB limit)_"

    # Pretty-printing can expand a dense payload several-fold. Skip it when a conservative
    # 2x estimate already exceeds the cap, rather than allocating the full pretty string
    # first and discarding it afterwards (SEC hardening M2).
    if compact_bytes * 2 > MAX_FULL_PAYLOAD_BYTES:
        return compact_json

    # Otherwise try pretty-print
    try:
        pretty_json = json.dumps(
            alert.raw_payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError, RecursionError, MemoryError):
        return "_(Unable to serialize alert payload)_"

    # If pretty exceeds cap, fall back to compact (still complete)
    if len(pretty_json.encode("utf-8")) > MAX_FULL_PAYLOAD_BYTES:
        return compact_json

    return pretty_json
