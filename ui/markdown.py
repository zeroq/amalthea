"""Server-side sanitized Markdown rendering (ADR-005).

This module provides a single pure function `render_markdown` that parses CommonMark
with `markdown-it-py` (raw HTML in source is escaped, not passed through) and then
sanitizes the resulting HTML with `nh3` against a strict allow-list.

The sanitizer is the **only trusted XSS boundary** — parser output is never returned
unsanitized. On any failure (parsing or sanitizing), the function fails closed by
returning the input escaped as plain text.
"""

from __future__ import annotations

import functools
from typing import Final
from urllib.parse import urlsplit

import nh3
from django.utils.html import escape
from markdown_it import MarkdownIt
from markdown_it.rules_core.state_core import StateCore

# CommonMark parser with raw HTML disabled (html=False means HTML in source is escaped as text).
_MD: Final = MarkdownIt("commonmark", {"html": False})

# Strict allow-list covering CommonMark output only.
_ALLOWED_TAGS: Final = frozenset(
    {
        "p",
        "br",
        "hr",
        "strong",
        "em",
        "del",
        "code",
        "pre",
        "blockquote",
        "ul",
        "ol",
        "li",
        "a",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)

_ALLOWED_ATTRIBUTES: Final = {
    "a": frozenset({"href", "title"}),
}

# Only these URL schemes are permitted in href/src attributes.
_ALLOWED_URL_SCHEMES: Final = frozenset({"http", "https", "mailto"})


def _strip_protocol_relative_links(state: StateCore) -> None:
    """Drop the `href` of a protocol-relative link (`//host/path`), keeping its text.

    CommonMark accepts `[x](//evil.example)`, and such a target carries **no scheme** for
    `_ALLOWED_URL_SCHEMES` to inspect -- it inherits the page's, so the link becomes a
    same-scheme pointer at an arbitrary host (a phishing aid, not XSS; TODO §11.3). The
    href is removed rather than rewritten, so the text stays readable but is inert.
    Root-relative (`/path`) and scheme-bearing (`https:`, `mailto:`) links are untouched.
    """
    for token in state.tokens:
        for candidate in [token, *(token.children or [])]:
            if candidate.type != "link_open":
                continue
            href = candidate.attrGet("href")
            if href and href.startswith("//") and not urlsplit(href).scheme:
                if candidate.attrs is not None:
                    candidate.attrs.pop("href", None)


# Runs after CommonMark's inline pass, i.e. once link tokens exist, and before `text_join`.
_MD.core.ruler.after("inline", "strip_protocol_relative_links", _strip_protocol_relative_links)


@functools.lru_cache(maxsize=512)
def render_markdown(text: str) -> str:
    """Render a Markdown string to sanitized HTML.

    Args:
        text: The Markdown source text (analyst-authored content).

    Returns:
        Sanitized HTML string (plain `str`, NOT marked safe). Empty/None input
        returns an empty string. On any error, returns the input escaped as text.
    """
    if not text:
        return ""

    try:
        # Parse CommonMark (raw HTML in source is escaped, not passed through).
        html = _MD.render(text)
        # Sanitize with nh3 — the single trusted XSS boundary.
        return nh3.clean(
            html,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            url_schemes=_ALLOWED_URL_SCHEMES,
            link_rel="noopener noreferrer",
        )
    except Exception:
        # Fail closed: never return raw or partially-sanitized content.
        return escape(text)
