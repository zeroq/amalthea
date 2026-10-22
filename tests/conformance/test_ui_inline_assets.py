"""ADR-003 guard: the UI templates must not carry inline scripts, styles, or handlers.

The app ships CSP `script-src 'self'; style-src 'self'` with no nonce and no
`'unsafe-inline'` (see `amalthea/settings/base.py`). An inline `<script>`, a
`style="..."` attribute, or an `on*=` handler is therefore **silently dropped by the
browser** and the feature it drives is dead in production even though it may work in a
permissive dev setup. This test scans the template sources so that regression fails here
rather than in a console nobody reads.

`{% comment %}`/`{# #}` blocks are stripped before scanning: they are not rendered, and
design prose that *mentions* `<script>` must not trip the guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "ui" / "templates" / "ui"

_COMMENT_BLOCK = re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", re.DOTALL)
# Django's `{# #}` is single-line ONLY: a multi-line one is *not* a comment, it is rendered into the
# page verbatim. So this deliberately does NOT use `re.DOTALL` — with DOTALL the leaked text was
# stripped here as if it were a comment, which is how a four-line `{# … #}` in `settings_sources.html`
# sailed through this scan and shipped literal `{# … #}` markup on a live Settings page (found
# 2026-10-16; guarded by test_no_multiline_inline_comment below).
_COMMENT_INLINE = re.compile(r"{#[^\n]*?#}")

# A `<script>` tag that does not carry `src=` is inline and will be blocked by CSP.
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)", re.IGNORECASE)
_STYLE_ATTR = re.compile(r"\sstyle\s*=", re.IGNORECASE)
_ON_ATTR = re.compile(
    r"\son(?:click|change|submit|load|input|keydown|keyup|keypress|focus|blur|"
    r"mouseover|mouseout|mouseenter|mouseleave|dblclick|reset|error|invalid|select)\s*=",
    re.IGNORECASE,
)


def _templates() -> list[Path]:
    return sorted(TEMPLATE_DIR.glob("*.html"))


def _strip_comments(text: str) -> str:
    return _COMMENT_INLINE.sub("", _COMMENT_BLOCK.sub("", text))


def _unclosed_inline_comment_lines(text: str) -> list[int]:
    """Line numbers where a `{#` has no matching `#}` on the same line.

    Django's `{# #}` comment syntax is **single-line only** — unlike
    `{% comment %}…{% endcomment %}`, which may span lines. A `{#` whose `#}` falls on a later
    line is therefore emitted verbatim into the rendered HTML (e.g. a multi-line "comment"
    leaks its prose, including any `data-*` tokens it names, into the page). Legitimate
    `{% comment %}` blocks are stripped first so prose inside them cannot false-positive.
    """
    without_blocks = _COMMENT_BLOCK.sub("", text)
    offenders: list[int] = []
    for lineno, line in enumerate(without_blocks.splitlines(), start=1):
        pos = line.find("{#")
        while pos != -1:
            if "#}" not in line[pos:]:
                offenders.append(lineno)
                break
            pos = line.find("{#", pos + 2)
    return offenders


def _read(path: Path) -> str:
    return _strip_comments(path.read_text(encoding="utf-8"))


def test_template_dir_is_discovered() -> None:
    # A guard test that silently finds nothing is worse than no guard.
    assert len(_templates()) >= 20


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_inline_script(path: Path) -> None:
    match = _INLINE_SCRIPT.search(_read(path))
    assert match is None, (
        f"{path.name}: inline <script> is blocked by CSP — move it to a static file "
        f"loaded with <script src=...>"
    )


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_inline_style_attribute(path: Path) -> None:
    match = _STYLE_ATTR.search(_read(path))
    assert match is None, (
        f"{path.name}: inline style= is blocked by CSP — add a class in "
        f"ui/static/ui/app.css instead"
    )


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_inline_event_handler(path: Path) -> None:
    match = _ON_ATTR.search(_read(path))
    assert match is None, (
        f"{path.name}: inline {match.group().strip() if match else 'on*='} handler is "
        f"blocked by CSP — attach the listener from a static JS file"
    )


def test_htmx_indicator_styles_are_disabled() -> None:
    # htmx (v2) injects a `<style>` block into <head> at load when `includeIndicatorStyles`
    # is left at its default of true. That block is blocked by our `style-src 'self'` and shows
    # up as a console CSP violation. The declarative meta below (no JS, no nonce) turns it off;
    # the templates use no `hx-indicator`, so nothing is lost. Removing the meta silently
    # re-introduces the violation, so it is pinned here.
    html = TEMPLATE_DIR.joinpath("base.html").read_text(encoding="utf-8")
    assert re.search(r'<meta\s+name="htmx-config"', html), (
        'base.html must carry a <meta name="htmx-config"> so htmx does not inject an '
        "indicator <style> that our CSP blocks"
    )
    assert re.search(r'"includeIndicatorStyles"\s*:\s*false', html), (
        "the htmx-config meta must set includeIndicatorStyles to false — otherwise htmx "
        "injects a <style> at load which CSP (style-src 'self') refuses"
    )


def test_only_base_template_loads_htmx() -> None:
    # The htmx-config pin above guards base.html; if another template ever loads htmx it would
    # bypass that guard, so the single-loader assumption is itself asserted.
    loaders = [
        path.name
        for path in _templates()
        if "htmx.min.js" in _strip_comments(path.read_text(encoding="utf-8"))
    ]
    assert loaders == ["base.html"], f"htmx is loaded by {loaders}; extend the guard to all"


def test_no_template_renders_json_via_json_script() -> None:
    # `json_script` emits an inline `<script type="application/json">`, which our CSP
    # blocks; config blobs are formatted server-side by the `prettyjson` filter instead.
    offenders = [
        path.name for path in _templates() if "json_script" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_multiline_inline_comment(path: Path) -> None:
    # A `{#` closed on a later line is not a comment to Django — it renders as literal text.
    lines = _unclosed_inline_comment_lines(path.read_text(encoding="utf-8"))
    assert lines == [], (
        str(path.name)
        + ": `{#` at line(s) "
        + str(lines)
        + " has no closing `#}` on the same line. Django's `{# #}` comment is single-line "
        "only, so it is rendered as literal text; use `{% comment %}...{% endcomment %}`."
    )


def _multiline_tag_lines(text: str) -> list[int]:
    """Line numbers where a `{% … %}` or `{{ … }}` is not closed on the same line.

    Django's lexer does not use `re.DOTALL`, so a block tag or expression whose closer is on a
    later line is not parsed as a tag at all — it is emitted into the page as literal text.
    `{% comment %}` bodies are stripped first because they are inert by design.
    """
    without_blocks = _COMMENT_BLOCK.sub("", text)
    offenders: list[int] = []
    for opener, closer in (("{%", "%}"), ("{{", "}}")):
        pos = without_blocks.find(opener)
        while pos != -1:
            line_end = without_blocks.find("\n", pos)
            close = without_blocks.find(closer, pos)
            if close == -1 or (line_end != -1 and close > line_end):
                offenders.append(without_blocks.count("\n", 0, pos) + 1)
            pos = without_blocks.find(opener, pos + 1)
    return sorted(set(offenders))


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_multiline_template_tags(path: Path) -> None:
    # A block tag/expression closed on a later line is rendered verbatim, not executed.
    lines = _multiline_tag_lines(path.read_text(encoding="utf-8"))
    assert lines == [], (
        str(path.name)
        + ": multi-line `{% … %}`/`{{ … }}` starting at line(s) "
        + str(lines)
        + " is rendered as literal text (Django's lexer is line-based); keep the tag on one line."
    )


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_template_expression_inside_block_tag(path: Path) -> None:
    # `{% include … title="{{ obj.name }}" %}` passes the *literal* string `{{ obj.name }}`:
    # quoted tag arguments are constants and are not re-parsed. Pass an unquoted variable or a
    # `{% with %}` alias instead.
    match = re.search(r"\{%[^%]*?\{\{", _strip_comments(path.read_text(encoding="utf-8")))
    assert match is None, (
        f"{path.name}: `{{{{ … }}}}` inside a block tag is literal text — use an unquoted "
        f"variable or a `{{% with %}}` alias"
    )
