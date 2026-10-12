# Verification Report: UI completeness wave — Batch P2

Plan: `docs/planning/PLAN-2026-10-11-ui-completeness.md` §8 P2 (C1–C3) + §13c  
Verified: 2026-10-12 00:00 UTC  
Verifier: `verifier` (read-only)  
Status: **Pass** (all ACs met)

## Scope

- Approved plan: `docs/planning/PLAN-2026-10-11-ui-completeness.md` §8 P2 + §13c
- Relevant spec/deviations: `docs/spec/deviations.md` "Wave 6.16-P2"
- AGENTS.md: §2/§6
- Changes inspected (uncommitted): `ui/views.py` (alert_status, case_edit, observable_edit, observable_delete), `cases/update.py` (new shared applier), `ui/templates/ui/case_edit.html` (new), `ui/templates/ui/observable_detail.html`, `ui/templates/ui/alert_detail.html`, routes in `ui/urls.py`, `identity/scoping.py` (resolve_alert/resolve_observable)

## Method

1. Read plan §8 P2 + §13c, AGENTS.md §2/§6, deviations "Wave 6.16-P2".
2. Inspected actual implementation in `ui/views.py`, `cases/update.py`, templates (`case_edit.html`, `observable_detail.html`, `alert_detail.html`), `ui/urls.py`, `identity/scoping.py`.
3. Evaluated each plan item (C1–C3) against ACs.
4. Ran the specified conformance/quality commands and cited outputs.
5. Wrote this persistent report to `docs/verification/VERIFY-2026-10-11-p2-ui-completeness.md`.

## Criterion-by-Criterion

| Criterion | Plan requirement (AC) | Status | Evidence (file:line + notes/test name) |
|---|---|---|---|
| **C1 — Alert triage** | Applying only an existing `AlertStatus.value`; `AuditLog(action="alert.status_changed")`; case-ledger entry when alert has a case; uniform 404 cross-org; POST-only. | **Met** | `ui/views.py:1301-1362` (`alert_status`, POST-only via `@require_POST`); validates against existing `AlertStatus` vocabulary (stage in ALERT_STAGES, exclude Imported) matching on `value` only — never mints (W616P2-1). Writes `AuditLog(action="alert.status_changed", actor/org/target_org/target_id=str, IP)`. If `alert.case_id`, appends timeline event kind `status-changed` with metadata `{"alert":..., "to": value}`. Uniform 404 on miss via `resolve_alert`. Cross-org: `test_alert_status_is_org_scoped_uniform_404`. POST-only: `test_alert_status_requires_post` (405). Audit+timeline: `test_alert_status_post_changes_status_and_audits`, `test_alert_status_post_writes_case_timeline`. Template posts value (not pk), no Imported: `ui/templates/ui/alert_detail.html:62-71`; `test_alert_detail_renders_triage_control` asserts vocabulary values and absence of pk/Imported. |
| **C2 — Case edit** | Parity between `ui-case-edit` POST and API PATCH (same persisted state + same timeline events); bounded `CaseStatus`; CSRF; uniform 404 GET+POST; flag clearing. | **Met** | `ui/views.py:1355-1469`, `ui/urls.py:32`; shared applier `cases/update.py:144` (`apply_case_update`) used by both surfaces (C2). GET pre-fills and renders `ui/templates/ui/case_edit.html`. POST builds payload; bounded `status` against existing `CaseStatus` (no mint); flag cleared when absent; assignee login or None when cleared; tags comma-split. Calls `apply_case_update` in transaction; `CaseUpdateError` re-renders with message. Writes `AuditLog(action="case.updated", target_type="Case", target_id=str(case.id))`. Redirects to `ui-case-detail`. Uniform 404 GET+POST cross-org: `test_case_edit_is_org_scoped_uniform_404`. CSRF: `test_case_edit_post_requires_csrf`. Bounded status/no mint: `test_case_edit_status_is_bounded`. Flag clearing: `test_case_edit_flag_can_be_cleared`. Timeline parity: `test_case_edit_status_change_writes_timeline`. Updates persist: `test_case_edit_post_applies_updates`. Preselection (int-vs-str): `test_case_edit_get_prefills_and_preselects`. Edit link present: `test_case_detail_has_edit_link`. |
| **C3 — Observable edit/delete** | `data` immutable; camelCase applier fields apply; malformed JSON / unknown `dataType` re-render without persisting; `force` required on BOTH edit and delete of multi-case observable; delete audit stores real pk. | **Met** | `ui/views.py:1497-1704`, `ui/urls.py:112-117`. Edit GET shows `data` read-only (template `#f-observable-data` readonly/disabled), passes `dataType` options, TLP 0-3 only (W616P2-5), enrichment JSON serialized. POST uses camelCase (`dataType`, `message`, `tlp`, `pap`, `ioc`, `sighted`, `ignoreSimilarity`); does **not** include `data` (immutable). Checks `force` via `_observable_cross_case_guard` (shared with API) → re-render without force on multi-case. Enrichment: parses JSON; `json.JSONDecodeError` or non-dict → re-render with message. Calls `_set_observable_fields`; on validation returns re-render. Applies enrichment if touched, saves touched fields+updated_at. Writes `AuditLog(action="observable.updated", target_id=str(observable.id))`. Delete: checks force+guard with `action="deletion"` → redirects to detail on refusal; **captures `observable_pk = str(observable.id)` and `target_org` before `delete()`** (fix for "None"); atomic delete + `AuditLog(action="observable.deleted", target_id=observable_pk)`. Form contract: `name="dataType"`, `name="ignoreSimilarity"`, `name="enrichment_data"`; no hidden `force` (checkbox required only when multi-case). Tests: immutable data (`test_observable_edit_post_updates_and_ignores_data`), bad JSON re-renders (`test_observable_edit_post_bad_json_rerenders`), unknown type rejected/not persisted (`test_observable_edit_post_unknown_type_is_reported`), multi-case edit needs force (`test_observable_edit_shared_requires_force`), delete single-case logs real pk (`test_observable_delete_single_case`), delete shared refused without force → redirects to detail (`test_observable_delete_shared_without_force_is_refused`), delete shared with force deletes (`test_observable_delete_shared_with_force_deletes`), org-scoped uniform 404 GET/POST (`test_observable_mutations_are_org_scoped_uniform_404`), affordances + checkbox inside form (`test_observable_detail_has_edit_and_delete_affordances`). |

## Robustness (verified)

- `resolve_alert`/`resolve_observable` catch `ValidationError`, `ValueError`, `TypeError` in addition to `DoesNotExist` → uniform 404 for non-UUID ids (W616P2-6). `identity/scoping.py:20,144-189`.
- Case edit validates `status` exists in `CaseStatus` vocabulary before calling shared applier (prevents UI minting; API keeps tolerant resolver by design) — W616P2-1/W616P2-2.
- Observable form lists TLP `0..3` only (W616P2-5) — avoids API validation error from `TLP_CHOICES` including 4/Unknown.

## Command log

```bash
.venv/bin/python -m pytest tests/conformance/test_ui_p2_wiring.py -q
# → 24 passed in 5.38s

.venv/bin/python -m pytest tests/conformance/test_tenant_scoping.py -q
# → 92 passed in 14.58s

.venv/bin/ruff check
# → All checks passed!

.venv/bin/mypy
# → Success: no issues found in 105 source files
```

## Verdict

**Pass.** All three criteria (C1, C2, C3) are **Met** with concrete evidence and passing conformance tests. No partials/not-met. Documented deviations W616P2-1–W616P2-6 are as recorded in `docs/spec/deviations.md` ("Wave 6.16-P2"); they are implementation/robustness choices consistent with plan constraints. No new unrecorded deviations.

## New deviation

None.

---

# Re-verification 2026-10-11 — post security remediation (M1 / L1 / L2)

Plan: `docs/planning/PLAN-2026-10-11-ui-completeness.md` §8 P2 (C1–C3) + §13c  
Re-verified: 2026-10-12 06:14 UTC  
Verifier: `verifier` (read-only; this report is the only artefact written)  
Baseline: uncommitted working tree vs `cc9e569` (`cases/update.py` and
`tests/conformance/test_ui_p2_wiring.py` are untracked)  
Trigger: remediation of `docs/reviews/REVIEW-2026-10-11-p2-ui-mutations.md`
(M1 cross-tenant assignee, L1 out-of-range enum 500, L2 audit-atomicity).

## Scope of the re-check

Files that changed since the prior verification: `ui/views.py`, `cases/views.py`,
`cases/update.py` (new/untracked), `tests/conformance/test_ui_p2_wiring.py` (new/untracked).
The four product views and their templates were re-read end-to-end; the shared applier and
both its callers (API `_update_case`/`_bulk`, UI `case_edit`) were traced for parity.

## Per-item verdict

| Item | Prior verdict | Re-verified verdict | What backs it now |
|---|---|---|---|
| **C1 — Alert triage** | Met | **Met (unchanged)** | `ui/views.py:1301-1350`: `@require_POST`, bounded to existing `AlertStatus` vocabulary (stage in `ALERT_STAGES`, excludes `Imported`), writes `AuditLog(action="alert.status_changed")`, appends case timeline when `alert.case_id`, uniform 404 via `resolve_alert`. Paths/tests unchanged; `ui-views` route covered by `test_tenant_scoping.py`. |
| **C2 — Case edit** | Met | **Met (unchanged; hardened)** | `ui/views.py:1356-1477` + `cases/update.py:126-168`. Same field set and timeline kinds as the API PATCH (they call the same `apply_case_update`). New: assignee org-scoped in the UI (`ui/views.py:1440-1451`) *and* in the applier (`cases/update.py:94-96` → `_resolve_login(value, org=case.owner_org)`), enum range check (`cases/update.py:78-87`), and audit row now inside the mutation transaction (`ui/views.py:1461-1471`). |
| **C3 — Observable edit/delete** | Met | **Met (unchanged; hardened)** | `ui/views.py:1523-1735`. Still camelCase wire shape, `data` immutable, `force` guard server-side on edit *and* delete, delete captures pk before `delete()`. New: edit audit inside the mutation transaction (`ui/views.py:1632-1642`). |

## Earlier deviations — unchanged

`docs/spec/deviations.md` §"Wave 6.16-P2" still records exactly **W616P2-1 … W616P2-6**
(+ the W616P2-6 bug-fix detail row). No row was added, removed, or re-scoped by the
remediation. The M1/L1/L2 changes are corrections of the security review's findings and
enforce constraints the plan already required (C2 parity, bounded enums, audited mutations),
so they are **not new plan deviations**.

## Security-regression tests (4)

All four exist in `tests/conformance/test_ui_p2_wiring.py` and pass:

| Test | Line | Asserts |
|---|---|---|
| `test_case_edit_cross_org_assignee_is_refused` | 496 | UI re-renders 200 and leaves the assignee unchanged for an org-B login **and** UUID |
| `test_shared_applier_scopes_assignee_to_owner_org` | 514 | API parity: the shared applier resolves a cross-org login to `None` |
| `test_case_edit_out_of_range_severity_rerenders_not_500` | 521 | UI `severity=99` → 200 re-render, row unchanged (no 500) |
| `test_shared_applier_rejects_out_of_range_enums` | 532 | API parity: `severity`/`tlp`/`pap` = 99 → `CaseUpdateError` |

`test_ui_p2_wiring.py` now collects **28** cases (was 24).

## Parity regression check — `ui-case-edit` POST vs `PATCH /api/v1/case/<id>`

Both surfaces call the one applier, so parity is structural, not coincidental:

- **Field set unchanged.** `CASE_SCALAR_FIELDS = ("title","description","summary","severity","tlp","pap","flag")`
  (`cases/update.py:23`) is byte-identical to the old API `_CASE_SCALAR_FIELDS`; the applier
  additionally accepts `status`, `assignee` and `tags`/`addTags`/`removeTags` — the same set
  the API accepted before the extraction. No field the API contract allows was dropped from
  either surface.
- **Range check applies identically.** `_set_case_fields` (`cases/update.py:78-87`) is the
  single range gate, reached by *both* the API `_update_case` and the UI POST. Bounds are
  `severity 1..4`, `tlp 0..4`, `pap 0..3` (`core/enums.py:40-60`) — matching the choices the
  UI form renders (`TLP_CHOICES`/`PAP_CHOICES`/`SEVERITY_CHOICES`), so the UI cannot offer a
  value the applier rejects, and the API can no longer reach the DB `CheckConstraint` as a 500.
- **Error shape preserved on the API.** `CaseUpdateError` carries `to_response_payload()`
  (`cases/update.py:48-49`); `_update_case` catches it and returns the same
  `{"type","message","fields"}` 400 as before (`cases/views.py:341-350`). Verified by
  `test_unknown_fields.py` and `test_t2_p4_surface.py` (both green).
- **Bulk path preserved.** `case_bulk_update.apply_one` no longer receives a `Response`;
  `CaseUpdateError` is a `ValueError` subclass and `bulk_patch` already catches `ValueError`
  per item (`compat/bulk.py:78-80`), so an out-of-range value is still a per-item 400 without
  rolling back siblings — `test_bulk_case_patch_reports_an_out_of_range_value_without_rolling_back_the_batch`
  passes.

**No parity regression found.**

## Observations (not regressions, not new deviations)

- `alert_status` (`ui/views.py:1326-1338`) still writes `alert.save()` + `AuditLog` without a
  wrapping `transaction.atomic()`. This matches the prior P2 state and was **out of scope** of
  the review's L2, which named only `case_edit` and `observable_edit`. Low residual of the same
  class; not introduced by this remediation.
- The UI `case_edit` pre-check (`ui/views.py:1442-1445`) and the applier guard are redundant by
  design ("defence in depth"): the UI gives a clear message, the applier guarantees the API is
  equally safe. Intentional.
- A transient untracked probe, `tests/conformance/test_zz_reaudit_probe2.py`, existed briefly
  during the first full-suite run and was deleted mid-collection (its two items raised
  `FileNotFoundError`). It is not present now and is not part of the product tree; the clean
  re-run below confirms it was an environmental artefact, not a failure.

## Command log (this re-verification)

```bash
.venv/bin/python -m pytest tests/conformance/test_ui_p2_wiring.py tests/conformance/test_tenant_scoping.py -q
# → 120 passed in 20.61s            (28 P2 wiring + 92 tenant scoping)

.venv/bin/python -m pytest tests/conformance/test_ui_p2_wiring.py -q
# → 28 passed in 6.57s

.venv/bin/ruff check
# → All checks passed!

.venv/bin/mypy
# → Success: no issues found in 105 source files

# extra confidence (not requested): full conformance suite
.venv/bin/python -m pytest tests/conformance -q -p no:randomly
# → 1442 passed, 3 skipped in 73.19s   (3 skips are documented Postgres-only cases)

# supporting API-parity suites
.venv/bin/python -m pytest tests/conformance/test_t2_p4_surface.py tests/conformance/test_unknown_fields.py tests/conformance/test_enum_contracts.py tests/conformance/test_t1_surface.py -q
# → 162 passed in 12.76s
```

## Re-verification verdict

**Pass.** C1/C2/C3 remain **Met**. The four new security-regression tests exist and pass.
The earlier deviations W616P2-1–6 are unchanged. The M1/L1/L2 remediation is present and
correct, and **no parity regression** exists between `ui-case-edit` POST and
`PATCH /api/v1/case/<id>` — the shared applier still accepts the full API field set and the new
enum range check fires identically on both surfaces.

**New deviation:** none.