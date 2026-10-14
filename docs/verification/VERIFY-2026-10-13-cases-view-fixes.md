# Verification Report: Cases view — trailing-slash 404 + New-case modal

**Plan:** `docs/planning/PLAN-2026-10-13-cases-view-fixes.md`  
**Verified:** 2026-10-13 14:32 UTC  
**Status:** Pass

---

## Summary

| Metric | Count |
|--------|-------|
| Total ACs | 16 |
| Met | 16 |
| Not Met | 0 |
| Deviated | 0 |

All 16 acceptance criteria (T1.1–T1.4, T2.1–T2.9, T3.1–T3.2) are **Met**. The full quality gate (`make check`) is green: 1689 passed, 3 skipped.

---

## Criterion-by-Criterion

| ID | Criterion | Verdict | Evidence |
|----|-----------|---------|----------|
| **T1.1** | `GET /cases/` → 301 → `/cases` → 200 (authenticated writer) | **Met** | `tests/conformance/test_ui_trailing_slash.py::test_ui_cases_trailing_slash_301_then_200` (lines 46–74): anonymous gets 301→/cases→login; analyst gets 301→/cases→200; `resolve("/cases")` confirms `ui-case-list`. |
| **T1.2** | Every top-level UI page tolerates trailing slash via one rule | **Met** | `test_ui_trailing_slash.py::test_ui_top_level_pages_trailing_slash_301` (lines 101–113) parametrised over 8 pages (`/alerts/`, `/observables/`, `/tags/`, `/taxonomy/`, `/automation/`, `/sources/`, `/case-templates/`, `/settings/users/` implied via `/settings/organisations/`). Single middleware rule, no per-route list. |
| **T1.3** | `/api/v1/case/` and `/admin/…` not rewritten | **Met** | `test_ui_trailing_slash.py::test_api_v1_case_trailing_slash_not_rewritten` (lines 134–147): anon GET `/api/v1/case/` → 401, not 301/308; Location ≠ `/api/v1/case`. `test_admin_trailing_slash_keeps_own_behaviour` (lines 150–154): `/admin/` → 302 to `/admin/login/…`. Middleware exclusions at `core/middleware.py:29,61–62`. |
| **T1.4** | `POST /cases/new/` → 308 preserving method + body | **Met** | `test_ui_trailing_slash.py::test_ui_case_create_post_trailing_slash_308` (lines 121–126): analyst POST → 308, Location `/cases/new`. Middleware returns `HttpResponsePermanentRedirect308` for unsafe methods (`core/middleware.py:32–40,79–80`). |
| **T2.1** | Rail contains no `New case` link (source-level, `base.html`) | **Met** | `test_ui_case_create.py::test_ui_case_list_rail_has_no_new_case_link` (lines 1139–1154): parses `base.html` rail `<ul>`, asserts no `ui-case-create` URL; rendered page lacks `<span class="k">n</span>`. |
| **T2.2** | Cases list renders `New case` button with `href="/cases/new"` + `data-modal-open="new-case-modal"` | **Met** | `test_ui_case_create.py::test_ui_case_list_renders_new_case_button` (lines 1157–1164): button present with exact attributes. Template: `case_list.html:14`. |
| **T2.3** | Cases list contains server-rendered `#new-case-modal` with 11 controls, footer form POSTs to `/cases/new` | **Met** | `test_ui_case_create.py::test_ui_case_list_renders_new_case_modal_controls` (lines 1166–1175): dialog `id="new-case-modal"`, form `id="new-case-form"`, `action="/cases/new"`, all 11 control names (`title`…`tags`, `origin`) present. |
| **T2.4** | Valid modal POST (`origin=cases`) creates case → 302 to detail | **Met** | `test_ui_case_create.py::test_ui_case_create_modal_post_success` (lines 1177–1188): POST with `origin=cases` → 302 to case detail; case persisted with title. |
| **T2.5** | Invalid modal POST (`origin=cases`) → 200 on cases list, modal auto-open, values echoed, error shown | **Met** | `test_ui_case_create.py::test_ui_case_create_modal_post_invalid_status_rerenders_list` (lines 1191–1218): 200, back on list (contains `/cases?closed=1`, not standalone heading), dialog has `data-autoopen="1"`, submitted title echoed, error message "Unknown case status: NoSuchStatus" in messages region. View logic at `ui/views.py:612–620` (`_reject` helper). |
| **T2.6** | `n` opens modal on cases list; else navigates to `/cases?new=1` | **Met** | `ui/static/ui/keys.js:81–88`: keydown handler clicks `[data-modal-open="new-case-modal"]` if present, else `window.location.href = "/cases?new=1"`. `_NON_NAV_KEYS` in `test_ui_nav.py:51` includes `"n"`. |
| **T2.7** | `GET /cases?new=1` renders dialog with `data-autoopen` | **Met** | `test_ui_case_create.py::test_ui_case_list_new_query_param_autoopens_modal` (lines 1221–1233): `?new=1` → dialog has `data-autoopen="1"`; plain `/cases` → dialog present but no `data-autoopen`. View: `case_list:552` passes `open_new_case=request.GET.get("new") == "1"`. |
| **T2.8** | Viewer: no rail link, no button, no modal, no `#new-case-modal`; POST 404; GET `/cases/` redirects | **Met** | `test_ui_case_create.py::test_ui_case_list_viewer_has_no_modal_affordance` (lines 1254–1267): viewer GET `/cases` → 200, no modal/button/href/`New case` text. POST → 404. `test_ui_trailing_slash.py::test_ui_cases_trailing_slash_viewer_redirects` (lines 187–206): viewer GET `/cases/` → 301 → `/cases` → 200. Template gating: `can_mutate` (`base.html:236`, `case_list.html:13,47`). |
| **T2.9** | `/cases/new` still renders standalone (no-JS fallback) with same fields, no `form=` attributes | **Met** | `test_ui_case_create.py::test_ui_case_create_standalone_fields_have_no_form_attribute` (lines 1236–1251): GET `/cases/new` → 200, `id="h-create"`, all 10 standalone controls lack `form=` attribute. `test_ui_case_create_get_returns_form` (lines 144–171): all 11 field names present. Template: `case_create.html:17–26` wraps shared partial without `form_id`. |
| **T3.1** | `test_ui_nav.py` green: rail keys == `ROUTES` keys; cheat-sheet ≡ `CHEATSHEET`; `n` documented as non-nav | **Met** | `test_ui_nav.py` all 7 tests pass. `_NON_NAV_KEYS = {"/", "?", "n"}` (line 51). Rail/cheat-sheet/JS arrays in sync (source-level parsing). Rendered dashboard shows `<kbd>n</kbd>` for analyst (line 134). |
| **T3.2** | Full gate green: ruff + format + mypy + migrations + css-check + pytest | **Met** | `make check` output: all lint/type/check phases pass; pytest 1689 passed, 3 skipped (Postgres-only). |

---

## Independent Reproduction

```bash
# 1. Conformance test suite (trailing slash, case create modal, nav)
$ .venv/bin/python -m pytest tests/conformance/test_ui_trailing_slash.py \
    tests/conformance/test_ui_case_create.py \
    tests/conformance/test_ui_nav.py -p no:randomly -q
.......................................................................
71 passed in 10.81s

# 2. Full quality gate
$ make check
...
1689 passed, 3 skipped in 101.42s
✓ check passed
```

Manual probe (via pytest fixtures — the test suite exercises the exact Client flows):
- `GET /cases/` (analyst) → 301, Location `/cases` → followed → 200 ✅
- `POST /cases/new/` (analyst) → 308, Location `/cases/new` ✅
- `GET /api/v1/case/` (anon) → 401, not 301/308 ✅
- Analyst `/cases` HTML contains `id="new-case-modal"` ✅
- Viewer `/cases` HTML lacks `id="new-case-modal"` ✅
- Viewer `GET /cases/` → 301 → `/cases` → 200 ✅
- Viewer `POST /cases/new` → 404 ✅

---

## Risks (Plan §9)

| Risk | Mitigation | Verified |
|------|------------|----------|
| **R1** Weakening `test_ui_nav` rail↔routes invariant by making `n` non-navigating | `n` kept in cheat-sheet (`base.html:163`, `can_mutate`-gated); added to `_NON_NAV_KEYS` with comment (`test_ui_nav.py:51`) | ✅ Tests enforce cheat-sheet ≡ `CHEATSHEET`; `n` documented but excluded from route equality check |
| **R2** Middleware rewrites API paths | Exclusion prefixes `/api/`, `/admin/`, `/static/`, `/media/`, `/ws/` (`core/middleware.py:29,61–62`); dedicated test T1.3 | ✅ `test_api_v1_case_trailing_slash_not_rewritten` asserts 401, not redirect |
| **R3** Two form-render call sites drifting | Single `_render_case_list` helper owns modal context (`ui/views.py:501–541`); used by `case_list` (line 552) and `case_create` error path (lines 612–620) | ✅ Shared helper ensures identical modal context; tests cover both paths |
| **R4** Non-JS users lose the modal | Button is real `<a href="/cases/new" …>` (`case_list.html:14`); error re-renders list with flash message (`ui/views.py:612–620`); standalone page unchanged (`case_create.html`) | ✅ Progressive enhancement: modal is enhancement, not requirement |

---

## Deviations / Follow-ups

**None.** Implementation matches the plan exactly across all acceptance criteria. No deviations from the approved design in §4/§5.

The three skipped tests in `make check` are pre-existing, Postgres-specific tests unrelated to this change (case numbering sequence collision and index planner shape — see `tests/conformance/test_case_numbering.py:340,371` and `test_indexes.py:421`).

---

## Conclusion

**Verdict: Pass.** All 16 acceptance criteria satisfied. The trailing-slash middleware correctly normalises UI routes (301 for GET/HEAD, 308 for unsafe) while excluding API/admin/static paths. The "New case" action has moved from the left rail to a modal on the cases list, with full progressive enhancement (standalone page remains), viewer guarantees intact, and keyboard shortcut `n` preserved as a non-navigating cheat-sheet entry that opens the modal or deep-links to `/cases?new=1`. Conformance tests and full quality gate are green.