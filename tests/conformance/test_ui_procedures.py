"""P3 — Procedures/TTP UI (plan P3, A5).

Covers:
- Case detail renders the Procedures card and each attached procedure's ATT&CK id + description.
- Valid POST → 302 to case detail, Procedure created with the right pattern_id, appears on page.
- Invalid ttpId → 302 + error message, never 500.
- Bad occurDate → 302 + error message, never 500.
- Cross-org case → uniform 404 on POST.
- Anonymous → redirect to login.
- Page works with HTMX-less plain form (real <form method="post"> with CSRF token).

Fixtures/helpers mirror test_ui_p2_wiring.py and test_playbook_roundtrip.py style.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from cases.models import TTP, Case, CaseStatus, Procedure
from identity.models import Organisation, User

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="P3 Org")


@pytest.fixture
def other_org(db: None) -> Organisation:
    return Organisation.objects.create(name="P3 Other Org")


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="p3analyst",
        login="p3analyst",
        password="hunter2-correct",
        email="p3analyst@example.net",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def outsider(db: None, other_org: Organisation) -> User:
    return User.objects.create_user(
        username="p3outsider",
        login="p3outsider",
        password="hunter2-correct",
        email="p3outsider@example.net",
        org=other_org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="p3analyst", password="hunter2-correct")
    return client


@pytest.fixture
def outsider_browser(outsider: User) -> Client:
    client = Client()
    assert client.login(username="p3outsider", password="hunter2-correct")
    return client


@pytest.fixture
def csrf_browser(analyst: User) -> Client:
    """A client that actually enforces CSRF, so the guard can be asserted."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(analyst)
    return client


@pytest.fixture
def case(db: None, org: Organisation, analyst: User) -> Case:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(
        title="P3 case", status=status, owner_org=org, assignee=analyst, tlp=2, pap=2
    )


@pytest.fixture
def procedure(db: None, case: Case) -> Procedure:
    """A procedure attached to the case."""
    return Procedure.objects.create(
        case=case,
        pattern_id="T1566.001",
        pattern_name="Phishing: Spearphishing Attachment",
        tactic="Initial Access",
        description="Observed phishing email with malicious attachment.",
        occur_date=None,
    )


@pytest.fixture
def ttps(db: None) -> list[TTP]:
    """Create some TTP vocabulary entries for the select dropdown."""
    return [
        TTP.objects.create(
            name="Spearphishing Attachment",
            ttp_code="T1566.001",
            tactic="Initial Access",
            description="Adversaries may send spearphishing emails with a malicious attachment.",
        ),
        TTP.objects.create(
            name="PowerShell",
            ttp_code="T1059.001",
            tactic="Execution",
            description="Adversaries may abuse PowerShell commands and scripts for execution.",
        ),
        TTP.objects.create(
            name="Ingress Tool Transfer",
            ttp_code="T1105",
            tactic="Command and Control",
            description="Adversaries may transfer tools or other files from an external system.",
        ),
    ]


# --------------------------------------------------------------------------
# Rendering tests
# --------------------------------------------------------------------------


def test_case_detail_renders_procedures_card(
    browser: Client, case: Case, procedure: Procedure, ttps: list[TTP]
) -> None:
    """P3: case detail renders the Procedures card with count and list."""
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    response = browser.get(url)
    assert response.status_code == 200
    body = response.content.decode()

    # Card heading with count (scoped to this card — the page has several `.count` spans)
    assert 'id="h-procs">Procedures <span class="count">1</span>' in body

    # Procedure entry shows ATT&CK ID (pattern_id) and description
    assert "T1566.001" in body
    assert "Phishing: Spearphishing Attachment" in body
    assert "Initial Access" in body
    assert "Observed phishing email with malicious attachment" in body

    # Form is present with CSRF token
    assert (
        f'action="{reverse("ui-case-procedure-create", kwargs={"case_id": case.number})}"' in body
    )
    assert 'name="csrfmiddlewaretoken"' in body
    assert 'name="patternId"' in body
    assert 'name="patternName"' in body
    assert 'name="tactic"' in body
    assert 'name="occurDate"' in body
    assert 'name="description"' in body
    assert 'name="ttpId"' in body

    # TTP dropdown includes our vocabulary entries
    for ttp in ttps:
        assert f'value="{ttp.id}"' in body
        assert ttp.name in body


def test_case_detail_procedure_shows_ttp_code_when_linked(
    browser: Client, case: Case, ttps: list[TTP]
) -> None:
    """P3: when a procedure has a linked TTP, the chip shows the TTP's code, not the pattern_id.

    The two values are deliberately different (`T9001` vs `T1566.001`) — otherwise the assertion
    passes whichever one the template happens to render.
    """
    linked = TTP.objects.create(
        name="Unique Linked Technique",
        ttp_code="T9001",
        tactic="Initial Access",
    )
    Procedure.objects.create(
        case=case,
        ttp=linked,
        pattern_id="T1566.001",
        pattern_name="Phishing: Spearphishing Attachment",
        description="Linked TTP takes precedence.",
    )

    response = browser.get(reverse("ui-case-detail", kwargs={"case_id": case.number}))
    assert response.status_code == 200
    body = response.content.decode()

    chip = re.search(r'class="procedure-attack-id">\s*([^<]*?)\s*</span>', body)
    assert chip is not None, "no procedure ATT&CK-ID chip rendered"
    assert chip.group(1) == "T9001", f"chip shows {chip.group(1)!r}, expected the TTP code T9001"


def test_case_detail_procedures_empty_state(browser: Client, case: Case, ttps: list[TTP]) -> None:
    """P3: empty state when no procedures attached."""
    Procedure.objects.filter(case=case).delete()
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    response = browser.get(url)
    assert response.status_code == 200
    body = response.content.decode()

    assert 'id="h-procs">Procedures <span class="count">0</span>' in body
    assert "No procedures attached yet" in body


# --------------------------------------------------------------------------
# Mutation tests — single create
# --------------------------------------------------------------------------


def test_procedure_create_post_valid_redirects_and_creates(
    browser: Client, case: Case, ttps: list[TTP]
) -> None:
    """P3: valid POST creates Procedure and redirects to case detail."""
    url = reverse("ui-case-procedure-create", kwargs={"case_id": case.number})
    ttp = ttps[0]
    response = browser.post(
        url,
        {
            "patternId": "T1059.001",
            "patternName": "PowerShell",
            "tactic": "Execution",
            "occurDate": "2024-01-15T10:30:00Z",
            "description": "Saw powershell execution in logs.",
            "ttpId": str(ttp.id),
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("ui-case-detail", kwargs={"case_id": case.number})

    # Procedure created with correct fields
    proc = Procedure.objects.filter(case=case).first()
    assert proc is not None
    assert proc.pattern_id == "T1059.001"
    assert proc.pattern_name == "PowerShell"
    assert proc.tactic == "Execution"
    assert proc.description == "Saw powershell execution in logs."
    assert proc.ttp_id == ttp.id
    assert proc.occur_date is not None


def test_procedure_create_post_without_ttp_works(browser: Client, case: Case) -> None:
    """P3: valid POST without ttpId creates Procedure with no TTP link."""
    url = reverse("ui-case-procedure-create", kwargs={"case_id": case.number})
    response = browser.post(
        url,
        {
            "patternId": "T1190",
            "patternName": "Exploit Public-Facing Application",
            "tactic": "Initial Access",
            "description": "Exploited public-facing app.",
            "ttpId": "",  # blank = no link
        },
    )

    assert response.status_code == 302
    proc = Procedure.objects.filter(case=case).first()
    assert proc is not None
    assert proc.pattern_id == "T1190"
    assert proc.ttp is None


def test_procedure_create_post_invalid_ttp_id_shows_message_not_500(
    browser: Client, case: Case
) -> None:
    """P3 AC: invalid ttpId → 302 + error message, never 500."""
    url = reverse("ui-case-procedure-create", kwargs={"case_id": case.number})
    response = browser.post(
        url,
        {
            "patternId": "T1566.001",
            "patternName": "Phishing",
            "tactic": "Initial Access",
            "description": "Test",
            "ttpId": "00000000-0000-0000-0000-000000000000",  # non-existent UUID
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("ui-case-detail", kwargs={"case_id": case.number})

    # No procedure created
    assert Procedure.objects.filter(case=case).count() == 0

    # Follow redirect to see the error message
    response = browser.get(response.url)
    assert response.status_code == 200
    body = response.content.decode()
    assert "unknown ttpId" in body or "Invalid ttpId" in body


def test_procedure_create_post_bad_occur_date_shows_message_not_500(
    browser: Client, case: Case
) -> None:
    """P3 AC: bad occurDate → 302 + error message, never 500."""
    url = reverse("ui-case-procedure-create", kwargs={"case_id": case.number})
    response = browser.post(
        url,
        {
            "patternId": "T1566.001",
            "patternName": "Phishing",
            "tactic": "Initial Access",
            "description": "Test",
            "occurDate": "not-a-timestamp",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("ui-case-detail", kwargs={"case_id": case.number})

    # No procedure created
    assert Procedure.objects.filter(case=case).count() == 0

    # Follow redirect to see the error message
    response = browser.get(response.url)
    assert response.status_code == 200
    body = response.content.decode()
    assert "occurDate" in body
    assert "valid timestamp" in body or "not a valid" in body


def test_procedure_create_post_missing_pattern_id_shows_message_not_500(
    browser: Client, case: Case
) -> None:
    """P3: missing required patternId → 302 + error message, never 500."""
    url = reverse("ui-case-procedure-create", kwargs={"case_id": case.number})
    response = browser.post(
        url,
        {
            "patternName": "Phishing",
            "tactic": "Initial Access",
            "description": "Test",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("ui-case-detail", kwargs={"case_id": case.number})

    assert Procedure.objects.filter(case=case).count() == 0

    response = browser.get(response.url)
    assert response.status_code == 200
    body = response.content.decode()
    assert "patternId" in body or "ATT&CK ID" in body


def test_procedure_create_cross_org_returns_404(outsider_browser: Client, case: Case) -> None:
    """P3: another org gets uniform 404 on POST and cannot create."""
    url = reverse("ui-case-procedure-create", kwargs={"case_id": case.number})
    response = outsider_browser.post(
        url,
        {
            "patternId": "T1566.001",
            "patternName": "Phishing",
            "tactic": "Initial Access",
            "description": "Test",
        },
    )

    assert response.status_code == 404
    assert Procedure.objects.filter(case=case).count() == 0


def test_procedure_create_anonymous_redirects_to_login(client: Client, case: Case) -> None:
    """P3: anonymous user is redirected to login."""
    url = reverse("ui-case-procedure-create", kwargs={"case_id": case.number})
    response = client.post(
        url,
        {
            "patternId": "T1566.001",
            "patternName": "Phishing",
            "tactic": "Initial Access",
            "description": "Test",
        },
    )

    assert response.status_code == 302
    assert "/login" in response.url


def test_procedure_create_requires_csrf(csrf_browser: Client, case: Case) -> None:
    """P3: the mutation is CSRF-protected."""
    url = reverse("ui-case-procedure-create", kwargs={"case_id": case.number})
    response = csrf_browser.post(
        url,
        {
            "patternId": "T1566.001",
            "patternName": "Phishing",
            "tactic": "Initial Access",
            "description": "Test",
        },
    )

    assert response.status_code == 403
    assert Procedure.objects.filter(case=case).count() == 0


def test_procedure_create_post_only_no_get(browser: Client, case: Case) -> None:
    """P3: GET is not allowed (405) — the endpoint is a POST-only mutation.

    This keeps the route in _UI_WRITE_ONLY_ROUTES for tenant-scoping conformance.
    """
    url = reverse("ui-case-procedure-create", kwargs={"case_id": case.number})
    assert browser.get(url).status_code == 405


# --------------------------------------------------------------------------
# Mutation tests — bulk create
# --------------------------------------------------------------------------


def test_procedures_bulk_create_valid_creates_multiple(
    browser: Client, case: Case, ttps: list[TTP]
) -> None:
    """P3: bulk POST creates multiple procedures and reports count."""
    url = reverse("ui-case-procedures-create", kwargs={"case_id": case.number})
    ttp1, ttp2 = ttps[0], ttps[1]
    response = browser.post(
        url,
        {
            "patternId_0": "T1059.001",
            "patternName_0": "PowerShell",
            "tactic_0": "Execution",
            "description_0": "First procedure",
            "ttpId_0": str(ttp1.id),
            "patternId_1": "T1105",
            "patternName_1": "Ingress Tool Transfer",
            "tactic_1": "Command and Control",
            "description_1": "Second procedure",
            "ttpId_1": str(ttp2.id),
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("ui-case-detail", kwargs={"case_id": case.number})

    procs = list(Procedure.objects.filter(case=case).order_by("created_at"))
    assert len(procs) == 2
    # Check both procedures exist with correct data (order by created_at for creation order)
    pattern_ids = {p.pattern_id for p in procs}
    assert pattern_ids == {"T1059.001", "T1105"}
    ttp_ids = {p.ttp_id for p in procs}
    assert ttp_ids == {ttp1.id, ttp2.id}


def test_procedures_bulk_create_with_list_style_fields(browser: Client, case: Case) -> None:
    """P3: bulk POST also accepts list-style repeated field names."""
    url = reverse("ui-case-procedures-create", kwargs={"case_id": case.number})
    response = browser.post(
        url,
        {
            "patternId": ["T1059.001", "T1105"],
            "patternName": ["PowerShell", "Ingress Tool Transfer"],
            "tactic": ["Execution", "Command and Control"],
            "description": ["First", "Second"],
            "ttpId": ["", ""],
        },
    )

    assert response.status_code == 302
    procs = list(Procedure.objects.filter(case=case).order_by("created_at"))
    assert len(procs) == 2
    # Check both procedures exist with correct data (order by created_at for creation order)
    pattern_ids = {p.pattern_id for p in procs}
    assert pattern_ids == {"T1059.001", "T1105"}


def test_procedures_bulk_create_invalid_item_skipped_with_message(
    browser: Client, case: Case
) -> None:
    """P3: invalid item in bulk is skipped, error message shown, others created."""
    url = reverse("ui-case-procedures-create", kwargs={"case_id": case.number})
    response = browser.post(
        url,
        {
            "patternId_0": "T1059.001",
            "patternName_0": "PowerShell",
            "tactic_0": "Execution",
            "description_0": "Valid one",
            "ttpId_0": "",
            "patternId_1": "T1105",
            "patternName_1": "Ingress Tool Transfer",
            "tactic_1": "Command and Control",
            "description_1": "Bad ttpId",
            "ttpId_1": "00000000-0000-0000-0000-000000000000",  # invalid
        },
    )

    assert response.status_code == 302
    procs = list(Procedure.objects.filter(case=case).order_by("id"))
    assert len(procs) == 1
    assert procs[0].pattern_id == "T1059.001"

    # Follow redirect to see messages
    response = browser.get(response.url)
    body = response.content.decode()
    assert "unknown ttpId" in body or "Invalid ttpId" in body


def test_procedures_bulk_create_survives_a_db_level_failure_in_one_item(
    browser: Client, case: Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P3: a DB-level error in one item must not poison the rest of the batch.

    Each item runs in its own savepoint. Without that, the first `IntegrityError` marks the
    surrounding transaction as failed and every *later* item dies with `TransactionManagementError`
    — one bad row silently becomes N, so the "bad item is skipped, the others are created" promise
    only held for errors the parser catches before touching the database.
    """
    from cases import procedures as procedures_module

    real = procedures_module.create_procedure
    calls = {"n": 0}

    def flaky(**kwargs: object) -> Procedure:
        calls["n"] += 1
        if calls["n"] == 2:
            # An orphan procedure: violates the `procedure_exactly_one_parent` CHECK in the DB.
            Procedure.objects.create(pattern_id="orphan")
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(procedures_module, "create_procedure", flaky)

    url = reverse("ui-case-procedures-create", kwargs={"case_id": case.number})
    response = browser.post(
        url,
        {
            "patternId_0": "T1059.001",
            "patternName_0": "PowerShell",
            "tactic_0": "Execution",
            "patternId_1": "T1105",
            "patternName_1": "Ingress Tool Transfer",
            "tactic_1": "Command and Control",
            "patternId_2": "T1566.001",
            "patternName_2": "Phishing",
            "tactic_2": "Initial Access",
        },
    )

    assert response.status_code == 302
    # Items 1 and 3 survived the item-2 DB error; the error text is logged, not flashed.
    assert sorted(Procedure.objects.filter(case=case).values_list("pattern_id", flat=True)) == [
        "T1059.001",
        "T1566.001",
    ]

    body = browser.get(response.url).content.decode()
    assert "Procedure 2: could not be attached" in body
    assert "1 procedure(s) had errors" in body
    # The internal exception text never reaches the analyst.
    assert "procedure_exactly_one_parent" not in body


def test_procedures_bulk_create_rejects_more_than_the_cap(browser: Client, case: Case) -> None:
    """P3: the form-controlled item count is capped, so one request cannot fan out into N inserts."""
    from ui.views import MAX_BULK_PROCEDURES

    url = reverse("ui-case-procedures-create", kwargs={"case_id": case.number})
    data = {f"patternId_{i}": f"T{1000 + i}" for i in range(MAX_BULK_PROCEDURES + 1)}
    response = browser.post(url, data)

    assert response.status_code == 302
    assert Procedure.objects.filter(case=case).count() == 0
    body = browser.get(response.url).content.decode()
    assert f"max {MAX_BULK_PROCEDURES}" in body


def test_procedures_bulk_create_cross_org_returns_404(outsider_browser: Client, case: Case) -> None:
    """P3: another org gets uniform 404 on bulk POST."""
    url = reverse("ui-case-procedures-create", kwargs={"case_id": case.number})
    response = outsider_browser.post(
        url,
        {
            "patternId_0": "T1059.001",
            "patternName_0": "PowerShell",
            "tactic_0": "Execution",
            "description_0": "Test",
        },
    )

    assert response.status_code == 404
    assert Procedure.objects.filter(case=case).count() == 0


def test_procedures_bulk_create_post_only_no_get(browser: Client, case: Case) -> None:
    """P3: GET is not allowed (405) on bulk endpoint."""
    url = reverse("ui-case-procedures-create", kwargs={"case_id": case.number})
    assert browser.get(url).status_code == 405


# --------------------------------------------------------------------------
# Template/AJAX surface contract
# --------------------------------------------------------------------------


def test_case_detail_procedure_form_is_real_form_with_csrf(
    browser: Client, case: Case, ttps: list[TTP]
) -> None:
    """P3: the attach form is a real <form method="post"> with CSRF token (no-JS fallback)."""
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    response = browser.get(url)
    body = response.content.decode()

    form_action = reverse("ui-case-procedure-create", kwargs={"case_id": case.number})
    # The attach form is now a modal (`ui/_modal.html`), whose `<form>` tag carries `id` before
    # `method`/`action` and may wrap across lines. Match the attributes in any order and require
    # both, so the invariant (a real no-JS POST form to the procedure-create URL) still holds.
    form_tag = re.search(rf'<form [^>]*action="{re.escape(form_action)}"[^>]*>', body)
    assert form_tag is not None, "the procedure attach form is not a real <form action=…>"
    assert 'method="post"' in form_tag.group(0)
    assert 'name="csrfmiddlewaretoken"' in body
    # Submit button has visible label (icon supplements, never replaces)
    assert "Attach procedure" in body
    assert "fa-diagram-project" in body


def test_case_detail_procedure_description_rendered_via_markdown_filter(
    browser: Client, case: Case, ttps: list[TTP]
) -> None:
    """P3: procedure description is rendered through the sanitized markdown filter.

    The filter is loaded via {% load ui_tags %} in base.html; description goes through |markdown.
    """
    Procedure.objects.create(
        case=case,
        pattern_id="T1566.001",
        pattern_name="Phishing",
        tactic="Initial Access",
        description="**Bold** and `code` in description.",
    )
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    response = browser.get(url)
    body = response.content.decode()

    # Markdown should be rendered (sanitized HTML)
    assert "<strong>Bold</strong>" in body or "<b>Bold</b>" in body
    assert "<code>code</code>" in body


def test_case_detail_procedure_description_xss_payload_escaped(
    browser: Client, case: Case, ttps: list[TTP]
) -> None:
    """P3: an XSS payload in a procedure description is neutralised, not executed.

    Markdown escapes raw HTML before `nh3` sanitizes, so the payload must survive only as inert
    text (`&lt;script&gt;`) — never as a live tag, and never with a live ``onerror`` attribute.
    """
    Procedure.objects.create(
        case=case,
        pattern_id="T1566.001",
        pattern_name="Phishing",
        tactic="Initial Access",
        description='<img src=x onerror="alert(1)"><script>alert(1)</script>',
    )
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    response = browser.get(url)
    assert response.status_code == 200
    body = response.content.decode()

    assert "procedure-description" in body  # the entry actually rendered
    # Dangerous markup is escaped, never live.
    assert "<script>alert(1)</script>" not in body
    assert "<img src=x" not in body
    assert "&lt;script&gt;" in body
    assert "&lt;img src=x" in body
    # The quotes stay literal, but in element content they are inert: assert the handler never
    # becomes a real attribute of any tag.
    assert re.search(r"<[^>]*\bon[a-z]+\s*=", body) is None
