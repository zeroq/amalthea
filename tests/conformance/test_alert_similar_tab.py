"""Conformance tests for Amalthea section 24: alert detail Similar tab.

Authoritative sources:
- docs/planning/IMPL-2026-11-21-alert-similar-tab.md
- TODO.md section 24.6-24.10

Coverage map:
- helper scope/caps/signals -> AC-B1..B6
- detail tab and fragment    -> AC-B7..B11
- templates and accessibility -> AC-F1..F5
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from alerts.models import Alert, AlertObservable, AlertStatus
from cases.correlation import SIMILAR_CLOSED_ALERTS_LIMIT, alerts_similar_to_alert
from identity.models import AuditLog, Organisation, User
from observables.models import Observable, ObservableType

pytestmark = pytest.mark.django_db


SETTINGS = {
    "ALERT_FUZZY_SIMILARITY_THRESHOLD": 60,
    "ALERT_FUZZY_TITLE_SIMILARITY_THRESHOLD": 70,
    "ALERT_FUZZY_TITLE_GATE_ENABLED": True,
}


@pytest.fixture(autouse=True)
def _similarity_settings() -> None:
    with override_settings(**SETTINGS):
        yield


def _org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _user(login: str, org: Organisation, *, role: str = "analyst") -> User:
    return User.objects.create_user(
        username=login,
        login=login,
        password=f"pw-{login}",
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


def _status(stage: str) -> AlertStatus:
    return AlertStatus.objects.get_or_create(
        value=f"Stage-{stage}", defaults={"stage": stage, "order": 1}
    )[0]


def _alert(
    org: Organisation,
    ref: str,
    *,
    title: str = "Phishing attack",
    ssdeep: str = "eaaaab",
    stage: str = "Closed",
    verdict: str = "undefined",
) -> Alert:
    return Alert.objects.create(
        owner_org=org,
        status=_status(stage),
        type="test",
        source="test-source",
        source_ref=ref,
        title=title,
        summary="Summary",
        raw_payload={},
        payload_ssdeep=ssdeep,
        verdict=verdict,
    )


def _observable(org: Organisation, value: str, *, ignore: bool = False) -> Observable:
    otype, _ = ObservableType.objects.get_or_create(
        name="ip", defaults={"is_case_sensitive": False}
    )
    return Observable.objects.create(
        owner_org=org,
        data_type=otype,
        data=value,
        normalized_data=value,
        ignore_similarity=ignore,
    )


def _link(alert: Alert, observable: Observable) -> None:
    AlertObservable.objects.create(alert=alert, observable=observable)


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _fragment(client: Client, alert: Alert) -> str:
    url = reverse("ui-alert-similar", kwargs={"alert_id": alert.id})
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def _panel(html: str, slug: str) -> str:
    marker = f'id="panel-{slug}"'
    start = html.rfind("<section", 0, html.index(marker))
    end = html.find('<section class="tabpanel"', start + 10)
    return html[start:] if end == -1 else html[start:end]


class TestHelperScope:
    def test_closed_only_and_source_excluded(self) -> None:
        org = _org("OrgClosedOnly")
        analyst = _user("analyst", org)
        source = _alert(org, "source")
        closed = _alert(org, "closed")
        open_candidate = _alert(org, "open", stage="New")

        with mock.patch("ppdeep.compare", return_value=100):
            rows, total = alerts_similar_to_alert(source, user=analyst)

        assert {row.alert.id for row in rows} == {closed.id}
        assert total == 1
        assert source.id not in {row.alert.id for row in rows}
        assert open_candidate.id not in {row.alert.id for row in rows}

    def test_caller_and_source_org_scope_even_for_superadmin(self) -> None:
        org1 = _org("OrgScope1")
        org2 = _org("OrgScope2")
        superadmin = _user("super", org1, role="superAdmin")
        source = _alert(org1, "source")
        same_org = _alert(org1, "same")
        cross_org = _alert(org2, "cross")

        with mock.patch("ppdeep.compare", return_value=100):
            rows, _ = alerts_similar_to_alert(source, user=superadmin)

        assert {row.alert.id for row in rows} == {same_org.id}
        assert cross_org.id not in {row.alert.id for row in rows}

    def test_no_usable_signals_returns_empty(self) -> None:
        org = _org("OrgEmptySignals")
        analyst = _user("analyst", org)
        source = _alert(org, "source", ssdeep="")
        _alert(org, "candidate")

        assert alerts_similar_to_alert(source, user=analyst) == ([], 0)


class TestHelperFuzzy:
    def test_below_payload_threshold_excluded(self) -> None:
        org = _org("OrgThreshold")
        analyst = _user("analyst", org)
        source = _alert(org, "source")
        _alert(org, "candidate")

        with mock.patch("ppdeep.compare", return_value=59):
            rows, total = alerts_similar_to_alert(source, user=analyst)

        assert rows == []
        assert total == 0

    def test_title_gate_includes_same_title(self) -> None:
        org = _org("OrgTitleInclude")
        analyst = _user("analyst", org)
        source = _alert(org, "source", title="Phishing attack")
        candidate = _alert(org, "candidate", title="Phishing attack")

        with mock.patch("ppdeep.compare", return_value=100):
            rows, _ = alerts_similar_to_alert(source, user=analyst)

        assert {row.alert.id for row in rows} == {candidate.id}

    def test_title_gate_excludes_different_title(self) -> None:
        org = _org("OrgTitleExclude")
        analyst = _user("analyst", org)
        source = _alert(org, "source", title="Phishing attack")
        _alert(org, "candidate", title="Disk health warning")

        with mock.patch("ppdeep.compare", return_value=100):
            rows, total = alerts_similar_to_alert(source, user=analyst)

        assert rows == []
        assert total == 0

    def test_ppdeep_import_failure_degrades_to_zero(self) -> None:
        org = _org("OrgNoPpdeep")
        analyst = _user("analyst", org)
        source = _alert(org, "source")
        _alert(org, "candidate")

        with mock.patch.dict("sys.modules", {"ppdeep": None}):
            rows, total = alerts_similar_to_alert(source, user=analyst)

        assert rows == []
        assert total == 0


class TestHelperObservables:
    def test_shared_observable_match_and_ignore_similarity(self) -> None:
        org = _org("OrgObsIgnore")
        analyst = _user("analyst", org)
        source = _alert(org, "source", ssdeep="")
        good = _observable(org, "10.10.10.10")
        ignored = _observable(org, "10.10.10.11", ignore=True)
        _link(source, good)
        _link(source, ignored)

        candidate_good = _alert(org, "good", ssdeep="")
        _link(candidate_good, good)
        candidate_ignored = _alert(org, "ignored", ssdeep="")
        _link(candidate_ignored, ignored)

        rows, _ = alerts_similar_to_alert(source, user=analyst)

        by_id = {row.alert.id: row for row in rows}
        assert candidate_good.id in by_id
        assert by_id[candidate_good.id].similarity is None
        assert by_id[candidate_good.id].shared_observable_count == 1
        assert candidate_ignored.id not in by_id

    def test_dual_channel_union_preserves_signals(self) -> None:
        org = _org("OrgDual")
        analyst = _user("analyst", org)
        source = _alert(org, "source")
        shared = _observable(org, "10.20.20.20")
        _link(source, shared)

        fuzzy_only = _alert(org, "fuzzy")
        obs_only = _alert(org, "obs", ssdeep="")
        both = _alert(org, "both")
        _link(obs_only, shared)
        _link(both, shared)

        with mock.patch("ppdeep.compare", return_value=90):
            rows, total = alerts_similar_to_alert(source, user=analyst)

        by_id = {row.alert.id: row for row in rows}
        assert total == 3
        assert by_id[fuzzy_only.id].similarity == 90
        assert by_id[fuzzy_only.id].shared_observable_count == 0
        assert by_id[obs_only.id].similarity is None
        assert by_id[obs_only.id].shared_observable_count == 1
        assert by_id[both.id].similarity == 90
        assert by_id[both.id].shared_observable_count == 1


class TestHelperOrderAndCap:
    def test_ordering_is_by_updated_at_not_similarity(self) -> None:
        org = _org("OrgOrdering")
        analyst = _user("analyst", org)
        source = _alert(org, "source")

        high = _alert(org, "high", ssdeep="high")
        low = _alert(org, "low", ssdeep="low")
        Alert.objects.filter(pk=high.pk).update(
            updated_at=timezone.make_aware(datetime(2026, 1, 1, 9, 0))
        )
        Alert.objects.filter(pk=low.pk).update(
            updated_at=timezone.make_aware(datetime(2026, 1, 2, 9, 0))
        )

        def _score(candidate_hash: str, _source_hash: str) -> int:
            return 90 if candidate_hash == "high" else 70

        with mock.patch("ppdeep.compare", side_effect=_score):
            rows, _ = alerts_similar_to_alert(source, user=analyst)

        assert [row.alert.id for row in rows] == [low.id, high.id]

    def test_final_cap_and_honest_total(self) -> None:
        org = _org("OrgCap")
        analyst = _user("analyst", org)
        source = _alert(org, "source")
        for i in range(25):
            _alert(org, f"candidate-{i}")

        with mock.patch("ppdeep.compare", return_value=100):
            rows, total = alerts_similar_to_alert(source, user=analyst)

        assert len(rows) == SIMILAR_CLOSED_ALERTS_LIMIT
        assert total == 25


class TestDetailTab:
    def test_tab_order_badge_activation_and_icon(self) -> None:
        org = _org("OrgTab")
        analyst = _user("analyst", org)
        source = _alert(org, "source", title="Candidate title")
        _alert(org, "candidate", title="Candidate title")

        with mock.patch("ppdeep.compare", return_value=100):
            response = _client(analyst).get(
                reverse("ui-alert-detail", kwargs={"alert_id": source.id}) + "?tab=similar"
            )

        html = response.content.decode()
        positions = [
            html.index(f'id="tab-{slug}"')
            for slug in ("general", "observables", "similar", "raw-data")
        ]
        assert positions == sorted(positions)
        assert '<span class="badge">1</span>' in html
        assert "fa-diagram-project" in html
        assert "Candidate title" in _panel(html, "similar")
        assert "hidden" not in _panel(html, "similar")[:200]

    def test_default_tab_uses_lazy_panel(self) -> None:
        org = _org("OrgLazy")
        analyst = _user("analyst", org)
        source = _alert(org, "source", ssdeep="")

        response = _client(analyst).get(reverse("ui-alert-detail", kwargs={"alert_id": source.id}))
        html = response.content.decode()
        lazy_url = reverse("ui-alert-similar", kwargs={"alert_id": source.id})

        assert f'data-lazy-src="{lazy_url}"' in _panel(html, "similar")
        assert "hidden" in _panel(html, "similar")[:200]

    def test_invalid_tab_falls_back_to_general(self) -> None:
        org = _org("OrgInvalidTab")
        analyst = _user("analyst", org)
        source = _alert(org, "source", ssdeep="")

        response = _client(analyst).get(
            reverse("ui-alert-detail", kwargs={"alert_id": source.id}) + "?tab=nope"
        )
        html = response.content.decode()

        assert 'id="tab-general"' in html
        assert 'aria-selected="true"' in html[html.index('id="tab-general"') :]
        general_panel = _panel(html, "general")
        assert "hidden" not in general_panel[:200]


class TestFragmentAccess:
    def test_login_required_viewer_read_and_404s(self) -> None:
        org = _org("OrgAccess")
        analyst = _user("analyst", org)
        viewer = _user("viewer", org, role="viewer")
        other_org = _org("OrgOtherAccess")
        cross_org = _alert(other_org, "cross", ssdeep="")
        source = _alert(org, "source", ssdeep="")
        superadmin = _user("super", org, role="superAdmin")
        url = reverse("ui-alert-similar", kwargs={"alert_id": source.id})

        assert Client().get(url).status_code == 302
        assert _client(viewer).get(url).status_code == 200
        assert (
            _client(analyst)
            .get(reverse("ui-alert-similar", kwargs={"alert_id": cross_org.id}))
            .status_code
            == 404
        )
        assert (
            _client(analyst)
            .get(
                reverse(
                    "ui-alert-similar",
                    kwargs={"alert_id": "00000000-0000-0000-0000-000000000000"},
                )
            )
            .status_code
            == 404
        )
        assert (
            _client(superadmin)
            .get(reverse("ui-alert-similar", kwargs={"alert_id": cross_org.id}))
            .status_code
            == 200
        )

    def test_get_is_read_only_and_emits_no_audit(self) -> None:
        org = _org("OrgNoAudit")
        analyst = _user("analyst", org)
        source = _alert(org, "source")
        _alert(org, "candidate")
        client = _client(analyst)
        before = AuditLog.objects.count()

        with mock.patch("ppdeep.compare", return_value=100):
            response = client.get(reverse("ui-alert-similar", kwargs={"alert_id": source.id}))

        assert response.status_code == 200
        assert AuditLog.objects.count() == before


class TestFragmentRendering:
    def test_rows_links_labels_dates_and_similarity_variants(self) -> None:
        org = _org("OrgRender")
        analyst = _user("analyst", org)
        source = _alert(org, "source", title="Fuzzy candidate")
        shared = _observable(org, "10.30.30.30")
        _link(source, shared)

        fuzzy = _alert(org, "fuzzy", title="Fuzzy candidate", verdict="true_positive")
        obs_only = _alert(org, "obs", title="Observable candidate", ssdeep="")
        both = _alert(org, "both", title="Fuzzy candidate")
        _link(obs_only, shared)
        _link(both, shared)

        with mock.patch("ppdeep.compare", return_value=90):
            body = _fragment(_client(analyst), source)

        assert f'href="{reverse("ui-alert-detail", kwargs={"alert_id": fuzzy.id})}"' in body
        assert "Fuzzy candidate" in body
        assert "true positive" in body.lower() or "true_positive" in body.lower()
        assert "90%" in body
        assert "1 shared obs" in body
        assert "90% · 1 shared obs" in body
        assert 'class="data-table data-table-tight"' in body
        assert '<caption class="sr-only">' in body
        assert 'scope="col"' in body

    def test_truncation_is_honest(self) -> None:
        org = _org("OrgTruncation")
        analyst = _user("analyst", org)
        source = _alert(org, "source")
        for i in range(21):
            _alert(org, f"candidate-{i}")

        with mock.patch("ppdeep.compare", return_value=100):
            body = _fragment(_client(analyst), source)

        assert "Showing" in body
        assert "20" in body
        assert "21" in body

    def test_empty_state(self) -> None:
        org = _org("OrgEmptyState")
        analyst = _user("analyst", org)
        source = _alert(org, "source", ssdeep="")

        body = _fragment(_client(analyst), source)

        assert "No closed alerts are similar enough right now." in body

    def test_url_name_resolves(self) -> None:
        url = reverse(
            "ui-alert-similar", kwargs={"alert_id": "00000000-0000-0000-0000-000000000000"}
        )
        assert url.endswith("/similar")
