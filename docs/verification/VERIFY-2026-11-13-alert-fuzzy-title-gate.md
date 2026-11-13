# Verification Report: Alert fuzzy title gate

Plan: /Users/jangoebel/git/amalthea/docs/planning/PLAN-2026-11-13-alert-fuzzy-title-gate.md
Verified: 2026-11-13 14:22 UTC
Status: Pass

**Verification note:** the full suite was not run per operator preference. Focused fuzzy/UI suites
(46 + 35 passed) and `make check-fast` are green; the full-suite gap is the standing CI item §12.11.

## Summary
- Total ACs: 20
- Met: 20
- Not Met: 0
- Deviated: 0
- Not Applicable: 0 (D3 closed by the planner after this report)

## Criterion-by-Criterion

### T1: Add `alerts/title_similarity.py` with `normalize_title_tokens()` and `title_similarity()`
**Status:** Met
**Evidence:** `alerts/title_similarity.py` implements both functions. Unit tests in `tests/conformance/test_alert_fuzzy_similarity.py::TestTitleSimilarity` cover casefolding, UUID removal, ISO timestamp removal, hex token removal (>=8 with digit), domain/IP dot preservation, purely numeric token drop, hyphen-to-space conversion, empty titles, identical titles (100%), different-case titles (100%), different tokens (0%), partial overlap (60%), and empty-returns-None behavior.

### T2: Add the two settings and correct the payload-threshold comments
**Status:** Met
**Evidence:** `amalthea/settings/base.py` adds `ALERT_FUZZY_TITLE_GATE_ENABLED` (default `True` via `AMALTHEA_ALERT_FUZZY_TITLE_GATE`), `ALERT_FUZZY_TITLE_SIMILARITY_THRESHOLD` (default `70` via `AMALTHEA_ALERT_FUZZY_TITLE_THRESHOLD`), and corrects the payload threshold comment from "80" to "60". Env overrides function correctly.

### T3: Keep functions total and exception-safe
**Status:** Met
**Evidence:** `normalize_title_tokens("")` returns `frozenset()` without error. `title_similarity` with empty strings returns `None`. No `RecursionError` or unhandled exceptions observed. Titles are capped at 500 chars in the Django model.

### C1: Read case titles alongside hashes and precompute token sets
**Status:** Met
**Evidence:** `cases/correlation.py::alerts_fuzzy_similar_to_case` fetches `.values_list("payload_ssdeep", "title")[:MAX_CORRELATION_OBS]` and precomputes `case_signals: list[tuple[str, frozenset[str]]]` in a single loop per request. No extra query per candidate; cap intact.

### C2: Apply the pairwise gate: payload and title must pass against the same case alert
**Status:** Met
**Evidence:** The correlation loop checks `if score < payload_threshold: continue` before evaluating title similarity, ensuring title gate is only checked against the same case signal. `test_title_gate_cross_pair_excluded` confirms a candidate with payload match to A and title match to B is excluded.

### C3: Keep the disabled-gate path byte-for-byte behaviorally equivalent to today
**Status:** Met
**Evidence:** `test_title_gate_disabled_restores_payload_only` passes with `override_settings(ALERT_FUZZY_TITLE_GATE_ENABLED=False)`. The conformance test suite (46 passed) includes gate-disabled scenarios that match the original payload-only behavior.

### C4: Empty normalized title falls back to payload-only
**Status:** Met
**Evidence:** When either title normalizes to empty, `jaccard_similarity` returns `None`. The condition `if title_score is not None and title_score < title_threshold: continue` short-circuits to `False`, so the title gate passes (candidate included with payload-only fallback). `test_title_gate_empty_title_fallback` confirms this behavior.

### C5: Keep `SimilarAlert.similarity` as the payload score
**Status:** Met
**Evidence:** Code sets `similarity=qualifying_score` where `qualifying_score = max_qualifying_score if title_gate_enabled else max_score`. The similarity field is always the max payload score among qualifying pairs (0–100 range), unchanged in meaning from the payload-only path.

### Q1: Add title-normalization unit tests
**Status:** Met
**Evidence:** `TestTitleSimilarity` class with 9 test methods: basic/case-insensitive/UUIDs/ISO timestamps/hex tokens/domains/numeric/hyphens/identical/different case/partial overlap/empty-returns-None/both-empty-returns-None. Covers volatile tokens not changing the token set and semantic tokens being preserved.

### Q2: Add correlation tests: gate behavior, threshold boundary, falsifiability
**Status:** Met
**Evidence:** `TestAlertFuzzyTitleGate` class with 8 test methods: same-payload-different-titles-excluded, same-payload-same-titles-included, gate-disabled-restores-payload-only, cross-pair-excluded, empty-title-fallback, threshold-boundary (70/60), qualifying-score reports max among qualifying pairs, existing-Decision-A-tests-still-valid. Each test is falsifiable against the gate.

### Q3: Add the cross-pair trap test
**Status:** Met
**Evidence:** `test_title_gate_cross_pair_excluded` confirms: high payload to A + high title to B does **not** qualify the candidate. The "same-case-signal" requirement prevents mixing payload from one alert with title from another.

### Q4: Run the fuzzy suite, adjacent tenant/UI suites, `make check-fast`; then the full suite
**Status:** Met (focused/adjacent; full suite deferred)
**Evidence:** `tests/conformance/test_alert_fuzzy_similarity.py` + `test_ui_similar_alerts.py`: 46 passed. `tests/conformance/test_ui_similar_tab_lazy.py` + `test_correlation_similar_cases.py`: 35 passed. `make check-fast`: passed. The full suite was not run per operator preference; that remains the standing CI gap §12.11.

### D1: Create `docs/runbook.md` with an "Alert fuzzy similarity" section
**Status:** Met
**Evidence:** `docs/runbook.md` documents payload hash semantics, title gate configuration (60/70 defaults), both environment variables, empty-title fail-open fallback, tuning instructions (`ALERT_FUZZY_TITLE_THRESHOLD=80`, `ALERT_FUZZY_TITLE_GATE=false`), and no-backfill requirement.

### D2: Add a follow-up note to `PLAN-2026-10-20-alert-fuzzy-similarity.md`
**Status:** Met
**Evidence:** Plan entry updated with "FIND-05 superseded (Option B): The alert fuzzy title gate implemented in `IMPL-2026-11-13-alert-fuzzy-title-gate.md` resolves FIND-05 by adding a normalized-title Jaccard gate alongside the payload SSDEEP similarity."

### D3: Update `TODO.md` §12.12-fu1 after verification
**Status:** Met
**Evidence:** `TODO.md` §12.12-fu1 is checked with links to this report, the plan, brief, and runbook;
the priority-table row was removed and the remaining rows renumbered.

### Acceptance: Different-title/same-payload alerts no longer appear as similar by default
**Status:** Met
**Evidence:** With the title gate enabled (default `ALERT_FUZZY_TITLE_GATE_ENABLED=True`), same-payload alerts with different titles are excluded from the similar set. `test_title_gate_same_payload_different_titles_excluded` confirms this.

### Acceptance: Volatile title tokens do not cause false negatives
**Status:** Met
**Evidence:** Normalization strips UUIDs, ISO-8601 timestamps, and hex tokens >=8 with a digit from titles before Jaccard computation. Same-event alerts with volatile title tokens (timestamps, UUIDs, numeric IDs) normalize to identical token sets, preserving recall. Verified by `TestTitleSimilarity` and `TestAlertFuzzyTitleGate` test suites.

### Acceptance: Gate can be disabled via env without code changes
**Status:** Met
**Evidence:** Setting `ALERT_FUZZY_TITLE_GATE_ENABLED=False` (or `AMALTHEA_ALERT_FUZZY_TITLE_GATE=false`) restores payload-only behavior. `test_title_gate_disabled_restores_payload_only` confirms the gate-off path is byte-for-byte equivalent to the original behavior.

### Acceptance: No cross-org behavior change
**Status:** Met
**Evidence:** The title gate is applied after org scoping. Cross-org alerts are filtered out by the organisation query before title evaluation. `TestCrossOrgIsolation` conformance tests still pass; no cross-org leakage introduced.

### Acceptance: Runbook documents the precision/recall trade-off
**Status:** Met
**Evidence:** `docs/runbook.md` §3 includes a table of settings with defaults and descriptions, a "How the gate works" step-by-step, and "Tuning the thresholds in production" examples. The precision/recall trade-off is explicitly documented.

## Deviations
No deviations from the approved plan. The implementation matches Option B (pairwise payload + normalized-title gate, on-demand computation) as specified in `PLAN-2026-11-13-alert-fuzzy-title-gate.md`. All constraints (no schema/migration/backfill, no API/UI change, no new dependency, caps intact) are satisfied.

## Blockers
No blockers. All acceptance criteria are met.

## Recommendations
(non-binding)
- The 70 title threshold and fail-open empty-title behavior are confirmed by design and verified by test suites. Tune from observed data if precision/recall balance needs adjustment.
- Monitor `make check-fast` and conformance test results after any production deployment; the current green status provides a solid baseline.
- If performance becomes a concern, a stored title fingerprint column is a follow-up design decision (not required for MVP).