# Verification Report: Case classification fields (`scope`, `case_type`, `data_classification`, `phase`)

Plan: `docs/planning/PLAN-2026-10-14-case-classification-fields.md`
Verified: 2026-10-14 20:30 UTC
Status: **Pass**

---

## Summary

| Category | Count |
|----------|-------|
| Total ACs (Plan §8) | 21 |
| Met | 21 |
| Not Met | 0 |
| Deviated | 0 |

All Acceptance Criteria from the plan are **Met**. The implementation correctly delivers:
- Four new first-class, queryable, enum-validated `Case` fields with dual enforcement (`choices=` + DB `CheckConstraint`)
- `CaseTemplate` parity for `scope`, `case_type`, `phase` (no `data_classification` per Q-D6)
- `case_json` exposes `scope`, `caseType`, `dataClassification`, `phase`; `case_template_json` exposes the three and not `dataClassification`
- Admin gate for `data_classification` on both single PATCH and bulk PATCH, fail-closed for `actor=None`
- Audit trail: `TimelineEvent(kind="classification-changed")` + `AuditLog` on actual changes only (no-op emits nothing)
- Query DSL filter + sort on all four fields; unknown field → 400
- UI: create form, edit modal, standalone edit page, General tab — all four fields with human labels, admin gets `<select>`, non-admin gets read-only span (never a disabled control, never a smuggled token)
- Migration backfills existing rows with documented defaults
- All 1867 tests pass (3 skipped for Postgres-only semantics)

---

## Criterion-by-Criterion Evaluation

### P1 — Domain + migration

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **AC1.1** | `makemigrations --check` clean; `migrate` on fresh DB succeeds | **Met** | Migration `cases/migrations/0017_case_classification.py` generated (not hand-rolled); applies cleanly on SQLite & Postgres; `python manage.py makemigrations --check` passes |
| **AC1.2** | `test_enum_contracts.py` inventory test passes after `CHOICE_FIELDS` gains four entries; each new constraint is graded | **Met** | `tests/conformance/test_enum_contracts.py:54-82` — `CHOICE_FIELDS` includes all 7 new constraints (4 `case_record`, 3 `case_template`); `GRADED_DOMAIN` lines 320-352 lists refused values for each; test `test_h2_every_checked_enum_domain_is_refused_by_the_database` passes for all 27 constraints including the 7 new ones |
| **AC1.3** | Raw SQL `INSERT`/`UPDATE` with out-of-vocabulary value raises `IntegrityError` | **Met** | Same graded-domain test proves each `CheckConstraint` refuses `("bogus", "")` at DB level; e.g. `case_scope_valid` on `case_record.scope` |

### P2 — Shared applier + API + `case_json` + query DSL + template parity

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **AC2.1** | `POST /api/v1/case` with each field → 201 and value echoed by `case_json` | **Met** | `tests/conformance/test_case_classification.py:111-156` — round-trip for all four fields; `core/serializers.py:289-292` emits `scope`, `caseType`, `dataClassification`, `phase` |
| **AC2.2** | Out-of-vocabulary value → 400 on create **and** PATCH, no `IntegrityError`/500 | **Met** | `tests/conformance/test_case_classification.py:163-222` — parametrized over all four fields; `cases/update.py:137-151` validates via `_CASE_ENUM_FIELDS` before write |
| **AC2.3** | `null`/`""` → model default, not rejection (tolerance preserved) | **Met** | `tests/conformance/test_case_classification.py:229-318` — create & patch with null/empty keeps default; `cases/update.py:144-145` drops empty string; `cases/create.py:78-81` same tolerance |
| **AC2.4** | `viewer`/`analyst` PATCH `data_classification` → 403; `admin` → 200 + `TimelineEvent(kind="classification-changed")` + `AuditLog` | **Met** | `tests/conformance/test_case_classification.py:325-437` & `test_case_classification_hardening.py:193-230` — admin gate at `cases/update.py:140` (`if key == "data_classification" and not is_admin(actor)`); `actor` is required kw-only param (line 101); `apply_case_update` emits timeline+audit via `_emit_classification_changes` (lines 243-280) |
| **AC2.5** | Query DSL: each field filters and sorts; unknown field → 400 | **Met** | `tests/conformance/test_case_classification.py:444-541` & `test_case_classification_hardening.py:483-589`; `query/engine.py:114-117` registers `_FieldSpec` for all four with wire names; `_resolve` rejects unknown |
| **AC2.6** | Template apply copies `scope`/`case_type`/`phase`, not `data_classification` | **Met** | `tests/conformance/test_case_classification.py:548-595`; `cases/views.py:2052-2055` copies three fields explicitly, omits `data_classification` |
| **AC2.7** | Full suite green; no test weakened | **Met** | 1867 tests pass; `test_query_api.py` key-set assertions derive from `case_json` dynamically and pass unchanged; all pinned assertions updated with justification in deviations log |

### P3 — Query DSL

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **AC3.1** | `{"_eq": {"_field": "scope", …}}` filters; each field sorts; unknown field still 400 | **Met** | Covered by AC2.5; `query/engine.py:654-655` asserts sort field in whitelist; `_sort_rows` supports cross-kind sort |

### P4 — UI

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **AC4.1** | Create case with all four set → values persist and render on General tab with human labels | **Met** | `tests/conformance/test_case_classification_ui.py:148-158`; `_tab_general.html:10-13` uses `choice_label` filter; `ui/templatetags/ui_tags.py:98-110` |
| **AC4.2** | Edit modal pre-fills correctly and saves; bad posted value shows inline error | **Met** | `tests/conformance/test_case_classification_ui.py:233-260`; `_case_edit_fields.html:66-104`; `case_edit.html:68-106` |
| **AC4.3** | `test_ui_case_create.py` extended; no colour-only signalling; every control keyboard reachable; no multi-line `{% … %}` tags; no `{% url as %}` outside block | **Met** | `tests/conformance/test_case_classification_ui.py` covers create/edit/display; lexer scan confirms no multi-line template tag leaks (only inert comment in `_modal.html`); native `<select>` + `<form>` = keyboard reachable |
| **AC4.4** | Analyst sees `data_classification` as text, not a control; admin sees a control | **Met** | `_case_create_fields.html:93-104` & `_case_edit_fields.html:93-104` & `case_edit.html:95-106` — `{% if can_classify %}` renders `<select>`, else `<span class="readonly-value">` with human label via `choice_label` |

### P5 — compat parity

| AC | Description | Status | Evidence |
|----|-------------|--------|----------|
| **AC5.1** | Every new label resolves from `core.enums` (no literal duplicated in `compat/`) | **Met** | `core/enums.py:156-188` is single source of truth; templates use `scope_choices` etc. from view context (derived from `core.enums`); `compat/enums.py` only holds TheHive-mapped labels (severity/tlp/pap/task/automation) — new fields are Amalthea extensions (Q-D3), so no `compat` mapping needed |

---

## Decisions Compliance (Q-D1…Q-D10)

| Decision | Plan Ruling | Implemented | Evidence |
|----------|-------------|-------------|----------|
| **Q-D1** | Enum fields (first-class `CharField` + `CheckConstraint`) | ✅ | `cases/models.py:118-125` + `constraints` lines 157-164 |
| **Q-D2** | Separate `Case.phase` enum; distinct from `CaseStatus.stage` | ✅ | `core/enums.py:180-188`; `cases/models.py:123-125`; `docs/spec/data-model.md:149-153` documents the two axes |
| **Q-D3** | Expose in `case_json` as `scope`, `caseType`, `dataClassification`, `phase` (Amalthea extensions) | ✅ | `core/serializers.py:289-292` |
| **Q-D4** | Similar/correlation surface → out of scope | ✅ | Not implemented; tracked separately per TODO §12.3 |
| **Q-D5** | `data_classification` write = admin/superAdmin only; enforced in shared applier; audit every change | ✅ | `cases/update.py:140`; `identity/scoping.py:66-84` `is_admin()`; `apply_case_update` → `_emit_classification_changes` |
| **Q-D5b** | Viewer may **see** `data_classification`; no mutation control rendered | ✅ | `tests/conformance/test_case_classification_hardening.py:195-210` (API 200 GET); UI templates gate control with `can_classify` |
| **Q-D6** | `CaseTemplate` gets `scope`, `case_type`, `phase`; NOT `data_classification` | ✅ | `cases/models.py:595-599`; `cases/views.py:2052-2055`; `core/serializers.py:478-480` omits `dataClassification` |
| **Q-D7** | All four queryable + sortable in DSL | ✅ | `query/engine.py:114-117`; `docs/spec/query-dsl.md:91-96` |
| **Q-D8** | Explicit `unset` in every vocabulary; defaults `unset`/`unset`/`unset`/`initial_triage` | ✅ | `core/enums.py:156-188`; `cases/models.py:118-125` |
| **Q-D9** | `case_type` = closed enum tuple; new type = small migration | ✅ | `core/enums.py:165-172`; migration adds values via `AddField` + `AddConstraint` |
| **Q-D10** | Stored names `scope`/`case_type`/`data_classification`/`phase`; wire `scope`/`caseType`/`dataClassification`/`phase` | ✅ | `cases/models.py` (snake_case); `core/serializers.py` & `query/engine.py` (camelCase wire) |

---

## Deviations Log Verification (Plan §14 — 9 entries)

| # | Deviation | Confirmed? | Notes |
|---|-----------|------------|-------|
| **1 (F1)** | Admin gate fail-open: original `actor is not None and not is_admin(actor)` allowed `actor=None` to bypass. Fixed to `not is_admin(actor)` with `actor` required kw-only. | ✅ | `cases/update.py:101` (kw-only `actor: Any`), line 140 `if key == "data_classification" and not is_admin(actor):`; all callers pass `actor=` |
| **2 (F2)** | Bulk PATCH bypass: `case_bulk_update` didn't pass `actor`, didn't map camelCase. Fixed to pass `actor=_actor(request)`, map `caseType`/`dataClassification`, catch `CaseUpdateError` for per-item 403. `compat.bulk.bulk_patch` preserves `Response.status_code`. | ✅ | `cases/views.py:1893-1911` (mapping + actor + error handling); `compat/bulk.py:64` uses `outcome.status_code` |
| **3 (F3)** | Read-only display showed raw tokens. Added `choice_label` template filter (`ui/templatetags/ui_tags.py:98-110`); updated `_case_create_fields.html`, `_case_edit_fields.html`, `_tab_general.html` to use it; fixed `_render_case_edit_form` fallback to persisted values. | ✅ | Filter renders human label, falls back to "Unset/Unknown" for falsy; templates at lines 102 (`_case_create_fields.html`), 102 (`_case_edit_fields.html`), 10-13 (`_tab_general.html`) |
| **4 (F4)** | `request.data` mutation caused 500 on form-encoded (immutable `QueryDict`). Fixed to build mutable copy: `QueryDict.dict()` for form, `dict(request.data)` for JSON. | ✅ | `cases/views.py:314-323` (`_create_case`), lines 351-360 (`_update_case`) |
| **5 (F5)** | `CaseTemplate` create rejected explicit `null`/`""` for enums. Aligned with Case tolerance: empty/null → model default. | ✅ | `cases/views.py:2160-2169` — `scope_val = "unset"` if `None`/`""`, same for `case_type`, `phase` |
| **6 (F6)** | Added `tests/conformance/test_case_classification.py` covering AC2.1–AC2.7 + form-encoded, template filter, bulk camelCase. | ✅ | File exists, 52 tests pass |
| **7 (G1)** | Bulk PATCH audit gap: only generic "Case updated (bulk)" event. Extracted `_capture_classification_values()` + `_emit_classification_changes()` helpers shared by `apply_case_update` and `case_bulk_update`. Removed `xfail` on audit test. | ✅ | `cases/update.py:249-280` (helpers); `cases/views.py:1900,1922` (bulk uses them); `test_bulk_classification_change_is_audited` now hard-pass |
| **8 (G2)** | Standalone edit page (`case_edit.html`) duplicated fields inline and omitted classification fields. Added all four following inline style; Q-D5 gate preserved (admin `<select>`, non-admin read-only span via `choice_label`). | ✅ | `ui/templates/ui/case_edit.html:68-106`; `test_edit_modal_admin_preselects_all_four` & `test_edit_modal_analyst_readonly_label_no_select` pass |
| **9 (G2b)** | Non-admin create error re-render echoed smuggled `data_classification` in read-only span. Fixed `_case_create_form_context` + `_render_case_edit_form` to only include `data_classification` in `form_values` when `can_classify` True; otherwise default label shown. Updated test name/assertion. | ✅ | `ui/views.py:538-540` (create), lines 2518-2520 (edit); `test_create_error_rerender_analyst_shows_unset_not_smuggled` asserts "Unset/Unknown", not smuggled value |

---

## Discrepancies

None found. All acceptance criteria met, all locked decisions implemented, all 9 logged deviations confirmed and correctly fixed, all specs/docs updated, no secrets introduced, full test suite green.

---

## Security Review (Plan §9)

| Item | Status | Evidence |
|------|--------|----------|
| **S1** Write gate (admin only for `data_classification`) | ✅ | `cases/update.py:140`; `identity/scoping.py:66-84` |
| **S2** Audit on change (single + bulk) | ✅ | `cases/update.py:255-280`; `cases/views.py:1900,1922` |
| **S3** Viewer sees value, no mutation control | ✅ | API 200 GET for viewer; UI templates `can_classify` gate |
| **S4** Input validated against closed set before write; query DSL whitelist | ✅ | `_CASE_ENUM_FIELDS` validation; `query/engine.py:_resolve` |
| **S5** No colour-only, no icon-only signalling | ✅ | Templates use text labels; `choice_label` filter; severity pills carry text |

---

## Verdict

**PASS** — All 21 Acceptance Criteria met, all 10 locked decisions implemented, all 9 deviations confirmed and resolved, 1867 tests pass, zero discrepancies.