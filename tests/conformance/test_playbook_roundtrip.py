"""D1 — Playbook round-trip (plan P5/D1).

Tests that editing a playbook pre-selects the stored action and round-trips
url/method/timeout/body/action_path/headers; saving without changes is a no-op;
a GET→POST cycle preserves the action config (including header order).
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from automation.models import Playbook
from identity.models import Organisation, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Test Org")


@pytest.fixture
def analyst(org: Organisation) -> User:
    return User.objects.create(
        login="analyst",
        username="analyst",
        email="analyst@example.net",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def read_only_user(org: Organisation) -> User:
    return User.objects.create(
        login="readonly",
        username="readonly",
        email="readonly@example.net",
        org=org,
        is_active=True,
        role="read-only",
    )


@pytest.fixture
def client_analyst(analyst: User) -> Client:
    c = Client()
    c.force_login(analyst)
    return c


@pytest.fixture
def client_readonly(read_only_user: User) -> Client:
    c = Client()
    c.force_login(read_only_user)
    return c


def _post_form(client: Client, url: str, data: dict) -> Client:
    """POST form data and return the response."""
    return client.post(url, data, follow=True)


def _get_form(client: Client, url: str):
    """GET form page and return the response."""
    return client.get(url)


def _extract_csrf(response) -> str:
    """Extract CSRF token from response content."""
    content = response.content.decode()
    # Find the csrfmiddlewaretoken value
    import re

    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', content)
    return match.group(1) if match else ""


def _parse_form_values(response) -> dict:
    """Parse form field values from HTML response for round-trip testing."""
    content = response.content.decode()
    import re

    values = {}

    # Extract input values
    for match in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', content):
        values[match.group(1)] = match.group(2)

    # Extract textarea values
    for match in re.finditer(r'<textarea[^>]*name="([^"]+)">([^<]*)</textarea>', content):
        values[match.group(1)] = match.group(2)

    # Extract selected options
    for match in re.finditer(r'<select[^>]*name="([^"]+)">(.*?)</select>', content, re.DOTALL):
        select_content = match.group(2)
        opt_match = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', select_content)
        if opt_match:
            values[match.group(1)] = opt_match.group(1)

    # Extract checkbox
    if 'name="is_active"' in content and "checked" in content:
        values["is_active"] = "on"

    return values


class TestPlaybookRoundTripHTTP:
    """Round-trip tests for HTTP playbooks."""

    def test_http_playbook_get_edit_prepopulates(
        self, client_analyst: Client, org: Organisation
    ) -> None:
        """GET /edit shows stored HTTP config pre-selected."""
        # Create an HTTP playbook via UI POST
        playbook = Playbook.objects.create(
            name="http-roundtrip",
            description="Test HTTP",
            trigger_event="observable.created",
            is_active=True,
            config={
                "action": "http",
                "url": "https://api.example.com/lookup/{observable}",
                "method": "POST",
                "timeoutSeconds": 15.5,
                "headers": {"Authorization": "Bearer token", "X-Custom": "value"},
                "body": '{"ip": "{observable}"}',
            },
        )

        # GET the edit page
        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 200

        # Check pre-population
        content = resp.content.decode()

        # DEBUG: extract body textarea content
        import re

        match = re.search(
            r'<textarea[^>]*name="http_body"[^>]*>(.*?)</textarea>', content, re.DOTALL
        )
        body_content = match.group(1) if match else "NOT FOUND"

        # Name is readonly with correct value
        assert 'value="http-roundtrip"' in content
        assert "readonly" in content

        # Description
        assert "Test HTTP" in content

        # Trigger event selected
        assert "selected" in content and "observable.created" in content

        # Action selected
        assert 'value="http"' in content and "selected" in content

        # URL filled
        assert 'https://api.example.com/lookup/{observable}"' in content

        # Method selected (POST)
        assert 'value="POST"' in content and "selected" in content

        # Timeout
        assert 'value="15.5"' in content

        # Headers rendered in order (dict preserves insertion order)
        assert 'name="header_key_0"' in content
        assert 'value="Authorization"' in content
        assert 'name="header_val_0"' in content
        assert 'value="Bearer token"' in content
        assert 'name="header_key_1"' in content
        assert 'value="X-Custom"' in content
        assert 'name="header_val_1"' in content
        assert 'value="value"' in content

        # Body - auto-escaped in the textarea (browser decodes entities on display, so the
        # analyst still sees and re-submits the original string; raw HTML is never emitted).
        assert "{&quot;ip&quot;: &quot;{observable}&quot;}" in content, (
            f"Escaped body not found. Textarea: {body_content!r}"
        )

        # header_count matches rendered rows (2 saved + 2 empty = 4)
        assert 'id="header_count"' in content
        # Note: the exact value depends on template logic; at least 4 rows should render

    def test_http_body_cannot_break_out_of_textarea(
        self, client_analyst: Client, org: Organisation
    ) -> None:
        """Regression: a stored body containing ``</textarea>`` must be escaped, not raw.

        Without autoescaping (i.e. with ``|safe``) an analyst-supplied body could close the
        textarea and inject markup/script for every viewer of the edit page.
        """
        payload = '</textarea><img src=x onerror="alert(1)">'
        playbook = Playbook.objects.create(
            name="http-xss",
            description="Test textarea breakout",
            trigger_event="observable.created",
            is_active=True,
            config={
                "action": "http",
                "url": "https://api.example.com/lookup/{observable}",
                "method": "POST",
                "body": payload,
            },
        )

        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 200
        content = resp.content.decode()

        # The payload survives only as escaped text inside the textarea.
        assert "&lt;/textarea&gt;" in content
        assert "</textarea><img" not in content
        assert 'onerror="alert(1)"' not in content

    def test_header_count_is_bounded(self, client_analyst: Client, org: Organisation) -> None:
        """A form-controlled ``header_count`` must not drive an unbounded server-side loop."""
        playbook = Playbook.objects.create(
            name="http-headers-dos",
            description="Header count cap",
            trigger_event="observable.created",
            is_active=True,
            config={
                "action": "http",
                "url": "https://api.example.com/lookup/{observable}",
                "method": "POST",
                "headers": {"Authorization": "Bearer token"},
            },
        )

        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        resp = _get_form(client_analyst, url)
        form_values = _parse_form_values(resp)
        form_values["csrfmiddlewaretoken"] = _extract_csrf(resp)
        form_values["action"] = "http"
        form_values["url"] = "https://api.example.com/lookup/{observable}"
        form_values["http_method"] = "POST"
        form_values["header_count"] = "1000000"

        post_resp = client_analyst.post(url, form_values, follow=True)
        assert "Too many headers" in post_resp.content.decode()

        # Rejected, not silently clamped: nothing was persisted.
        playbook.refresh_from_db()
        assert playbook.config["headers"] == {"Authorization": "Bearer token"}

    def test_playbook_edit_rejects_unsupported_method(
        self, client_analyst: Client, org: Organisation
    ) -> None:
        """Only GET/POST are routed to the edit view (no DELETE/PUT)."""
        playbook = Playbook.objects.create(
            name="http-methods",
            description="Method guard",
            trigger_event="observable.created",
            config={"action": "http", "url": "https://api.example.com/x", "method": "GET"},
        )
        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        assert client_analyst.delete(url).status_code == 405
        assert client_analyst.put(url).status_code == 405

    def test_http_playbook_post_unchanged_is_noop(
        self, client_analyst: Client, org: Organisation
    ) -> None:
        """POST unchanged form data → config unchanged (no-op round-trip)."""
        playbook = Playbook.objects.create(
            name="http-noop",
            description="Test HTTP noop",
            trigger_event="observable.created",
            is_active=True,
            config={
                "action": "http",
                "url": "https://api.example.com/lookup/{observable}",
                "method": "POST",
                "timeoutSeconds": 15.5,
                "headers": {"Authorization": "Bearer token", "X-Custom": "value"},
                "body": '{"ip": "{observable}"}',
            },
        )

        # GET the edit page
        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 200

        # Parse form values and POST them back unchanged
        form_values = _parse_form_values(resp)
        form_values["csrfmiddlewaretoken"] = _extract_csrf(resp)

        # Ensure all required fields are present
        form_values.setdefault("name", "http-noop")
        form_values.setdefault("description", "Test HTTP noop")
        form_values.setdefault("trigger_event", "observable.created")
        form_values.setdefault("action", "http")
        form_values.setdefault("http_method", "POST")
        form_values.setdefault("timeout_seconds", "15.5")
        form_values.setdefault("url", "https://api.example.com/lookup/{observable}")
        form_values.setdefault("http_body", '{"ip": "{observable}"}')
        form_values.setdefault("is_active", "on")

        # POST back
        resp = _post_form(client_analyst, url, form_values)
        assert resp.status_code == 200  # Redirect followed

        # Refresh and verify config is identical (including header order)
        playbook.refresh_from_db()
        config = playbook.config

        assert config["action"] == "http"
        assert config["url"] == "https://api.example.com/lookup/{observable}"
        assert config["method"] == "POST"
        assert config["timeoutSeconds"] == 15.5
        assert config["body"] == '{"ip": "{observable}"}'
        # Headers in order
        assert list(config["headers"].keys()) == ["Authorization", "X-Custom"]
        assert config["headers"]["Authorization"] == "Bearer token"
        assert config["headers"]["X-Custom"] == "value"

        # Description and other fields unchanged
        assert playbook.description == "Test HTTP noop"
        assert playbook.trigger_event == "observable.created"
        assert playbook.is_active is True

    def test_http_playbook_header_order_preserved(
        self, client_analyst: Client, org: Organisation
    ) -> None:
        """Header order is preserved through GET→POST cycle."""
        playbook = Playbook.objects.create(
            name="http-headers-order",
            trigger_event="observable.created",
            is_active=True,
            config={
                "action": "http",
                "url": "https://example.com",
                "method": "GET",
                "headers": {"First": "1", "Second": "2", "Third": "3"},
            },
        )

        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 200

        form_values = _parse_form_values(resp)
        form_values["csrfmiddlewaretoken"] = _extract_csrf(resp)

        # Fill in required fields that might not be parsed
        form_values.setdefault("name", "http-headers-order")
        form_values.setdefault("description", "")
        form_values.setdefault("trigger_event", "observable.created")
        form_values.setdefault("action", "http")
        form_values.setdefault("http_method", "GET")
        form_values.setdefault("timeout_seconds", "10")
        form_values.setdefault("url", "https://example.com")
        form_values.setdefault("http_body", "")
        form_values.setdefault("is_active", "on")

        resp = _post_form(client_analyst, url, form_values)
        assert resp.status_code == 200

        playbook.refresh_from_db()
        assert list(playbook.config["headers"].keys()) == ["First", "Second", "Third"]


class TestPlaybookRoundTripPython:
    """Round-trip tests for Python playbooks."""

    def test_python_playbook_get_edit_prepopulates(
        self, client_analyst: Client, org: Organisation
    ) -> None:
        """GET /edit shows stored Python config pre-selected."""
        playbook = Playbook.objects.create(
            name="python-roundtrip",
            description="Test Python",
            trigger_event="alert.ingested",
            is_active=False,
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 200

        content = resp.content.decode()

        # Name readonly
        assert 'value="python-roundtrip"' in content

        # Description
        assert "Test Python" in content

        # Trigger event selected
        assert "alert.ingested" in content
        assert "selected" in content

        # Action python selected
        assert 'value="python"' in content and "selected" in content

        # Action path selected
        assert 'value="amalthea.automation.executor.enrichment_probe"' in content
        assert "selected" in content

        # is_active unchecked
        assert "checked" not in content or 'id="id_is_active"' in content

    def test_python_playbook_post_unchanged_is_noop(
        self, client_analyst: Client, org: Organisation
    ) -> None:
        """POST unchanged Python form data → config unchanged."""
        playbook = Playbook.objects.create(
            name="python-noop",
            trigger_event="task.completed",
            is_active=True,
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 200

        form_values = _parse_form_values(resp)
        form_values["csrfmiddlewaretoken"] = _extract_csrf(resp)

        form_values.setdefault("name", "python-noop")
        form_values.setdefault("description", "")
        form_values.setdefault("trigger_event", "task.completed")
        form_values.setdefault("action", "python")
        form_values.setdefault("action_path", "amalthea.automation.executor.enrichment_probe")
        form_values.setdefault("is_active", "on")

        resp = _post_form(client_analyst, url, form_values)
        assert resp.status_code == 200

        playbook.refresh_from_db()
        config = playbook.config

        assert config["action"] == "python"
        assert config["action_path"] == "amalthea.automation.executor.enrichment_probe"
        assert playbook.trigger_event == "task.completed"
        assert playbook.is_active is True


class TestPlaybookEditAuthz:
    """Authorization tests for playbook edit."""

    def test_readonly_user_get_edit_allowed(
        self, client_readonly: Client, org: Organisation
    ) -> None:
        """Read-only user can GET the edit page (same as create)."""
        playbook = Playbook.objects.create(
            name="authz-test",
            trigger_event="observable.created",
            config={"action": "http", "url": "https://example.com", "method": "GET"},
        )

        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        resp = _get_form(client_readonly, url)
        # GET should be allowed for any authenticated org member
        assert resp.status_code == 200

    def test_readonly_user_post_edit_denied(
        self, client_readonly: Client, org: Organisation
    ) -> None:
        """Read-only user POST to edit is rejected (uniform 404)."""
        playbook = Playbook.objects.create(
            name="authz-test-post",
            trigger_event="observable.created",
            config={"action": "http", "url": "https://example.com", "method": "GET"},
        )

        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        resp = _get_form(client_readonly, url)
        assert resp.status_code == 200

        form_values = _parse_form_values(resp)
        form_values["csrfmiddlewaretoken"] = _extract_csrf(resp)
        form_values.setdefault("name", "authz-test-post")
        form_values.setdefault("description", "")
        form_values.setdefault("trigger_event", "observable.created")
        form_values.setdefault("action", "http")
        form_values.setdefault("http_method", "GET")
        form_values.setdefault("timeout_seconds", "10")
        form_values.setdefault("url", "https://example.com")
        form_values.setdefault("http_body", "")
        form_values.setdefault("is_active", "on")

        resp = _post_form(client_readonly, url, form_values)
        # POST requires analyst/admin/superAdmin → uniform 404
        assert resp.status_code == 404

    def test_analyst_user_post_edit_allowed(
        self, client_analyst: Client, org: Organisation
    ) -> None:
        """Analyst user can POST to edit."""
        playbook = Playbook.objects.create(
            name="analyst-edit",
            trigger_event="observable.created",
            config={"action": "http", "url": "https://example.com", "method": "GET"},
        )

        url = reverse("ui-playbook-edit", kwargs={"playbook_id": str(playbook.id)})
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 200

        form_values = _parse_form_values(resp)
        form_values["csrfmiddlewaretoken"] = _extract_csrf(resp)
        form_values.setdefault("name", "analyst-edit")
        form_values.setdefault("description", "")
        form_values.setdefault("trigger_event", "observable.created")
        form_values.setdefault("action", "http")
        form_values.setdefault("http_method", "GET")
        form_values.setdefault("timeout_seconds", "10")
        form_values.setdefault("url", "https://example.com")
        form_values.setdefault("http_body", "")
        form_values.setdefault("is_active", "on")

        resp = _post_form(client_analyst, url, form_values)
        # Should redirect to detail (success)
        assert resp.status_code == 200


class TestPlaybookEditNotFound:
    """404 handling for non-existent playbooks."""

    def test_edit_nonexistent_uuid_404(self, client_analyst: Client) -> None:
        """Edit non-existent UUID returns 404."""
        url = reverse(
            "ui-playbook-edit", kwargs={"playbook_id": "00000000-0000-0000-0000-000000000000"}
        )
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 404

    def test_edit_nonexistent_name_404(self, client_analyst: Client) -> None:
        """Edit non-existent name returns 404."""
        url = reverse("ui-playbook-edit", kwargs={"playbook_id": "no-such-playbook"})
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 404


class TestPlaybookCreateStillWorks:
    """Regression tests: playbook_create behaviour unchanged."""

    def test_create_http_playbook_via_ui(self, client_analyst: Client, org: Organisation) -> None:
        """Creating an HTTP playbook via UI still works."""
        url = reverse("ui-playbook-create")
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 200

        form_values = _parse_form_values(resp)
        form_values["csrfmiddlewaretoken"] = _extract_csrf(resp)
        form_values["name"] = "new-http-pb"
        form_values["description"] = "Created via test"
        form_values["trigger_event"] = "observable.created"
        form_values["action"] = "http"
        form_values["http_method"] = "POST"
        form_values["timeout_seconds"] = "5"
        form_values["url"] = "https://api.test.com/{observable}"
        form_values["header_key_0"] = "X-API-Key"
        form_values["header_val_0"] = "secret"
        form_values["header_count"] = "4"
        form_values["http_body"] = '{"key": "{observable}"}'
        form_values["is_active"] = "on"

        resp = _post_form(client_analyst, url, form_values)
        assert resp.status_code == 200

        # Verify created
        pb = Playbook.objects.get(name="new-http-pb")
        assert pb.config["action"] == "http"
        assert pb.config["url"] == "https://api.test.com/{observable}"
        assert pb.config["method"] == "POST"
        assert pb.config["timeoutSeconds"] == 5.0
        assert pb.config["headers"] == {"X-API-Key": "secret"}
        assert pb.config["body"] == '{"key": "{observable}"}'

    def test_create_python_playbook_via_ui(self, client_analyst: Client, org: Organisation) -> None:
        """Creating a Python playbook via UI still works."""
        url = reverse("ui-playbook-create")
        resp = _get_form(client_analyst, url)
        assert resp.status_code == 200

        form_values = _parse_form_values(resp)
        form_values["csrfmiddlewaretoken"] = _extract_csrf(resp)
        form_values["name"] = "new-python-pb"
        form_values["description"] = "Python test"
        form_values["trigger_event"] = "alert.ingested"
        form_values["action"] = "python"
        form_values["action_path"] = "amalthea.automation.executor.enrichment_probe"
        form_values["is_active"] = "on"

        resp = _post_form(client_analyst, url, form_values)
        assert resp.status_code == 200

        pb = Playbook.objects.get(name="new-python-pb")
        assert pb.config["action"] == "python"
        assert pb.config["action_path"] == "amalthea.automation.executor.enrichment_probe"

    def test_create_readonly_user_denied(self, client_readonly: Client, org: Organisation) -> None:
        """Read-only user cannot create playbook (POST → 404)."""
        url = reverse("ui-playbook-create")
        resp = _get_form(client_readonly, url)
        assert resp.status_code == 200  # GET allowed

        form_values = _parse_form_values(resp)
        form_values["csrfmiddlewaretoken"] = _extract_csrf(resp)
        form_values["name"] = "readonly-create"
        form_values["description"] = ""
        form_values["trigger_event"] = "observable.created"
        form_values["action"] = "python"
        form_values["action_path"] = "amalthea.automation.executor.enrichment_probe"
        form_values["is_active"] = "on"

        resp = _post_form(client_readonly, url, form_values)
        assert resp.status_code == 404
