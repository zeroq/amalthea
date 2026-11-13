"""API conformance tests for ``GET /api/v1/search`` (plan §9.2)."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from identity.models import AuditLog


@pytest.mark.django_db
class TestSearchAPIBasic:
    def test_route_resolves_both_slash_and_no_slash(self, anonymous_api) -> None:
        for name in ("search-api", "search-api-slash"):
            resp = anonymous_api.get(reverse(name), format="json")
            assert resp.status_code == 401

    def test_blank_q_returns_empty_envelope(self, api) -> None:
        resp = api.get(reverse("search-api"), {"q": "   "}, format="json")
        assert resp.status_code == 200
        assert resp.json() == {
            "query": "",
            "type": "all",
            "total": 0,
            "counts": {"case": 0, "alert": 0, "observable": 0},
            "sections": [],
        }
        assert resp["X-Total"] == "0"

    def test_invalid_type_returns_400(self, api) -> None:
        resp = api.get(reverse("search-api"), {"q": "x", "type": "Case"}, format="json")
        assert resp.status_code == 400
        assert resp.json()["type"] == "BadRequest"

    def test_invalid_pagination_returns_400(self, api) -> None:
        for params in (
            {"q": "x", "type": "case", "page": "0"},
            {"q": "x", "type": "case", "page": "nope"},
            {"q": "x", "type": "case", "per_page": "0"},
            {"q": "x", "type": "case", "per_page": "201"},
        ):
            resp = api.get(reverse("search-api"), params, format="json")
            assert resp.status_code == 400, params
            assert resp.json()["type"] == "BadRequest"

    def test_all_ignores_per_page(self, api) -> None:
        resp = api.get(
            reverse("search-api"), {"q": "x", "type": "all", "per_page": "201"}, format="json"
        )
        assert resp.status_code == 200

    def test_get_only(self, api) -> None:
        url = reverse("search-api")
        for method in ("post", "put", "patch", "delete"):
            assert getattr(api, method)(url, format="json").status_code == 405


@pytest.mark.django_db
class TestSearchAPISemantics:
    def test_case_fields_and_icontains(self, api, analyst, make_case) -> None:
        make_case(org=analyst.org, title="Apache2 HTTP Server")
        make_case(org=analyst.org, title="Other", description="apache in body")
        make_case(org=analyst.org, title="Other", summary="apache in summary")
        make_case(org=analyst.org, title="Different")

        resp = api.get(reverse("search-api"), {"q": "APACHE", "type": "case"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["counts"]["case"] == 3

    def test_multi_term_is_anded_across_fields(self, api, analyst, make_case) -> None:
        make_case(org=analyst.org, title="Apt29 Activity", description="hash observed")
        make_case(org=analyst.org, title="Just hash", description="nothing else")
        resp = api.get(reverse("search-api"), {"q": "apt29 hash", "type": "case"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["counts"]["case"] == 1

    def test_case_number_is_exact_and_ordered_first(self, api, analyst, make_case) -> None:
        exact = make_case(org=analyst.org, title="Needle target", severity=1)
        make_case(org=analyst.org, title="Needle high", severity=4)

        resp = api.get(
            reverse("search-api"), {"q": f"#{exact.number}", "type": "case"}, format="json"
        )
        assert resp.status_code == 200
        results = resp.json()["sections"][0]["results"]
        assert [r["id"] for r in results] == [str(exact.id)]
        assert results[0]["number"] == exact.number

    def test_digit_only_term_also_matches_case_text_fields(self, api, analyst, make_case) -> None:
        exact = make_case(org=analyst.org, title="Needle exact", severity=1)
        title_hit = make_case(org=analyst.org, title=f"Ticket {exact.number}", severity=4)
        description_hit = make_case(
            org=analyst.org, title="Other", description=f"event {exact.number}", severity=2
        )
        summary_hit = make_case(
            org=analyst.org, title="Other", summary=f"{exact.number} in summary", severity=3
        )
        make_case(org=analyst.org, title="Different")

        resp = api.get(
            reverse("search-api"), {"q": str(exact.number), "type": "case"}, format="json"
        )
        assert resp.status_code == 200
        results = resp.json()["sections"][0]["results"]
        assert {r["id"] for r in results} == {
            str(exact.id),
            str(title_hit.id),
            str(description_hit.id),
            str(summary_hit.id),
        }
        assert results[0]["id"] == str(exact.id)
        assert results[0]["number"] == exact.number

    def test_digit_only_term_matches_all_entity_text(
        self, api, analyst, make_case, make_alert, make_observable
    ) -> None:
        case = make_case(org=analyst.org, title="Case without the number")
        make_alert(org=analyst.org, title=f"Alert {case.number}")
        make_observable(org=analyst.org, data=f"10.0.0.{case.number}", data_type="ip")

        resp = api.get(reverse("search-api"), {"q": str(case.number), "type": "all"}, format="json")
        assert resp.status_code == 200
        counts = resp.json()["counts"]
        assert counts["case"] == 1
        assert counts["alert"] == 1
        assert counts["observable"] == 1

    def test_alert_and_observable_fields(self, api, analyst, make_alert, make_observable) -> None:
        make_alert(org=analyst.org, title="Needle alert", description="x", summary="y")
        make_observable(org=analyst.org, data="needle.example", data_type="domain")
        resp = api.get(reverse("search-api"), {"q": "needle", "type": "all"}, format="json")
        assert resp.status_code == 200
        counts = resp.json()["counts"]
        assert counts["alert"] == 1
        assert counts["observable"] == 1


@pytest.mark.django_db
class TestSearchAPISectionsAndPaging:
    def test_typed_pagination_uses_offset_and_clamps(self, api, analyst, make_case) -> None:
        for index in range(60):
            make_case(org=analyst.org, title=f"Paged case {index}")
        url = reverse("search-api")
        first = api.get(url, {"q": "paged", "type": "case", "per_page": "50"}, format="json")
        assert first.status_code == 200
        section = first.json()["sections"][0]
        assert section["page"] == 1
        assert section["per_page"] == 50
        assert len(section["results"]) == 50
        assert section["has_next"] is True

        second = api.get(
            url, {"q": "paged", "type": "case", "page": "2", "per_page": "50"}, format="json"
        )
        section = second.json()["sections"][0]
        assert section["page"] == 2
        assert len(section["results"]) == 10
        assert section["has_next"] is False

        clamped = api.get(
            url, {"q": "paged", "type": "case", "page": "999", "per_page": "50"}, format="json"
        )
        section = clamped.json()["sections"][0]
        assert section["page"] == 2
        assert len(section["results"]) == 10

    def test_all_returns_ten_per_kind_and_all_counts(
        self, api, analyst, make_case, make_alert
    ) -> None:
        for index in range(15):
            make_case(org=analyst.org, title=f"Common case {index}")
        make_alert(org=analyst.org, title="Common alert")
        resp = api.get(reverse("search-api"), {"q": "common", "type": "all"}, format="json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"] == {"case": 15, "alert": 1, "observable": 0}
        assert data["total"] == 16
        assert resp["X-Total"] == "16"
        by_type = {section["type"]: section for section in data["sections"]}
        assert len(by_type["case"]["results"]) == 10
        assert by_type["case"]["has_next"] is False
        assert by_type["case"]["per_page"] == 10
        assert len(by_type["alert"]["results"]) == 1

    def test_typed_reports_all_counts(self, api, analyst, make_case, make_alert) -> None:
        make_case(org=analyst.org, title="Common case")
        make_alert(org=analyst.org, title="Common alert")
        resp = api.get(reverse("search-api"), {"q": "common", "type": "case"}, format="json")
        data = resp.json()
        assert data["counts"] == {"case": 1, "alert": 1, "observable": 0}
        assert data["total"] == 2


@pytest.mark.django_db
class TestSearchAPIScopeAndAudit:
    def test_cross_org_values_absent(
        self, api, analyst, make_case, make_alert, make_observable
    ) -> None:
        from identity.models import Organisation

        other = Organisation.objects.create(name="Other Org")
        make_case(org=other, title="Secret needle")
        make_alert(org=other, title="Secret needle")
        make_observable(org=other, data="secret-needle.example", data_type="domain")

        resp = api.get(reverse("search-api"), {"q": "needle", "type": "all"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["counts"] == {"case": 0, "alert": 0, "observable": 0}
        assert resp["X-Total"] == "0"

    def test_shared_case_is_visible_to_recipient_org(self, api, analyst, make_case) -> None:
        from cases.models import Share
        from identity.models import Organisation

        owner = Organisation.objects.create(name="Owner Org")
        case = make_case(org=owner, title="Shared needle")
        Share.objects.create(case=case, organisation=analyst.org, permissions={"read": True})

        resp = api.get(reverse("search-api"), {"q": "shared", "type": "case"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["counts"]["case"] == 1
        assert resp.json()["sections"][0]["results"][0]["id"] == str(case.id)

    def test_superadmin_sees_all_and_audits_per_kind_and_org(
        self, super_admin, analyst, make_case, make_alert
    ) -> None:
        from identity.models import Organisation

        other = Organisation.objects.create(name="Other Org")
        make_case(org=analyst.org, title="Shared needle")
        make_alert(org=other, title="Shared needle")

        client = APIClient()
        client.force_authenticate(user=super_admin)
        resp = client.get(reverse("search-api"), {"q": "shared", "type": "all"}, format="json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["case"] == 1
        assert data["counts"]["alert"] == 1
        assert data["total"] == 2

        logs = AuditLog.objects.filter(action="cross_org_read_search")
        assert logs.count() == 2
        assert set(logs.values_list("target_type", flat=True)) == {"Case", "Alert"}


@pytest.mark.django_db
class TestSearchAPIRawText:
    def test_titles_are_raw_and_not_highlighted(self, api, analyst, make_case) -> None:
        make_case(org=analyst.org, title="Needle <script>alert(1)</script>")
        resp = api.get(reverse("search-api"), {"q": "needle", "type": "case"}, format="json")
        title = resp.json()["sections"][0]["results"][0]["title"]
        assert title == "Needle <script>alert(1)</script>"
        assert "<mark>" not in title
