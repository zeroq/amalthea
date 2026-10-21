"""Conformance tests for Phase D — fuzzy (SSDEEP) alert similarity (AC-D1).

Authoritative sources:
- ``docs/planning/PLAN-2026-10-20-alert-fuzzy-similarity.md`` §3.2 (Decision (A): title
  excluded from the hash), §3.7, §3.8, §7 Phase D (AC-D1), §9.
- ``docs/planning/BRIEF-2026-10-20-alert-fuzzy-similarity.md`` Phase D.

Mapping (test → AC):
 1. TestDeterminism            → AC-A2 (deterministic hash / canonical text)
 2. TestVolatileKeys           → AC-A2 (volatile-only diffs → identical text+hash) [falsifiable]
 3. TestTitleExcludedDecisionA → Plan §3.2 Decision (A)
 4. TestThresholdBoundary      → AC-B1 + Q3 (default 80, inclusive ``>= threshold``)
 5. TestCrossOrgIsolation      → AC-B1/AC-C1 + prior AC-5 (helper, UI, cross-org POST) [falsifiable]
 6. TestAttachedExclusion      → AC-B1 (attached alerts never returned) [falsifiable]
 7. TestThresholdGate          → AC-B1 (below-threshold alert does not leak) [falsifiable]
 8. TestViewerReadOnly         → AC-C1 + prior AC-6
 9. TestLinkFlow               → prior AC-3/AC-4/AC-7 (link + ledger + audit, idempotent)
10. TestBackfillCommand        → AC-A5 (idempotent, second run updates 0, --dry-run writes nothing)
11. TestUnhashablePayloads     → AC-A2 (oversize/overdeep/exception → "" without raising)
12. TestEmptyStates            → AC-C1 (both reworded empty states)
13. TestTabGating              → AC-B2 (fuzzy helper computed only for tab=similar)

This module also restores the still-valid guarantees that were stripped from
``tests/conformance/test_ui_similar_alerts.py`` during Phase B (tenant isolation #5,
viewer read-only #8, link-flow idempotence #9, tab-gating #13, empty states #12);
the two "Similar cases" status-label tests in that module are left untouched.

Falsifiability (PLAN §7.5 / §8): #5, #6, #7 and #2 are proven by temporary sabotage of
the guard under test (org filter / ``case__isnull=True`` / ``>= threshold`` check /
volatile-key drop), run and restored per the Phase D instructions.
"""

from __future__ import annotations

import io
import re
import sys
from unittest import mock

import ppdeep
import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from alerts.hashing import VOLATILE_KEYS, canonicalize_payload, compute_payload_ssdeep
from alerts.models import Alert, AlertStatus
from cases.correlation import MAX_FUZZY_COMPARISONS, alerts_fuzzy_similar_to_case
from cases.models import Case, CaseStatus, TimelineEvent
from identity.models import AuditLog, Organisation, User

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SUMMARY = "Phishing email reported by user jdoe — credential harvest link"
SENTINEL_TITLE = "ZZ-SENTINEL-CROSSORG-LEAK must not render in org1"


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


def _near_dup_payload() -> dict:
    """Same event, different noise: only volatile keys differ (top level AND nested)."""
    payload = _base_payload()
    payload["id"] = "evt-999"
    payload["timestamp"] = "2026-10-20T10:05:33Z"
    payload["pid"] = 9999
    payload["process"]["pid"] = 7777
    payload["process"]["timestamp"] = "2026-10-20T10:05:33Z"
    payload["event"]["id"] = "evt-999"
    return payload


def _variant_payload() -> dict:
    """A genuinely different event — scores < 80 against ``_base_payload()`` (measured 68)."""
    return {
        "src_ip": "198.51.100.7",
        "dst_user": "asmith",
        "sender": "hr@corp-internal.example",
        "url": "http://intranet.example/payroll",
        "file_hash": "00112233445566778899aabbccddeeff",
        "rule": "BENIGN_SPAM",
        "action": "allowed",
        "process": {"name": "chrome.exe", "pid": 12, "timestamp": "2026-10-20T11:00:00Z"},
        "event": {"id": "evt-555", "severity_hint": "low"},
        "tags": ["newsletter"],
    }


def _overdeep_payload(levels: int = 40) -> dict:
    """A payload nested deeper than ``WEBHOOK_MAX_DEPTH`` (default 30)."""
    node: dict = {"leaf": True}
    for _ in range(levels):
        node = {"nested": node}
    return node


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
    payload_ssdeep: str | None = None,
) -> Alert:
    """Create an Alert, computing ``payload_ssdeep`` unless it is passed explicitly.

    ``payload_ssdeep=None`` (default) computes the hash exactly the way both ingest
    paths do; ``payload_ssdeep=""`` simulates a pre-feature row (hashable or not).
    """
    if payload is None:
        payload = _base_payload()
    if payload_ssdeep is None:
        payload_ssdeep = compute_payload_ssdeep(summary, payload)
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
        payload_ssdeep=payload_ssdeep,
    )


def _login(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _case_url(case: Case, tab: str | None = None) -> str:
    url = reverse("ui-case-detail", kwargs={"case_id": case.number})
    return f"{url}?tab={tab}" if tab else url


def _similar_url(case: Case) -> str:
    return _case_url(case, "similar")


def _link_url(case: Case) -> str:
    return reverse("ui-case-alert-link", kwargs={"case_id": case.number})


def _ids(rows) -> set:
    return {row.alert.id for row in rows}


# ---------------------------------------------------------------------------
# 1. Determinism (AC-A2)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """AC-A2 — same (summary, payload) → same hash; key order never changes the text."""

    def test_same_summary_and_payload_produces_same_hash(self):
        first = compute_payload_ssdeep(SUMMARY, _base_payload())
        second = compute_payload_ssdeep(SUMMARY, _base_payload())
        assert first != ""
        assert first == second

    def test_canonical_text_ignores_dict_key_order(self):
        d1 = {
            "alpha": 1,
            "nested": {"inner": 2, "deep": {"x": 3}},
            "scalar_list": ["b", "a"],
        }
        d2 = {
            "scalar_list": ["a", "b"],
            "nested": {"deep": {"x": 3}, "inner": 2},
            "alpha": 1,
        }
        assert canonicalize_payload(d1) == canonicalize_payload(d2)
        assert compute_payload_ssdeep("s", d1) == compute_payload_ssdeep("s", d2)


# ---------------------------------------------------------------------------
# 2. Volatile-key insensitivity (AC-A2) — falsifiable via the volatile-key drop
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVolatileKeys:
    """AC-A2 — payloads differing only in volatile keys (top level + nested) are equal."""

    def test_volatile_only_differences_give_identical_text_and_hash(self, db: None):
        stable = _base_payload()
        noisy = _near_dup_payload()

        # The volatile keys exercised here really are in the audited constant.
        assert {"id", "timestamp", "pid"} <= VOLATILE_KEYS

        assert canonicalize_payload(stable) == canonicalize_payload(noisy)
        h1 = compute_payload_ssdeep(SUMMARY, stable)
        h2 = compute_payload_ssdeep(SUMMARY, noisy)
        assert h1 != ""
        assert h1 == h2

        # Non-vacuous control: a NON-volatile change does change the canonical text.
        changed = _base_payload()
        changed["action"] = "allowed"
        assert canonicalize_payload(changed) != canonicalize_payload(stable)


# ---------------------------------------------------------------------------
# 3. Decision (A) — title excluded from the hash (PLAN §3.2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTitleExcludedDecisionA:
    """Decision (A): two alerts with different titles but identical summary+payload
    hash identically (``title`` is not an input to ``compute_payload_ssdeep``)."""

    def test_different_titles_identical_summary_and_payload_hash_equal(self, db: None):
        org = _mk_org("OrgTitleExcluded")
        a123 = _mk_alert(
            org=org,
            title="ALERT-123 Credential phishing attempt",
            source_ref="ref-123",
            payload=_base_payload(),
        )
        a99 = _mk_alert(
            org=org,
            title="ALERT-99 Credential phishing attempt",
            source_ref="ref-99",
            payload=_base_payload(),
        )

        assert a123.title != a99.title
        assert a123.payload_ssdeep != ""
        assert a123.payload_ssdeep == a99.payload_ssdeep
        assert ppdeep.compare(a123.payload_ssdeep, a99.payload_ssdeep) == 100


# ---------------------------------------------------------------------------
# 4. Threshold boundary (AC-B1 + Q3)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestThresholdBoundary:
    """A near-duplicate at/above the default threshold is included; a variant below it
    is excluded. An explicit ``threshold=`` pins the inclusive ``>=`` boundary exactly."""

    def test_default_and_explicit_threshold_include_exclude(self, db: None):
        org = _mk_org("OrgThreshold")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Boundary", org)

        attached = _mk_alert(
            org=org, title="Attached seed", source_ref="seed-1", payload=_base_payload(), case=case
        )
        near_dup = _mk_alert(
            org=org, title="Near duplicate", source_ref="near-1", payload=_near_dup_payload()
        )
        variant = _mk_alert(
            org=org, title="Different event", source_ref="variant-1", payload=_variant_payload()
        )

        score_near = ppdeep.compare(near_dup.payload_ssdeep, attached.payload_ssdeep)
        score_variant = ppdeep.compare(variant.payload_ssdeep, attached.payload_ssdeep)

        # AC-A4: the shipped default is 80 and the crafted pair straddles it.
        assert settings.ALERT_FUZZY_SIMILARITY_THRESHOLD == 80
        assert score_near >= settings.ALERT_FUZZY_SIMILARITY_THRESHOLD
        assert score_variant < settings.ALERT_FUZZY_SIMILARITY_THRESHOLD

        # Default threshold: include near-dup, exclude variant.
        rows, total = alerts_fuzzy_similar_to_case(case, user=analyst)
        assert near_dup.id in _ids(rows)
        assert variant.id not in _ids(rows)
        assert total == 1
        assert next(r.similarity for r in rows if r.alert.id == near_dup.id) == score_near

        # Explicit threshold pinned to the variant's score: ``>=`` is inclusive.
        rows, _ = alerts_fuzzy_similar_to_case(case, user=analyst, threshold=score_variant)
        assert variant.id in _ids(rows)
        assert near_dup.id in _ids(rows)

        # One point higher: the variant falls out, the near-dup stays.
        rows, _ = alerts_fuzzy_similar_to_case(case, user=analyst, threshold=score_variant + 1)
        assert variant.id not in _ids(rows)
        assert near_dup.id in _ids(rows)

        # The gate applies to the high scorer too (nothing can beat a perfect 100).
        rows, total = alerts_fuzzy_similar_to_case(case, user=analyst, threshold=score_near + 1)
        assert rows == []
        assert total == 0

    def test_cost_cap_constant(self):
        # Q7 / §3.7 — the comparison bound is a separate, smaller cap.
        assert MAX_FUZZY_COMPARISONS == 500


# ---------------------------------------------------------------------------
# 5. Cross-org isolation (AC-B1/AC-C1 + prior AC-5) — falsifiable via the org filter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCrossOrgIsolation:
    """An identical-payload, SAME-hash alert in another org must never be returned by
    the helper nor rendered in the UI (and can never be linked into the case)."""

    def test_same_hash_alert_in_other_org_neither_returned_nor_rendered(self, db: None):
        org1 = _mk_org("Org1Fuzzy")
        org2 = _mk_org("Org2Fuzzy")
        analyst1 = _mk_user("analyst1", org1)
        case = _mk_case("Case Org1", org1)

        attached = _mk_alert(
            org=org1,
            title="Attached seed",
            source_ref="x-attached",
            payload=_base_payload(),
            case=case,
        )
        same_org = _mk_alert(
            org=org1, title="Same-org candidate", source_ref="x-same-org", payload=_base_payload()
        )
        cross_org = _mk_alert(
            org=org2,
            title=SENTINEL_TITLE,
            source_ref="x-cross-org",
            payload=_base_payload(),
        )

        # The cross-org alert carries the SAME hash as the case's own attached alert.
        assert cross_org.payload_ssdeep != ""
        assert cross_org.payload_ssdeep == attached.payload_ssdeep

        rows, total = alerts_fuzzy_similar_to_case(case, user=analyst1)
        assert same_org.id in _ids(rows)  # positive control: the org filter admits org1
        assert cross_org.id not in _ids(rows)
        assert total == 1
        assert all(row.alert.owner_org_id == org1.id for row in rows)

        # Rendered UI: the card shows the same-org row, never the cross-org sentinel.
        body = _login(analyst1).get(_similar_url(case)).content.decode()
        assert "Same-org candidate" in body
        assert SENTINEL_TITLE not in body
        assert str(cross_org.id) not in body

    def test_cross_org_link_post_is_404_and_leaves_alert_unlinked(self, db: None):
        org1 = _mk_org("Org1Post")
        org2 = _mk_org("Org2Post")
        analyst1 = _mk_user("analyst1", org1)
        case = _mk_case("Case Org1", org1)
        cross_org = _mk_alert(
            org=org2, title=SENTINEL_TITLE, source_ref="x-post-cross", payload=_base_payload()
        )

        resp = _login(analyst1).post(
            _link_url(case), {"alert_id": str(cross_org.id), "tab": "similar"}
        )

        assert resp.status_code == 404
        cross_org.refresh_from_db()
        assert cross_org.case_id is None
        assert TimelineEvent.objects.filter(case=case, kind="correlation-bundle").count() == 0
        assert (
            AuditLog.objects.filter(
                target_type="Case", target_id=str(case.id), action="case.alert_bundled"
            ).count()
            == 0
        )

    def test_superadmin_cross_org_link_is_refused_and_not_persisted(self, db: None):
        """FIND-04: cross-org alert bundling is refused for *every* role, superAdmin included.

        The regular-analyst guard is covered above; this pins the stronger invariant found
        during audit: ``record_alert_bundled`` raises ``PermissionDenied("Cross-org alert
        bundle refused")`` regardless of role, so the atomic block rolls back and nothing is
        linked or audited — even though ``resolve_alert(write=True)`` *does* return the alert
        for a superAdmin (ADR-004 "sees all"), the merge itself is refused.
        """
        org1 = _mk_org("Org1Super")
        org2 = _mk_org("Org2Super")
        superadmin = _mk_user("root", org1, role="superAdmin")
        case = _mk_case("Case Super", org1)
        cross_org = _mk_alert(
            org=org2, title=SENTINEL_TITLE, source_ref="x-super-cross", payload=_base_payload()
        )

        resp = _login(superadmin).post(
            _link_url(case), {"alert_id": str(cross_org.id), "tab": "similar"}
        )

        # Redirect happens either way (success or error banner) — the invariant is persistence.
        assert resp.status_code == 302
        cross_org.refresh_from_db()
        assert cross_org.case_id is None
        assert TimelineEvent.objects.filter(case=case).count() == 0
        assert (
            AuditLog.objects.filter(
                target_type="Case", target_id=str(case.id), action="case.alert_bundled"
            ).count()
            == 0
        )


# ---------------------------------------------------------------------------
# 6. Attached-alert exclusion (AC-B1) — falsifiable via case__isnull=True
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAttachedAlertExclusion:
    """An alert already linked to the case must never be returned, even with a 100 score."""

    def test_attached_alerts_never_returned(self, db: None):
        org = _mk_org("OrgAttached")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Attached", org)

        attached_a = _mk_alert(
            org=org, title="Attached A", source_ref="att-a", payload=_base_payload(), case=case
        )
        # Identical hash to attached_a, also linked to the case → equally "perfect" match.
        attached_b = _mk_alert(
            org=org, title="Attached B", source_ref="att-b", payload=_base_payload(), case=case
        )
        unlinked = _mk_alert(
            org=org, title="Unlinked candidate", source_ref="unlinked-1", payload=_base_payload()
        )

        assert attached_b.payload_ssdeep == attached_a.payload_ssdeep == unlinked.payload_ssdeep

        rows, total = alerts_fuzzy_similar_to_case(case, user=analyst)
        ids = _ids(rows)

        assert unlinked.id in ids
        assert attached_a.id not in ids
        assert attached_b.id not in ids
        assert not ids & {attached_a.id, attached_b.id}
        assert total == 1


# ---------------------------------------------------------------------------
# 7. Threshold gate (AC-B1) — falsifiable via the >= threshold check
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestThresholdGate:
    """A below-threshold alert (non-empty hash, same org, unlinked) must not leak."""

    def test_below_threshold_alert_does_not_leak(self, db: None):
        org = _mk_org("OrgGate")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Gate", org)

        attached = _mk_alert(
            org=org,
            title="Attached seed",
            source_ref="gate-seed",
            payload=_base_payload(),
            case=case,
        )
        variant = _mk_alert(
            org=org, title="Below threshold", source_ref="gate-variant", payload=_variant_payload()
        )

        # Non-vacuous: the candidate is otherwise fully eligible (hash, org, unlinked)…
        assert variant.payload_ssdeep != ""
        assert variant.case_id is None
        assert variant.owner_org_id == org.id
        # …and genuinely scores below the shipped default.
        score = ppdeep.compare(variant.payload_ssdeep, attached.payload_ssdeep)
        assert score < settings.ALERT_FUZZY_SIMILARITY_THRESHOLD

        rows, total = alerts_fuzzy_similar_to_case(case, user=analyst)
        assert rows == []
        assert total == 0
        assert variant.id not in _ids(rows)


# ---------------------------------------------------------------------------
# 8. Viewer read-only (AC-C1 + prior AC-6)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestViewerReadOnly:
    """A viewer sees the card (rows render) but gets no Link button; POST is refused
    and the alert stays unlinked."""

    def test_viewer_sees_rows_without_link_button_and_post_is_refused(self, db: None):
        org = _mk_org("OrgViewerFuzz")
        analyst = _mk_user("analyst", org)
        viewer = _mk_user("viewer", org, role="viewer")
        case = _mk_case("Case Viewer", org)

        _mk_alert(
            org=org,
            title="Attached seed",
            source_ref="v-seed",
            payload=_base_payload(),
            case=case,
        )
        candidate = _mk_alert(
            org=org,
            title="Viewer-visible candidate",
            source_ref="v-cand",
            payload=_base_payload(),
        )

        # Positive control: the analyst (can_mutate) gets the Link button.
        analyst_body = _login(analyst).get(_similar_url(case)).content.decode()
        assert "Viewer-visible candidate" in analyst_body
        assert "Link to case" in analyst_body

        viewer_client = _login(viewer)
        viewer_body = viewer_client.get(_similar_url(case)).content.decode()
        assert "Viewer-visible candidate" in viewer_body  # the card/row is visible
        assert "Link to case" not in viewer_body  # …but no affordance to act on it

        resp = viewer_client.post(
            _link_url(case), {"alert_id": str(candidate.id), "tab": "similar"}
        )
        assert resp.status_code == 404
        candidate.refresh_from_db()
        assert candidate.case_id is None
        assert TimelineEvent.objects.filter(case=case, kind="correlation-bundle").count() == 0
        assert (
            AuditLog.objects.filter(
                target_type="Case", target_id=str(case.id), action="case.alert_bundled"
            ).count()
            == 0
        )


# ---------------------------------------------------------------------------
# 9. Link flow idempotence (prior AC-3/AC-4/AC-7)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLinkFlow:
    """Linking the fuzzy-returned alert: 302 → tab=similar, one ledger + one audit row;
    re-POST is a no-op that keeps exactly one of each."""

    def test_link_post_attaches_writes_ledger_and_audit_then_idempotent(self, db: None):
        org = _mk_org("OrgLinkFlow")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Link", org)

        _mk_alert(
            org=org,
            title="Attached seed",
            source_ref="l-seed",
            payload=_base_payload(),
            case=case,
        )
        candidate = _mk_alert(
            org=org, title="Fuzzy candidate", source_ref="l-cand", payload=_base_payload()
        )

        # The alert we link is exactly what the fuzzy helper returned.
        rows, total = alerts_fuzzy_similar_to_case(case, user=analyst)
        assert total == 1 and _ids(rows) == {candidate.id}

        client = _login(analyst)
        resp = client.post(_link_url(case), {"alert_id": str(candidate.id), "tab": "similar"})
        assert resp.status_code == 302
        assert "tab=similar" in resp["Location"]

        candidate.refresh_from_db()
        assert candidate.case_id == case.id

        ledger = TimelineEvent.objects.filter(case=case, kind="correlation-bundle")
        audit = AuditLog.objects.filter(
            target_type="Case", target_id=str(case.id), action="case.alert_bundled"
        )
        assert ledger.count() == 1
        assert audit.count() == 1

        # Re-POST: still a redirect to the same tab, still exactly one ledger + audit row.
        resp2 = client.post(_link_url(case), {"alert_id": str(candidate.id), "tab": "similar"})
        assert resp2.status_code == 302
        assert "tab=similar" in resp2["Location"]
        assert ledger.count() == 1
        assert audit.count() == 1

        # And the now-linked alert no longer shows up as a similar candidate.
        rows, total = alerts_fuzzy_similar_to_case(case, user=analyst)
        assert total == 0
        assert candidate.id not in _ids(rows)


# ---------------------------------------------------------------------------
# 10. Backfill idempotence (AC-A5)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBackfillCommand:
    """AC-A5 — populates empty hashes, second run updates 0, --dry-run writes nothing."""

    def test_backfill_idempotent_second_run_updates_0_and_dry_run_writes_nothing(self, db: None):
        org = _mk_org("OrgBackfill")
        fill_a = _mk_alert(org=org, title="Pre-feature A", source_ref="bf-a", payload_ssdeep="")
        fill_b = _mk_alert(
            org=org,
            title="Pre-feature B",
            source_ref="bf-b",
            payload=_variant_payload(),
            payload_ssdeep="",
        )
        preset = _mk_alert(
            org=org, title="Already hashed", source_ref="bf-preset", payload_ssdeep="SENTINEL-HASH"
        )
        unhashable = _mk_alert(
            org=org,
            title="Overdeep pre-feature",
            source_ref="bf-deep",
            payload=_overdeep_payload(),
            payload_ssdeep="",
        )

        out = io.StringIO()
        call_command("backfill_alert_ssdeep", stdout=out, no_color=True)
        fill_a.refresh_from_db()
        fill_b.refresh_from_db()
        preset.refresh_from_db()
        unhashable.refresh_from_db()

        assert fill_a.payload_ssdeep == compute_payload_ssdeep(SUMMARY, _base_payload())
        assert fill_b.payload_ssdeep == compute_payload_ssdeep(SUMMARY, _variant_payload())
        assert fill_a.payload_ssdeep != "" and fill_b.payload_ssdeep != ""
        # A previously computed hash is untouched without --force (deviation §11.2).
        assert preset.payload_ssdeep == "SENTINEL-HASH"
        # An unhashable payload is skipped, never blanked into a wrong value.
        assert unhashable.payload_ssdeep == ""
        assert re.search(r"Updated:\s+2\b", out.getvalue())

        # Second run: only the still-unhashable row is eligible → 0 updated.
        before = {alert.id: alert.payload_ssdeep for alert in (fill_a, fill_b, preset, unhashable)}
        out2 = io.StringIO()
        call_command("backfill_alert_ssdeep", stdout=out2, no_color=True)
        fill_a.refresh_from_db()
        fill_b.refresh_from_db()
        preset.refresh_from_db()
        unhashable.refresh_from_db()
        assert re.search(r"Updated:\s+0\b", out2.getvalue())
        assert before == {
            fill_a.id: fill_a.payload_ssdeep,
            fill_b.id: fill_b.payload_ssdeep,
            preset.id: preset.payload_ssdeep,
            unhashable.id: unhashable.payload_ssdeep,
        }

        # --dry-run: computes, reports, writes nothing.
        dry = _mk_alert(org=org, title="Dry-run target", source_ref="bf-dry", payload_ssdeep="")
        out3 = io.StringIO()
        call_command("backfill_alert_ssdeep", "--dry-run", stdout=out3, no_color=True)
        dry.refresh_from_db()
        unhashable.refresh_from_db()
        assert "DRY RUN" in out3.getvalue()
        assert dry.payload_ssdeep == ""
        assert unhashable.payload_ssdeep == ""


# ---------------------------------------------------------------------------
# 11. Unhashable payloads → "" without raising (AC-A2)
# ---------------------------------------------------------------------------


class TestUnhashablePayloads:
    """AC-A2 — oversize/overdeep/exception payloads yield "" and never raise."""

    def test_oversize_payload_returns_empty_string(self):
        with override_settings(WEBHOOK_MAX_BODY_SIZE=64):
            assert compute_payload_ssdeep(SUMMARY, {"pad": "x" * 200}) == ""

    def test_overdeep_payload_returns_empty_string(self):
        with override_settings(WEBHOOK_MAX_DEPTH=3):
            assert compute_payload_ssdeep(SUMMARY, _overdeep_payload(levels=8)) == ""

    def test_exception_payload_returns_empty_string(self):
        # Not JSON-serializable → json.dumps raises inside canonicalize → caught → "".
        assert compute_payload_ssdeep(SUMMARY, {"boom": object()}) == ""

    def test_depth_check_is_iterative_not_recursive(self):
        """FIND-02: the depth probe must not recurse on attacker-controlled JSON.

        A payload nested far beyond Python's recursion limit must be measured
        iteratively (no RecursionError) and rejected by the depth bound → "".
        Falsifiable: the pre-FIND-02 recursive ``_depth`` raises ``RecursionError``
        while probing this structure.
        """
        from alerts.hashing import _max_json_depth

        deep_levels = sys.getrecursionlimit() + 500
        payload: dict[str, object] = {}
        node: dict[str, object] = payload
        for _ in range(deep_levels):
            child: dict[str, object] = {}
            node["n"] = child
            node = child

        # Iterative probe returns the real depth without raising.
        assert _max_json_depth(payload) == deep_levels
        # And the public entry point rejects it (overdeep) with "" rather than crashing.
        assert compute_payload_ssdeep(SUMMARY, payload) == ""

    @pytest.mark.django_db
    def test_empty_hash_alert_is_never_fuzzy_comparable(self, db: None):
        org = _mk_org("OrgEmptyHash")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Empty Hash", org)
        # A pre-feature row whose hash could not be computed stays ""…
        unhashed = _mk_alert(
            org=org, title="Not fuzzy-comparable", source_ref="eh-1", payload_ssdeep=""
        )
        assert unhashed.payload_ssdeep == ""
        # …and an attached "" hash gives the helper no signal at all.
        _mk_alert(
            org=org,
            title="Attached unhashed",
            source_ref="eh-attached",
            case=case,
            payload_ssdeep="",
        )

        rows, total = alerts_fuzzy_similar_to_case(case, user=analyst)
        assert rows == []
        assert total == 0


# ---------------------------------------------------------------------------
# 12. Empty states (AC-C1) — restored from test_ui_similar_alerts.py
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmptyStates:
    """AC-C1 — two distinct, reworded empty states on the Similar tab."""

    def test_has_attached_alerts_but_no_matches(self, db: None):
        org = _mk_org("OrgEmptyNoMatch")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case No Match", org)

        _mk_alert(
            org=org,
            title="Attached seed",
            source_ref="es-seed",
            payload=_base_payload(),
            case=case,
        )
        # An unlinked alert exists and has a hash — it just isn't similar enough.
        _mk_alert(
            org=org,
            title="Not-similar candidate",
            source_ref="es-variant",
            payload=_variant_payload(),
        )

        body = _login(analyst).get(_similar_url(case)).content.decode()
        assert "No unlinked alerts are similar enough right now." in body
        assert "This case has no alerts to compare against." not in body
        assert "Not-similar candidate" not in body

    def test_no_attached_alerts(self, db: None):
        org = _mk_org("OrgEmptyNoAlerts")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Without Alerts", org)

        body = _login(analyst).get(_similar_url(case)).content.decode()
        assert "This case has no alerts to compare against." in body
        assert "No unlinked alerts are similar enough right now." not in body


# ---------------------------------------------------------------------------
# 13. Tab gating (AC-B2) — restored from test_ui_similar_alerts.py
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTabGating:
    """AC-B2 — the fuzzy helper runs only when the Similar tab is rendered."""

    def test_fuzzy_helper_called_once_on_similar_and_never_on_general(self, db: None):
        org = _mk_org("OrgTabGate")
        analyst = _mk_user("analyst", org)
        case = _mk_case("Case Tabs", org)
        _mk_alert(
            org=org,
            title="Attached seed",
            source_ref="tg-seed",
            payload=_base_payload(),
            case=case,
        )
        client = _login(analyst)

        with mock.patch("ui.views.alerts_fuzzy_similar_to_case", return_value=([], 0)) as patched:
            resp_general = client.get(_case_url(case, "general"))
            assert resp_general.status_code == 200
            assert patched.call_count == 0

            resp_similar = client.get(_similar_url(case))
            assert resp_similar.status_code == 200
            assert patched.call_count == 1

        # The real helper is what populates the card outside the mock.
        body = client.get(_similar_url(case)).content.decode()
        assert "No unlinked alerts are similar enough right now." in body
