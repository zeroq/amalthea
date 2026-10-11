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
_COMMENT_INLINE = re.compile(r"{#.*?#}", re.DOTALL)

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


def test_no_template_renders_json_via_json_script() -> None:
    # `json_script` emits an inline `<script type="application/json">`, which our CSP
    # blocks; config blobs are formatted server-side by the `prettyjson` filter instead.
    offenders = [
        path.name for path in _templates() if "json_script" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
