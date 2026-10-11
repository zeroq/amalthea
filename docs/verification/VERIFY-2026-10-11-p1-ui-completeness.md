# Verification Report: UI completeness wave — Batch P1

Plan: `docs/planning/PLAN-2026-10-11-ui-completeness.md` (P1 phase, §8)
Verified: 2026-10-11 11:57 UTC
Verifier: `verifier` (read-only)
Status: **Partial**

## Summary

- Total P1 ACs evaluated: **14** (A1, A2, A3a, A3b, A3c/Q2, B1, B2, B4, C4, C7, D2, D6, D7, D8)
  plus the tech-lead pre-fixes and the guard-test check.
- **Met: 11** — A1, A2, A3a, A3b, A3c/Q2, B1, B2, B4, C4, C7, D8
- **Partial: 3** — D2, D6, D7
- **Not Met: 0**
- Tests: `1401 passed / 3 skipped` in `tests/conformance`; the requested 6-file subset `217 passed`.
- Gates: `mypy` clean (104 files), `ruff check .` clean.

## Criterion-by-Criterion

| AC | Status | Evidence |
|---|---|---|
| **A1** Attachments on case detail | Met | `ui/templates/ui/case_detail.html:190-239` (section, `enctype="multipart/form-data"`, `name="attachments"`, list `name`/`size|filesizeformat`/`created_by.login|default:"—"`, download link, `<dialog>` delete + POST). Context `ui/views.py:348` `attachments=case.attachments.select_related("created_by").order_by("-created_at")`. API field `cases/views.py:1824` `request.FILES.getlist("attachments")`; model field `created_by`. Redirects `ui/views.py:1424,1462` → `ui-case-detail`. Tests `test_case_detail_passes_attachments_queryset`, `..._form_field_name_matches_api`, `..._list_uses_created_by`, `test_attachment_upload_redirects_to_case_detail`, `test_attachment_delete_redirects_to_case_detail`, `test_standalone_attachment_list_still_works` — pass. |
| **A2** Merge `<dialog>` posts `target_case` | Met | `case_detail.html:243-262` + `ui/templates/ui/_case_merge_form.html:7` (`name="target_case"`, `form="case-merge-form"`). View reads `target_case` at `ui/views.py:1379`; GET path renders `ui/case_merge.html` (exists) and is exercised by `test_ui_urls_smoke.py::test_every_get_routable_ui_page_renders`. No dedicated POST-integration test (see Deviations). |
| **A3a** Export JSON plain GET link | Met | `case_detail.html:18` `<a class="btn" href="{% url 'ui-case-export' case.number %}">`; view `ui/views.py:885-900` returns `application/json` with `Content-Disposition: attachment`. Route `ui/urls.py:30`. |
| **A3b** Apply-template link | Met | `case_detail.html:19` `<a ... {% url 'ui-case-apply-template' case.number %}>`; route `ui/urls.py:37`; GET renders `case_template_apply.html` (`ui/views.py:942-953`). |
| **A3c / Q2** `ui-case-bulk` stub removed | Met | Route removed (`ui/urls.py`); `case_bulk` view removed (`ui/views.py`); `rg "ui-case-bulk|case_bulk"` clean in `ui/` (only tests + docs mention it). Tests `test_ui_case_bulk_route_removed` (NoReverseMatch) and `test_ui_case_bulk_returns_404` pass. Q2 decided = delete. |
| **B1** Rail Observables (`o`) | Met | `ui/templates/ui/base.html:55-60`; target `ui-observable-list` (`ui/urls.py:105`). |
| **B2** Rail Tags (`t`) / Case templates (`m`) / Taxonomy (`x`) | Met | `base.html:79-96`; targets `ui-tag-list` (`:100`), `ui-case-template-list` (`:82/:109`), `ui-taxonomy` (`:108`). |
| **B4** Tenant indicator | Met (untested) | `base.html:107-115`: org → `{{ request.user.org.name }}`; else superAdmin → link "All organisations" → `ui-settings-organisations`; else → "No organisation". **No test asserts this** (see Deviations). Precedence shows org name before the superAdmin branch. |
| **C4** Case-template edit | Met | Route `ui/urls.py:89-93`; GET prefills via `case_template_form.html` `value=`/`selected`/`checked`; POST updates all fields (`_parse_template_form` + `setattr`) `ui/views.py:1083-1137`; audit `AuditLog(action="template.updated")`. Edit link `case_template_detail.html:9`. Tests `test_case_template_edit_get_prefilled`, `..._post_updates`, `..._audits_mutation`, `test_case_template_detail_has_edit_link` pass. Templates are global (no org filter) — consistent with the in-code audit comment "templates are global". |
| **C7** Password change | Met | Route `ui/urls.py:139-146` (`PasswordChangeView`, `template_name="ui/password_change.html"`, `success_url=reverse_lazy("ui-dashboard")`). Login-required proven by `test_password_change_requires_login` (302→`/login`). Themed template `password_change.html`, no inline assets (test + read). Rail-foot link `base.html:116` before Sign out (`test_base_html_has_password_change_link`). Unusable-password branch `password_change.html:48-55` (`test_password_change_shows_unusable_password_message`). |
| **D2** Themed 404/403/500 + handlers | **Partial** | Handlers as dotted strings `amalthea/urls.py:9-11`; resolve via `get_callable` (`test_error_handlers_are_dotted_paths_that_resolve`). No `__test_*` route remains (`rg` clean). `DEBUG=False` unmatched URL → themed 404 (`test_unmatched_url_uses_themed_404_handler`). Templates extend base, no inline assets (`test_error_templates_have_no_inline_assets`). Only `ui/404.html` exists (old inline `style=` removed). **Gap:** `handler500` is DB-free only for anonymous requests — see Deviations D2-1. |
| **D6** Server-rendered cheat-sheet | **Partial** | `<dialog id="cheatsheet-modal">` server-rendered in `base.html:143-176` with a server-side table; `keys.js` builds no HTML (`openCheatsheet` → `modal.showModal()`, `keys.js:65-70`); `?` opens it (`keys.js:83-87`); `innerHTML` absent from `ui/static/` (only prose comments in `live.js`/`modal.js`). **Gap:** no visible affordance to open it — see Deviations D6-1. |
| **D7** Font Awesome rail icons | **Partial** | Every rail entry carries `<i class="fa-solid fa-…" aria-hidden="true">` + visible label (`base.html:37-100`); icon-only controls carry accessible names (cheatsheet close `aria-label="Close"`). **Gap:** 4 rail icons used by P1 are not defined in the shipped subset font — see Deviations D7-1. |
| **D8** Keyboard coverage + nav guard | Met | `keys.js` `ROUTES`/`CHEATSHEET` and the rail/server table agree for `g a c o r p s t m x ,` (+ `/ ?`). `tests/conformance/test_ui_nav.py` (7 tests) passes; independently confirmed all keys present in rail (`base.html`), `ROUTES`, `CHEATSHEET`, and the server table. |

### Pre-fixes requested for confirmation

| Item | Result |
|---|---|
| `amalthea/urls.py` string handlers resolve; no DEBUG `__test_*` route remains | **Confirmed** — `amalthea/urls.py:9-11`; `test_error_handlers_are_dotted_paths_that_resolve`; `rg "__test_"` = no hits. |
| Attachment input `name="attachments"`, template uses `created_by` | **Confirmed** — `case_detail.html:194` `name="attachments"`; `case_detail.html:207` `attachment.created_by.login`; API `cases/views.py:1824`. |
| `.btn-err` defined in compiled CSS | **Confirmed** — `.btn-err{background:color-mix(...);border-color:...;color:var(--color-err)}` present in `ui/static/ui/app.css`. |
| `test_ui_nav.py` = 7 tests, rail ⇄ ROUTES ⇄ CHEATSHEET ⇄ server guards, each route resolves to `ui-` | **Confirmed** — 7 `def test_`, all pass. |

## Deviations

### D7-1 — Four rail icons (and one pre-existing badge icon) are absent from the Font Awesome subset — **Medium**
P1 added `<i>` icon classes to the rail and never extended `ui/static/vendor/fontawesome/css/fontawesome-subset.min.css`. The subset defines 50 icon classes; the following are **not** defined, so those `<i>` elements render blank (the labels remain, so no functional break and no a11y break — the icons are `aria-hidden`):

- `fa-gauge-high` — `base.html:37` (Dashboard) — **introduced by P1**
- `fa-book` — `base.html:69` (Playbooks) — **introduced by P1**
- `fa-rss` — `base.html:75` (Sources) — **introduced by P1**
- `fa-gear` — `base.html:100` (Settings) — **introduced by P1**
- `fa-building-lock` — `case_detail.html:10`, `alert_detail.html`, `observable_detail.html` (cross-org badge) — pre-existing

Evidence (script over templates vs subset):
```
MISSING from subset:
fa-book            ['ui/templates/ui/base.html']
fa-building-lock   ['.../alert_detail.html', '.../case_detail.html', '.../observable_detail.html']
fa-gauge-high      ['ui/templates/ui/base.html']
fa-gear            ['ui/templates/ui/base.html']
fa-rss             ['ui/templates/ui/base.html']
```
This directly contradicts the D7 AC "Font Awesome icons on rail entries". The subset CSS is hand-maintained (not produced by `make css`), so it must be extended with these five rules (the glyphs are present in the full `fa-solid-900.woff2`, 156 KB).

### D6-1 — No visible affordance opens the cheat-sheet — **Medium**
Plan AC D6: "opened by `?` **and by a visible affordance**". The modal is server-rendered and `?` opens it, but `rg 'data-modal-open="cheatsheet' ui/` returns **no** trigger. A mouse-only user has no way to open it, which also weakens the §6 requirement that primary actions be reachable without a keyboard. Suggested fix: a rail-foot "Keyboard shortcuts (?)" `linkish` control carrying `data-modal-open="cheatsheet-modal"`.

### D2-1 — `handler500` is DB-free only for anonymous requests — **Low/Medium**
`ui/views.py:1922` `handler500` calls `render(request, "ui/500.html", status=500)` with no `_base_context` and no `messages` — correct. However `500.html` extends `base.html`, which reads `request.user.org.name` (`base.html:109-110`) and `request.user.role`. Measured with `CaptureQueriesContext`:
```
ANON   handler500 queries: 0
AUTH   handler500 queries: 2   (SELECT identity_user…, SELECT identity_organisation…)
```
So for an authenticated session the themed 500 render touches the DB, and in a DB-down scenario the handler would itself raise and Django would fall back to its plain 500. The existing `test_handler500_works_with_debug_false_no_db` passes a user-less `RequestFactory` request and asserts **no query count**, so it does not catch this. This is a design tension with the D2 instruction to "extend the base layout"; acceptable if the DB-down case is accepted, but the AC claim "does not touch the DB" is only conditionally true.

### D1 — Unused new partial `_attachment_delete_modal_body.html` — **Low**
`ui/templates/ui/_attachment_delete_modal_body.html` is created but referenced by **zero** templates/views (`rg` = no hits); the equivalent dialog body is inlined directly at `case_detail.html:211-230` instead. Dead file. (The other new partial, `_case_merge_form.html`, is used at `case_detail.html:252`.)

### D2 — `_case_merge_form.html` header comment is inaccurate — **Low (doc drift)**
The partial's comment says it "Renders inside the modal body via `body_template` in `_modal.html`"; it is in fact included directly by `case_detail.html`. No functional impact.

### D3 — B4 tenant indicator has no test — **Low**
`rg "All organisations|No organisation" tests/` finds nothing, so the three branches (org name / "All organisations" / "No organisation") are unverified by the suite. Also the precedence is org-first, so a superAdmin who happens to have an `org` set would see the org name, not "All organisations" (no constraint forbids a superAdmin having an org).

### D4 — A2 merge POST integration is untested — **Low**
No test posts `target_case` to `ui-case-merge` and asserts the surviving case. The wiring is correct by inspection (`ui/views.py:1379`), but the integration is unguarded.

### D5 — P1 outcome documentation not recorded — **Low (process)**
Plan §13a / line 258 requires "update `TODO.md` §6.16 / `COMPLETED.md` / `deviations.md`" after each batch. The working-tree doc edits are for §6.15 (Wave 6.3B); `TODO.md` adds §6.16 but leaves it `[ ]`, `COMPLETED.md` has no P1/§6.16 entry, and `deviations.md` has no "UI completeness wave" section (the Q2 decision to delete the bulk stub is not recorded there). The plan (line 260) says deviations should be recorded in `deviations.md` under "UI completeness wave".

### D6 — Duplicate route registrations in `ui/urls.py` — **Low, pre-existing**
`name="ui-case-template-list"` is registered twice (`ui/urls.py:82` and `:109`), and the procedure routes twice (`:71-80` and `:111-120`). These duplicates are present in `HEAD` (pre-P1), so **not** a P1 deviation; noted only for follow-up.

### D7 — Out-of-P1 changes co-mingled in the working tree — **Informational**
The working tree also contains the §6.15 Wave 6.3B work (`ui/static/ui/playbook_form.js` + `playbook_form.html`, `playbook_detail.html`, `observable_detail.html`, `observable_list.html`, `case_tags.html`, `ui/templatetags/ui_tags.py::prettyjson`, and the §6.15 docs). These were not evaluated as P1 ACs; they pass the gates (`test_ui_inline_assets`, `test_ui_table_markup`, mypy, ruff).

## Blockers

None. No AC is **Not Met**; the three Partial ACs (D2, D6, D7) have concrete, low/medium-severity gaps.

## Commands run (with result tails)

```
$ .venv/bin/pytest tests/conformance/test_ui_nav.py tests/conformance/test_ui_p1_wiring.py \
    tests/conformance/test_ui_inline_assets.py tests/conformance/test_ui_urls_smoke.py \
    tests/conformance/test_ui_table_markup.py tests/conformance/test_settings_ui.py -q
217 passed in 5.45s

$ .venv/bin/pytest tests/conformance -q
1401 passed, 3 skipped in 65.29s (0:01:05)

$ .venv/bin/mypy
Success: no issues found in 104 source files

$ .venv/bin/ruff check .
All checks passed!

$ python3 <icon diff script over ui/templates vs fontawesome-subset.min.css>
MISSING from subset: fa-book, fa-building-lock, fa-gauge-high, fa-gear, fa-rss

$ pytest <temp probe> -s
ANON handler500 queries: 0
AUTH-LAZY handler500 queries: 2  (identity_user, identity_organisation)
```

## Recommendations (non-binding)

1. Extend `fontawesome-subset.min.css` with `fa-gauge-high`, `fa-book`, `fa-rss`, `fa-gear` (and, opportunistically, `fa-building-lock`) — otherwise D7 is cosmetic-only.
2. Add a visible rail-foot "Keyboard shortcuts" control with `data-modal-open="cheatsheet-modal"` to satisfy D6's discoverability clause and mouse reachability.
3. Add a query-count assertion (or a minimal 500 template that does not read `request.user`) if the "500 render must be DB-free" invariant is intended to hold for authenticated sessions.
4. Delete `_attachment_delete_modal_body.html` or use it from `case_detail.html`; correct the `_case_merge_form.html` comment.
5. Add tests for the B4 tenant-indicator branches and for the A2 merge POST round-trip.
6. Record the P1 outcome and the Q2 decision in `COMPLETED.md` / `deviations.md`, and tick `TODO.md` §6.16 when the phase closes.
