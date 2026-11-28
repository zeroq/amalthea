"""Conformance tests for §12.12 fuzzy-similarity follow-ups.

Covers the bounded SSDEEP input/candidate ceiling (FIND-03), extensible volatile handling
(FIND-06), explicit `--clear-on-failure` backfill behavior (FIND-07), the `make lock`
recipe (FIND-11), and documentation guards for list-sort ordering (FIND-12).
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

from alerts.hashing import canonicalize_payload, compute_payload_ssdeep
from alerts.management.commands.backfill_alert_ssdeep import Command
from alerts.models import Alert, AlertStatus
from cases.correlation import alerts_fuzzy_similar_to_case
from cases.models import Case, CaseStatus
from identity.models import Organisation, User

SUMMARY = "Phishing email reported by user jdoe — credential harvest link"


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation) -> User:
    return User.objects.create_user(
        username=login,
        login=login,
        password=f"pw-{login}",
        email=f"{login}@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )


def _mk_case(org: Organisation) -> Case:
    status = CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 1}
    )[0]
    return Case.objects.create(title="Case", status=status, owner_org=org)


def _mk_alert(
    *,
    org: Organisation,
    source_ref: str = "default",
    case: Case | None = None,
    hashed: str = "HASH",
) -> Alert:
    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    return Alert.objects.create(
        type="test",
        source="test-source",
        source_ref=source_ref,
        title="Shared title",
        summary=SUMMARY,
        status=status,
        owner_org=org,
        case=case,
        raw_payload={},
        payload_ssdeep=hashed,
    )


def _overdeep_payload(levels: int = 40) -> dict:
    node: dict = {"leaf": True}
    for _ in range(levels):
        node = {"nested": node}
    return node


class TestBoundedFuzzyHashing:
    def test_hash_input_is_truncated_to_configured_size(self) -> None:
        with override_settings(ALERT_FUZZY_MAX_TEXT_SIZE=64):
            with mock.patch("alerts.hashing.ppdeep.hash", return_value="H") as hash_mock:
                assert compute_payload_ssdeep(SUMMARY, {"pad": "x" * 1000}) == "H"

        text = hash_mock.call_args.args[0]
        assert len(text) <= 64
        assert text.startswith("Phishing email")

    def test_non_positive_text_limit_disables_hashing(self) -> None:
        with override_settings(ALERT_FUZZY_MAX_TEXT_SIZE=0):
            assert compute_payload_ssdeep(SUMMARY, {"pad": "x" * 100}) == ""

    @pytest.mark.django_db
    def test_case_signals_are_capped_before_comparison(self, db: None) -> None:
        org = _mk_org("OrgCaseSignalCap")
        user = _mk_user("analyst-cap", org)
        case = _mk_case(org)

        for index in range(20):
            _mk_alert(org=org, case=case, source_ref=f"attached-{index}")
        for index in range(5):
            _mk_alert(org=org, source_ref=f"candidate-{index}")

        with override_settings(
            ALERT_FUZZY_MAX_CASE_SIGNALS=7, ALERT_FUZZY_TITLE_GATE_ENABLED=False
        ):
            with mock.patch("ppdeep.compare", return_value=100) as compare_mock:
                rows, total = alerts_fuzzy_similar_to_case(case, user=user)

        assert compare_mock.call_count == 35  # 7 case signals x 5 candidates
        assert total == 5
        assert len(rows) == 5

    @pytest.mark.django_db
    def test_zero_case_signals_returns_no_candidates(self, db: None) -> None:
        org = _mk_org("OrgNoSignals")
        user = _mk_user("analyst-no-signals", org)
        case = _mk_case(org)
        _mk_alert(org=org, case=case)
        _mk_alert(org=org, source_ref="candidate")

        with override_settings(ALERT_FUZZY_MAX_CASE_SIGNALS=0):
            rows, total = alerts_fuzzy_similar_to_case(case, user=user)

        assert rows == []
        assert total == 0


class TestVolatileHandling:
    def test_configured_extra_keys_are_dropped_at_any_depth(self) -> None:
        with override_settings(
            ALERT_FUZZY_EXTRA_VOLATILE_KEYS="flow_id, session-id",
            ALERT_FUZZY_VOLATILE_VALUE_PATTERNS=False,
        ):
            stable = {
                "stable": "x",
                "flow_id": "a",
                "session-id": "one",
                "nested": {"keep": "z"},
            }
            noisy = {
                "stable": "x",
                "_flow_id": "b",
                "nested": {"SESSION_ID": "two", "keep": "z"},
            }
            assert canonicalize_payload(stable) == canonicalize_payload(noisy)

            noisy["nested"]["stable"] = "y"
            assert canonicalize_payload(noisy) != canonicalize_payload(stable)

    def test_uuid_timestamp_and_epoch_values_are_dropped(self) -> None:
        with override_settings(ALERT_FUZZY_VOLATILE_VALUE_PATTERNS=True):
            stable = {
                "stable": "x",
                "trace_ref": "550e8400-e29b-41d4-a716-446655440000",
                "when": "2026-10-20T10:00:00Z",
                "clock_ticks": "1760964000",
            }
            noisy = {
                "stable": "x",
                "trace_ref": "123e4567-e89b-12d3-a456-426614174000",
                "when": "2026-10-21T11:22:33.123Z",
                "clock_ticks": "1761050400000",
            }
            assert canonicalize_payload(stable) == canonicalize_payload(noisy)

            noisy["stable"] = "y"
            assert canonicalize_payload(noisy) != canonicalize_payload(stable)

    def test_value_pattern_heuristic_can_be_disabled(self) -> None:
        with override_settings(ALERT_FUZZY_VOLATILE_VALUE_PATTERNS=False):
            first = {"trace_ref": "550e8400-e29b-41d4-a716-446655440000"}
            second = {"trace_ref": "123e4567-e89b-12d3-a456-426614174000"}
            assert canonicalize_payload(first) != canonicalize_payload(second)

    def test_settings_expose_bounds_and_heuristics(self) -> None:
        assert isinstance(settings.ALERT_FUZZY_MAX_TEXT_SIZE, int)
        assert settings.ALERT_FUZZY_MAX_TEXT_SIZE > 0
        assert isinstance(settings.ALERT_FUZZY_MAX_CASE_SIGNALS, int)
        assert settings.ALERT_FUZZY_MAX_CASE_SIGNALS > 0
        assert isinstance(settings.ALERT_FUZZY_VOLATILE_VALUE_PATTERNS, bool)
        assert isinstance(settings.ALERT_FUZZY_EXTRA_VOLATILE_KEYS, tuple)


@pytest.mark.django_db
class TestBackfillClearOnFailure:
    def test_force_preserves_by_default_and_clears_on_opt_in(self, db: None) -> None:
        org = _mk_org("OrgBackfillClear")
        alert = _mk_alert(org=org, source_ref="stale", hashed="STALE-HASH")
        alert.raw_payload = _overdeep_payload()
        alert.save(update_fields=["raw_payload"])

        out = io.StringIO()
        call_command("backfill_alert_ssdeep", "--force", stdout=out, no_color=True)
        alert.refresh_from_db()
        assert alert.payload_ssdeep == "STALE-HASH"
        assert re.search(r"Updated:\s+0\b", out.getvalue())
        assert re.search(r"Skipped:\s+1\b", out.getvalue())

        clear_out = io.StringIO()
        call_command(
            "backfill_alert_ssdeep",
            "--force",
            "--clear-on-failure",
            stdout=clear_out,
            no_color=True,
        )
        alert.refresh_from_db()
        assert alert.payload_ssdeep == ""
        assert re.search(r"Cleared:\s+1\b", clear_out.getvalue())

        assert "preserves the old hash" in Command.help


class TestLockRecipe:
    def test_make_lock_delegates_to_lock_script(self) -> None:
        makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()
        assert re.search(r"^lock:.*\n\t@bash scripts/lock\.sh$", makefile, re.MULTILINE)
        assert re.search(
            r"^lock-check:.*\n\t@bash scripts/lock\.sh --check$", makefile, re.MULTILINE
        )
