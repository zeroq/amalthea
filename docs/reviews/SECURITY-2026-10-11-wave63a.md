# Security Re-Audit — Wave 6.3A (Org/User hardening + CSP-safe Settings modals)

**Date:** 2026-10-11 (round 2 — follow-up verification after remediation)
**Auditor:** security-auditor (read-only; no code/tests/other docs modified — this report is the only artefact written)
**Scope:** the Wave 6.3A remediation of `SECURITY-2026-10-11-org-user-management.md` (F3, F5, F6, F7, F8, F9, F10), plus the round-1 residuals (N1 org-delete race, N2 admin `secret_hash`, N3 share 500), plus a regression check of F1/F2/F4.
**Baseline:** working tree vs `4c4c5f8` (everything reviewed is uncommitted on `main`).
**Files read:** `identity/services.py`, `identity/views.py`, `identity/admin.py`, `identity/audit.py`, `identity/models.py`, `identity/urls.py`, `ui/views.py` (Settings + share regions), `ui/urls.py`, `ui/templates/ui/_modal.html`, `ui/templates/ui/settings_*.html`, `ui/templates/ui/_settings_*.html`, `ui/static/ui/modal.js`/`modal.css`, `ui/templates/ui/base.html`, `amalthea/settings/base.py`/`prod.py`, and the tests (`test_org_user_hardening.py`, `test_settings_ui.py`, `test_org_user_management.py`, `test_tenant_scoping.py`).
**Method:** source review + targeted dynamic verification (admin-form introspection and `makemigrations --check` via `django.setup()`; targeted pytest).
**Gate observed:** `pytest tests/conformance/test_org_user_hardening.py tests/conformance/test_settings_ui.py tests/conformance/test_org_user_management.py -q` → **139 passed**; the selected hardening/scoping tests (incl. the three new ones) → **23 passed**.

## Verdict

**CLEAR FOR SECURITY SIGN-OFF.**

All previously reported findings (F3/F5/F6/F7/F8/F9/F10) and the three round-1 residuals (N1/N2/N3) are now genuinely resolved, as verified below. F1/F2/F4 show no regression. There are no open Critical/High/Medium security issues in this wave; the remaining items are accepted low-severity residual risks (no DB-level superAdmin invariant; the Postgres lock semantics are untested under SQLite).

## Verification matrix (round 2)

| # | Claimed | Verdict | Evidence |
|---|---------|---------|----------|
| F1 | fixed earlier | **Holds (no regression)** | `identity/services.py` `self_lockout`/`last_superadmin` guards; org-delete tests pass |
| F2 | fixed earlier | **Holds (no regression)** | audit rows written inside `transaction.atomic()` before `org.delete()`; race/reassign audit tests pass |
| F3 | fixed | **FIXED** | no `innerHTML`/`escapejs`/string-built HTML in settings templates or `_modal.html`; autoescaping relied on; tests pass |
| F4 | fixed earlier | **Holds (no regression)** | UI create/edit org+user call `record_*_change`; 4 audit tests pass |
| F5 | fixed | **FIXED** (incl. org path) | user paths locked via `serializes_superadmin_changes`/`locks_active_superadmins`; `delete_organisation` now also decorated (below) |
| F6 | fixed | **FIXED** | guard before resolution for PATCH/DELETE; identical `_not_found("Resource")` bodies; test passes |
| F7 | fixed | **FIXED** | `as_uuid`/`resolve_organisation` on Settings inputs; `<uuid:>` converters; truncate-then-unique + `IntegrityError`; tests pass |
| F8 | fixed | **FIXED** | `parse_bool` accepts bool + explicit `"true"/"false"`, 400 otherwise; tests pass |
| F9 | was Not fixed | **FIXED (round 2)** | `exclude = ("secret_hash",)` + `has_add_permission → False`; verified dynamically (below) |
| F10 | fixed | **FIXED** | external `modal.js`/`modal.css`; no inline script/style; CSP unchanged (`script-src 'self'`) |
| N1 | residual | **FIXED (round 2)** | `delete_organisation` locked (below) |
| N2 | residual | **FIXED (round 2)** | same as F9 |
| N3 | residual | **FIXED (round 2)** | `case_share_list` uses `resolve_organisation` + redirect (below) |

## Round-2 verification detail

### F9/N2 — `secret_hash` now excluded from the admin (verified)

`identity/admin.py:48-69`: `IngestionTokenAdmin` now sets `exclude = ("secret_hash",)` and `has_add_permission() -> False`, keeping `readonly_fields = ("prefix", "last_used_at")`.

Dynamic proof (test settings, `django.setup()`):
```
exclude: ('secret_hash',)
readonly_fields: ('prefix', 'last_used_at')
has_add_permission: False
get_fields:          ['source','organisation','name','revoked_at','prefix','last_used_at']
form base_fields:    ['source','organisation','name','revoked_at']
model secret_hash editable: True        # model unchanged; exclusion is admin-only
makemigrations --check --dry-run -> "No changes detected"
```
So the hash is no longer an editable (or read-only) admin form field, `get_fields`/`base_fields` omit it, **no `editable` model-field migration was introduced** (the field remains `editable=True` at the model layer, which is fine — the admin is the only form bound to the model today). `test_admin_does_not_display_secret_hash` now asserts `get_fields`/`get_form().base_fields`, not just `readonly_fields`, and passes.

### F5/N1 — org-delete guard is now serialised (verified)

`identity/services.py` `delete_organisation` is decorated `@locks_active_superadmins`. The decorator (`services.py:101-105`) opens `transaction.atomic()` and calls `lock_active_superadmins()` **before** invoking the wrapped function, so the row lock is taken before the blocker pre-flight and the `members="delete"` self/last-superAdmin checks, and is held across the inner member mutations, audits and `org.delete()` until commit (it nests cleanly with the function's own `atomic()`).

Both callers route through the decorated service, so both are covered:
- API: `identity/views.py:327` (`_delete_organisation` → `delete_organisation`).
- UI: `ui/views.py:1588` (`settings_organisations_delete` → `delete_organisation`).

On Postgres (READ COMMITTED) the second concurrent delete now blocks on the locked active-superAdmin rows, then re-reads the post-commit count, closing the two-concurrent-deletes → zero-admins race described in round 1. `test_delete_organisation_lock_wrapper_acquires_superadmin_lock` (and the update-user equivalent) pass. Note: the lock is a no-op on SQLite, so the suite proves only that the wrapper is applied; the concurrency guarantee itself rests on source review of the Postgres semantics.

### N3 — share path no longer 500s on a malformed org id (verified)

`ui/views.py:1211-1216`: the POST now calls `resolve_organisation(org_id)` and, when it returns `None`, emits an error message and `redirect`s; there is no `get_object_or_404(Organisation, pk=<POST value>)` left on that path (the remaining `get_object_or_404` calls in `ui/views.py` are unrelated to Settings/share org input). `test_ui_case_share_list_malformed_org_id_does_not_500` passes (302, no share created, no `ValidationError`).

## Re-confirmed from round 1

- **F3** — grep of `settings_*.html`, `_settings_*.html` and `_modal.html` shows no `innerHTML`/`escapejs`/`document.write`/`outerHTML`/`insertAdjacentHTML` (the only textual match is a comment). Values use Django autoescaping; the markup-escape test still passes.
- **F6** — `identity/views.py:469-477` still runs `_require_superadmin` before `_resolve_user` for PATCH/DELETE; missing and existing both return `_not_found("Resource")`; uniformity test passes.
- **F7** — `ui/views.py:1577` (org-delete `target_org`), and the create/edit org fields still use `resolve_organisation`; names truncated to 200 before uniqueness with `IntegrityError` caught; malformed/oversize tests pass.
- **F8** — `parse_bool` still used in `user_collection` (`identity/views.py:430`) and `_update_user` (`:539`); boolean tests pass.
- **F10** — `ui/templates/ui/base.html:22,105` loads `ui/modal.css`/`ui/modal.js`; `_modal.html` remains markup-only; CSP is unchanged and strict.
- **F1/F2/F4** — guards, atomic audit ordering and UI audit calls are intact; their tests pass.

## Remaining residual risk (accepted, non-blocking)

- **No DB-level "≥1 active superAdmin" invariant.** All in-app mutation paths are now consistently locked, but a superuser via the Django admin `User` change form (pre-existing), a management command, or direct SQL can still zero the administrators. A `CHECK`/trigger would make the invariant unconditional.
- **Postgres lock semantics untested in CI.** SQLite no-ops `SELECT … FOR UPDATE`, so the concurrency guarantee is source-reviewed, not exercised; a `--ds=amalthea.settings.test_pg` concurrency test would close this.
- **Share target not validated against `available_orgs`.** An owner can still POST an org that is their own or already shared (idempotent `update_or_create`); a business-logic nit, not a security break.
- Minor: UI edit/delete superAdmin guards run inside the lock (an authenticated non-super POST briefly takes the row locks); legacy non-super own-org `PATCH` writes no audit row (pre-existing); `update_user` always includes `"password"` in `update_fields` (cosmetic).

*End of report.*
