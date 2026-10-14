"""ADR-006 invariant — no JSON endpoint accepts an unsafe verb from a viewer.

The guarantee is global and fail-closed: `identity.permissions.ViewerReadOnly` sits
in `DEFAULT_PERMISSION_CLASSES`, so every present and future DRF endpoint is covered
without being touched. This module enumerates the `/api/v1/` URLconf and proves it by
brute force — POST/PATCH/PUT/DELETE as a viewer must never answer 2xx/3xx.

Allow-list: `login` and `logout` (`AllowAny` by design). The ingest webhook is a plain
Django view with its own token authentication and is not part of DRF's permission
list; it answers 401 to an unauthenticated POST.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import URLPattern, URLResolver, get_resolver

from core.enums import VIEWER_ROLE
from identity.permissions import ViewerReadOnly

pytestmark = pytest.mark.django_db

API_ALLOWED_ROUTES: frozenset[str] = frozenset({"api-login", "api-logout"})
UNSAFE_METHODS = ("post", "patch", "put", "delete")

#: The API's denial for a viewer: DRF's 403 from `ViewerReadOnly`, a 404/405 from the
#: routing layer, or a 401 when the route needs its own credential (the webhook).
API_DENIAL_STATUSES = frozenset({401, 403, 404, 405})

_CONVERTER = re.compile(r"<(?:uuid|str|slug|int|path):([^>]+)>")


def _substitute(pattern: str) -> str:
    def repl(match: re.Match[str]) -> str:
        converter = match.group(0)
        if "uuid" in converter:
            return "11111111-1111-1111-1111-111111111111"
        if "int" in converter:
            return "1"
        return "x"

    return _CONVERTER.sub(repl, pattern)


def _iter_api_routes(patterns=None, prefix: str = ""):
    for pattern in patterns if patterns is not None else get_resolver().url_patterns:
        full = prefix + str(getattr(pattern, "pattern", "")).replace("^", "").replace("$", "")
        if isinstance(pattern, URLResolver):
            yield from _iter_api_routes(pattern.url_patterns, full)
        elif isinstance(pattern, URLPattern) and full.startswith("api/"):
            yield getattr(pattern, "name", "") or full, full


def _api_routes() -> list[tuple[str, str]]:
    routes = list(_iter_api_routes())
    assert len(routes) > 60, f"api enumeration looks broken: only {len(routes)} routes"
    for sample in ("case", "alert", "observable", "playbook"):
        assert any(sample in pattern for _, pattern in routes), f"{sample} route missing"
    return routes


def _viewer(org, username: str = "viewer-api"):
    # `User.org` is NOT NULL (ADR-007): a viewer must belong to an organisation.
    return get_user_model().objects.create(username=username, role=VIEWER_ROLE, org=org)


def _is_allowed(name: str) -> bool:
    """`api-login-slash` is the same view as `api-login` under a different spelling."""
    return name.removesuffix("-slash").removesuffix("_slash") in API_ALLOWED_ROUTES


def test_every_api_route_denies_a_viewer_unsafe_verb(client, default_org) -> None:
    """Every unsafe verb on every endpoint is refused for a viewer."""
    client.force_login(_viewer(default_org, "viewer-api"))
    failures: list[str] = []

    for name, pattern in _api_routes():
        if _is_allowed(name):
            continue
        url = "/" + _substitute(pattern)
        for method in UNSAFE_METHODS:
            response = getattr(client, method)(url, {"dummy": "1"}, content_type="application/json")
            if response.status_code not in API_DENIAL_STATUSES:
                failures.append(f"{method.upper()} {url} ({name}) answered {response.status_code}")

    assert not failures, "viewer was not denied:\n" + "\n".join(failures)


def test_api_reads_still_work_for_a_viewer(client, default_org) -> None:
    """The rule is additive: safe verbs keep working for a viewer."""
    client.force_login(_viewer(default_org, "viewer-api-reads"))
    response = client.get("/api/v1/case")
    assert response.status_code == 200


def test_viewer_cannot_write_through_the_query_api(client, default_org) -> None:
    """`POST /api/v1/query` is a read-through-POST and is denied (deviation, ADR-006 §decision 8)."""
    client.force_login(_viewer(default_org, "viewer-query"))
    assert client.post("/api/v1/query", {"query": "{}"}).status_code == 403


def test_api_key_belonging_to_a_viewer_is_denied_writes(make_user) -> None:
    """A viewer's own `readwrite` key is still refused — role ∧ scope (ADR-006 decision 8)."""
    from tests.conformance._t1_seed import _api_key_client

    viewer = make_user(role=VIEWER_ROLE)
    client, _token = _api_key_client(viewer, scope="readwrite")
    response = client.post(
        "/api/v1/case",
        {"title": "from-viewer-key", "description": "", "severity": 2},
        content_type="application/json",
    )
    assert response.status_code == 403, response.content


# --- The permission class in isolation: additive, not an allow-list ------------


class _Request:
    def __init__(self, method: str, user: object) -> None:
        self.method = method
        self.user = user


def _perm() -> ViewerReadOnly:
    return ViewerReadOnly()


def test_viewer_readonly_denies_unsafe_for_a_viewer(make_user) -> None:
    viewer = make_user(role=VIEWER_ROLE)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert _perm().has_permission(_Request(method, viewer), None) is False, method


def test_viewer_readonly_allows_safe_methods(make_user) -> None:
    viewer = make_user(role=VIEWER_ROLE)
    for method in ("GET", "HEAD", "OPTIONS"):
        assert _perm().has_permission(_Request(method, viewer), None) is True, method


@pytest.mark.parametrize("role", ["analyst", "admin", "superAdmin"])
def test_viewer_readonly_never_denies_a_non_viewer(make_user, role: str) -> None:
    user = make_user(role=role)
    assert _perm().has_permission(_Request("POST", user), None) is True


def test_viewer_readonly_passes_anonymous_through() -> None:
    """`IsAuthenticated` owns the 401; this class must not turn it into a 403."""
    from django.contrib.auth.models import AnonymousUser

    assert _perm().has_permission(_Request("POST", AnonymousUser()), None) is True
