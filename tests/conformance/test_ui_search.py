"""UI conformance tests for global search (plan §8/§9.3)."""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from ui.templatetags.ui_tags import highlight


def test_highlight_escapes_source_html() -> None:
    rendered = str(highlight("<script>alert(1)</script>", "script"))
    assert "&lt;" in rendered and "&gt;" in rendered
    assert "<script>" not in rendered
    assert "<mark>script</mark>" in rendered


def test_highlight_escapes_query_and_handles_blank() -> None:
    assert str(highlight("plain text", " ")) == "plain text"
    rendered = str(highlight("Needle <b>bold</b>", "needle"))
    assert "<mark>Needle</mark>" in rendered
    assert "&lt;b&gt;" in rendered
    assert "<b>" not in rendered


@pytest.mark.django_db
class TestSearchUI:
    def test_requires_login(self, client) -> None:
        response = client.get(reverse("ui-search"), {"q": "x"})
        assert response.status_code == 302
        assert response.url.startswith("/login?next=")

    def test_search_form_is_present_on_search_page(self, client, analyst) -> None:
        client.force_login(analyst)
        response = client.get(reverse("ui-search"))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="global-search-q"' in content, "search input must have id='global-search-q'"
        assert 'name="q"' in content, "search input must have name='q'"
        assert 'value="phish"' in content or 'value=""' in content, (
            "search input should be prefilled or blank"
        )
        assert 'type="search"' in content or 'type="text"' in content, (
            "search input must have type='search' or type='text'"
        )
        assert re.search(r'<button[^>]*type="submit"[^>]*>\s*Search\s*</button>', content), (
            "visible Search button must be present"
        )
        assert 'method="get"' in content, "form method must be 'get'"
        assert 'action="/search"' in content, "form action must be '/search'"
        assert 'name="type"' in content, "hidden type field must be present"

    def test_search_form_is_not_on_other_pages(self, client, analyst) -> None:
        client.force_login(analyst)
        response = client.get(reverse("ui-dashboard"))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="global-search-q"' not in content, (
            "dashboard page must not contain the global search input"
        )
        assert 'action="/search"' not in content, "dashboard page must not contain action='/search'"
        response = client.get(reverse("ui-alert-list"))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="global-search-q"' not in content, (
            "alerts page must not contain the global search input"
        )

    def test_search_form_prefills_query_and_type(self, client, analyst) -> None:
        client.force_login(analyst)
        response = client.get(reverse("ui-search"), {"q": "phish", "type": "case"})
        assert response.status_code == 200
        content = response.content.decode()
        assert 'value="phish"' in content, "search input must be prefilled with the query"
        assert 'name="type" value="case"' in content, "hidden type field must be 'case'"

    def test_blank_search_form_autofocuses(self, client, analyst) -> None:
        client.force_login(analyst)
        response = client.get(reverse("ui-search"))
        assert response.status_code == 200
        content = response.content.decode()
        assert re.search(r'<input[^>]*id="global-search-q"[^>]*autofocus', content), (
            "blank search form must have autofocus attribute on #global-search-q input"
        )

        response = client.get(reverse("ui-search"), {"q": "phish", "type": "case"})
        assert response.status_code == 200
        content = response.content.decode()
        assert not re.search(r'<input[^>]*id="global-search-q"[^>]*autofocus', content), (
            "typed search form must not have autofocus attribute on #global-search-q input"
        )

    def test_viewer_can_search(self, client, analyst, make_case) -> None:
        from identity.models import User

        viewer = User.objects.create(
            login="viewer",
            username="viewer",
            email="viewer@example.net",
            org=analyst.org,
            role="viewer",
        )
        make_case(org=analyst.org, title="Visible to viewer")
        client.force_login(viewer)
        response = client.get(reverse("ui-search"), {"q": "visible", "type": "case"})
        assert response.status_code == 200
        assert "<mark>Visible</mark> to viewer" in response.content.decode()

    def test_blank_query_shows_instruction(self, client, analyst) -> None:
        client.force_login(analyst)
        response = client.get(reverse("ui-search"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Try a case number" in content
        assert "No matches" not in content

    def test_typed_results_show_number_and_safe_highlight(self, client, analyst, make_case) -> None:
        case = make_case(org=analyst.org, title="Phish <script>alert(1)</script>")
        client.force_login(analyst)
        response = client.get(reverse("ui-search"), {"q": "phish", "type": "case"})
        assert response.status_code == 200
        content = response.content.decode()
        assert f"#{case.number}" in content
        assert "<mark>Phish</mark>" in content
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
        assert "<script>alert(1)</script>" not in content

        by_number = client.get(reverse("ui-search"), {"q": f"#{case.number}", "type": "case"})
        assert f"<mark>#{case.number}</mark>" in by_number.content.decode()

    def test_all_view_groups_and_links(self, client, analyst, make_case, make_alert) -> None:
        make_case(org=analyst.org, title="Common case")
        make_alert(org=analyst.org, title="Common alert")
        client.force_login(analyst)
        response = client.get(reverse("ui-search"), {"q": "common", "type": "all"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "<mark>Common</mark> case" in content
        assert "<mark>Common</mark> alert" in content
        assert "View all 1 case results" in content
        assert "View all 1 alert results" in content
        assert "?q=common&type=case" in content

    def test_invalid_type_falls_back_to_all(self, client, analyst, make_case) -> None:
        make_case(org=analyst.org, title="Fallback case")
        client.force_login(analyst)
        response = client.get(reverse("ui-search"), {"q": "fallback", "type": "nonsense"})
        assert response.status_code == 200
        assert "<mark>Fallback</mark> case" in response.content.decode()

    def test_typed_pager_links_keep_query(self, client, analyst, make_case) -> None:
        for index in range(60):
            make_case(org=analyst.org, title=f"Paged case {index}")
        client.force_login(analyst)
        response = client.get(reverse("ui-search"), {"q": "paged", "type": "case"})
        content = response.content.decode()
        assert "Page 1 of 2" in content
        assert "q=paged" in content

    def test_superadmin_sees_owning_organisation(self, client, super_admin, make_case) -> None:
        from identity.models import Organisation

        other = Organisation.objects.create(name="Other Org")
        make_case(org=other, title="Secret needle")
        client.force_login(super_admin)
        response = client.get(reverse("ui-search"), {"q": "secret", "type": "all"})
        assert response.status_code == 200
        content = response.content.decode()
        assert "<mark>Secret</mark> needle" in content
        assert "Other Org" in content
