"""Conformance tests for UI trailing-slash tolerance (Part A of PLAN-2026-10-13).

The middleware `StripTrailingSlashMiddleware` must:
- Redirect `/cases/` → `/cases` (301 for GET/HEAD, 308 for unsafe methods)
- Apply to all top-level UI pages, not just `/cases/`
- Preserve query strings
- Never rewrite `/api/...`, `/admin/...`, `/static/...`, `/media/...`, `/ws/...`
- Never redirect a path whose slash-less form does not resolve (stays 404)
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import resolve, reverse


@pytest.fixture
def analyst(db: None):
    from identity.models import Organisation, User

    org = Organisation.objects.create(name="Trailing Slash Org")
    return User.objects.create_user(
        username="tsanalyst",
        login="tsanalyst",
        password="hunter2-correct",
        email="tsanalyst@example.net",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def browser(analyst) -> Client:
    client = Client()
    client.force_login(analyst)
    return client


# --------------------------------------------------------------------------
# 1. Core redirect behaviour for /cases/
# --------------------------------------------------------------------------


def test_ui_cases_trailing_slash_301_then_200(browser: Client, analyst) -> None:
    """T1.1: GET /cases/ → 301 → /cases → 200 (for an authenticated writer)."""
    # Unauthenticated: /cases/ should redirect to /cases (which then redirects to login)
    anon = Client()
    response = anon.get("/cases/")
    assert response.status_code == 301
    assert response["Location"] == "/cases"

    # Follow the redirect chain: /cases/ → /cases → /login?next=/cases
    response = anon.get("/cases/", follow=True)
    assert response.status_code == 200
    assert response.redirect_chain[0] == ("/cases", 301)
    assert response.redirect_chain[1][0].startswith("/login?next=/cases")

    # Authenticated: /cases/ → 301 → /cases → 200
    response = browser.get("/cases/")
    assert response.status_code == 301
    assert response["Location"] == "/cases"

    response = browser.get("/cases/")
    assert response.status_code == 301
    assert response["Location"] == "/cases"

    # The target resolves to the case list view
    match = resolve("/cases")
    assert match.url_name == "ui-case-list"

    response = browser.get("/cases")
    assert response.status_code == 200


def test_ui_cases_trailing_slash_preserves_query_string(browser: Client) -> None:
    """Query string preserved: GET /cases/?closed=1 → 301, Location == /cases?closed=1."""
    response = browser.get("/cases/?closed=1")
    assert response.status_code == 301
    assert response["Location"] == "/cases?closed=1"


# --------------------------------------------------------------------------
# 2. Parametrised over all top-level converter-free UI pages
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,url_name",
    [
        ("/alerts/", "ui-alert-list"),
        ("/observables/", "ui-observable-list"),
        ("/tags/", "ui-tag-list"),
        ("/taxonomy/", "ui-taxonomy"),
        ("/automation/", "ui-automation-list"),
        ("/sources/", "ui-sources-list"),
        ("/case-templates/", "ui-case-template-list"),
    ],
)
def test_ui_top_level_pages_trailing_slash_301(browser: Client, path: str, url_name: str) -> None:
    """Every top-level UI page tolerates the trailing slash via one rule."""
    response = browser.get(path)
    assert response.status_code == 301, f"{path} → {response.status_code}, expected 301"

    expected = reverse(url_name)
    assert response["Location"] == expected, (
        f"{path} redirected to {response['Location']!r}, expected {expected!r}"
    )

    # The target actually resolves
    match = resolve(expected)
    assert match.url_name == url_name


# --------------------------------------------------------------------------
# 3. POST /cases/new/ → 308 (method-preserving)
# --------------------------------------------------------------------------


def test_ui_case_create_post_trailing_slash_308(browser: Client) -> None:
    """POST /cases/new/ → 308, Location == /cases/new (preserves method + body)."""
    response = browser.post("/cases/new/", {"title": "Test"})
    # The middleware returns 308 before the view runs (no CSRF check in test client)
    assert response.status_code == 308, f"expected 308, got {response.status_code}"
    assert response["Location"] == "/cases/new"


# --------------------------------------------------------------------------
# 4. Exclusions: /api/ and /admin/ must not be rewritten
# --------------------------------------------------------------------------


def test_api_v1_case_trailing_slash_not_rewritten() -> None:
    """T1.3: /api/v1/case/ is NOT rewritten by the middleware."""
    # Use an anonymous client so the API returns 401 (not 200 via session auth)
    anon = Client()
    response = anon.get("/api/v1/case/")
    # The API resolves /api/v1/case/ explicitly (both spellings registered)
    # and returns 401 (unauthenticated for API), NOT a 301/308 redirect.
    assert response.status_code not in (301, 308), (
        f"/api/v1/case/ was rewritten: {response.status_code} -> {response.get('Location')}"
    )
    assert response.status_code == 401, f"expected 401, got {response.status_code}"
    # Location header must not be the stripped path
    loc = response.get("Location", "")
    assert loc != "/api/v1/case", f"Location was rewritten to {loc}"


def test_admin_trailing_slash_keeps_own_behaviour(browser: Client) -> None:
    """/admin/ keeps its own behaviour: 302 to /admin/login/..."""
    response = browser.get("/admin/")
    assert response.status_code == 302
    assert response["Location"].startswith("/admin/login")


# --------------------------------------------------------------------------
# 5. Unknown path with trailing slash stays 404 (no redirect)
# --------------------------------------------------------------------------


def test_unknown_trailing_slash_stays_404(browser: Client) -> None:
    """An unknown path like /definitely-not-a-real-page/ stays 404."""
    response = browser.get("/definitely-not-a-real-page/")
    assert response.status_code == 404, (
        f"unknown path with trailing slash was redirected: {response.status_code}"
    )


# --------------------------------------------------------------------------
# 6. HEAD method also gets 301 (safe method)
# --------------------------------------------------------------------------


def test_ui_cases_trailing_slash_head_301(browser: Client) -> None:
    """HEAD /cases/ → 301 (safe method)."""
    response = browser.generic("HEAD", "/cases/")
    assert response.status_code == 301
    assert response["Location"] == "/cases"


# --------------------------------------------------------------------------
# 7. Viewer user also gets the redirect (list is readable)
# --------------------------------------------------------------------------


def test_ui_cases_trailing_slash_viewer_redirects(db: None) -> None:
    """GET /cases/ correctly redirects for a viewer too (list is readable; only creation is not)."""
    from identity.models import Organisation, User

    org = Organisation.objects.create(name="Viewer Slash Org")
    viewer = User.objects.create_user(
        username="tsviewer",
        login="tsviewer",
        password="hunter2-correct",
        email="tsviewer@example.net",
        org=org,
        is_active=True,
        role="viewer",
    )
    browser = Client()
    browser.force_login(viewer)

    response = browser.get("/cases/")
    assert response.status_code == 301
    assert response["Location"] == "/cases"

    response = browser.get("/cases")
    assert response.status_code == 200
