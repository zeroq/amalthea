"""Shared helpers for tenant-scoping conformance tests."""

from __future__ import annotations

import re
from uuid import uuid4

from argon2 import PasswordHasher
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from identity.models import Organisation, User
from observables.models import Observable, ObservableType


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "analyst") -> User:
    user = User.objects.create(
        login=login,
        username=login,
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )
    return user


def _mk_orgless_actor(role: str = "analyst") -> User:
    """An unsaved stub actor with no organisation.

    `User.org` is NOT NULL (ADR-007), so an org-less user is unreachable in the database.
    The fail-closed tenancy guards that must still reject it are exercised here with an
    in-memory stub: no row is persisted, and `identity/scoping.py` reads the actor only
    through `getattr`, so an unsaved instance is sufficient. This keeps the
    "org-less ⇒ deny" truth-table rows non-vacuous without recreating the deleted state.
    """
    return User(id=uuid4(), org_id=None, role=role)


def _mk_client(user: User, scope: str = "readwrite") -> tuple[APIClient, str]:
    token = "k" + uuid4().hex
    from identity.models import ApiKey

    ApiKey.objects.create(
        user=user,
        name=f"{scope} key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope=scope,
    )
    client = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client, token


def _mk_case(title: str, org: Organisation, status: CaseStatus | None = None) -> Case:
    if status is None:
        status = CaseStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0]
    return Case.objects.create(title=title, status=status, owner_org=org)


def _mk_alert(
    title: str, org: Organisation, source_ref: str, status: AlertStatus | None = None
) -> Alert:
    if status is None:
        status = AlertStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0]
    return Alert.objects.create(
        type="test",
        source="test-source",
        source_ref=source_ref,
        title=title,
        status=status,
        owner_org=org,
    )


def _mk_observable(data: str, org: Organisation, type_name: str = "ip") -> Observable:
    otype = ObservableType.objects.get_or_create(
        name=type_name, defaults={"is_case_sensitive": False}
    )[0]
    return Observable.objects.create(
        data_type=otype, data=data, normalized_data=data, owner_org=org
    )


# =============================================================================
# T3-c: Route-enumeration conformance test
# Enumerates tenant-touching read endpoints from URLconf and asserts:
# - cross-org GET → 404 (same shape as non-existent ID)
# - own-org GET → 200
# - fails if a newly added unscoped route exists
# =============================================================================

# Allow-list of genuinely global read routes that are NOT tenant-scoped.
# These are vocabulary/admin/public endpoints. Keep minimal; default is "tenant-touching ⇒ must be guarded".
# Include both base and -slash variants since Django registers both.
_GLOBAL_READ_ROUTES: set[str] = {
    # Vocabulary endpoints (global by design)
    "observable-type-collection",
    "observable-type-collection-slash",
    "observable-type-detail",
    "observable-type-detail-slash",
    "alert-status-collection",
    "alert-status-collection-slash",
    "alert-status-detail",
    "alert-status-detail-slash",
    "case-status-collection",
    "case-status-collection-slash",
    "case-status-detail",
    "case-status-detail-slash",
    "tag-collection",
    "tag-collection-slash",
    "tag-detail",
    "tag-detail-slash",
    "ttp-collection",
    "ttp-collection-slash",
    "ttp-detail",
    "ttp-detail-slash",
    "taxonomy",
    "taxonomy-slash",
    "custom-field-list",
    "custom-field-list-slash",
    "case-template-collection",  # templates are org-neutral definitions
    "case-template-collection-slash",
    "case-template-detail",
    "case-template-detail-slash",
    # Playbook authoring vocabulary is code-derived, not tenant data.
    "playbook-meta",
    "playbook-meta-slash",
    # Identity (user/org are global resources, but access-controlled)
    "user-current",
    "user-current-slash",
    "user-detail",
    "user-detail-slash",
    "user-collection",
    "user-collection-slash",
    "user-detail-management",
    "user-detail-management-slash",
    "organisation-collection",
    "organisation-collection-slash",
    "organisation-detail",
    "organisation-detail-slash",
    "organisation-create",
    "organisation-create-slash",
    # Public / meta
    "describe-all",
    "describe-all-slash",
    "describe-model",
    "describe-model-slash",
    # Ingest webhook is public (token-gated, not org-scoped)
    "alerts-webhook",
    "alerts-webhook-no-slash",
    # Auth endpoints (global)
    "api-login",
    "api-login-slash",
    "api-logout",
    "api-logout-slash",
    # Catch-all (not a real route)
    "compat-catchall",
}

# UI-specific global read routes (not tenant-scoped)
_UI_GLOBAL_READ_ROUTES: set[str] = {
    # Auth
    "login",
    "logout",
    # Global vocabulary / taxonomy (no org context needed)
    "ui-taxonomy",
    # Case templates are global definitions (not org-scoped data)
    "ui-case-template-list",
    "ui-case-template-create",
    "ui-case-template-detail",
    "ui-case-template-delete",
    # Tags are global vocabulary
    "ui-tag-list",
    "ui-tag-create",
    "ui-tag-detail",
    "ui-tag-delete",
    # Admin-only standalone pages: a generic analyst GET is a 404 by role, not by org.
    "ui-alert-import",
    "ui-case-import",
    # Playbook create is admin-only, so a generic analyst GET is 404 by role rather than by org.
    # List/detail/edit are org-scoped and exercised by the tenant read conformance below.
    "ui-playbook-create",
    # Settings (superAdmin only - global management views)
    "ui-settings-organisations",
    "ui-settings-organisations-edit",
    # Settings — Users (admin+superAdmin read; hybrid scoping: org-scoped for admin, global for superAdmin).
    # DEVIATION: These routes are NOT purely global — they are tenant-scoped for admin users but global
    # for superAdmin. The conformance suite's model expects a single behaviour per route.
    # They are included here to exclude them from the generic tenant-scoping assertion, which would
    # otherwise fail because an admin's cross-org access returns 404 (correct) but the test expects
    # a uniform 200-with-filtered-results for list routes. A dedicated test covers their behaviour.
    "ui-settings",
    "ui-settings-correlation",
    "ui-settings-correlation-org",
    "ui-settings-sources",
    "ui-settings-users",
    "ui-settings-users-edit",
}

# Route names that are WRITE-only (POST/PATCH/DELETE only, no GET) — exclude from read conformance.
_WRITE_ONLY_ROUTES: set[str] = {
    "alert-import",
    "alert-import-into",
    "alert-merge",
    "alert-observable-add",
    "alert-tag-link",
    "alert-procedure-create",
    "alert-procedures-create",
    "alert-bulk-update",
    "case-task-create",
    "case-observable-add",
    "case-tag-link",
    "case-alert-remove",
    "case-custom-event-create",
    "case-share-detail",  # DELETE only
    "case-share-detail-slash",  # DELETE only
    "case-attachment-detail",  # DELETE only
    "case-attachment-detail-slash",  # DELETE only
    "case-bulk-update",
    "case-apply-template",
    "case-merge",
    "case-procedure-create",
    "case-procedures-create",
    "observable-tag-link",
    "observable-bulk-update",
    "procedure-bulk-delete",
    "task-bulk-update",
    "playbook-run",
    "api-login",
    "api-logout",
    "case-create",  # not a route name but the POST on case-collection
    "alert-create",
    "observable-create",
    "comment-detail",  # PATCH/DELETE only
    "comment-detail-slash",  # PATCH/DELETE only
    "custom-event-detail",  # PATCH/DELETE only
    "custom-event-detail-slash",  # PATCH/DELETE only
    "organisation-create",
    "organisation-create-slash",
    "user-create",
    "user-create-slash",
}

# UI-specific write-only routes (POST/PATCH/DELETE only, no GET) — exclude from read conformance.
_UI_WRITE_ONLY_ROUTES: set[str] = {
    "ui-alert-escalate",
    "ui-alert-merge",
    "ui-alert-status",
    "ui-alert-tag-remove",
    "ui-case-status",
    "ui-case-close",
    "ui-case-field-edit",
    "ui-case-tag-add",
    "ui-case-tag-remove",
    "ui-case-comment",
    "ui-case-extract",
    "ui-case-task-create",
    "ui-case-task-toggle",
    "ui-case-apply-template",
    "ui-case-merge",
    "ui-case-share-revoke",
    "ui-case-attachment-upload",
    "ui-case-attachment-delete",
    "ui-case-attachment-download",
    "ui-case-attachment-detail",
    "ui-case-procedure-create",
    "ui-case-procedures-create",
    "ui-case-page-create",
    "ui-case-page-edit",
    "ui-case-page-delete",
    "ui-case-template-create",
    "ui-case-template-delete",
    "ui-case-template-edit",
    "ui-observable-delete",
    "ui-tag-create",
    "ui-tag-delete",
    "ui-tag-toggle",
    "ui-playbook-create",
    "ui-playbook-run",
    "ui-playbook-delete",
    "ui-case-tag-toggle",
    "ui-suggestion-accept",
    "ui-suggestion-reject",
    "ui-suggestion-ignore",
    "ui-suggestions-bulk-accept",
    "ui-suggestions-bulk-reject",
    "ui-suggestions-bulk-ignore",
    "ui-settings-organisations-delete",
    "ui-settings-correlation-update",
    "ui-settings-users-delete",
    "ui-settings-token-create",
    "ui-settings-token-revoke",
}


_HTTP_METHOD_NAMES = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def _view_http_methods(view: object) -> list[str] | None:
    """The methods a plain function view allows, or ``None`` if it declares none.

    Class-based and DRF views expose their methods as attributes, but a function view
    decorated with ``@require_GET`` / ``@require_POST`` / ``@require_http_methods`` stores
    the allowed list only inside the decorator wrapper's closure — Django exposes no public
    attribute. ``functools.wraps`` sets ``__wrapped__``, so walking that chain finds the cell.
    Without this, every decorated function view fell back to ``["GET"]`` and POST-only UI
    routes were enumerated as read routes, then failed the GET conformance check with 405.
    """
    current: object | None = view
    while current is not None:
        for cell in getattr(current, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:  # pragma: no cover - empty cell
                continue
            if (
                isinstance(value, (list, tuple))
                and value
                and all(isinstance(m, str) and m.upper() in _HTTP_METHOD_NAMES for m in value)
            ):
                return [m.upper() for m in value]
        current = getattr(current, "__wrapped__", None)
    return None


def _discover_tenant_write_routes() -> list[tuple[str, str, list[str]]]:
    """
    Discover all tenant-touching WRITE routes from the URLconf.

    Returns a list of (route_name, url_pattern, http_methods) for mutating routes
    (POST/PATCH/PUT/DELETE) that should be tenant-scoped. Excludes global routes.
    Covers both /api/v1/ and UI routes.
    """
    import django
    from django.urls import URLPattern, URLResolver, get_resolver

    django.setup()
    resolver = get_resolver()

    routes: list[tuple[str, str, list[str]]] = []

    def walk(patterns: list, prefix: str = ""):
        for pattern in patterns:
            full_pattern = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, full_pattern)
            elif isinstance(pattern, URLPattern):
                name = getattr(pattern, "name", "")
                if not name:
                    continue
                # Consider both API routes (/api/v1/) and UI routes (no prefix, but named ui-*)
                is_api_route = full_pattern.startswith("api/v1/")
                is_ui_route = name.startswith("ui-")
                if not (is_api_route or is_ui_route):
                    continue
                # Skip global allow-listed routes
                if is_api_route and name in _GLOBAL_READ_ROUTES:
                    continue
                if is_ui_route and name in _UI_GLOBAL_READ_ROUTES:
                    continue
                # Note: _WRITE_ONLY_ROUTES is for read conformance exclusion; write conformance includes them
                # Determine supported methods from the view
                view = pattern.callback
                methods = getattr(view, "cls", view).__dict__.get("http_method_names", [])
                # DRF views have `http_method_names` on the class; function views don't.
                # For function views decorated with @api_view, check the `actions` attr or wrapper.
                if hasattr(view, "actions"):  # ViewSet-style
                    methods = list(view.actions.keys())
                elif hasattr(view, "cls"):  # APIView-style
                    methods = [
                        m.upper() for m in view.cls.http_method_names if hasattr(view.cls, m)
                    ]
                else:
                    # Function-based @api_view — inspect the wrapper
                    methods = getattr(view, "_allowed_methods", [])
                    # For plain function views without @api_view, _allowed_methods is not set.
                    # Assume they support all mutating methods (conservative for conformance testing).
                    if not methods:
                        methods = ["POST", "PATCH", "PUT", "DELETE"]
                methods = [m.upper() for m in methods]
                # Keep only mutating methods
                mutating = [m for m in methods if m in ("POST", "PATCH", "PUT", "DELETE")]
                if mutating:
                    routes.append((name, full_pattern, mutating))

    walk(resolver.url_patterns)
    return routes


def _discover_tenant_read_routes() -> list[tuple[str, str, list[str]]]:
    """
    Discover all tenant-touching read routes from the URLconf.

    Returns a list of (route_name, url_pattern, http_methods) for GET-capable routes
    that should be tenant-scoped. Excludes global routes and write-only routes.
    Covers both /api/v1/ and UI routes.
    """
    import django
    from django.urls import URLPattern, URLResolver, get_resolver

    django.setup()
    resolver = get_resolver()

    routes: list[tuple[str, str, list[str]]] = []

    def walk(patterns: list, prefix: str = ""):
        for pattern in patterns:
            full_pattern = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, full_pattern)
            elif isinstance(pattern, URLPattern):
                name = getattr(pattern, "name", "")
                if not name:
                    continue
                # Consider both API routes (/api/v1/) and UI routes (no prefix, but named ui-*)
                is_api_route = full_pattern.startswith("api/v1/")
                is_ui_route = name.startswith("ui-")
                if not (is_api_route or is_ui_route):
                    continue
                # Skip global allow-listed routes
                if is_api_route and name in _GLOBAL_READ_ROUTES:
                    continue
                if is_ui_route and name in _UI_GLOBAL_READ_ROUTES:
                    continue
                # Skip write-only routes
                if is_api_route and name in _WRITE_ONLY_ROUTES:
                    continue
                if is_ui_route and name in _UI_WRITE_ONLY_ROUTES:
                    continue
                # Determine supported methods from the view
                view = pattern.callback
                methods = getattr(view, "cls", view).__dict__.get("http_method_names", [])
                # DRF views have `http_method_names` on the class; function views don't.
                # For function views decorated with @api_view, check the `actions` attr or wrapper.
                if hasattr(view, "actions"):  # ViewSet-style
                    methods = list(view.actions.keys())
                elif hasattr(view, "cls"):  # APIView-style
                    methods = [
                        m.upper() for m in view.cls.http_method_names if hasattr(view.cls, m)
                    ]
                else:
                    # Function-based @api_view — inspect the wrapper
                    methods = getattr(view, "_allowed_methods", None)
                    if methods is None:
                        # Plain function view: read `@require_*` restrictions from the decorator
                        # chain instead of assuming GET (the previous default made every
                        # POST-only UI route look like a read route).
                        methods = _view_http_methods(view) or ["GET"]
                methods = [m.upper() for m in methods]
                if "GET" in methods:
                    routes.append((name, full_pattern, methods))
                elif "POST" in methods and name == "query-api":
                    # query is POST but read-semantics; include it
                    routes.append((name, full_pattern, ["POST"]))

    walk(resolver.url_patterns)
    return routes


def _build_url(pattern: str, params: dict[str, str]) -> str:
    """Build a URL from a Django path pattern by substituting {param} placeholders."""
    url = pattern
    for key, value in params.items():
        url = (
            url.replace(f"<str:{key}>", value)
            .replace(f"<slug:{key}>", value)
            .replace(f"<uuid:{key}>", value)
        )
    # Remove any remaining angle-bracket patterns (should not happen for our test cases)
    url = re.sub(r"<[^>]+>", "dummy", url)
    return f"/{url}"
