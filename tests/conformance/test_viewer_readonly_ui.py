"""ADR-006 invariant — a viewer can never mutate anything through the UI.

Fail-closed and drift-proof: the route table is enumerated from the URLconf, so a
new mutating view is covered the day it is added. The assertion is deliberately
brute — POST a dummy body to every UI route as a viewer and demand that the
response is never a success or a redirect. That is stricter than "does this view
carry the decorator", and it is sensitive: without the gate, the view's own
validation path answers 2xx/3xx (a re-rendered form, or a `redirect()` carrying an
error message). `test_the_invariant_is_not_vacuous` pins that sensitivity.

Allow-list: login, logout, password change — the self-service auth a viewer keeps.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from core.enums import VIEWER_ROLE

pytestmark = pytest.mark.django_db

#: Self-service auth a viewer is allowed to reach (ADR-006 decision 7).
VIEWER_ALLOWED_ROUTES: frozenset[str] = frozenset({"login", "logout", "ui-password-change"})

#: Uniform 404 from `not_viewer`, or 405 when the route is GET-only (the method guard
#: rejects first — still not a success).
VIEWER_DENIAL_STATUSES = frozenset({404, 405})

#: Routes this plan explicitly claims are gated; asserted so the enumeration cannot
#: silently stop covering the mutating surface.
_MUTATION_ROUTE_SAMPLES = frozenset(
    {
        "ui-alert-escalate",
        "ui-alert-status",
        "ui-case-comment",
        "ui-case-create",
        "ui-case-status",
        "ui-case-close",
        "ui-case-field-edit",
        "ui-case-tag-add",
        "ui-case-tag-remove",
        "ui-case-task-create",
        "ui-case-procedure-create",
        "ui-case-procedures-create",
        "ui-case-share-revoke",
        "ui-case-attachment-upload",
        "ui-observable-delete",
        "ui-playbook-create",
        "ui-playbook-run",
        "ui-tag-create",
        "ui-settings-users-delete",
    }
)

_CONVERTER = re.compile(r"<(?:uuid|str|slug|int|path):([^>]+)>")


def _substitute(pattern: str) -> str:
    """Replace every path converter with a syntactically valid placeholder value."""

    def repl(match: re.Match[str]) -> str:
        converter = match.group(0)
        if "uuid" in converter:
            return "11111111-1111-1111-1111-111111111111"
        if "int" in converter:
            return "1"
        return "x"

    return _CONVERTER.sub(repl, pattern)


def _iter_named_routes(patterns=None, prefix: str = ""):
    """Yield `(name, route_string)` for every *named* route in the project."""
    for pattern in patterns if patterns is not None else get_resolver().url_patterns:
        full = prefix + str(getattr(pattern, "pattern", "")).replace("^", "").replace("$", "")
        if isinstance(pattern, URLResolver):
            yield from _iter_named_routes(pattern.url_patterns, full)
        elif isinstance(pattern, URLPattern) and pattern.name:
            yield pattern.name, full


def _ui_routes() -> list[tuple[str, str]]:
    """Every `ui-`-named route, plus the allow-listed auth routes.

    The auth routes are returned so the enumeration sees them, but the sweeps skip
    them: they are exempt by design, and POSTing to `logout` would end the viewer's
    session and turn every later assertion into a login redirect. Their
    reachability is asserted separately.
    """
    routes = [
        (name, pattern)
        for name, pattern in _iter_named_routes()
        if name.startswith("ui-") or name in VIEWER_ALLOWED_ROUTES
    ]
    assert len(routes) > 55, f"route enumeration looks broken: only {len(routes)} routes"
    names = {name for name, _ in routes}
    missing = _MUTATION_ROUTE_SAMPLES - names
    assert not missing, f"enumeration no longer covers known mutation routes: {sorted(missing)}"
    return routes


def _make_viewer(org, username: str):
    # `User.org` is NOT NULL (ADR-007): a viewer must belong to an organisation.
    return get_user_model().objects.create(username=username, role=VIEWER_ROLE, org=org)


def test_every_ui_route_denies_a_viewer_post(client, default_org) -> None:
    """POST a dummy body to every UI route as a viewer: never a success, never a redirect."""
    client.force_login(_make_viewer(default_org, "viewer-sweep"))
    failures: list[str] = []

    for name, pattern in _ui_routes():
        if name in VIEWER_ALLOWED_ROUTES:
            continue
        url = "/" + _substitute(pattern)
        status = client.post(url, {"dummy": "1"}).status_code
        if status not in VIEWER_DENIAL_STATUSES:
            failures.append(f"{name} ({url}): viewer POST answered {status}")

    assert not failures, "viewer was not denied:\n" + "\n".join(failures)


def test_every_ui_route_denies_a_viewer_on_every_mutating_verb(client, default_org) -> None:
    """PATCH/PUT/DELETE are refused too — the gate is not a POST-only guard."""
    client.force_login(_make_viewer(default_org, "viewer-verbs"))
    failures: list[str] = []
    for name, pattern in _ui_routes():
        if name in VIEWER_ALLOWED_ROUTES:
            continue
        url = "/" + _substitute(pattern)
        for method in ("patch", "put", "delete"):
            status = getattr(client, method)(
                url, {"dummy": "1"}, content_type="application/json"
            ).status_code
            if status not in VIEWER_DENIAL_STATUSES:
                failures.append(f"{name} ({url}) {method.upper()}: answered {status}")
    assert not failures, "viewer was not denied:\n" + "\n".join(failures)


def test_the_invariant_is_not_vacuous(client, make_case, default_org) -> None:
    """The same request reaches the view's own path for an analyst, 404s for a viewer.

    An analyst posting an empty note gets the view's validation behaviour (a redirect
    with an error message); a viewer gets 404 before that path is reached. Without
    `not_viewer` the sweep above would see that same 302 and fail.
    """
    case = make_case()
    analyst = get_user_model().objects.create(
        username="viewer-sweep-analyst", role="analyst", org=case.owner_org
    )
    client.force_login(analyst)
    url = reverse("ui-case-comment", kwargs={"case_id": case.number})
    assert client.post(url, {"body": ""}).status_code in (200, 302)

    client.force_login(_make_viewer(default_org, "viewer-sweep-vacuity"))
    assert client.post(url, {"body": ""}).status_code == 404


def test_viewer_keeps_the_read_surface(client, default_org) -> None:
    """Reads are untouched: the case list and the dashboard render for a viewer."""
    client.force_login(_make_viewer(default_org, "viewer-reads"))
    assert client.get(reverse("ui-case-list")).status_code == 200
    assert client.get(reverse("ui-dashboard")).status_code == 200


def test_allow_listed_auth_routes_stay_reachable(client, default_org) -> None:
    """login/logout/password-change must not be caught by the viewer gate."""
    client.force_login(_make_viewer(default_org, "viewer-auth"))
    for name in sorted(VIEWER_ALLOWED_ROUTES):
        assert client.get(reverse(name)).status_code != 404, name


def test_viewer_is_denied_before_parsing(client, default_org) -> None:
    """Malformed input never reaches validation: the denial is gate-first."""
    client.force_login(_make_viewer(default_org, "viewer-malformed"))
    url = reverse("ui-case-procedure-create", kwargs={"case_id": "not-a-case"})
    response = client.post(url, {"patternId": "\x00" * 100})
    assert response.status_code == 404
