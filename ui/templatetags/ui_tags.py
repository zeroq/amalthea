"""Template filters for the analyst UI.

Severity needs two renderings — a CSS class and a human label — and deriving both in a template means
writing `severity_names|default_if_none:''` gymnastics that silently produce `sev-3` when a lookup
misses. These filters keep the mapping in Python, next to `SEVERITY_CHOICES`, and fail loudly in
review rather than quietly in the page.

`sev_label` deliberately returns text alongside the colour: severity is the fastest triage signal in
the product and must never be conveyed by hue alone (WCAG 1.4.1).

`reason_label`/`is_unresolved` do the same job for `ingest.mapping`'s field warnings: the alert page and
the re-map dialog both render them, and the wording plus the "produced no value" set live next to the
engine that emits them rather than in two templates.
"""

from __future__ import annotations

import json
from typing import Any

from django import template
from django.utils.safestring import mark_safe

from core.enums import SEVERITY_CHOICES
from ingest.mapping import REASON_LABELS, UNRESOLVED_REASONS
from ui.markdown import render_markdown

register = template.Library()

_LABELS = dict(SEVERITY_CHOICES)
_NAMES = {1: "low", 2: "medium", 3: "high", 4: "critical"}


@register.filter
def sev_class(value: Any) -> str:
    """CSS class suffix for a severity, e.g. `3` -> `high`."""
    try:
        return _NAMES.get(int(value), "unknown")
    except (TypeError, ValueError):
        return "unknown"


@register.filter
def sev_label(value: Any) -> str:
    """Human label for a severity, e.g. `3` -> `High`."""
    try:
        return _LABELS.get(int(value), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


@register.filter
def get_item(mapping: Any, key: Any) -> Any:
    """`mapping[key]` in a template, for the few dict-valued contexts (severity counts)."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def prettyjson(value: Any) -> str:
    """Render a JSON-able value as indented JSON for a `<pre>`/`<textarea>`.

    The value is formatted **server-side** so the page needs no inline `<script>` to
    pretty-print it (ADR-003: `script-src 'self'`). Returns a plain string; the template
    engine escapes it for the surrounding element. Strings are parsed first so a
    JSONField that round-trips as text still formats; non-JSON strings pass through.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


@register.filter
def markdown(value: Any) -> str:
    """Render analyst-authored Markdown as sanitized HTML (ADR-005).

    The filter delegates to `ui.markdown.render_markdown`, which parses CommonMark
    (raw HTML in source is escaped) and then sanitizes the result with `nh3` against
    a strict allow-list. `nh3` is the single trusted XSS boundary — parser output is
    never returned unsanitized. On failure, the input is escaped as plain text.

    Returns a `SafeString` so the template engine does not double-escape the sanitized
    HTML. Templates use `{{ value|markdown }}` for `Case.description` and comment
    bodies (`TimelineEvent.kind == "comment"`).
    """
    html = render_markdown(str(value or ""))
    # Already sanitized by nh3 (ADR-005); marking safe is precisely the filter's job. S308 is a
    # blanket heuristics warning and the allow-list in ui.markdown is the control that answers it.
    return mark_safe(html)  # noqa: S308


@register.filter
def choice_label(value: Any, choices: Any) -> str:
    """Human label for a choice value, e.g. `personal_data` + DATA_CLASSIFICATION_CHOICES -> `Personal Data (PII)`.

    Falls back to the raw value if not found, and to "Unset/Unknown" for falsy values.
    This ensures the UI always shows a human-readable label, never the stored token.
    """
    if not value:
        return "Unset/Unknown"
    try:
        choice_dict = dict(choices)
        return choice_dict.get(value, str(value))
    except (TypeError, ValueError):
        return str(value)


@register.filter
def choice_name(value: Any, choices: Any) -> str:
    """Human name for a choice whose 0 is a real value (TLP/PAP).

    Unlike `choice_label` this does NOT collapse falsy values: TLP 0 / PAP 0 mean "White".
    """
    try:
        return dict(choices).get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value)


@register.filter
def reason_label(reason: Any) -> str:
    """Sentence for a `FieldWarning.reason`, e.g. `bad_type` -> "a value of the wrong type".

    The engine's tokens are stored (in `ingestion_warnings["mapping"]`) and asserted by tests, so the
    wording lives in `ingest.mapping.REASON_LABELS` and reaches templates through here rather than being
    re-typed in two pages. An unknown reason renders as-written rather than blank: a reason this table
    has not heard of is still more useful than nothing.
    """
    return REASON_LABELS.get(str(reason), str(reason))


@register.filter
def is_unresolved(reason: Any) -> bool:
    """Whether a `mapping` reason means "no value was produced" (`ingest.mapping.UNRESOLVED_REASONS`).

    The alert page uses it to mark the lines that explain a fallback: `truncated` is also a warning and is
    *not* one of them, because that row did get a value.
    """
    return str(reason) in UNRESOLVED_REASONS
