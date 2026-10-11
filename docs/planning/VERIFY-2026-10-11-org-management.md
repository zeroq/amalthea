# Verification Report: PLAN-2026-10-10 — Organisation & User Management (SuperAdmin Settings)

**Plan:** `docs/planning/PLAN-2026-10-10-org-management.md`  
**Verified:** 2026-10-11 23:15 UTC  
**Status:** Pass  
**Git State:** Working tree uncommitted on `main` (vs `4c4c5f8`), all changes present  

## Summary

- **Total ACs:** 24 (O1-a–d, O2-a–e, O3-a–e, O4-a–e, O5-a–b)
- **Met:** 24
- **Not Met:** 0
- **Deviated:** 0

## Test Results
- `tests/conformance/test_org_user_management.py`: 69 passed
- `tests/conformance/test_settings_ui.py`: 37 passed  
- `tests/conformance/test_ui_urls_smoke.py`: 5 passed
- `tests/conformance/test_tenant_scoping.py`: 91 passed (relevant subset)
- `make check`: 1218 passed, 3 skipped

## Criterion-by-Criterion

### O1 — Organisations API + permissions

#### AC O1-a: Non-superAdmin → uniform 404 on every mutating route and no directory
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestOrgManagementO1::test_org_collection_get_non_superadmin_returns_own_only` - GET collection for non-superAdmin returns only own org (no directory of all orgs)
- `tests/conformance/test_org_user_management.py::TestOrgManagementO1::test_org_mutations_non_superadmin_404` - POST/PATCH/DELETE to organisation detail return 404 for non-superAdmin
- `identity/views.py:94-101` - `_require_superadmin` returns uniform 404 `"Resource not found"` for non-superAdmin
- `identity/views.py:199-238` - organisation_detail denies DELETE for non-superAdmin with 404; superAdmin required for mutate ops

#### AC O1-b: Create/edit/duplicate-name happy+error paths
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestOrgManagementO1::test_org_create_ok_by_superadmin` - SuperAdmin can create org
- `tests/conformance/test_org_user_management.py::TestOrgManagementO1::test_org_create_duplicate_name_400` - Duplicate name returns 400
- `tests/conformance/test_org_user_management.py::TestOrgManagementO1::test_org_create_blank_name_400` - Blank name returns 400
- `tests/conformance/test_org_user_management.py::TestOrgManagementO1::test_org_update_ok_by_superadmin` - SuperAdmin can update org name/description
- `tests/conformance/test_org_user_management.py::TestOrgManagementO1::test_org_update_duplicate_name_400` - Duplicate name on update returns 400
- `identity/views.py:174-193` (POST create), `identity/views.py:261-298` (superAdmin PATCH), `identity/views.py:240-258` (own-org PATCH)

#### AC O1-c: Exactly one `AuditLog` row per mutation with the right action
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestOrgManagementO1::test_org_mutations_audited` - Verifies audit log entries for create/update/delete with correct actions
- `identity/audit.py:136-167` - `record_organisation_change` writes audit with action/target_type/target_id
- `identity/views.py:190-193` (org created), `identity/views.py:291-298` (org updated), `identity/views.py:328-349` + `identity/services.py:116-125` (org deleted)

#### AC O1-d: Own-org `GET`/`PATCH` unchanged for a plain member
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestOrgManagementO1::test_own_org_get_patch_unchanged_for_member` - Plain member can GET/PATCH their own org; non-superAdmin cannot access other orgs
- `identity/views.py:211-221` - Non-superAdmin branch allows GET/PATCH only when `org.pk == getattr(user, "org_id", None)`, else 404

### O2 — Organisation deletion guard

#### AC O2-a: Blocked-409 with counts when org owns case/alert/observable/token; no member option overrides
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestOrgDeleteO2::test_delete_blocked_by_case_409`, `test_delete_blocked_by_alert_409`, `test_delete_blocked_by_observable_409`, `test_delete_blocked_by_ingestion_token_409`
- `identity/services.py:34-41` - `organisation_blockers` checks cases/alerts/observables/ingestion_tokens (PROTECT FKs)
- `identity/services.py:72-76` - Pre-flight checks blockers first regardless of member action

#### AC O2-b: Empty-org delete succeeds, shares removed, audit preserved
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestOrgDeleteO2::test_delete_empty_org_succeeds`, `test_delete_removes_shares_and_audits`
- `identity/services.py:96-125` - Atomic deletion writes org deleted audit with target_org=None and captures target_id before deletion

#### AC O2-c: `members=reassign` moves every member then deletes
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestOrgDeleteO2::test_delete_reassign_moves_members_then_deletes`
- `identity/services.py:100-106,121-125` - Inside atomic block: reassign members, delete org, audit

#### AC O2-d: `members=delete` removes the accounts then deletes
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestOrgDeleteO2::test_delete_member_accounts_then_deletes_org`
- `identity/services.py:106-115` - Records user.deleted audits, deletes member accounts

#### AC O2-e: No partial state on failure
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestOrgDeleteAtomicity::test_delete_atomic_reassign_not_partial_on_race`, `test_delete_atomic_member_delete_not_partial_on_race`
- `identity/services.py:97-120` - Member handling and org.delete() in transaction.atomic(); ProtectedError caught to prevent partial commits

### O3 — Users API

#### AC O3-a: Non-superAdmin → uniform 404
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestUserManagementO3::test_user_collection_get_non_superadmin_404`, `test_user_mutations_non_superadmin_404`
- `identity/views.py:365-368,460-464` - Collection and PATCH/DELETE require superAdmin

#### AC O3-b: Create with validators + duplicate rejection
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestUserManagementO3::test_user_create_ok_by_superadmin`, `test_user_create_unknown_org_400`, `test_user_create_password_validation_fails_400`, duplicate username/login tests
- `identity/views.py:380-439` - Validation + Django password validators

#### AC O3-c: Cannot delete self or the last superAdmin
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestUserManagementO3::test_user_delete_self_400`, `test_user_delete_last_active_superadmin_400`, `test_user_delete_other_superadmin_ok_if_multiple`, `test_user_delete_inactive_superadmin_ok`, `test_user_delete_other_last_active_superadmin_400`
- `identity/views.py:571-590` - Guards enforce rules

#### AC O3-d: Role/org changes audited
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestUserManagementO3::test_user_update_role_audited`, `test_user_update_org_audited`, `test_user_update_is_active_audited`
- `identity/views.py:472-568` + `identity/audit.py:169-198`

#### AC O3-e: `login` derived from `username`; no blank login
**Status:** Met  
**Evidence:**
- `tests/conformance/test_org_user_management.py::TestUserManagementO3::test_user_create_sets_login_from_username`
- `identity/views.py:433-434` - save() derives login; duplicate checks prevent collisions

### O4 — Settings UI

#### AC O4-a: Nav hidden for non-superAdmin; routes 404 for non-superAdmin
**Status:** Met  
**Evidence:**
- `tests/conformance/test_settings_ui.py::TestSettingsNav` - nav hidden/visible correctly; routes return 404 for non-superAdmin
- `ui/templates/ui/base.html:66-72`, `ui/views.py:1435-1444`

#### AC O4-b: Full CRUD on both pages via UI
**Status:** Met  
**Evidence:**
- `tests/conformance/test_settings_ui.py::TestOrganisationsCRUD`, `TestUsersCRUD` - all CRUD operations pass

#### AC O4-c: Destructive actions use `<dialog>` (Esc/Tab-trap), never native `confirm()`
**Status:** Met  
**Evidence:**
- `tests/conformance/test_settings_ui.py::TestSettingsUIDialogs` - org/user create/delete use dialog elements
- `ui/templates/ui/_modal.html` - dialog with ESC/Tab-trap, no confirm()/alert()/prompt()

#### AC O4-d: Org-delete modal shows counts, disables confirm when blocked, offers reassign-or-delete for members
**Status:** Met  
**Evidence:**
- `tests/conformance/test_settings_ui.py::TestOrgDeleteModalBlockers` - modal shows blockers and member reassign options
- `ui/templates/ui/settings_organisations.html:172-276`

#### AC O4-e: Keyboard shortcut + cheat-sheet updated
**Status:** Met  
**Evidence:**
- `tests/conformance/test_settings_ui.py::TestKeyboardShortcuts` - `,` shortcut and Settings in cheatsheet
- `ui/static/ui/keys.js:19,30`

### O5 — Docs + admin

#### AC O5-a: Docs match behaviour with code pointers
**Status:** Met  
**Evidence:**
- Docs updated: `docs/spec/identity-auth.md`, `docs/spec/data-model.md`, `docs/spec/api.md`, `docs/spec/deviations.md`, `README.md`, `TODO.md`

#### AC O5-b: No stale "deferred" text
**Status:** Met  
**Evidence:**
- O1-O4 implemented; backlog items (ingestion tokens, audit log viewer) correctly tracked in TODO per Q1/Q8

## Corrections Validated (D1–D4)

- **D1:** `GET /api/v1/user/{idOrLogin}` returns `user_json`; single route in `identity/urls.py`; regression tests pass
- **D2:** Uniform 404 body is "Resource not found" (no "Not found not found"); see `_require_superadmin`
- **D3:** Settings templates fixed (URL as variables, stringified UUIDs, correct data attributes, form associations) - all UI tests pass
- **D4:** `identity/services.py::delete_organisation` is atomic (transaction.atomic) and validates reassign targets; UI mirrors validation; atomicity tests pass

## Additional Checks

- **API contract regression:** `TestOrgUserSerializerRegression` all pass (user_json unchanged, organisation/user admin JSON fields correct)
- **Cross-org/tenant scoping:** tenant_scoping tests all pass (91 passed)
- **Full suite:** `make check` → 1218 passed, 3 skipped (no regressions)

## Residual Risk

**Low.** All ACs met with corrections applied. Comprehensive test coverage.

## Blockers

None.

## Recommendations

None. Feature is ready.
