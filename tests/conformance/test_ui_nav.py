"""D8 — the keyboard-shortcut surfaces must not drift.

The keyboard-navigation path is split across three artefacts by construction:

* the rail links in ``ui/templates/ui/base.html`` (reachable by mouse and tab order),
* ``ROUTES`` in ``ui/static/ui/keys.js`` (what a key actually does),
* the cheat-sheet (``CHEATSHEET`` in ``keys.js`` *plus* the server-rendered table in
  ``base.html``).

CSP forbids an inline ``<script>``, so the lists cannot share one server-side variable at
runtime. This test is the substitute: it fails the moment they disagree. A shortcut that
navigates somewhere the rail does not list, or a documented key with no handler, is a bug.

The checks are source-level (they parse the two files) on purpose: a drift is then reported
as ``'x' -> /taxonomy`` rather than as a mystery 404 in a browser.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.test import Client
from django.urls import resolve, reverse

from identity.models import User

ROOT = Path(__file__).resolve().parents[2]
BASE_HTML = ROOT / "ui" / "templates" / "ui" / "base.html"
KEYS_JS = ROOT / "ui" / "static" / "ui" / "keys.js"

# `<a href="{% url 'ui-x' %}" …> [<i …></i>] <span class="k">K</span> Label [badge] </a>`
# Only (url_name, key) are captured: rail labels ("Automation") intentionally differ from the
# cheat-sheet labels ("Automation runs"), and a trailing `.badge` count may follow the keycap.
_RAIL_LINK_RE = re.compile(
    r"""href="\{%\s*url\s+'([\w-]+)'\s*%\}"[^>]*>"""
    r"""\s*(?:<i[^>]*>\s*</i>\s*)?"""
    r"""<span class="k">([^<])</span>""",
    re.S,
)
# <td class="cheatsheet-key"><kbd>K</kbd></td><td>Label</td>
_CHEAT_ROW_RE = re.compile(r'<td class="cheatsheet-key"><kbd>([^<]+)</kbd></td><td>([^<]+)</td>')
# { key: "g", label: "Go to Dashboard" },
_JS_CHEAT_RE = re.compile(r'\{\s*key:\s*"([^"]+)",\s*label:\s*"([^"]+)"\s*\}')
# g: "/",   or   ",": "/settings/organisations",
_JS_ROUTE_RE = re.compile(r'(?:"([^"]+)"|([A-Za-z]+)):\s*"([^"]+)"')

# Documented keys that have no route (they act on the current page).
_NON_NAV_KEYS = {"/", "?"}


def _rail() -> dict[str, str]:
    """key -> url_name for every rail link."""
    entries: dict[str, str] = {}
    for url_name, key in _RAIL_LINK_RE.findall(BASE_HTML.read_text(encoding="utf-8")):
        assert key not in entries, f"duplicate rail shortcut {key!r}"
        entries[key] = url_name
    assert entries, "no rail links parsed — did base.html markup change?"
    return entries


def _routes() -> dict[str, str]:
    """key -> path, from the `ROUTES` object in keys.js."""
    text = KEYS_JS.read_text(encoding="utf-8")
    block = text.split("var ROUTES = {", 1)[1].split("};", 1)[0]
    entries: dict[str, str] = {}
    for quoted, bare, path in _JS_ROUTE_RE.findall(block):
        key = quoted or bare
        assert key not in entries, f"duplicate ROUTES key {key!r}"
        entries[key] = path
    assert entries, "no ROUTES entries parsed — did keys.js change shape?"
    return entries


def _js_cheatsheet() -> dict[str, str]:
    """key -> label, from the `CHEATSHEET` array in keys.js."""
    return dict(_JS_CHEAT_RE.findall(KEYS_JS.read_text(encoding="utf-8")))


def _rendered_cheatsheet() -> dict[str, str]:
    """key -> label, from the server-rendered cheat-sheet table in base.html."""
    entries: dict[str, str] = {}
    for key, label in _CHEAT_ROW_RE.findall(BASE_HTML.read_text(encoding="utf-8")):
        assert key not in entries, f"duplicate cheat-sheet row {key!r}"
        entries[key] = label
    assert entries, "no cheat-sheet rows parsed — did base.html markup change?"
    return entries


def test_rail_and_routes_cover_the_same_keys() -> None:
    assert set(_rail()) == set(_routes())


def test_each_rail_link_targets_the_route_key_it_advertises() -> None:
    routes = _routes()
    mismatches = [
        f"{key!r}: rail {url_name} -> {reverse(url_name)!r}, ROUTES -> {routes[key]!r}"
        for key, url_name in _rail().items()
        if reverse(url_name) != urlsplit(routes.get(key, "/__missing__")).path
    ]
    assert not mismatches, "rail and ROUTES disagree:\n" + "\n".join(mismatches)


def test_routes_resolve_to_namespaced_ui_urls() -> None:
    for key, path in _routes().items():
        match = resolve(path)  # raises Resolver404 if the JS path is a typo
        assert match.url_name is not None and match.url_name.startswith("ui-"), (
            f"shortcut {key!r} points at {path!r}, which resolves to {match.url_name!r}"
        )


def test_cheatsheet_documents_exactly_the_bound_keys() -> None:
    documented = set(_rendered_cheatsheet())
    assert not set(_routes()) - documented, "a shortcut has no cheat-sheet row"
    assert not documented - set(_routes()) - _NON_NAV_KEYS, "a cheat-sheet row has no handler"


def test_cheatsheet_labels_match_between_js_and_server() -> None:
    """The `CHEATSHEET` array and the rendered table are the same list, character for character."""
    assert _js_cheatsheet() == _rendered_cheatsheet()


def test_rendered_dashboard_shows_the_cheatsheet_and_every_shortcut(analyst: User) -> None:
    """Render the real dashboard: the modal exists and every bound key appears as <kbd>."""
    client = Client()
    client.force_login(User.objects.get(pk=analyst.pk))
    response = client.get(reverse("ui-dashboard"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="cheatsheet-modal"' in body
    # `,` (Settings) is superAdmin-only, so it is correctly absent for a plain analyst.
    for key in set(_routes()) - {","} | _NON_NAV_KEYS:
        assert f"<kbd>{key}</kbd>" in body, f"shortcut {key!r} missing from the rendered page"


@pytest.mark.django_db
def test_non_nav_keys_render_as_well() -> None:
    """`/` and `?` are documented too — the table must list them even though they have no route."""
    rendered = _rendered_cheatsheet()
    assert _NON_NAV_KEYS <= set(rendered)
