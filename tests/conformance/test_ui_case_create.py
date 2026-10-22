"""Conformance tests for the UI case create surface (`ui-case-create`).

Covers the acceptance criteria from PLAN-2026-10-13-ui-case-create.md:
- P1-a..f (shared service behaviour through the API)
- P2-a..k (UI view behaviour)
- P4-b..d (CSRF, number allocation, title truncation)

Follows the fixture/style of `test_ui_p2_wiring.py` and `test_ui_urls_smoke.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from cases.models import Case, CaseStatus, TimelineEvent
from identity.models import AuditLog, Organisation, User
from observables.models import ObservableType

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Create Case Org")


@pytest.fixture
def other_org(db: None) -> Organisation:
    return Organisation.objects.create(name="Create Case Other Org")


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="createanalyst",
        login="createanalyst",
        password="hunter2-correct",
        email="createanalyst@example.net",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def outsider(db: None, other_org: Organisation) -> User:
    return User.objects.create_user(
        username="createoutsider",
        login="createoutsider",
        password="hunter2-correct",
        email="createoutsider@example.net",
        org=other_org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def viewer(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="createviewer",
        login="createviewer",
        password="hunter2-correct",
        email="createviewer@example.net",
        org=org,
        is_active=True,
        role="viewer",
    )


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="createanalyst", password="hunter2-correct")
    return client


@pytest.fixture
def outsider_browser(outsider: User) -> Client:
    client = Client()
    assert client.login(username="createoutsider", password="hunter2-correct")
    return client


@pytest.fixture
def viewer_browser(viewer: User) -> Client:
    client = Client()
    assert client.login(username="createviewer", password="hunter2-correct")
    return client


@pytest.fixture
def csrf_browser(analyst: User) -> Client:
    """A client that actually enforces CSRF, so the guard can be asserted."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(analyst)
    return client


@pytest.fixture
def case_status(db: None) -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


@pytest.fixture
def status_in_progress(db: None) -> CaseStatus:
    return CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 2}
    )[0]


# --------------------------------------------------------------------------
# GET /cases/new
# --------------------------------------------------------------------------


def test_ui_case_create_get_returns_form(browser: Client) -> None:
    """P2-a: GET /cases/new → 200 with the form present; all field names appear."""
    url = reverse("ui-case-create")
    response = browser.get(url)
    assert response.status_code == 200
    body = response.content.decode()

    # CSRF token present
    assert "csrfmiddlewaretoken" in body

    # All eleven controls with labels
    for field_name in (
        "title",
        "description",
        "summary",
        "severity",
        "tlp",
        "pap",
        "status",
        "assignee",
        "flag",
        "tags",
    ):
        assert f'name="{field_name}"' in body, f"missing field {field_name}"

    # Title is required
    assert 'name="title"' in body
    assert "required" in body


def test_ui_case_create_get_form_labels_and_maxlength(browser: Client) -> None:
    """P2-a: every control has a visible <label for> / id pairing; the title input carries maxlength="500"."""
    url = reverse("ui-case-create")
    response = browser.get(url)
    assert response.status_code == 200
    body = response.content.decode()

    # The title input keeps the template's bound (ui/case_create.html:21, plan §7 maxlength=500;
    # P4-d pins the 600-char truncation that the same bound protects).
    assert 'name="title"' in body
    assert 'maxlength="500"' in body

    # Visible labels paired with control ids (the flag checkbox is a wrapping <label class="check">,
    # not a for=/id= pair, so it is deliberately absent from this loop).
    for control_id in (
        "f-title",
        "f-description",
        "f-summary",
        "f-severity",
        "f-tlp",
        "f-pap",
        "f-status",
        "f-assignee",
        "f-tags",
    ):
        assert f'id="{control_id}"' in body, f"missing control id {control_id}"
        assert f'for="{control_id}"' in body, f"missing label for {control_id}"

    assert 'type="submit"' in body
    assert "Create case" in body


def test_ui_case_create_anonymous_redirects_to_login() -> None:
    """P2-g: unauthenticated GET /cases/new → 302 to the login page with ?next=/cases/new."""
    url = reverse("ui-case-create")
    response = Client().get(url)

    assert response.status_code == 302
    assert response.url == "/login?next=/cases/new"


# --------------------------------------------------------------------------
# POST /cases/new — success path
# --------------------------------------------------------------------------


def test_ui_case_create_post_title_only_redirects_to_detail(
    browser: Client, status_in_progress: CaseStatus, analyst: User
) -> None:
    """P2-b: POST title only → 302 to case-detail; case exists with defaults; timeline + audit."""
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "Minimal case"})

    assert response.status_code == 302
    assert response.url.endswith("/cases/1")

    case = Case.objects.get(number=1)
    assert case.title == "Minimal case"
    assert case.status == status_in_progress
    assert case.severity == 2  # default
    assert case.tlp == 2  # default
    assert case.pap == 2  # default
    assert case.owner_org == analyst.org

    # Timeline event: case-created
    event = TimelineEvent.objects.filter(case=case, kind="case-created").first()
    assert event is not None
    assert event.title == "Case created"
    assert event.actor == analyst

    # AuditLog row
    audit = AuditLog.objects.filter(
        action="case.created", target_type="Case", target_id=str(case.id)
    ).first()
    assert audit is not None
    assert audit.actor == analyst
    assert audit.target_org == analyst.org


def test_ui_case_create_post_with_extractable_artifact_creates_observable(
    browser: Client, status_in_progress: CaseStatus, analyst: User
) -> None:
    """P2-i (extraction on): description with extractable artifact → Suggestion created for case.

    The extractor vocabulary (observables/extractor.py) recognises:
    - mail: attacker@example.net
    - ip: 192.0.2.10
    - hash: a-f/0-9 32/40/64 chars
    - fqdn: evil.example.com
    - url: http://...
    - file: /path/to/file.txt

    P6: Extraction now creates ObservableSuggestion rows (status=proposed) that must be accepted
    to become CaseObservable links. This test verifies the suggestion is created.
    """
    url = reverse("ui-case-create")
    # Description contains an email address (mail type)
    response = browser.post(
        url,
        {
            "title": "Phishing case",
            "description": "Received from attacker@example.net with malicious payload",
        },
    )

    assert response.status_code == 302
    case = Case.objects.get(number=1)

    # A suggestion of type mail should be created (P6: suggestions flow)
    from observables.models import ObservableSuggestion

    suggestions = ObservableSuggestion.objects.filter(case=case, status="proposed")
    assert suggestions.exists()

    mail_type = ObservableType.objects.get(name="mail")
    mail_suggestion = suggestions.filter(data_type=mail_type).first()
    assert mail_suggestion is not None
    assert mail_suggestion.normalized_data == "attacker@example.net"


def test_ui_case_create_two_posts_two_distinct_numbers(
    browser: Client, status_in_progress: CaseStatus
) -> None:
    """P4-c: Two POSTs → two distinct Case.number values (allocation is not title-keyed)."""
    url = reverse("ui-case-create")
    browser.post(url, {"title": "First case"})
    browser.post(url, {"title": "Second case"})

    case1 = Case.objects.get(number=1)
    case2 = Case.objects.get(number=2)
    assert case1.number == 1
    assert case2.number == 2
    assert case1.title == "First case"
    assert case2.title == "Second case"


def test_ui_case_create_title_truncated_to_500(browser: Client) -> None:
    """P4-d: 600-char title is truncated to 500 on the created row."""
    url = reverse("ui-case-create")
    long_title = "x" * 600
    response = browser.post(url, {"title": long_title})

    assert response.status_code == 302
    case = Case.objects.get(number=1)
    assert len(case.title) == 500


def test_ui_case_create_post_tags_creates_tag_links(browser: Client) -> None:
    """P2-b extension: POST tags="a, b" → two Tag rows linked to the case."""
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "Tagged case", "tags": "phishing, apt29"})

    assert response.status_code == 302
    case = Case.objects.get(number=1)
    tag_names = set(case.tags.values_list("name", flat=True))
    assert tag_names == {"phishing", "apt29"}


def test_ui_case_create_post_mass_assignment_keys_ignored(
    browser: Client, org: Organisation, other_org: Organisation, analyst: User
) -> None:
    """§9: a UI create POST carrying owner_org/number/id/created_at/closed_date/start_date/end_date sets none.

    The service reads only CASE_SCALAR_FIELDS + status/assignee/tags (cases/create.py:132-143);
    `owner_org` is the actor's org, `number` is allocated by `Case.save()`, and `id`/`created_at`
    are server-assigned — plan §9's field-injection pin (the security review probed this by hand).
    """
    from uuid import uuid4

    supplied_id = str(uuid4())
    url = reverse("ui-case-create")
    response = browser.post(
        url,
        {
            "title": "Mass assignment probe",
            "owner_org": str(other_org.pk),
            "number": "999",
            "id": supplied_id,
            "created_at": "1999-01-01T00:00:00Z",
            "closed_date": "1999-01-01T00:00:00Z",
            "start_date": "1999-01-01T00:00:00Z",
            "end_date": "1999-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 302
    assert response.url.endswith("/cases/1")
    case = Case.objects.get(number=1)
    # owner_org is the actor's organisation, never the injected one.
    assert case.owner_org == analyst.org
    assert case.owner_org != other_org
    # number is a fresh allocation, not the injected 999.
    assert case.number == 1
    # id is server-assigned; the supplied UUID was ignored and the case is canonical/reachable.
    assert str(case.pk) != supplied_id
    assert reverse("ui-case-detail", args=[case.number]).endswith("/cases/1")
    # Timestamp fields are server-assigned (recent), not the injected 1999 values.
    assert case.created_at.year != 1999
    assert case.start_date.year != 1999
    assert case.closed_date is None
    assert case.end_date is None


def test_ui_case_create_post_full_field_set_creates_case(
    browser: Client, status_in_progress: CaseStatus, analyst: User
) -> None:
    """§13.2: one POST with every form field → 302; all values, timeline, audit and observable present."""
    url = reverse("ui-case-create")
    response = browser.post(
        url,
        {
            "title": "Full field case",
            "description": "Phishing from attacker@example.net asking for credentials",
            "summary": "Credentials phishing",
            "severity": "4",
            "tlp": "3",
            "pap": "1",
            "status": "InProgress",
            "assignee": "createanalyst",
            "flag": "on",
            "tags": "phishing, apt29",
        },
    )

    assert response.status_code == 302
    assert response.url.endswith("/cases/1")
    case = Case.objects.get(number=1)
    assert case.title == "Full field case"
    assert case.description == "Phishing from attacker@example.net asking for credentials"
    assert case.summary == "Credentials phishing"
    assert case.severity == 4
    assert case.tlp == 3
    assert case.pap == 1
    assert case.status == status_in_progress
    assert case.assignee == analyst
    assert case.flag is True
    assert {t.name for t in case.tags.all()} == {"phishing", "apt29"}
    assert case.owner_org == analyst.org

    # The case-created ledger entry (P2-b / §13.2).
    event = TimelineEvent.objects.filter(case=case, kind="case-created").first()
    assert event is not None
    assert event.title == "Case created"
    assert event.actor == analyst

    # The in-transaction audit row (case.created, T2.4).
    audit = AuditLog.objects.filter(
        action="case.created", target_type="Case", target_id=str(case.id)
    ).first()
    assert audit is not None
    assert audit.actor == analyst
    assert audit.target_org == analyst.org

    # Extraction (W102-2: unconditional): the embedded mail address creates a Suggestion.
    mail_type = ObservableType.objects.get(name="mail")
    from observables.models import ObservableSuggestion

    suggestion = ObservableSuggestion.objects.filter(
        case=case, data_type=mail_type, status="proposed"
    ).first()
    assert suggestion is not None
    assert suggestion.normalized_data == "attacker@example.net"


def test_ui_case_create_auto_created_default_status_flashes_warning(
    browser: Client, analyst: User
) -> None:
    """T1.3b/T2.5: an auto-created status records an ingestion warning and the UI flashes it.

    With the seeded InProgress row removed, create_case's default status resolve
    (`resolve_case_status(DEFAULT_CASE_STATUS)`, alerts/escalation.py:51-58) get_or_creates it and
    records `case_status_created: ...` on `ingestion_warnings` (T1.3b); ui/views.py:554-555 flashes
    that warning list rather than swallowing it (T2.5).
    """
    CaseStatus.objects.filter(value="InProgress").delete()
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "Warning case"})

    assert response.status_code == 302
    case = Case.objects.get(number=1)
    warnings = case.ingestion_warnings.get("warnings", []) or []
    assert any("case_status_created" in w and "InProgress" in w for w in warnings), warnings
    # The default resolve re-created the status row.
    assert CaseStatus.objects.filter(value="InProgress").exists()

    # T2.5 — the warning is surfaced as a message.warning flash on the redirect target.
    follow = browser.get(response.url)
    assert follow.status_code == 200
    body = follow.content.decode()
    assert "Created, with warnings:" in body
    assert "case_status_created" in body


# --------------------------------------------------------------------------
# POST /cases/new — validation errors (200 re-render, no case created)
# --------------------------------------------------------------------------


def test_ui_case_create_post_empty_title_rerenders(browser: Client) -> None:
    """P2-c: Empty/whitespace title → 200 re-render, error message, no case row."""
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "   "})

    assert response.status_code == 200
    assert Case.objects.count() == 0
    body = response.content.decode()
    assert "required" in body.lower() or "error" in body.lower()


def test_ui_case_create_post_blank_title_rerenders(browser: Client) -> None:
    """P2-c: Blank title → 200 re-render, error message, no case row."""
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": ""})

    assert response.status_code == 200
    assert Case.objects.count() == 0


def test_ui_case_create_post_cross_org_assignee_rerenders(
    browser: Client, outsider: User, status_in_progress: CaseStatus
) -> None:
    """P2-e: Assignee from another org → 200 re-render, no case created."""
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "Cross-org attempt", "assignee": outsider.login})

    assert response.status_code == 200
    assert Case.objects.count() == 0
    body = response.content.decode()
    assert "organisation" in body.lower() or "error" in body.lower()


def test_ui_case_create_post_cross_org_assignee_uuid_rerenders(
    browser: Client, outsider: User, status_in_progress: CaseStatus
) -> None:
    """P2-e: Assignee UUID from another org → 200 re-render, no case created."""
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "Cross-org UUID", "assignee": str(outsider.pk)})

    assert response.status_code == 200
    assert Case.objects.count() == 0


def test_ui_case_create_post_unknown_status_rerenders(browser: Client) -> None:
    """P2-d: Unknown status value → 200 re-render, no case row, no CaseStatus minted."""
    before = CaseStatus.objects.count()
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "Bad status", "status": "MadeUpStage"})

    assert response.status_code == 200
    assert Case.objects.count() == 0
    assert CaseStatus.objects.count() == before


def test_ui_case_create_post_severity_zero_rerenders(browser: Client) -> None:
    """Stricter enum validation: severity=0 → 200 re-render (clean 400, not 500)."""
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "Bad severity", "severity": "0"})

    assert response.status_code == 200
    assert Case.objects.count() == 0


def test_ui_case_create_post_tlp_nine_rerenders(browser: Client) -> None:
    """Stricter enum validation: tlp=9 → 200 re-render (clean 400, not 500)."""
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "Bad TLP", "tlp": "9"})

    assert response.status_code == 200
    assert Case.objects.count() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("severity", "abc"), ("tlp", "abc")],
)
def test_ui_case_create_post_non_integer_enum_rerenders(
    browser: Client, field: str, value: str
) -> None:
    """T1.2: a non-integer severity/tlp re-renders (200) with an error and writes no Case (W102-3)."""
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "Bad enum", field: value})

    assert response.status_code == 200
    assert Case.objects.count() == 0
    body = response.content.decode()
    assert "must be between" in body


@pytest.mark.parametrize(
    ("field", "value"),
    [("severity", "99"), ("tlp", "abc")],
)
def test_ui_case_create_post_out_of_range_or_malformed_enum_rerenders(
    browser: Client, field: str, value: str
) -> None:
    """P2-d: severity=99 (out-of-range) / tlp=abc (non-integer) → 200 + error, no Case, no 500.

    The out-of-range branch (`int(value)` succeeds, bounds check fails) is exercised by
    `severity=99`; the non-integer branch (`int(value)` raises) by `tlp=abc` — the two
    `_set_case_fields` code paths from the T1.2 range check.
    """
    url = reverse("ui-case-create")
    response = browser.post(url, {"title": "Bad enum", field: value})

    assert response.status_code == 200
    assert Case.objects.count() == 0
    body = response.content.decode()
    assert "must be between" in body


def test_ui_case_create_rejected_post_echoes_submitted_scalars(browser: Client) -> None:
    """P2-c: a rejected POST re-renders the submitted scalar values, not defaults or ['x'] lists (N1)."""
    url = reverse("ui-case-create")
    response = browser.post(
        url,
        {
            "title": "   ",  # whitespace title → service rejection; everything else is echoed
            "description": "Echoed description",
            "summary": "Echoed summary",
            "severity": "4",
            "tlp": "1",
            "pap": "3",
            "tags": "alpha, beta",
        },
    )

    assert response.status_code == 200
    assert Case.objects.count() == 0
    body = response.content.decode()
    assert "title is required" in body  # the error message is shown
    # Scalar echo (read via QueryDict.get(), never dict(...) — the P2 N1 lesson):
    assert "Echoed description" in body
    assert "Echoed summary" in body
    assert 'value="alpha, beta"' in body
    assert "['" not in body  # a list rendering would appear as ['Echoed description'] etc.
    # The selects keep the submitted choice.
    assert 'value="4" selected' in body  # severity=4
    assert 'value="1" selected' in body  # tlp=1
    assert 'value="3" selected' in body  # pap=3


# --------------------------------------------------------------------------
# Viewer enforcement (ADR-006)
# --------------------------------------------------------------------------


def test_ui_case_create_viewer_get_404(viewer_browser: Client) -> None:
    """P2-f: Viewer GET → 404."""
    url = reverse("ui-case-create")
    assert viewer_browser.get(url).status_code == 404


def test_ui_case_create_viewer_post_404(viewer_browser: Client) -> None:
    """P2-f: Viewer POST → 404 (uniform, before validation)."""
    url = reverse("ui-case-create")
    assert viewer_browser.post(url, {"title": "Viewer case"}).status_code == 404
    assert Case.objects.count() == 0


def test_ui_case_list_hides_new_case_affordance_for_viewer(
    browser: Client, viewer_browser: Client
) -> None:
    """P2-f: a viewer's rendered case list carries no `New case` link and no `n` cheat-sheet row.

    The rail link, the case-list button/empty-state CTA and the `<kbd>n</kbd>` cheat-sheet row are
    all `can_mutate`-gated (ui/templates/ui/base.html:55-62,170-172; case_list.html:13-15,44). The
    analyst page is the control: if the affordance vanished for writers too, the viewer assertion
    would be vacuous (the test_viewer_affordances.py pairing rule).
    """
    href = reverse("ui-case-create")  # /cases/new
    analyst_html = browser.get(reverse("ui-case-list")).content.decode()
    assert href in analyst_html, (
        "analyst case list no longer offers New case, so the viewer check is vacuous"
    )
    assert "<kbd>n</kbd>" in analyst_html

    response = viewer_browser.get(reverse("ui-case-list"))
    assert response.status_code == 200
    viewer_html = response.content.decode()
    assert href not in viewer_html, "viewer was offered the New case link"
    assert "New case" not in viewer_html, "viewer was offered the New case affordance"
    assert "<kbd>n</kbd>" not in viewer_html, "viewer was offered the n cheat-sheet row"


# --------------------------------------------------------------------------
# CSRF protection
# --------------------------------------------------------------------------


def test_ui_case_create_post_requires_csrf(csrf_browser: Client) -> None:
    """P4-b: POST without CSRF token → 403, no case row."""
    url = reverse("ui-case-create")
    response = csrf_browser.post(url, {"title": "CSRF bypass"})

    assert response.status_code == 403
    assert Case.objects.count() == 0


# --------------------------------------------------------------------------
# API parity — the same service is used by POST /api/v1/case
# --------------------------------------------------------------------------


def test_api_case_create_accepts_full_field_set(org: Organisation, analyst: User) -> None:
    """P1-a: POST /api/v1/case with full field set → 201 with all values applied."""
    from uuid import uuid4

    from argon2 import PasswordHasher
    from rest_framework.test import APIClient

    from identity.models import ApiKey

    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=analyst,
        name="test key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope="readwrite",
    )
    api = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")

    payload = {
        "title": "API full case",
        "description": "API description",
        "summary": "API summary",
        "severity": 4,
        "tlp": 3,
        "pap": 1,
        "status": "InProgress",
        "flag": True,
        "tags": ["a", "b"],
        "assignee": "createanalyst",
    }
    response = api.post("/api/v1/case", payload, format="json")

    assert response.status_code == 201, response.content
    data = response.json()
    assert data["title"] == "API full case"
    assert data["severity"] == 4
    assert data["tlp"] == 3
    assert data["pap"] == 1
    assert data["flag"] is True
    assert data["summary"] == "API summary"
    assert data["assignee"] == "createanalyst"
    # Tags are accepted by the service but not yet in the case_json output (detail view)
    # Verify they were persisted by fetching the case directly
    case = Case.objects.get(pk=data["_id"])
    assert {t.name for t in case.tags.all()} == {"a", "b"}


def test_api_case_create_blank_title_400(org: Organisation, analyst: User) -> None:
    """P1-b: POST blank title → 400 with exact envelope {type, message, fields}."""
    from uuid import uuid4

    from argon2 import PasswordHasher
    from rest_framework.test import APIClient

    from identity.models import ApiKey

    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=analyst,
        name="test key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope="readwrite",
    )
    api = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api.post("/api/v1/case", {"title": ""}, format="json")

    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "BadRequest"
    assert "title" in body["fields"]


@pytest.mark.django_db
def test_create_case_service_denies_an_actor_without_an_org() -> None:
    """Defence-in-depth: `create_case` still refuses a caller with no organisation (ADR-007 §14.3).

    `User.org` is NOT NULL now, so the API's org-less-actor 403 can no longer be reached over
    HTTP (and no persisted org-less user can exist). The service-level guard in
    `cases/create.py:119-120` is deliberately kept, so it is exercised directly against an
    unsaved stub actor with `org_id = None`.
    """
    from uuid import uuid4

    from cases.create import create_case
    from cases.update import CaseUpdateError

    stub = User(id=uuid4(), username="orgless", login="orgless", org_id=None, role="analyst")

    with pytest.raises(CaseUpdateError) as excinfo:
        create_case({"title": "No org"}, actor=stub, org=None)

    assert excinfo.value.error_type == "AuthorizationError"
    assert excinfo.value.message
    assert excinfo.value.fields == {}


def test_api_case_create_severity_nine_400(org: Organisation, analyst: User) -> None:
    """P1-d: POST severity=9 → 400 (not 500), no case row."""
    from uuid import uuid4

    from argon2 import PasswordHasher
    from rest_framework.test import APIClient

    from identity.models import ApiKey

    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=analyst,
        name="test key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope="readwrite",
    )
    api = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api.post("/api/v1/case", {"title": "Bad", "severity": 9}, format="json")

    assert response.status_code == 400
    assert Case.objects.count() == 0


def test_api_case_create_unknown_field_still_201(org: Organisation, analyst: User) -> None:
    """P1-e: POST with unknown field → still 201 (tolerance)."""
    from uuid import uuid4

    from argon2 import PasswordHasher
    from rest_framework.test import APIClient

    from identity.models import ApiKey

    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=analyst,
        name="test key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope="readwrite",
    )
    api = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api.post("/api/v1/case", {"title": "OK", "nonsense": 1}, format="json")

    assert response.status_code == 201


def test_api_case_create_bare_title_defaults(org: Organisation, analyst: User) -> None:
    """P1-f: Bare {"title": "X"} → InProgress, severity/tlp/pap=2, no tags/assignee, no extraction."""
    from uuid import uuid4

    from argon2 import PasswordHasher
    from rest_framework.test import APIClient

    from identity.models import ApiKey

    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=analyst,
        name="test key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope="readwrite",
    )
    api = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api.post("/api/v1/case", {"title": "Bare"}, format="json")

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "InProgress"
    assert data["severity"] == 2
    assert data["tlp"] == 2
    assert data["pap"] == 2
    assert data["assignee"] is None
    # ingestion_warnings is stored on the model but not in case_json output;
    # empty when no warnings from status resolution
    case = Case.objects.get(pk=data["_id"])
    assert case.ingestion_warnings == {}
    # Tags are accepted but not in case_json output; verify persisted
    assert list(case.tags.all()) == []


# --------------------------------------------------------------------------
# API parity — regression tests for falsy status (M1 fix)
# --------------------------------------------------------------------------


def test_api_case_create_empty_status_defaults_to_inprogress(
    org: Organisation, analyst: User, status_in_progress: CaseStatus
) -> None:
    """M1 regression: POST {"title": "...", "status": ""} → 201 with status InProgress, no CaseStatus("")."""
    from uuid import uuid4

    from argon2 import PasswordHasher
    from rest_framework.test import APIClient

    from identity.models import ApiKey

    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=analyst,
        name="test key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope="readwrite",
    )
    api = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api.post("/api/v1/case", {"title": "Empty status", "status": ""}, format="json")

    assert response.status_code == 201, response.content
    data = response.json()
    assert data["status"] == "InProgress"
    case = Case.objects.get(pk=data["_id"])
    assert case.status == status_in_progress
    # No CaseStatus row with value="" should exist
    assert not CaseStatus.objects.filter(value="").exists()


def test_api_case_create_null_status_defaults_to_inprogress(
    org: Organisation, analyst: User, status_in_progress: CaseStatus
) -> None:
    """M1 regression: POST {"title": "...", "status": null} → 201 with status InProgress, no CaseStatus("")."""
    from uuid import uuid4

    from argon2 import PasswordHasher
    from rest_framework.test import APIClient

    from identity.models import ApiKey

    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=analyst,
        name="test key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope="readwrite",
    )
    api = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api.post("/api/v1/case", {"title": "Null status", "status": None}, format="json")

    assert response.status_code == 201, response.content
    data = response.json()
    assert data["status"] == "InProgress"
    case = Case.objects.get(pk=data["_id"])
    assert case.status == status_in_progress
    # No CaseStatus row with value="" should exist
    assert not CaseStatus.objects.filter(value="").exists()


def test_api_case_create_explicit_status_works(org: Organisation, analyst: User) -> None:
    """M1 regression: POST {"title": "...", "status": "Open"} → 201 with that status (non-empty path)."""
    from uuid import uuid4

    from argon2 import PasswordHasher
    from rest_framework.test import APIClient

    # Seed a custom status "Open"
    from cases.models import CaseStatus
    from identity.models import ApiKey

    CaseStatus.objects.get_or_create(value="Open", defaults={"stage": "New", "order": 3})

    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=analyst,
        name="test key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope="readwrite",
    )
    api = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api.post(
        "/api/v1/case", {"title": "Explicit status", "status": "Open"}, format="json"
    )

    assert response.status_code == 201, response.content
    data = response.json()
    assert data["status"] == "Open"
    case = Case.objects.get(pk=data["_id"])
    assert case.status.value == "Open"


def test_api_case_create_mass_assignment_keys_ignored(
    org: Organisation, other_org: Organisation, analyst: User
) -> None:
    """§9 (API parity): POST /api/v1/case with owner_org/number/id/created_at/closed_date/start_date → 201, none applied."""
    from uuid import uuid4

    from argon2 import PasswordHasher
    from rest_framework.test import APIClient

    from identity.models import ApiKey

    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=analyst,
        name="test key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope="readwrite",
    )
    api = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")

    supplied_id = str(uuid4())
    response = api.post(
        "/api/v1/case",
        {
            "title": "API mass assignment probe",
            "owner_org": str(other_org.pk),
            "number": 999,
            "id": supplied_id,
            "created_at": "1999-01-01T00:00:00Z",
            "closed_date": "1999-01-01T00:00:00Z",
            "start_date": "1999-01-01T00:00:00Z",
            "end_date": "1999-01-01T00:00:00Z",
        },
        format="json",
    )

    assert response.status_code == 201, response.content
    data = response.json()
    case = Case.objects.get(pk=data["_id"])
    assert data["_id"] != supplied_id
    assert case.owner_org == org
    assert case.owner_org != other_org
    assert case.number == 1  # a fresh allocation, not the injected 999
    assert case.created_at.year != 1999
    assert case.start_date.year != 1999
    assert case.closed_date is None
    assert case.end_date is None


def test_api_case_create_auto_created_status_records_ingestion_warning(
    org: Organisation, analyst: User
) -> None:
    """T1.3b: an auto-created status (valid stage, unseeded) lands on ingestion_warnings, not swallowed."""
    from uuid import uuid4

    from argon2 import PasswordHasher
    from rest_framework.test import APIClient

    from identity.models import ApiKey

    # Remove the seeded New status so resolve_case_status must mint it (creating a warning).
    CaseStatus.objects.filter(value="New").delete()

    token = "k" + uuid4().hex
    ApiKey.objects.create(
        user=analyst,
        name="test key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope="readwrite",
    )
    api = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api.post("/api/v1/case", {"title": "Auto status", "status": "New"}, format="json")

    assert response.status_code == 201, response.content
    case = Case.objects.get(pk=response.json()["_id"])
    assert case.status.value == "New"
    warnings = case.ingestion_warnings.get("warnings", []) or []
    assert any("case_status_created" in w and "New" in w for w in warnings), warnings


# --------------------------------------------------------------------------
# Route does not shadow case detail (P2-j)
# --------------------------------------------------------------------------


def test_ui_case_create_route_order(browser: Client) -> None:
    """P2-j: /cases/new resolves to ui-case-create, not ui-case-detail with case_id='new'."""
    from django.urls import resolve

    # /cases/new must resolve to ui-case-create
    match = resolve("/cases/new")
    assert match.url_name == "ui-case-create"

    # /cases/7 must resolve to ui-case-detail
    match = resolve("/cases/7")
    assert match.url_name == "ui-case-detail"
    assert match.kwargs["case_id"] == "7"


# --------------------------------------------------------------------------
# Cases view — the New-case modal (PLAN-2026-10-13 Part B / T2.*)
#
# The "New case" action moved off the left rail into a button + <dialog> on the
# cases list. These tests pin that move: no rail link, a modal that is present
# only for writers, the origin-aware error re-render, and the no-JS standalone
# page. They complement test_ui_nav.py (rail/cheat-sheet invariants) and
# test_viewer_affordances.py (viewer UX), and must not duplicate them.
# --------------------------------------------------------------------------

BASE_HTML = Path(__file__).resolve().parents[2] / "ui" / "templates" / "ui" / "base.html"

# The ten visible controls plus the hidden `origin` marker the modal posts.
_MODAL_CONTROL_NAMES = (
    "title",
    "description",
    "summary",
    "severity",
    "tlp",
    "pap",
    "status",
    "assignee",
    "flag",
    "tags",
    "origin",
)
# The standalone page renders the same fields, minus the modal-only `origin` marker.
_STANDALONE_CONTROL_NAMES = tuple(n for n in _MODAL_CONTROL_NAMES if n != "origin")


def _tag_with_name(html: str, name: str) -> str:
    """Return the opening tag of the control carrying ``name="<name>"``.

    A single tag is enough to answer "does this control carry a ``form=`` attribute?";
    the regex cannot cross a ``>`` so it never spans two elements.
    """
    match = re.search(rf'<[a-z]+[^>]*\bname="{re.escape(name)}"[^>]*>', html)
    assert match is not None, f"control name={name!r} not found in rendered HTML"
    return match.group(0)


def _dialog_tag(html: str, modal_id: str) -> str:
    """Return the opening ``<dialog id="…">`` tag so its attributes can be inspected."""
    match = re.search(rf'<dialog id="{re.escape(modal_id)}"[^>]*>', html)
    assert match is not None, f"dialog id={modal_id!r} not found in rendered HTML"
    return match.group(0)


def test_ui_case_list_rail_has_no_new_case_link(browser: Client) -> None:
    """T2.1: base.html's rail-links carry no New-case link; the rendered list has no `n` rail keycap.

    Source-level (like test_ui_nav.py) so a template regression is reported as a stale
    rail rather than as a mystery button. The rendered half pins that the removed
    ``<span class="k">n</span>`` keycap is gone from the analyst's page.
    """
    source = BASE_HTML.read_text(encoding="utf-8")
    match = re.search(r'<ul class="rail-links">(.*?)</ul>', source, re.S)
    assert match is not None, "rail-links markup changed — this test is stale"
    rail = match.group(1)
    assert "ui-case-create" not in rail, "the rail still links to the New-case page"
    assert re.search(r"""<a\s+href="\{%\s*url\s+'ui-case-create'\s*%\}""", rail) is None

    body = browser.get(reverse("ui-case-list")).content.decode()
    assert '<span class="k">n</span>' not in body, "the removed rail `n` keycap is still rendered"


def test_ui_case_list_renders_new_case_button(browser: Client) -> None:
    """T2.2: the cases list offers a New-case control that is a real link + modal opener."""
    response = browser.get(reverse("ui-case-list"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-modal-open="new-case-modal"' in body
    assert f'href="{reverse("ui-case-create")}"' in body


def test_ui_case_list_renders_new_case_modal_controls(browser: Client) -> None:
    """T2.3: the server-rendered modal has its form and all eleven controls, POSTing to /cases/new."""
    body = browser.get(reverse("ui-case-list")).content.decode()
    assert 'id="new-case-modal"' in body
    assert 'id="new-case-form"' in body
    assert f'action="{reverse("ui-case-create")}"' in body
    assert "New case" in body
    for field_name in _MODAL_CONTROL_NAMES:
        assert f'name="{field_name}"' in body, f"missing modal control {field_name}"


def test_ui_case_create_modal_post_success(browser: Client, status_in_progress: CaseStatus) -> None:
    """T2.4: a valid modal POST (origin=cases) creates the case and 302s to its detail page."""
    url = reverse("ui-case-create")
    response = browser.post(
        url,
        {"title": "Modal-created case", "origin": "cases", "status": "InProgress"},
    )

    assert response.status_code == 302
    case = Case.objects.get(number=1)
    assert case.title == "Modal-created case"
    assert response.url == reverse("ui-case-detail", kwargs={"case_id": case.number})


def test_ui_case_create_modal_post_invalid_status_rerenders_list(browser: Client) -> None:
    """T2.5: an origin=cases rejection → 200 on the case list, modal auto-open, values + error kept.

    The error path must not dump the analyst onto the standalone page or silently close
    the modal and lose their input (PLAN §5).
    """
    url = reverse("ui-case-create")
    title = "Modal reject echo 7f3a9c"
    response = browser.post(url, {"title": title, "origin": "cases", "status": "NoSuchStatus"})

    assert response.status_code == 200
    assert Case.objects.count() == 0
    body = response.content.decode()

    # We are back on the case list, not the standalone create page.
    assert 'aria-label="Case filters"' in body  # the list's own filter card
    assert 'id="h-create"' not in body  # the standalone page's heading

    # The modal is present and marked auto-open.
    assert 'id="new-case-modal"' in body
    assert 'data-autoopen="1"' in _dialog_tag(body, "new-case-modal")

    # The submitted title is echoed back into the modal.
    assert title in body
    assert 'name="title"' in body

    # The error is surfaced in the Django messages region.
    assert "Unknown case status: NoSuchStatus" in body


def test_ui_case_list_new_query_param_autoopens_modal(browser: Client) -> None:
    """T2.6: ?new=1 marks the dialog data-autoopen; a bare /cases renders it closed."""
    auto = browser.get(reverse("ui-case-list") + "?new=1")
    assert auto.status_code == 200
    auto_body = auto.content.decode()
    assert 'id="new-case-modal"' in auto_body
    assert 'data-autoopen="1"' in _dialog_tag(auto_body, "new-case-modal")

    plain = browser.get(reverse("ui-case-list"))
    assert plain.status_code == 200
    plain_body = plain.content.decode()
    assert 'id="new-case-modal"' in plain_body
    assert 'data-autoopen="1"' not in _dialog_tag(plain_body, "new-case-modal")


def test_ui_case_create_standalone_fields_have_no_form_attribute(browser: Client) -> None:
    """T2.7: GET /cases/new still renders standalone with `id="h-create"` and no control carries `form=`.

    The shared partial must only emit ``form="{{ form_id }}"`` inside the modal (where it
    targets the modal's footer form); an orphan ``form=`` on the page would break submission.
    """
    response = browser.get(reverse("ui-case-create"))
    assert response.status_code == 200
    body = response.content.decode()

    assert 'id="h-create"' in body
    for field_name in _STANDALONE_CONTROL_NAMES:
        tag = _tag_with_name(body, field_name)
        assert "form=" not in tag, (
            f"standalone control {field_name!r} carries a modal form= attribute: {tag}"
        )


def test_ui_case_list_viewer_has_no_modal_affordance(viewer_browser: Client) -> None:
    """T2.8: a viewer may read /cases but is offered no button/modal, and POST /cases/new 404s."""
    response = viewer_browser.get(reverse("ui-case-list"))
    assert response.status_code == 200
    body = response.content.decode()

    assert 'data-modal-open="new-case-modal"' not in body
    assert 'id="new-case-modal"' not in body
    assert f'href="{reverse("ui-case-create")}"' not in body
    assert "New case" not in body

    assert (
        viewer_browser.post(reverse("ui-case-create"), {"title": "Viewer case"}).status_code == 404
    )


@pytest.mark.parametrize("role", ["superAdmin", "admin", "analyst"])
def test_ui_case_list_button_and_dialog_parity_for_every_mutator_role(
    db: None, default_org: Organisation, role: str
) -> None:
    """Regression for the original defect (ADR-007): every non-viewer gets the modal.

    An org-less `can_mutate` user used to receive the "New case" button while the dialog
    was suppressed (`form_values` was only supplied when `request.user.org is not None`),
    so `modal.js` correctly let the anchor navigate and the affordance silently degraded to
    the standalone page. With `User.org` NOT NULL the button and dialog gates are identical
    for every role, asserted here for all three mutating roles and for `?new=1`.
    """
    username = f"parity-{role.lower()}"
    User.objects.create_user(
        username=username,
        login=username,
        password="hunter2-correct",
        email=f"{username}@example.net",
        org=default_org,
        is_active=True,
        role=role,
    )
    client = Client()
    assert client.login(username=username, password="hunter2-correct")

    body = client.get(reverse("ui-case-list")).content.decode()
    assert 'data-modal-open="new-case-modal"' in body, f"{role}: New-case button missing"
    assert 'id="new-case-modal"' in body, f"{role}: modal dialog missing"
    assert 'id="new-case-form"' in body, f"{role}: modal form missing"
    for control in _MODAL_CONTROL_NAMES:
        tag = _tag_with_name(body, control)
        assert 'form="new-case-form"' in tag, (
            f"{role}: control {control!r} does not submit through the modal form"
        )

    auto_body = client.get(reverse("ui-case-list") + "?new=1").content.decode()
    assert 'id="new-case-modal"' in auto_body
    assert 'data-autoopen="1"' in _dialog_tag(auto_body, "new-case-modal")
