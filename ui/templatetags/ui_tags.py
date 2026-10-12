"""Template filters for the analyst UI.

Severity needs two renderings — a CSS class and a human label — and deriving both in a template means
writing `severity_names|default_if_none:''` gymnastics that silently produce `sev-3` when a lookup
misses. These filters keep the mapping in Python, next to `SEVERITY_CHOICES`, and fail loudly in
review rather than quietly in the page.

`sev_label` deliberately returns text alongside the colour: severity is the fastest triage signal in
the product and must never be conveyed by hue alone (WCAG 1.4.1).
"""

from __future__ import annotations

import json
from typing import Any

from django import template
from django.utils.safestring import mark_safe

from core.enums import SEVERITY_CHOICES
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
