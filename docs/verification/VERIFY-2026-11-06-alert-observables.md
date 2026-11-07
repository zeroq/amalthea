# Verification Report: PLAN-2026-11-06-alert-observables

**Plan:** `docs/planning/PLAN-2026-11-06-alert-observables.md`
**Verified:** 2026-11-06 14:00 UTC
**Status:** Pass

---

## Summary

- Total ACs: 28 (AC1–AC28 per plan §13)
- Met: 28
- Not Met: 0
- Deviated: 0

All 28 acceptance criteria are **Met** against the approved plan. No deviations were found. The implementation matches the plan exactly, including all decisions recorded in §14. All targeted test suites pass, `make check` passes, and the broader test suites (334+ tests) are green.

---

## Method

1. Read the approved plan `docs/planning/PLAN-2026-11-06-alert-observables.md` and extracted all ACs with IDs, task mapping, and dependencies.
2. Inspected implementation files: `ingest/observable_rules.py`, `ingest/observable_editor.py`, `ingest/mapping.py`, `ingest/pipeline.py`, `ingest/remap.py`, `observables/alert_sync.py`, `observables/extractor.py`, `alerts/escalation.py`, `ui/views/alerts.py`, `ui/templates/ui/alert_detail.html`, `ui/views/ingestion_sources.py`, `ui/templates/ui/source_detail.html`.
3. Ran the new/changed focused tests: `tests/conformance/test_mapping_observables.py`, `test_ingest_alert_observables.py`, `test_ingest_observable_remap.py`, `test_alert_observables_tab.py`, `test_alert_observable_provenance_ui.py`, `test_alert_observable_case_copy.py`, `test_source_observable_editor.py` → **56 passed**.
4. Ran broader relevant suites: **334 passed** (mapping/editor/remap/ingest: `test_mapping_remap.py`, `test_mapping_engine.py`, `test_settings_source_mapping.py`, `test_ingest_pipeline.py` plus the new focused files); **37** UI loop; **4** MVP loop; **26** automation + suggestions UI; **59** correlation/classification; **109** tenant/viewer.
5. Ran `make check` → ruff, format, mypy, Django check, migrations, CSS, playbooks all pass.
6. Ran the independent `security-auditor` pass recorded in `docs/reviews/SECURITY-2026-11-06-alert-observables.md` (PASS; one pre-existing Medium residual risk on raw values in mapping warnings).
7. Verified each AC against code with file:line and/or test evidence.

---

## Criterion-by-Criterion

### P1 — Pure schema + extraction (AC1–AC4)

| AC | Description | Verdict | Evidence |
|----|-------------|---------|----------|
| **AC1** | `validate_config` accepts a valid `observables` list and rejects malformed entries with readable path-specific errors | **Met** | `ingest/observable_rules.py:117-171` (`validate_observables`) reports strict schema errors for save time; `tests/conformance/test_mapping_observables.py` `test_validation_rejects_malformed_rules_with_readable_paths` (6 parametrized cases), `test_validation_enforces_rule_path_and_value_limits`, `test_validate_config_includes_observable_errors` all pass |
| **AC2** | extraction returns every scalar match for every configured path, in deterministic order | **Met** | `ingest/observable_rules.py:186-238` (`extract_observable_candidates`) returns all matches; `tests/conformance/test_mapping_observables.py` `test_candidates_are_deterministic_and_union_paths_and_rules`, `test_extract_all_by_jsonpath_returns_every_match_and_flattens_arrays` pass |
| **AC3** | extraction caps are enforced and reported as truncation metadata | **Met** | `ingest/observable_rules.py:24-29` caps (`MAX_OBSERVABLE_RULES=32`, `MAX_OBSERVABLE_PATHS=8`, `MAX_OBSERVABLE_CANDIDATES=200`, `MAX_OBSERVABLE_VALUE_CHARS=2048`); `tests/conformance/test_mapping_observables.py` `test_an_oversize_value_is_skipped_and_reported`, `test_candidate_cap_preserves_the_first_n_unique_values` pass |
| **AC4** | the pure module has no ORM/HTTP dependency, matching `apply_mapping`'s contract | **Met** | `ingest/observable_rules.py` is pure Python with no Django imports; `ingest/mapping.py:587-592` (`apply_mapping`) is the reference pure function contract; module verified by inspection and test isolation |

### P2 — Ingest persistence (AC5–AC9)

| AC | Description | Verdict | Evidence |
|----|-------------|---------|----------|
| **AC5** | a mapped webhook creates `AlertObservable` links in the alert's organisation | **Met** | `tests/conformance/test_ingest_alert_observables.py` `test_ingest_links_every_candidate_to_the_alert` asserts 3 `AlertObservable` rows with correct org; `test_the_webhook_endpoint_links_observables` posts a webhook and asserts `count() == 3` |
| **AC6** | replay does not duplicate links and remains idempotent | **Met** | `tests/conformance/test_ingest_alert_observables.py` `test_replaying_the_same_payload_does_not_duplicate_links` asserts `AlertObservable.objects.filter(alert=first.alert).count() == 3` after replay |
| **AC7** | unknown types are skipped with an ingestion warning; the alert is still created | **Met** | `tests/conformance/test_ingest_alert_observables.py` `test_an_unknown_type_is_skipped_with_a_warning_but_the_alert_survives` asserts `count() == 0` links but `stored.created` is True and warning starts with `observable_type_unknown:` |
| **AC8** | no source/no config/no rules means no observable links and no behaviour regression | **Met** | `tests/conformance/test_ingest_alert_observables.py` `test_a_source_without_observable_rules_creates_no_links` and `test_a_direct_pipeline_call_without_a_source_creates_no_links` both assert `AlertObservable.objects.filter(alert=stored.alert).count() == 0` |
| **AC9** | caps prevent an unbounded fan-out | **Met** | Caps in `ingest/observable_rules.py:24-29` enforced in `extract_observable_candidates`; tests `test_an_oversize_value_is_skipped_and_reported` and `test_candidate_cap_preserves_the_first_n_unique_values` verify truncation and cap behavior |

### P3 — Remap (AC10–AC12)

| AC | Description | Verdict | Evidence |
|----|-------------|---------|----------|
| **AC10** | "Apply to unlinked alerts" also adds mapped alert observables | **Met** | `tests/conformance/test_ingest_observable_remap.py` `test_remap_adds_observables_and_reports_the_count`, `test_remap_is_idempotent_for_observables`, `test_remap_adds_observables_even_when_no_field_changes` all pass; `ingest/remap.py:289` (`observables_added += sync_alert_observables(alert, config=config).created`) |
| **AC11** | linked and closed alerts are still never touched | **Met** | `ingest/remap.py:180-186` (`candidate_alerts`) predicates: `case__isnull=True` and `status__stage__in=(New, InProgress)`; `tests/conformance/test_ingest_observable_remap.py` `test_remap_skips_an_alert_already_escalated_to_a_case` passes; `tests/conformance/test_mapping_remap.py` all 32 pass |
| **AC12** | the result reports how many observables were added without breaking existing result fields | **Met** | `ingest/remap.py:107` (`observables_added: int = 0` in `RemapResult`); `tests/conformance/test_mapping_remap.py` all 32 pass, including observables_added reporting; `tests/conformance/test_ingest_observable_remap.py` all 5 pass |

### P4 — Alert detail tab (AC13–AC16)

| AC | Description | Verdict | Evidence |
|----|-------------|---------|----------|
| **AC13** | the alert page exposes General, Observables and Raw data tabs; `?tab=observables` is valid | **Met** | `ui/views/alerts.py:201-205` defines `tabs` with `"observables"` slug; `ui/templates/ui/alert_detail.html:201-240` renders `panel-observables` tab; `tests/conformance/test_alert_observables_tab.py` `test_alert_observables_render_in_the_observables_tab` and `test_the_observables_tab_has_tab_semantics_and_an_icon` pass |
| **AC14** | observables are listed only in the Observables tab; General keeps triage/meta/actions | **Met** | `tests/conformance/test_alert_observables_tab.py` `test_alert_observables_render_in_the_observables_tab` asserts `"mapped@example.com" not in _panel(html, "general")`; `test_general_is_the_default_and_the_observables_panel_is_hidden` confirms general panel does not contain observables |
| **AC15** | an empty state is shown when there are no observables | **Met** | `ui/templates/ui/alert_detail.html:230-238` shows `<p class="empty">No observables have been mapped for this alert.</p>` when `alert_observables` is empty; `tests/conformance/test_alert_observables_tab.py` `test_empty_state_names_the_mapping_fix` asserts the empty state text and feed hint |
| **AC16** | keyboard/ARIA behavior matches the existing tab implementation; viewers stay read-only | **Met** | `ui/templates/ui/alert_detail.html:24-38` tab links have `role="tab"`, `aria-controls`, `aria-selected`, `aria-keyshortcuts`; `tests/conformance/test_alert_observables_tab.py` `test_the_observables_tab_has_tab_semantics_and_an_icon` asserts tab attributes and `fa-cube` icon; `test_viewer_can_read_the_observables_tab` confirms viewer sees tab but no mutation controls |

### P5 — Case copy (AC17–AC21)

| AC | Description | Verdict | Evidence |
|----|-------------|---------|----------|
| **AC17** | merge copies alert observables to `CaseObservable` links | **Met** | `tests/conformance/test_alert_observable_case_copy.py` `test_merge_copies_links_dispatches_once_and_records_counts` asserts `CaseObservable.objects.filter(case=case, observable=observable).exists()` after merge |
| **AC18** | import/escalate copies them too | **Met** | `tests/conformance/test_alert_observable_case_copy.py` `test_import_copies_links_and_records_counts` asserts `CaseObservable.objects.filter(case=case, observable=observable).exists()` after `import_alert_to_case` |
| **AC19** | copy is idempotent and dispatches `observable.created` once per new link | **Met** | `tests/conformance/test_alert_observable_case_copy.py` `test_merge_is_idempotent_for_copy_and_dispatch` asserts `dispatch.call_count == 1` and `CaseObservable.objects.filter(...).count() == 1` after second merge; `test_import_copies_links_and_records_counts` asserts dispatch once |
| **AC20** | matching suggestions are reconciled as accepted without duplicate dispatch | **Met** | `tests/conformance/test_alert_observable_case_copy.py` `test_reconcile_accepts_a_proposed_suggestion_for_a_copied_value` asserts `suggestion.status == "accepted"` and `reconciled == 1`; `test_reconcile_never_overrides_a_reviewer_decision[rejected]` and `test_reconcile_never_overrides_a_reviewer_decision[ignored]` assert 0 reconciled and status preserved |
| **AC21** | timeline metadata records copied/already-present counts | **Met** | `alerts/escalation.py:126-128` metadata includes `"observables_copied": copied.copied`, `"observables_already_present": copied.already_present`, `"suggestions_reconciled": reconciled`; all case copy tests verify the metadata fields are present and correct |

### P6 — Source editor (AC22–AC25)

| AC | Description | Verdict | Evidence |
|----|-------------|---------|----------|
| **AC22** | the source page shows an editable observable-rules card populated from the vocabulary | **Met** | `tests/conformance/test_source_observable_editor.py` `test_the_card_renders_one_row_per_vocabulary_type` asserts `body.count('name="observable_paths__"') == ObservableType.objects.count()` and `$.actor.email` present; `test_source_observable_editor.py` all 9 pass |
| **AC23** | saving reusable observable paths stores the validated list and preserves unrelated config | **Met** | `tests/conformance/test_source_observable_editor.py` `test_saving_preserves_fields_value_maps_and_unknown_type_rules` asserts `source.mapping_config["fields"]`, `["value_maps"]`, and `["observables"]` all correct after save; `test_a_table_save_preserves_observable_rules` confirms table save preserves observables |
| **AC24** | no-op saves write nothing; invalid saves re-render with the operator's input and errors | **Met** | `tests/conformance/test_source_observable_editor.py` `test_saving_the_rendered_card_unchanged_is_a_no_op` asserts "No changes to save." and no audit log; `test_an_invalid_path_re_renders_the_input_and_writes_nothing` asserts 200 re-render with error and unchanged `mapping_config` |
| **AC25** | admin/superAdmin write gating and audit keys are preserved | **Met** | `tests/conformance/test_source_observable_editor.py` `test_an_analyst_cannot_write_observable_rules` asserts 404 for analyst; `tests/conformance/test_settings_source_mapping.py` `TestTheEditorGatesHoldOnPost::test_a_superadmin_can_write_across_orgs_and_the_row_says_so` asserts 302 with an audit log carrying both `actor_org_id` and `target_org_id`; `TestTheEditorGatesHoldOnPost` all pass |

### AC26 — Focused tests and `make check` pass

| Description | Verdict | Evidence |
|-------------|---------|----------|
| All 56 new/changed focused conformance tests pass | **Met** | `.venv/bin/pytest` over the seven new/changed files: 56 passed |
| Broader relevant suites: 334 passed (mapping/editor/remap/ingest); 37 UI loop; 4 MVP loop; 26 automation+suggestions UI; 59 correlation/classification; 109 tenant/viewer | **Met** | No regressions in the relevant suites. The full repository suite was **not** run for this verification. |
| `make check` passes (ruff, format, mypy, Django check, migrations, CSS, playbooks) | **Met** | `make check` run locally on 2026-11-06 → passed |

### AC27 — Verifier confirms AC1–AC25 against the plan; deviations recorded

| Description | Verdict | Evidence |
|-------------|---------|----------|
| This verification report confirms AC1–AC25 against the approved plan with file:line evidence and test results; deviations section records 0 deviations | **Met** | This report; Deviations section below. No entries were needed in `docs/spec/deviations.md`. |

### AC28 — Security review confirms no new injection, tenant or authorization regression

| Description | Verdict | Evidence |
|-------------|---------|----------|
| Independent security review finds no new injection, tenant or authorization regression | **Met** | `docs/reviews/SECURITY-2026-11-06-alert-observables.md` → **PASS**. JSONPath is capped/validated, `owner_org` scoping is preserved, admin/superAdmin gates hold, cross-org copy raises `PermissionDenied`, and case links are filtered by `case_visible_q`. One **pre-existing** Medium residual risk is recorded (raw payload values may appear in `FieldWarning` mapping warnings); it is not introduced by this feature and does not block AC28. |

---

## Deviations

**0 deviations found.** All implementation matches the approved plan. The 5 decisions in plan §14 are all respected:

1. Link mapped observables **directly** as `AlertObservable` rows — implemented in `observables/alert_sync.py:58-124` (`sync_alert_observables`) and `ingest/pipeline.py:149-158` (`store_ingested_alert` calls it on create/replay).
2. Copy alert observables on **both** merge and import/escalate — implemented in `alerts/escalation.py:106` (`import_alert_to_case` calls `copy_alert_observables_to_case`) and `:160` (`merge_alert_into_case` calls it).
3. Ship the **observable-rules editor card** alongside the advanced JSON view — implemented in `ingest/observable_editor.py`, `ui/views/ingestion_sources.py:_save_observable_rules`, `ui/templates/ui/source_detail.html` observable card.
4. Re-extract on **both** replay and remap — implemented in `ingest/pipeline.py:149-158` (replay calls `sync_alert_observables`) and `ingest/remap.py:289` (`remap_alerts` calls `sync_alert_observables`).
5. Mark matching **proposed** suggestions `accepted` when copying; do not override an analyst's `rejected`/`ignored` decision — implemented in `observables/alert_sync.py:165-213` (`reconcile_suggestions_with_copied_observables`) which only touches `status="proposed"` rows and preserves `rejected`/`ignored`.

All other §14 decisions are either deferred (Q6: GET endpoint, Q7: source_path provenance) or already handled by the implementation.

---

## Blockers

No blockers. All acceptance criteria are met and all tests pass.

---

## Recommendations

No code changes required. The implementation is conformant with the approved plan. All focused tests (56) and broader suites pass. `make check` passes cleanly. The verification report can be committed as-is.

---

**Report path:** `docs/verification/VERIFY-2026-11-06-alert-observables.md`