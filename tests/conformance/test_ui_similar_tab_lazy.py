"""Conformance tests — Phase P5 (Similar tab: lazy-load on click + combined-count badge).

Authoritative sources:
- ``docs/planning/PLAN-2026-10-21-similar-tab-lazy-badge.md`` §5 (architecture),
  §6 (data/API contract), §8 (phases P1 to P5), §10 (testing), §14 (implementation record
  & deviations — read §14.3 and §14.5 in particular).
- ``docs/planning/BRIEF-2026-10-21-similar-tab-lazy-badge.md`` P2 to P4.

Mapping (test → AC):
  1. TestFragmentEndpoint.test_analyst_fragment_renders_both_cards_and_data_attributes → AC-P2.1
  2. TestFragmentEndpoint.test_viewer_fragment_renders_cards_without_link_button       → AC-P2.2
  3. TestFragmentEndpoint.test_unknown_and_cross_org_case_ids_are_404                → AC-P2.3
  4. TestFragmentEndpoint.test_full_page_similar_tab_still_renders_both_cards        → AC-P2.4
  5. TestFragmentEndpoint.test_shared_helper_runs_once_per_case_detail_render         → AC-P2.5
  6. TestBadge.test_badge_shows_combined_similar_count                                → AC-P3.1
  7. TestBadge.test_no_badge_when_nothing_is_similar                                  → AC-P3.2
  8. TestLazyPanelContract.test_general_tab_emits_lazy_src_and_not_loaded            → AC-P4.3
  9. TestLazyPanelContract.test_similar_tab_emits_loaded_and_not_lazy_src            → AC-P4.3/AC-P4.4
 10. TestLazyPanelContract.test_similar_tab_is_fully_rendered_server_side            → AC-P4.4
 11. TestLazyPanelContract.test_tabs_js_keeps_the_lazy_guards                          → AC-P4.5

Extras (documented deviations / defence in depth, not separate ACs):
  - TestFragmentEndpoint.test_fragment_requires_an_authenticated_user   → §5.1 (@login_required)
  - TestFragmentEndpoint.test_fragment_rejects_non_get_requests         → §14.3 (@require_GET)
  - TestFragmentEndpoint.test_fragment_is_a_fragment_not_a_full_page    → §5.1 (D2)

Falsifiability (PLAN §7.5 / §10) — proven by temporary sabotage + byte-for-byte restore:
  - **AC-P3.3**: reverting the badge assignment to the pre-fix form
    (``counts["similar"] = combined`` instead of ``tab["count"] = combined``) makes the
    badge tests FAIL (the ``counts``→``tabs`` loop has already run at that point, so the
    badge never reaches the tab). Restored afterwards; SHA-256 of ``ui/views.py`` before
    and after the sabotage is identical.
  - **AC-P4.5**: always emitting ``data-lazy-src`` from ``case_detail.html`` (i.e.
    dropping the "the server already rendered it" branch) makes the client-contract
    tests FAIL. Restored afterwards; SHA-256 of the template before and after the
    sabotage is identical.

JS note: the repository ships **no JS test harness** (no ``package.json``, no jsdom), so
PLAN §10 sanctions pinning the contract instead of running the DOM. The *rendered*
contract (``data-lazy-src`` / ``data-loaded``) is asserted from the server response in
``TestLazyPanelContract``; the ``tabs.js`` guards are asserted at source level in
``test_tabs_js_keeps_the_lazy_guards``. The runtime behaviour of ``tabs.js`` (exactly one
fetch on first activation, no re-fetch afterwards) is documented as manually verified.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

from alerts.hashing import compute_payload_ssdeep
from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseObservable, CaseStatus
from identity.models import Organisation, User
from observables.models import Observable, ObservableType

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Fixtures / helpers (mirrors tests/conformance/test_alert_fuzzy_similarity.py)
# ---------------------------------------------------------------------------

SUMMARY = "Phishing email reported by user jdoe — credential harvest link"


def _base_payload() -> dict:
    """The canonical "same event" payload (stable non-volatile content)."""
    return {
        "src_ip": "203.0.113.45",
        "dst_user": "jdoe",
        "sender": "paypa1@secure-mail.example",
        "url": "http://evil.example/login",
        "file_hash": "d41d8cd98f00b204e9800998ecf8427e",
        "rule": "SUSPICIOUS_PHISH",
        "action": "blocked",
        "process": {"name": "outlook.exe", "pid": 4242, "timestamp": "2026-10-20T10:00:00Z"},
        "event": {"id": "evt-111", "severity_hint": "high"},
        "tags": ["phishing", "credential"],
    }


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "analyst") -> User:
    return User.objects.create_user(
        username=login,
        login=login,
        password=f"pw-{login}",
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


def _mk_case(title: str, org: Organisation) -> Case:
    status = CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 1}
    )[0]
    return Case.objects.create(title=title, status=status, owner_org=org)


def _mk_alert(
    *,
    org: Organisation,
    title: str,
    source_ref: str,
    summary: str = SUMMARY,
    payload: dict | None = None,
    case: Case | None = None,
) -> Alert:
    """Create an Alert, hashing the payload exactly the way the ingest paths do."""
    if payload is None:
        payload = _base_payload()
    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    return Alert.objects.create(
        type="test",
        source="test-source",
        source_ref=source_ref,
        title=title,
        summary=summary,
        status=status,
        owner_org=org,
        case=case,
        raw_payload=payload,
        payload_ssdeep=compute_payload_ssdeep(summary, payload),
    )


def _share_observable(org: Organisation, value: str, *cases: Case) -> Observable:
    """One observable row linked into every case passed (per-org dedupe row)."""
    otype = ObservableType.objects.get_or_create(name="ip", defaults={"is_case_sensitive": False})[
        0
    ]
    obs = Observable.objects.create(
        data_type=otype, data=value, normalized_data=value, owner_org=org
    )
    for case in cases:
        CaseObservable.objects.create(case=case, observable=obs)
    return obs


def _login(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _case_url(case: Case, tab: str | None = None) -> str:
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    return f"{url}?tab={tab}" if tab else url


def _fragment_url(case: Case) -> str:
    """The lazy-load fragment endpoint (PLAN §5.1 / D2)."""
    return reverse("ui-case-similar", kwargs={"case_id": case.number})


# ---------------------------------------------------------------------------
# HTML helpers — small, dependency-free extractors over the rendered response
# ---------------------------------------------------------------------------


def _attr(fragment: str, name: str) -> str | None:
    """Value of ``name="…"`` inside an HTML tag fragment."""
    m = re.search(rf'{re.escape(name)}="([^"]*)"', fragment)
    return m.group(1) if m else None


def _tab_anchor(body: str, slug: str) -> str:
    """Raw HTML of the tab link ``<a … id="tab-<slug>" …>…</a>`` (the badge lives inside)."""
    marker = f'id="tab-{slug}"'
    idx = body.index(marker)
    start = body.rindex("<a ", 0, idx)
    end = body.index("</a>", idx)
    return body[start : end + len("</a>")]


def _tab_count(body: str, slug: str) -> str | None:
    """Text of the ``.count`` chip inside the given tab link, or ``None`` when absent."""
    m = re.search(r'<span class="count">\s*([^<]*?)\s*</span>', _tab_anchor(body, slug))
    return m.group(1) if m else None


def _panel_open_tag(body: str, panel_id: str) -> str:
    """Opening ``<section …>`` tag of the panel with the given id."""
    idx = body.index(f'id="{panel_id}"')
    start = body.rindex("<section", 0, idx)
    end = body.index(">", idx)
    return body[start : end + 1]


def _fragment_root(body: str) -> str:
    """Opening tag of the fragment root ``<div data-similar-count …>``."""
    m = re.search(r"<div\b[^>]*data-similar-count[^>]*>", body)
    assert m is not None, "fragment root carrying data-similar-count was not found"
    return m.group(0)


# ---------------------------------------------------------------------------
# P2 — shared helper + fragment endpoint (AC-P2.1 … AC-P2.5)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFragmentEndpoint:
    """AC-P2.1 to AC-P2.5 — the ``ui-case-similar`` fragment endpoint and the shared helper."""

    def test_analyst_fragment_renders_both_cards_and_data_attributes(self, db: None):
        """AC-P2.1 — analyst GET → 200, both cards, correct ``data-similar-*`` attributes."""
        org = _mk_org("OrgFragment")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Fragment", org)
        neighbour = _mk_case("Neighbour Case", org)
        _share_observable(org, "10.42.42.42", case, neighbour)
        _mk_alert(org=org, title="Attached seed", source_ref="fr-seed", case=case)
        candidate = _mk_alert(org=org, title="Fuzzy candidate", source_ref="fr-cand")

        resp = _login(analyst).get(_fragment_url(case))
        assert resp.status_code == 200
        body = resp.content.decode()

        # Both card sections are present, and they carry their data.
        assert 'aria-labelledby="h-similar"' in body
        assert 'aria-labelledby="h-similar-alerts"' in body
        assert "Similar cases" in body
        assert "Similar alerts" in body
        assert "Neighbour Case" in body
        assert candidate.title in body

        # The root carries the combined count and its two components (PLAN §6).
        root = _fragment_root(body)
        assert _attr(root, "data-similar-count") == "2"  # 1 similar case + 1 similar alert
        assert _attr(root, "data-similar-cases") == "1"
        assert _attr(root, "data-similar-alerts") == "1"

    def test_viewer_fragment_renders_cards_without_link_button(self, db: None):
        """AC-P2.2 — a viewer may fetch the fragment (D3): 200, cards, no mutating control."""
        org = _mk_org("OrgFragmentViewer")
        analyst = _mk_user("analyst", org)
        viewer = _mk_user("viewer", org, role="viewer")
        case = _mk_case("Case Fragment Viewer", org)
        _mk_alert(org=org, title="Attached seed", source_ref="fv-seed", case=case)
        candidate = _mk_alert(org=org, title="Viewer candidate", source_ref="fv-cand")

        # Positive control: the analyst *does* get the Link button, so the negative
        # assertion below is not vacuous.
        analyst_body = _login(analyst).get(_fragment_url(case)).content.decode()
        assert "Link to case" in analyst_body

        resp = _login(viewer).get(_fragment_url(case))
        assert resp.status_code == 200
        body = resp.content.decode()

        # The cards render for the viewer…
        assert 'aria-labelledby="h-similar"' in body
        assert 'aria-labelledby="h-similar-alerts"' in body
        assert candidate.title in body
        # …but there is no affordance to act on them.
        assert "Link to case" not in body

    @pytest.mark.parametrize("role", ["analyst", "viewer"])
    def test_unknown_and_cross_org_case_ids_are_404(self, db: None, role: str):
        """AC-P2.3 — unknown case id and cross-org case id → 404 for any authenticated user."""
        org1 = _mk_org("OrgFragmentScoped1")
        org2 = _mk_org("OrgFragmentScoped2")
        user = _mk_user(f"user-{role}", org1, role=role)
        foreign = _mk_case("Foreign case", org2)

        client = _login(user)

        unknown = client.get(reverse("ui-case-similar", kwargs={"case_id": "999999"}))
        assert unknown.status_code == 404

        cross_org = client.get(_fragment_url(foreign))
        assert cross_org.status_code == 404
        # A 404 must not leak the foreign case's identity.
        assert "Foreign case" not in cross_org.content.decode()

    def test_full_page_similar_tab_still_renders_both_cards(self, db: None):
        """AC-P2.4 — ``?tab=similar`` on the full page still renders both cards.

        The helper extraction must be behaviour-preserving: the real link path (and
        therefore the JS-disabled path) is untouched.
        """
        org = _mk_org("OrgFragmentFullPage")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Full Page", org)
        neighbour = _mk_case("Neighbour Full Page", org)
        _share_observable(org, "10.43.43.43", case, neighbour)
        _mk_alert(org=org, title="Attached seed", source_ref="fp-seed", case=case)
        candidate = _mk_alert(org=org, title="Full page candidate", source_ref="fp-cand")

        resp = _login(analyst).get(_case_url(case, "similar"))
        assert resp.status_code == 200
        body = resp.content.decode()

        assert 'aria-labelledby="h-similar"' in body
        assert 'aria-labelledby="h-similar-alerts"' in body
        assert "Neighbour Full Page" in body
        assert candidate.title in body

    def test_shared_helper_runs_once_per_case_detail_render(self, db: None):
        """AC-P2.5 — the shared helper runs exactly once per case-detail render.

        D1 (PLAN §14.1/§14.5) makes the helper run on **every** case-detail load so the
        Similar tab can carry a badge; the invariant that survives is "once, not twice".
        """
        org = _mk_org("OrgFragmentHelper")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Helper", org)
        _mk_alert(org=org, title="Attached seed", source_ref="h-seed", case=case)
        client = _login(analyst)

        from ui.views import _similar_for_case as real_helper

        for tab in ("general", "similar"):
            with mock.patch("ui.views._similar_for_case", wraps=real_helper) as patched:
                resp = client.get(_case_url(case, tab))
                assert resp.status_code == 200
                assert patched.call_count == 1, (
                    f"_similar_for_case ran {patched.call_count} times on ?tab={tab}"
                )

    def test_fragment_requires_an_authenticated_user(self, db: None):
        """§5.1 — the fragment is ``@login_required``: an anonymous caller is redirected."""
        org = _mk_org("OrgFragmentAnon")
        case = _mk_case("Case Anon", org)

        resp = Client().get(_fragment_url(case))
        assert resp.status_code == 302
        assert "/login" in resp["Location"]

    def test_fragment_rejects_non_get_requests(self, db: None):
        """§14.3 — the implementation added ``@require_GET`` on top of ``@login_required``."""
        org = _mk_org("OrgFragmentMethod")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Method", org)

        resp = _login(analyst).post(_fragment_url(case))
        assert resp.status_code == 405

    def test_fragment_is_a_fragment_not_a_full_page(self, db: None):
        """§5.1 (D2) — the endpoint returns the two cards only, not the whole page."""
        org = _mk_org("OrgFragmentShape")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Shape", org)

        body = _login(analyst).get(_fragment_url(case)).content.decode()
        assert "<html" not in body.lower()
        assert "<body" not in body.lower()
        assert "data-similar-count=" in body


# ---------------------------------------------------------------------------
# P3 — combined-count badge (AC-P3.1, AC-P3.2, AC-P3.3)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBadge:
    """AC-P3.1 / AC-P3.2 — the Similar tab badge carries the combined count."""

    def test_badge_shows_combined_similar_count(self, db: None):
        """AC-P3.1 — ``#tab-similar`` renders ``.count`` with ``similar_total + similar_alerts_total``."""
        org = _mk_org("OrgBadge")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Badge", org)
        neighbour_a = _mk_case("Neighbour Badge A", org)
        neighbour_b = _mk_case("Neighbour Badge B", org)
        _share_observable(org, "10.44.44.44", case, neighbour_a, neighbour_b)
        _mk_alert(org=org, title="Attached seed", source_ref="b-seed", case=case)
        _mk_alert(org=org, title="Badge candidate", source_ref="b-cand")

        resp = _login(analyst).get(_case_url(case, "general"))
        assert resp.status_code == 200
        body = resp.content.decode()

        anchor = _tab_anchor(body, "similar")
        assert '<span class="count">' in anchor
        # 2 similar cases + 1 similar alert. A "max" (or either-half) implementation
        # would render 2 or 1 — only the sum renders 3.
        assert _tab_count(body, "similar") == "3"

    def test_no_badge_when_nothing_is_similar(self, db: None):
        """AC-P3.2 — with zero similar cases and zero similar alerts, no ``.count`` renders."""
        org = _mk_org("OrgBadgeNone")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Badge None", org)

        resp = _login(analyst).get(_case_url(case, "general"))
        assert resp.status_code == 200
        body = resp.content.decode()

        anchor = _tab_anchor(body, "similar")
        assert '<span class="count">' not in anchor
        assert _tab_count(body, "similar") is None

        # Non-vacuity control: the badge machinery is live on this very page — the
        # Alerts tab renders its own (zero) count chip.
        assert _tab_count(body, "alerts") == "0"


# ---------------------------------------------------------------------------
# P4 — client lazy-load contract (AC-P4.3, AC-P4.4, AC-P4.5)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLazyPanelContract:
    """AC-P4.3 / AC-P4.4 — the server-rendered half of the lazy-load contract.

    PLAN §10: the client behaviour is asserted through the rendered attributes
    (``data-lazy-src`` / ``data-loaded``); the JS runtime has no harness in this repo.
    """

    def test_general_tab_emits_lazy_src_and_not_loaded(self, db: None):
        """AC-P4.3 — on ``?tab=general`` the panel is lazy: ``data-lazy-src``, no ``data-loaded``."""
        org = _mk_org("OrgLazyGeneral")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Lazy General", org)

        resp = _login(analyst).get(_case_url(case, "general"))
        assert resp.status_code == 200
        body = resp.content.decode()

        expected = reverse("ui-case-similar", kwargs={"case_id": case.number})
        assert expected == f"/cases/{case.number}/similar"

        tag = _panel_open_tag(body, "panel-similar")
        assert f'data-lazy-src="{expected}"' in tag
        assert "data-loaded" not in tag

    def test_similar_tab_emits_loaded_and_not_lazy_src(self, db: None):
        """AC-P4.3/AC-P4.4 — when the server already rendered the tab, no lazy-src is emitted."""
        org = _mk_org("OrgLazySimilar")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Lazy Similar", org)

        resp = _login(analyst).get(_case_url(case, "similar"))
        assert resp.status_code == 200
        body = resp.content.decode()

        tag = _panel_open_tag(body, "panel-similar")
        assert "data-loaded" in tag
        assert "data-lazy-src" not in tag

    def test_similar_tab_is_fully_rendered_server_side(self, db: None):
        """AC-P4.4 — with JS disabled, ``?tab=similar`` still renders everything server-side."""
        org = _mk_org("OrgLazyServerSide")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Lazy Server Side", org)
        neighbour = _mk_case("Neighbour Server Side", org)
        _share_observable(org, "10.45.45.45", case, neighbour)
        _mk_alert(org=org, title="Attached seed", source_ref="ls-seed", case=case)
        candidate = _mk_alert(org=org, title="Server side candidate", source_ref="ls-cand")

        resp = _login(analyst).get(_case_url(case, "similar"))
        assert resp.status_code == 200
        body = resp.content.decode()

        assert 'aria-labelledby="h-similar"' in body
        assert 'aria-labelledby="h-similar-alerts"' in body
        assert "Neighbour Server Side" in body
        assert candidate.title in body

    def test_tabs_js_keeps_the_lazy_guards(self):
        """AC-P4.5 — source-level contract of ``ui/static/ui/tabs.js``.

        No JS harness exists in this repository (PLAN §10), so the load-bearing guards
        are pinned at source level; the *rendered* contract is pinned by the tests above.
        """
        source = (REPO_ROOT / "ui" / "static" / "ui" / "tabs.js").read_text()

        # Fetch only when the panel has a lazy source and has not been loaded yet.
        assert "panel.dataset.lazySrc" in source
        assert "!panel.dataset.loaded" in source
        # The loaded marker is set once the fragment has been swapped in.
        assert 'panel.dataset.loaded = "true"' in source
        # Concurrent requests are collapsed by the in-flight flag.
        assert "similarRefresh" in source
