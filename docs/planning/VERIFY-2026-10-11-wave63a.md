# Verification Report: Wave 6.3A — Settings UI hardening (PLAN §13A)

Plan: `docs/planning/PLAN-2026-10-10-org-management.md` (§13A / §12 "Security-review corrections")
Review input: `docs/reviews/SECURITY-2026-10-11-org-user-management.md` (F3/F5/F6/F7/F8/F9/F10)
Verified: 2026-10-11 09:31 UTC
Baseline: uncommitted working tree vs `4c4c5f8` (branch `main`)
Verifier: verification specialist (read-only; no code/tests modified)
Status: **Pass** — all in-scope security findings and all O4 acceptance criteria are met; one low-severity cosmetic deviation from the §5.4/§13A prose (edit form is a page, not a `<dialog>`); one process step pending (security-auditor re-run artefact).

## Summary

- Total criteria evaluated: 18 (6 §13A plan items + 5 O4 acceptance criteria + 7 findings)
- Met: 17
- Not Met: 0
- Deviated: 1 (low severity, no security/functional regression)
- Tests run: `tests/conformance/test_org_user_hardening.py tests/conformance/test_settings_ui.py -q` → **58 passed**; `tests/conformance/test_ui_urls_smoke.py -q` → **5 passed**
- Scope confirmed: only `ui/templates/ui/_modal.html` + the two Settings pages (and their 4 body partials) were de-inlined. Remaining inline `<script>`/`style=""` live in pre-existing templates (`playbook_form.html`, `observable_detail.html`, `case_template_detail.html`, `case_template_form.html`, `playbook_detail.html`, `case_tags.html`, `observable_list.html`, `404.html`) — that is **§13B**, explicitly out of this wave.

## Criterion-by-Criterion

### §13A plan items (the scope-A plan statement)

#### A1. Externalise all modal/interaction JS into `ui/static/ui/*.js` (CSP-compliant)
Status: **Met**
Evidence:
- `ui/static/ui/modal.js` (new, 118 lines) — open/close/backdrop/Esc/Tab-trap + org-delete member toggle; contains **no** server data and **no** `innerHTML`.
- `ui/static/ui/modal.css` (new, 115 lines) — all modal styles moved out of the template.
- `ui/templates/ui/_modal.html:1-7` states the split; the old inline `<script>`/`<style>` were deleted (`git diff 4c4c5f8 -- ui/templates/ui/_modal.html`).
- `ui/templates/ui/base.html:22` loads `ui/modal.css`; `base.html:105` loads `ui/modal.js` (defer).
- CSP remains `script-src 'self'` / `style-src 'self'` with no nonce/`unsafe-inline` (`amalthea/settings/base.py:65-78`), so the external assets are exactly what the shipped policy permits.
Notes: `modal.js` is generic (no Settings-specific data); the member-choice toggle is progressive enhancement.

#### A2. Render create/edit as real server-side forms inside `<dialog>` (degrades to plain POST)
Status: **Deviated** (low)
Evidence:
- Create: server-rendered inside the dialog via `body_template` — `ui/templates/ui/settings_organisations.html:58` and `settings_users.html:66`; bodies `ui/templates/ui/_settings_org_create_form.html`, `_settings_user_create_form.html`; `_modal.html:53` renders `{% include body_template %}`; fields target the footer form via `form="{{ form_id }}"` (`_settings_org_create_form.html:7,11`; `_settings_user_create_form.html:7-39`).
- Edit: implemented as a **standalone server-rendered page**, not a `<dialog>` — `ui/views.py:1517` (`settings_organisations_edit`) renders `ui/templates/ui/settings_organisation_form.html` (plain `<form method="post">`), and `ui/views.py:1696` (`settings_users_edit`) renders `ui/templates/ui/settings_user_form.html`; the list links straight to those pages (`settings_organisations.html:29,36`; `settings_users.html:44`).
Notes: This contradicts the §5.4/§13A prose ("add/edit via a `<dialog>` form"), but **no acceptance criterion requires edit to be a dialog**. Edit still degrades to a plain CSRF-protected POST, matches O4-b/c, and is arguably a stronger progressive-enhancement baseline. No security impact. Severity: Low (cosmetic/UX).

#### A3. Replace the `innerHTML` sinks with DOM APIs / `json_script`
Status: **Met**
Evidence: Both Settings templates render names/org-options server-side; `rg 'innerHTML|escapejs'` over the 9 scope files returns only comment text. Tests `test_modals_do_not_build_html_from_server_data` (no `innerHTML`/`escapejs`) and `test_org_name_with_markup_is_escaped` (`&lt;img src=x` present, raw `<img src=x` absent) pass.
Notes: No `json_script` was needed because the data is no longer passed to JS at all.

#### A4. Remove inline `style=""` attributes too (same `style-src` rule)
Status: **Met**
Evidence: `rg 'style="|<script'` over the 9 scope files returns only the `_modal.html`/`_settings_org_delete_form.html` comments. Test `test_settings_pages_ship_no_inline_script_or_style` asserts `'style="' not in content` for both rendered pages, and also asserts `ui/modal.js` / `ui/modal.css` ship.

#### A5. Add `select_for_update` guards (F5)
Status: **Met** (with a residual outside F5's stated scope)
Evidence:
- `identity/services.py:77-92` `lock_active_superadmins()` uses `User.objects.select_for_update().filter(role="superAdmin", is_active=True)`.
- `identity/services.py:95-107` `@locks_active_superadmins` wraps `update_user` (`:329`) and `delete_user` (`:423`).
- `identity/services.py:110-124` `@serializes_superadmin_changes` (mutating HTTP verbs only) wraps API `_update_user` (`identity/views.py:486`), API `_delete_user` (`:588`), and UI `settings_users_edit` (`ui/views.py:1695`) / `settings_users_delete` (`:1811`).
- Regression tests `test_lock_helper_counts_active_superadmins`, `test_delete_user_service_still_guards_last_superadmin`, `test_delete_user_service_still_works_and_audits` pass.
Residual (see Deviations §D2): `delete_organisation` and its two callers (`identity/views.py:300`, `ui/views.py:1563`) are **not** wrapped in the lock, so the `members=delete` last-superAdmin check (`identity/services.py:215-222`) remains check-then-act. This is the org-delete lockout class (F1 sequential case is fixed in D5), not one of F5's stated locations.

#### A6. Re-run `security-auditor` afterwards
Status: **Not evaluable / process pending**
Evidence: No Wave-6.3A security re-review artefact exists under `docs/reviews/` (only the input review `SECURITY-2026-10-11-org-user-management.md`). No functional test covers this; it is a process gate.
Notes: Non-blocking for the functional verdict; see Recommendations.

### O4 acceptance criteria (the org-management ACs §13A is scoped to fix)

#### O4-a. Nav hidden for non-superAdmin; Settings routes 404 for non-superAdmin
Status: **Met**
Evidence: `ui/templates/ui/base.html:67-73` gates the rail item on `request.user.role == "superAdmin"`; `ui/views.py:1433-1437` `_require_superadmin_ui` returns the standard 404 template. Tests: `test_nav_hidden_for_non_superadmin`, `test_nav_visible_for_superadmin`, `test_organisations_route_404_for_non_superadmin`, `test_users_route_404_for_non_superadmin`, `test_organisations_edit_route_404_for_non_superadmin`, `test_users_edit_route_404_for_non_superadmin` all pass. `tests/conformance/test_ui_urls_smoke.py` (5 passed) also covers the superAdmin-only Settings list routes.

#### O4-b. Full CRUD on both pages via UI (HTMX and no-HTMX)
Status: **Met**
Evidence: Create (`ui/views.py:1459-1491`, `1619-1682`), edit (`:1517-1558`, `:1696-1806`), delete (`:1561-1615`, `:1809-1836`), list (`:1493-1513`, `:1684-1691`). No `hx-` attributes are used on the Settings pages, so the same server-rendered forms are the no-HTMX path; the Django test client exercises them without JS/HTMX. `TestOrganisationsCRUD` (13) and `TestUsersCRUD` (12) pass.
Notes: The create form is server-rendered (F10 fix), so it is no longer "only injected by JS". A strictly JS-disabled browser still cannot `showModal()` the dialog (native `<dialog>` requires JS to open) — see Observation O1; the AC's explicit wording is HTMX/no-HTMX, which is met.

#### O4-c. Destructive actions use `<dialog>` (Esc/Tab-trap), never native `confirm()`
Status: **Met**
Evidence: Delete modals are `<dialog>` rendered by `ui/templates/ui/_modal.html:40-72`; Esc/backdrop/Tab-trap live in `ui/static/ui/modal.js:58-93`. Tests `test_org_delete_uses_dialog`, `test_user_delete_uses_dialog` assert `<dialog>` present and `"confirm("` absent.

#### O4-d. Org-delete modal shows counts, disables confirm when blocked, offers reassign-or-delete for members
Status: **Met**
Evidence: `ui/templates/ui/_settings_org_delete_form.html:10-15` renders `data-modal-blocked` + "Cannot delete: … owns tenant data ({{ org.blockers_text }})"; `:17-37` renders the member radio choice (reassign checked) + target select with `data-target-org`. `ui/views.py:1440-1455` builds `blockers_text`; `:1504-1507` attaches it. `modal.js:27-34` disables the submit when `[data-modal-blocked]` is present. Tests `test_delete_modal_blocker_is_rendered_server_side` ("Cannot delete:", "owns tenant data", "1 case", `data-modal-blocked`), `test_modal_shows_case_blocker`, `test_modal_shows_members_reassign_option` pass.

#### O4-e. Keyboard + cheat-sheet updated
Status: **Met**
Evidence: `ui/static/ui/keys.js:21` adds `",": "/settings/organisations"`; `:34` adds `{ key: ",", label: "Settings (Organisations)" }`. Tests `test_comma_shortcut_in_keys_js`, `test_cheatsheet_has_settings_entry` pass.

### Security-review findings in scope (§13A)

#### F3 — Stored XSS via `escapejs` + `innerHTML`
Status: **Met**
Evidence: Both Settings templates were rewritten to server-render all data; no `innerHTML`/`escapejs` remains (`test_modals_do_not_build_html_from_server_data`). Markup in an org name is auto-escaped by Django (`test_org_name_with_markup_is_escaped`). The only remaining `innerHTML` in the frontend is in `ui/static/ui/keys.js:45-51`, which concatenates the **static** `CHEATSHEET` constant — no server data, so not a sink.

#### F5 — Last-active-superAdmin TOCTOU
Status: **Met** (stated locations)
Evidence: see A5. Risk closed for the four stated sites by `select_for_update`; the same-class org-delete residual is recorded under D2.

#### F6 — User-existence oracle on `PATCH/DELETE /api/v1/user/{idOrLogin}`
Status: **Met**
Evidence: `identity/views.py:458-477` runs `_require_superadmin` **before** resolving the target for mutating verbs, and both a missing and a forbidden target return `_not_found("Resource")`. Test `test_mutating_user_detail_404_is_uniform` asserts identical status **and identical JSON** for an existing vs a random UUID. `_require_superadmin` body is uniform (`identity/views.py:93-99`).

#### F7 — Settings UI input paths returned unhandled 500s
Status: **Met**
Evidence: malformed ids go through `resolve_organisation`/`as_uuid` (`identity/services.py:27-46`) instead of raw ORM lookups — `ui/views.py:1573` (delete target), `:1647` (user-create org), `:1717` (user-edit org). Names are truncated to 200 **before** the uniqueness check with an `IntegrityError` catch — `ui/views.py:1466-1479` (create) and `:1526-1540` (edit). Tests `test_ui_user_create_malformed_org_does_not_500`, `test_ui_org_delete_malformed_target_does_not_500`, `test_ui_org_create_oversize_name_collision_does_not_500` pass.

#### F8 — API `is_active` accepted string `"false"` as truthy
Status: **Met**
Evidence: `parse_bool` (`identity/services.py:59-74`) accepts only real booleans and explicit `"true"`/`"false"`; used at `identity/views.py:430` (create) and `:539-541` (patch, 400 on invalid). Tests `test_api_patch_is_active_string_false_is_accepted_as_false`, `test_api_patch_is_active_true_string_is_accepted`, `test_api_patch_is_active_invalid_value_is_rejected`, `test_api_create_is_active_invalid_value_is_rejected` pass.

#### F9 — `IngestionToken.secret_hash` exposed in Django admin
Status: **Met**
Evidence: `identity/admin.py:58-61` — `readonly_fields = ("prefix", "last_used_at")`; `secret_hash` removed from both `readonly_fields` and `list_display`; docstring records the exclusion. Test `test_admin_does_not_display_secret_hash` passes.

#### F10 — Settings modals depend on inline `<script>` blocked by CSP
Status: **Met**
Evidence: `_modal.html` is now script/style-free; behaviour ships as `ui/static/ui/modal.js`, loaded by `base.html:105` under `script-src 'self'`. Rendered Settings pages contain no `<script>`/`style="` (`test_settings_pages_ship_no_inline_script_or_style`). The server-rendered create bodies remove the "inputs only exist if JS runs" defect that made O4-b unmeetable.

## Deviations

### D1 — Edit is a full page, not a `<dialog>` (Low)
- Plan refs: §5.4 ("add/edit via a `<dialog>` form"), §13A ("render create/edit as real server-side forms inside `<dialog>`").
- Actual: create is a server-rendered `<dialog>`; edit is a standalone server-rendered page (`settings_organisation_form.html`, `settings_user_form.html`).
- Impact: cosmetic/UX only. Edit still degrades to a plain CSRF-protected POST and satisfies O4-b/O4-c; no AC and no finding depends on it. No security impact.
- Rationale: plausible deliberate simplification (a page form is the most robust no-JS path); not documented as a decision in the plan.

### D2 — Org-delete last-superAdmin check is not lock-serialised (Low/Medium, residual)
- Plan ref: F5 remediates "last-active-superAdmin guards" with `select_for_update`; §13A A5.
- Actual: `identity/services.py:215-222` performs a plain `.count()` for the `members=delete` branch; `delete_organisation` is not wrapped by `@locks_active_superadmins`, and its callers (`identity/views.py:300`, `ui/views.py:1563`) are not wrapped by `@serializes_superadmin_changes`. Concurrent org deletes could still zero active superAdmins.
- Impact: same TOCTOU class as F5, but in the org-deletion path. F1 (the sequential lockout) is fixed (D5), and no F5 test targets this path. Low likelihood (two simultaneous superAdmin org deletes); medium blast radius if it occurs.
- Rationale: not in F5's stated "Where" list; likely an oversight while the user-path locks were added. Recommend locking `delete_organisation` as defence in depth.

### D3 — Process: security-auditor re-run not evidenced (Info)
- Plan ref: §13A ("Re-run `security-auditor` afterwards").
- Actual: no post-fix security report exists under `docs/reviews/`.
- Impact: process gate only; not a functional/security defect found by this verification.

## Observations (non-binding, out of the §13A AC set)

- **O1 — JS-disabled reachability.** The create `<dialog>` needs `showModal()` (JS) to open; without JS the `data-modal-open` anchors do nothing, so a strictly JS-disabled operator cannot reach the create form. The AC wording is HTMX/no-HTMX (met); a `<noscript>`/plain-link fallback would strengthen ADR-003 progressive enhancement if desired.
- **O2 — Unused service functions.** `identity/services.py::create_user` (`:268`) and `::update_user` (`:329`) are defined but not called by the API or UI (both re-implement the logic and call `record_*` directly). Not a defect, but duplicated validation/guard logic is a drift risk (F4 remediation chose the direct-audit route).
- **O3 — Out-of-scope `_modal.html` call site.** `ui/templates/ui/case_share_list.html:45` uses `modal_id="revoke-share-"|add:item.share.id` — the `add` filter cannot concatenate `str`+UUID, so the id is empty (the exact D3(b) bug class fixed in the Settings templates). This file is outside §13A scope (case-share feature); flagging for §13B/its owning wave.

## Blockers

None. No in-scope acceptance criterion or finding is Not Met.

## Recommendations

1. **Serialise `delete_organisation`** with the same active-superAdmin lock (or a single conditional delete) to close D2; add a concurrency regression test mirroring the F5 wrapper tests.
2. **Re-run `security-auditor`** against the 6.3A delta and record the artefact (closes D3 / §13A process item).
3. Either implement edit-in-`<dialog>` or record D1 as an accepted deviation in §12 of the plan (deviations must be recorded).
4. Consider a no-JS entry point for "New organisation"/"New user" (O1) and consolidating the API/UI mutations onto the shared `identity/services.py` functions (O2) to remove drift.
5. §13B remains the correct home for the repo-wide inline `<script>`/`style=""` cleanup (`playbook_form.html`, `observable_detail.html`, `case_template_*.html`, `playbook_detail.html`, `case_tags.html`, `observable_list.html`, `404.html`) and for O3.

---
*Read-only verification. No code, tests, or configuration were modified; this report is the only artefact written.*
