# Verification Report: Apply observable rules to existing unlinked alerts

## Plan
**Plan:** `docs/planning/PLAN-2026-11-09-source-observable-apply.md` (approved 2026-11-09)
**Verified:** 2026-11-09 14:25 UTC
**Status:** Pass

## Summary
- Total ACs: 9 (AC-SOA-1 through AC-SOA-9)
- Met: 9
- Not Met: 0
- Deviated: 0

## Criterion-by-Criterion

### AC-SOA-1: preview uses exactly `candidate_alerts`; linked/closed/dismissed alerts are excluded

**Status:** Met

**Evidence:**
- `ingest/observable_apply.py:99` — `preview_observable_rules` iterates `candidate_alerts(source)[:limit].iterator(chunk_size=200)`, the same candidate predicate used by the mapping re-map (P7, AC17).
- `tests/conformance/test_observable_apply.py:86-121` — `test_it_uses_the_same_candidate_predicate_and_writes_nothing` confirms the predicate excludes sibling feeds, linked alerts (with case), triaged/dismissed/imported alerts, and alerts from other feeds.

### AC-SOA-2: preview returns per-type `found/new/existing`, alerts-with-values, unknown types, truncation, and over-cap; writes nothing

**Status:** Met

**Evidence:**
- `ingest/observable_apply.py:136-143` — `preview_observable_rules` returns `ObservableApplyPreview` with `by_type` (per-type `found/new_links/existing_links`), `alerts_with_values`, `unknown_types`, `truncated_alerts`, and `over_cap`.
- `ingest/observable_apply.py:92` — `summary = summarise_candidates(source)` provides the full candidate count for the `over_cap` flag.
- `tests/conformance/test_observable_apply.py` — All preview tests (`test_it_uses_the_same_candidate_predicate_and_writes_nothing`, `test_an_existing_link_is_counted_as_already_present`, `test_two_raw_values_that_normalize_identically_are_one_new_and_one_existing`, `test_unknown_types_are_reported_and_never_created`, `test_truncation_is_reported`, `test_over_cap_reports_the_full_set_but_scans_only_the_limit`) verify the preview output and confirm **no DB writes** (alert counts, observable counts, audit counts unchanged pre/post).

### AC-SOA-3: confirm adds only missing links; a second run adds 0; no links removed when a rule is deleted

**Status:** Met

**Evidence:**
- `tests/conformance/test_source_observable_apply.py:174-189` — `test_a_second_confirm_adds_nothing` confirms a second `apply_confirmed` POST adds 0 new links and records `existing_links=1` in the audit.
- `tests/conformance/test_observable_apply.py:225-237` — `test_removing_a_rule_does_not_remove_evidence` confirms that applying an empty observable config (`observables: []`) after rules have been applied leaves existing `AlertObservable` links intact (`AlertObservable.objects.count() == 1`).

### AC-SOA-4: no field values, dates, status, assignment, case links, or field `mapping` records are changed by an observable-only apply

**Status:** Met

**Evidence:**
- `tests/conformance/test_observable_apply.py:239-251` — `test_it_never_rewrites_fields_or_mapping_records` confirms that after `apply_observable_rules`, all of `title`, `description`, `summary`, `severity`, `date`, `status`, `case`, and `ingestion_warnings` are unchanged.
- `tests/conformance/test_source_observable_apply.py:274-286` — `test_immediate_save_still_does_not_extract` confirms the immediate `Save observable rules` POST (no `intent`) creates 0 `AlertObservable` links and only records `ingestion_source.updated` audit.

### AC-SOA-5: first POST writes nothing (config, links, audit unchanged) and renders the modal

**Status:** Met

**Evidence:**
- `tests/conformance/test_source_observable_apply.py:106-129` — `test_it_writes_nothing_and_renders_the_modal` confirms the preview POST (`intent=apply`) returns 200, writes no config change, no observables, no audit rows, and the response body contains `id="observable-apply-modal"`, `data-autoopen="1"`, and "Save & extract".
- `test_it_writes_nothing_and_renders_the_modal` also verifies `source.mapping_config` and `source.updated_at` are unchanged.

### AC-SOA-6: confirm re-validates `pending_config`; invalid/over-cap writes nothing; success redirects with counts

**Status:** Met

**Evidence:**
- `ui/views/ingestion_sources.py:578-599` — `_apply_observable_rules` re-reads `pending_config` via `parse_json_config`; rejects invalid JSON with "Not valid JSON" and 200 re-render; rejects config changes to non-observable keys with "Only observable rules may change in this form." and 200 re-render.
- `ui/views/ingestion_sources.py:598-604` — Refuses over-cap with error message and redirect (no writes).
- `tests/conformance/test_source_observable_apply.py:191-206` — `test_a_tampered_config_that_changes_fields_is_refused` confirms tampered config is rejected, no writes.
- `tests/conformance/test_source_observable_apply.py:222-239` — `test_over_cap_writes_nothing` confirms over-cap refusal writes nothing (no config change, no observables, no audit).
- `tests/conformance/test_source_observable_apply.py:144-171` — `test_it_saves_extracts_and_audits_counts` confirms successful confirm redirects (302) with correct counts in audit.

### AC-SOA-7: cancel/non-admin/CSRF/invalid row behavior matches existing observable-save safety

**Status:** Met

**Evidence:**
- `tests/conformance/test_source_observable_apply.py:240-258` — `test_an_analyst_cannot_confirm` confirms a user with role `analyst` receiving 404 (uniform gate, same as mapping apply).
- `tests/conformance/test_source_observable_apply.py:260-272` — `test_a_missing_csrf_token_is_refused` confirms CSRF check gives 403 (same as other POST gates).
- `tests/conformance/test_source_observable_apply.py:274-286` — `test_immediate_save_still_does_not_extract` confirms the immediate `Save observable rules` path (no `intent`) creates 0 observables and only `ingestion_source.updated` audit.
- `tests/conformance/test_source_observable_apply.py:288-302` — `test_no_candidates_still_saves_the_rules` confirms zero candidates still saves the rules and records audit with `candidates: 0`.
- `tests/conformance/test_source_observable_apply_ui.py:128-136` — `test_no_candidates_says_the_rules_still_save` confirms the modal body shows "The rules will still be saved."

### AC-SOA-8: audit row `ingestion_source.observables_applied` carries counts only

**Status:** Met

**Evidence:**
- `identity/audit.py:368-414` — `record_ingestion_source_observables_applied` records `action="ingestion_source.observables_applied"` with details containing `changed` (keys), `alerts` (candidates/with_values/new_links/existing_links — counts only), and `types` (found/new/existing per-type counts — no raw values).
- `tests/conformance/test_source_observable_apply.py:159-171` — Verifies the audit row details match the expected structure with counts only (no payload values).
- `tests/conformance/test_source_observable_apply.py:186-188` — Second-confirm audit records `new_links: 0, existing_links: 1` and `changed: []`.

### AC-SOA-9: no regressions in existing source-mapping, remap, observable-editor, ingestion tests

**Status:** Met

**Evidence:**
- All 127 focused conformance tests pass (`tests/conformance/test_observable_apply.py`, `tests/conformance/test_source_observable_apply.py`, `tests/conformance/test_source_observable_apply_ui.py`, `tests/conformance/test_source_observable_editor.py`, `tests/conformance/test_settings_source_mapping.py`, `tests/conformance/test_mapping_remap.py`, `tests/conformance/test_ingest_alert_observables.py`).
- `make check-fast` passed (as noted by the user).
- mypy type checking passed (as noted by the user).

## Deviations

No unapproved deviations from the approved plan (`PLAN-2026-11-09-source-observable-apply.md`) were found. All implementation matches the planned design:

- **Trigger/UX — A:** Save stays immediate; `Apply to unlinked alerts…` added as second button. ✅
- **Scope:** same `candidate_alerts` as mapping — same feed, unlinked, `New`/`InProgress`. ✅
- **Rule removal:** keep existing alert observables; extraction is append-only. ✅
- **Preview detail:** exact per-type `found` / `new` / `already linked`, capped. ✅
- **Audit:** separate `ingestion_source.observables_applied` action. ✅
- **Field side effects:** none; use `sync_alert_observables`, not `remap_alerts`. ✅

## Blockers

No blocking issues. All 9 acceptance criteria are MET.

## Recommendations

No code changes required. The implementation conforms to all AC-SOA-1..AC-SOA-9 acceptance criteria as specified in the approved plan. All 127 focused tests pass, `make check-fast` passes, and mypy passes.
