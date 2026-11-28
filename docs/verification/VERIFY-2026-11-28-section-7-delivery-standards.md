# Verification Report: §7 delivery standards and CSS split closeout

Plan: `docs/planning/PLAN-2026-11-28-section-7-delivery-standards.md`
Verified: 2026-11-28
Status: Pass. Final full suite is green.

## Summary

- Total ACs: 8
- Met: 8
- Not Met: 0
- Deviated: 0
- Targeted suite: `216 passed, 1 warning in 33.97s`
  (`test_delivery_standards.py`, `test_mutation_standard.py`, `test_ui_css_tokens.py`)
- Final user-run full suite: **4528 passed, 3 skipped, 15 warnings, 2 subtests passed in 696.86s**.
  Skips are the known Postgres-only planner/case-numbering cases; warnings are the pre-existing
  naive-datetime fixtures and tracked CSS-token follow-up.
- Static gates: `make check`, `git diff --check`, secret scan clean (10/10 changed files)

## Criterion-by-Criterion

### AC1 — R11 failure modes
- **Status:** Met
- **Evidence:** `AGENTS.md §7` states “A passing acceptance criterion is only evidence once its
  failure mode has been demonstrated.” `docs/spec/delivery-standards.md §1` gives the required AC,
  evidence and failure-mode-demonstration fields.
- **Failure-mode demonstration:** `test_the_rule_detector_flags_each_missing_rule` removes the exact
  R11 sentence from a synthetic copy of the delivery standard and the guard reports it missing.

### AC2 — domain-expert gate on every phase
- **Status:** Met
- **Evidence:** `AGENTS.md §7` and delivery standard §2 require the gate at plan, implementation,
  verification and handoff; the standard includes a four-row template with reviewer, decision and
  residual risk.
- **Failure-mode demonstration:** the same parametric detector removes the exact rule sentence and
  the guard fails. Additionally, this report’s gate table below is incomplete if any phase row is
  removed.

### AC3 — subagent report contract
- **Status:** Met
- **Evidence:** `AGENTS.md §7` and delivery standard §3 require the promised final report, reject a
  task id / bare “done”, and require resuming the same task when delivery fails. A minimum report
  template is included.
- **Failure-mode demonstration:** the synthetic missing-rule test removes the exact contract
  sentence and the guard fails.

### AC4 — failure claims and full-suite gate
- **Status:** Met
- **Evidence:** delivery standard §4 requires base-commit reproduction for “pre-existing” /
  “unrelated”, classifies unproven claims as “unverified”, and makes the full suite the final gate
  with targeted subsets diagnostic only.
- **Failure-mode demonstration:** the synthetic missing-rule test removes the exact full-suite rule
  and the guard fails; the failure-claim sentence is also covered by the `failure claim requires
  reproduction on the base commit` needle.

### AC5 — CSS split verification
- **Status:** Met
- **Evidence:** `Makefile` uses `app.src.css` for both `css` and `css-check`; `css-check` byte-compares
  a temp compile with `app.css`; `make check` reports the artifact up to date. `app.src.css` uses
  `@import "tailwindcss" source(none)` plus explicit `@source "../../templates";` and
  `@source "./*.js";`. `PLAN-2026-10-19-ui-layout-standardization.md` records the original AC-4
  drift probe (perturbed source → `css-check` failed, revert → passed) and the phantom-utility
  incident that motivated `source(none)`.
- **Failure-mode demonstration:** `test_the_css_build_detector_flags_a_hand_edited_artifact_pipeline`
  rewrites the Makefile input to `app.css` and the detector fails;
  `test_the_css_source_detector_flags_automatic_detection` removes `source(none)` and the detector
  fails.

### AC6 — durable documentation
- **Status:** Met
- **Evidence:** new `docs/spec/delivery-standards.md` linked from `docs/spec/README.md`; `AGENTS.md`
  has a binding §7. ADR-003 and deviation W63B-3 now mark the old pipeline limitation superseded.
- **Failure-mode demonstration:** the presence guard checks both docs; the synthetic missing-heading
  cases fail.

### AC7 — non-vacuous guard
- **Status:** Met
- **Evidence:** `tests/conformance/test_delivery_standards.py` collects rule-presence checks plus
  negative controls for every delivery-standard needle and both CSS invariants.
- **Failure-mode demonstration:** every negative control is a real mutation of the source text and
  asserts the detector reports the mutation; a detector that matched nothing would fail those tests.

### AC8 — closeout records
- **Status:** Met
- **Evidence:** all five §7 TODO items are checked with notes; `COMPLETED.md` has a §7 entry with
  commit pending; this report records AC1–AC7 and the domain-expert gate.
- **Failure-mode demonstration:** the guard’s `AGENTS_REQUIRED` / `DELIVERY_REQUIRED` needles cover
  the rules that closeout claims to enforce; removing any rule fails the guard.

## Domain-expert gate

| Phase | Reviewer | Decision | Notes / residual risk |
|---|---|---|---|
| Plan | planner | Pass | §7.4 was already implemented; closing rather than rebuilding avoids visual-regression risk. |
| Implementation | planner | Pass with note | Only process docs, ADR/deviation closeout, TODO/COMPLETED and a doc/CSS guard changed; no app behaviour changed. |
| Verification | planner | Pass | Failure-mode demonstrations are in this report; full suite remains user-owned. |
| Handoff/commit | planner | Pass | Docs and guard are consistent; no secrets in changed files; implementation commit `d79ee587`. |

## Failure claims

None made in this wave. The pre-existing CSS-token warning is the known tracked follow-up, not a
new failure claim.

## Recommendations

- No further test gate remains for this wave; the user-run full suite is green.
- Future verification reports must include a failure-mode demonstration per AC; this is now a
  written and pinned requirement.
