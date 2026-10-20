# Security Audit Report: Org-Scoped User Management

**Feature:** Org-scoped User Management (Settings → Users)  
**Date:** 2026-10-20  
**Auditor:** security-auditor (adversarial review)  
**Scope:** Uncommitted implementation in `/Users/goebej/proggen/amalthea`  
**Method:** Static code analysis of `git diff`, cross-referenced with PLAN-2026-10-20, ADR-010, and VERIFY-2026-10-20. No runtime testing.

---

## Scope & Method

Reviewed the uncommitted changes enabling `admin` (own org) and `superAdmin` (all orgs) access to Settings → Users (list, create, edit, delete). Focused on **tenant isolation** (cross-org leakage, existence oracles) and **privilege escalation** (role minting, target-role guards, org relocation, self-lockout, last-superAdmin bypass). Also checked audit integrity, CSRF/method safety, mass-assignment, and secret hygiene.

Key files inspected:
- `ui/views.py` — `settings_users` (~4144), `settings_users_edit` (~4305), `settings_users_delete` (~4461)
- `ui/templates/ui/settings_users.html`, `_settings_user_create_form.html`, `settings_user_form.html`, `_settings_nav.html`, `base.html`
- `identity/services.py::delete_user` (audit delegation)
- `identity/scoping.py::is_admin`, `resolve_organisation`
- `core/enums.py` — `USER_ROLE_VALUES`, `SUPER_ADMIN_ROLE`, `ADMIN_ROLE`, `VIEWER_ROLE`, `CASE_CLASSIFICATION_ROLES`
- `tests/conformance/test_settings_users_scoping.py` (behavioural contracts)

---

## Findings Table

| ID | Severity | Area | Description | Evidence | Recommended Fix |
|----|----------|------|-------------|----------|-----------------|
| **SA-01** | **Medium** | Escalation (edit) | Admin edit allow-list guard uses `if role and role not in allowed_roles` — an empty-string `role=""` bypasses the allow-list check. Combined with `if role and user.role != role` (line 4366), no role change occurs, so **no escalation results**, but the validation path is inconsistent with create (which rejects empty role via `USER_ROLE_VALUES` membership). | `ui/views.py:4342-4345`, `4366` | Make the edit guard unconditional: `if role not in allowed_roles:` (empty string fails `USER_ROLE_VALUES` at line 4338 anyway). Or explicitly reject empty role. |
| **SA-02** | **Low** | Escalation (edit) | Admin self-demote: an `admin` editing themselves is blocked at line 4325-4326 (target role = `admin` → 404). This is correct but **implicit** — the guard is "admin cannot edit admin/superAdmin targets" rather than an explicit "cannot demote self". If Q1 were reversed (admin allowed to create admin), this would become a gap. | `ui/views.py:4325-4326` | Add explicit self-demote guard for admin (mirroring superAdmin at line 4369-4375) for defence-in-depth. |
| **SA-03** | **Low** | Escalation (edit) | Admin self-deactivate: same as SA-02 — blocked implicitly by target-role 404. No explicit guard. If target-role guard changed, self-deactivate would become possible. | `ui/views.py:4392-4399` (only checks `SUPER_ADMIN_ROLE`) | Add explicit self-deactivate guard for admin. |
| **SA-04** | **Info** | Tenant isolation (list) | SuperAdmin list uses per-org cap (`USERS_PER_ORG_CAP=200`) with honest disclosure. An org with >200 users shows only newest 200. Not a vulnerability (disclosure is honest), but a **visibility limitation** for superAdmin. | `ui/views.py:4141`, `4235-4242`, `settings_users.html:73-75` | Document as known limitation; consider per-org pagination in future. |
| **SA-05** | **Info** | Audit | Delete audit delegated to `identity.services.delete_user` (line 4499) which writes `user.deleted` with `target_org=user.org`. Verified: no double-audit, correct `target_org`. Cross-org mutations distinguishable via `actor_org != target_org`. | `ui/views.py:4499`, `identity/services.py:465-471` | No fix needed — delegation is correct and reduces duplication. |
| **SA-06** | **Info** | CSRF / Method safety | `settings_users_edit` lacks `@require_POST` (unlike `settings_users_delete` at line 4463). Mutation only occurs in `if request.method == "POST"` block, so **no GET mutation**, but the decorator would be a harmless belt-and-suspenders. | `ui/views.py:4305-4307` vs `4461-4464` | Add `@require_POST` to `settings_users_edit` for consistency (defence-in-depth). |
| **SA-07** | **Info** | Secrets | No secrets, hardcoded credentials, or API keys introduced. `USERS_PER_ORG_CAP=200` is a non-secret constant. Passwords handled via Django's `set_password`/`validate_password`. | `git diff` — no secret-like patterns | No fix needed. |

---

## Adversarial Questions — Explicit Answers

### 1. Existence Oracle
**Guarded.**  
- **Edit (GET/POST):** Admin uses `get_object_or_404(User, pk=user_id, org_id=request.user.org_id)` (line 4322). The `org_id` filter is applied **before** the lookup, so a cross-org user and a non-existent user both return the same uniform 404 (`render(request, "ui/404.html", status=404)`). No timing difference, no error message leakage.  
- **Delete (POST):** Same pattern at line 4479 — `org_id` filter before `get_object_or_404`.  
- **Create (POST):** Admin ignores any posted `org_id` entirely (line 4188-4189: `org = request.user.org; # Silently ignore any posted org_id`). Whether the posted org exists or not, behaviour is identical — no oracle.  
- **Error messages / redirects / status codes:** All cross-org and non-existent paths return 404 with the same template. No distinguisher.

### 2. Escalation via Create
**Guarded.**  
- **Normal POST (`role=admin`/`superAdmin`):** Rejected at line 4175-4178 — admin allow-list is `{VIEWER_ROLE, "analyst"}`; `role not in allowed_roles` triggers error.  
- **Omitting `role`:** Defaults to `"analyst"` at line 4160 (`role = request.POST.get("role") or "analyst"`). Safe.  
- **Empty `role` (`role=""`):** Fails `role not in USER_ROLE_VALUES` at line 4171 (empty string not in tuple). Rejected before allow-list.  
- **Invalid value:** Same — fails `USER_ROLE_VALUES` membership.  
- **Case tricks (`"Admin"`, `"SUPERADMIN"`):** Exact string match required; `"Admin" != "admin"`, `"SUPERADMIN" != "superAdmin"`. Rejected by `USER_ROLE_VALUES` check.  
- **Allow-list position:** Checked **after** `USER_ROLE_VALUES` but **before** any write (line 4175-4178 vs user construction at line 4203). No bypass.  
- **Default role at `User(...)` construction:** The `role` variable is fully validated before line 4203; no fallback default in the model constructor path.

### 3. Escalation via Edit
**Guarded.**  
- **Promote existing user to admin/superAdmin:** Two layers:  
  1. **Target-role guard** (line 4325-4326): If target user's *current* role is `admin` or `superAdmin`, admin gets 404 **before** form renders. Cannot even reach the edit form for such targets.  
  2. **Posted-role allow-list** (line 4342-4345): `if role and role not in allowed_roles` — rejects `admin`/`superAdmin` if `role` is truthy.  
- **Can admin edit a target whose role is admin/superAdmin?** No — 404 at line 4325-4326.  
- **Allow-list `if role and ...` condition:** The `if role and` means an **empty string bypasses the allow-list**, but line 4366 (`if role and user.role != role:`) also requires truthy `role` to apply any change. So `role=""` → no role change → safe. However, inconsistent with create (which rejects empty role via `USER_ROLE_VALUES`). See SA-01.  
- **Admin POSTs `role=admin` on own-org target:** Caught by allow-list at line 4344 (role is truthy, not in allowed_roles). Error message returned, no change persisted.

### 4. Org Relocation
**Guarded.**  
- **Template:** Admin edit form omits org `<select>`, renders hidden `<input name="org" value="{{ user_obj.org_id }}">` (line 47-50 of `settings_user_form.html`). Create modal does the same (line 39-42 of `_settings_user_create_form.html`).  
- **Server-side (edit):** Line 4387-4390 — `if is_superadmin and org and user.org_id != org.id: user.org = org` — **only superAdmin** executes org move. Admin branch falls through to comment "silently ignore any org change".  
- **Server-side (create):** Line 4187-4189 — admin pins to `request.user.org`, ignores posted `org_id`.  
- **Malicious POST with `org=<other_org_id>`:** Ignored for admin; processed for superAdmin (by design, Q3). No relocation possible for admin.

### 5. Self-Lockout / Last-SuperAdmin Bypass
**Guarded (with race-window mitigation).**  
- **Self-delete:** Line 4486-4488 — `if user.pk == request.user.pk` blocks for **all roles** (admin + superAdmin).  
- **Self-demote (superAdmin):** Line 4369-4375 — explicit guard for superAdmin.  
- **Self-demote (admin):** **Implicitly blocked** by target-role 404 at line 4325-4326 (admin editing self → target role = `admin` → 404). No explicit guard, but effective. See SA-02.  
- **Self-deactivate (superAdmin):** Line 4393-4399 — explicit guard.  
- **Self-deactivate (admin):** **Implicitly blocked** by target-role 404 (same as self-demote). See SA-03.  
- **Last active superAdmin demote/deactivate/delete:** Lines 4377-4383, 4401-4407, 4491-4495 — all check `active_superadmins == 1` and block.  
- **Race window:** `@serializes_superadmin_changes` decorator (lines 4307, 4464) wraps mutating requests in `transaction.atomic()` + `lock_active_superadmins()` which uses `select_for_update()` on active superAdmin rows. On PostgreSQL this serializes concurrent demote/deactivate/delete; on SQLite (dev) it's a no-op (documented in `identity/services.py:86-87`). **Mitigation present and correct for production target.**

### 6. IDOR / Mass-Assignment
**Guarded.**  
- **Create:** Explicitly handles only `username`, `email`, `password`, `role`, `org`, `is_active` (lines 4157-4162). No `is_staff`, `is_superuser`, `date_joined`, `last_login`, etc.  
- **Edit:** Explicitly handles only `email`, `role`, `org`, `is_active`, `password` (lines 4329-4333). No `is_staff`, `is_superuser`.  
- **`is_active` handling:** Admin can deactivate users in their org — **by design** (soft lockout vector but intended administrative capability). No mass-assignment of `is_active` to unintended targets due to org scoping.  
- **No unexpected fields writable.**

### 7. Audit Integrity
**Complete.**  
- **Create:** Line 4214-4220 — `record_user_change(..., target_org=user.org)` ✓  
- **Edit:** Line 4424-4430 — `record_user_change(..., target_org=user.org)` ✓  
- **Delete:** Line 4499 → `identity.services.delete_user` (line 465-471) — captures `target_org=user.org` **before** `delete()`, writes `user.deleted` ✓  
- **Double-audit?** No — delete view delegates to service; does not also call `record_user_change` in the view.  
- **`target_org` = target user's org:** Yes — `user.org` in all three paths. For superAdmin cross-org mutations, `actor_org != target_org` in the audit row, which is the designed cross-org marker (ADR-010 §4).  
- **All mutating paths audited:** Yes.

### 8. CSRF / Method Safety
**Guarded.**  
- **Create:** `settings_users` POST block (line 4156) — protected by Django's `CsrfViewMiddleware` (global).  
- **Edit:** `settings_users_edit` POST block (line 4328) — protected by CSRF middleware. **Missing `@require_POST`** (SA-06) but no GET mutation.  
- **Delete:** `settings_users_delete` has `@require_POST` (line 4463) + CSRF middleware.  
- **No state change on GET:** All mutations inside `if request.method == "POST"` blocks.  
- **CSRF token in forms:** Templates use `{% csrf_token %}` via modal form inclusion (`_modal.html`).

### 9. Secrets
**Clean.**  
- No secrets, API keys, tokens, passwords, or private keys in the diff.  
- `USERS_PER_ORG_CAP = 200` is a non-secret configuration constant.  
- Passwords handled via Django's secure `set_password` / `validate_password`.  
- `.env`/secrets hygiene unchanged.

---

## Overall Risk Verdict

### **COMMIT WITH NOTES**

**Severity counts:** Critical: 0 | High: 0 | Medium: 1 | Low: 2 | Info: 4

**Rationale:** The implementation correctly enforces tenant isolation (uniform 404 resolver, org-pinned create, org-scoped list) and privilege escalation guards (server-side role allow-list, target-role 404, org-move restriction). All existing self-lockout and last-superAdmin invariants are preserved with row-lock serialization for race safety. Audit coverage is complete with correct `target_org`. No secrets introduced.

**Top findings requiring attention before/at commit:**
1. **SA-01 (Medium):** Edit allow-list guard uses `if role and ...` — inconsistent with create; tighten to unconditional check.
2. **SA-02/SA-03 (Low):** Admin self-demote/self-deactivate rely on implicit target-role 404; add explicit guards for defence-in-depth.
3. **SA-06 (Info):** Add `@require_POST` to `settings_users_edit` for consistency.

**Non-blocking observations (SA-04, SA-05, SA-07):** Documented for awareness; no code change required.

---

## Appendix: Key Guard Locations (for reviewer cross-check)

| Guard | File | Lines |
|-------|------|-------|
| Role gate (`_require_admin_ui`) | `ui/views.py` | 3941-3950 |
| Admin list scoping | `ui/views.py` | 4279-4283 |
| Admin create org pinning | `ui/views.py` | 4187-4189 |
| Create role allow-list (admin) | `ui/views.py` | 4175-4178 |
| Edit target-role 404 (admin) | `ui/views.py` | 4325-4326 |
| Edit role allow-list (admin) | `ui/views.py` | 4342-4345 |
| Edit org-move restriction | `ui/views.py` | 4387-4390 |
| Delete target-role 404 (admin) | `ui/views.py` | 4482-4483 |
| Self-delete guard | `ui/views.py` | 4486-4488 |
| Last-superAdmin guards (edit role) | `ui/views.py` | 4377-4383 |
| Last-superAdmin guards (edit active) | `ui/views.py` | 4401-4407 |
| Last-superAdmin guards (delete) | `ui/views.py` | 4491-4495 |
| Row-lock serialization | `identity/services.py` | 80-127 |
| Audit create | `ui/views.py` | 4214-4220 |
| Audit edit | `ui/views.py` | 4424-4430 |
| Audit delete (service) | `identity/services.py` | 465-471 |

---

## Addendum — Remediation (2026-10-20, planner)

Applied after the audit, before commit:

- **SA-01 (Medium) → FIXED.** `settings_users_edit` now rejects a present-but-empty/forged `role` instead
  of silently ignoring it, matching the create path:
  ```python
  allowed_roles = {VIEWER_ROLE, "analyst"}
  ...
  if not is_superadmin and "role" in request.POST and role not in allowed_roles:
      errors["role"] = ...
  ```
  An *absent* `role` key still means "leave unchanged" (partial posts such as a password reset keep
  working). Regression tests added: `TestEscalationGuards::test_admin_empty_role_post_is_rejected` and
  `test_admin_absent_role_post_leaves_role_unchanged`. `make check` green; scoping suite 38 passed.

- **SA-06 (Info) → NOT APPLIED (false positive).** `settings_users_edit` intentionally serves a **GET**
  form (it renders `settings_user_form.html` on GET) as well as handling POST, so `@require_POST` would
  break the edit form. The method-safety property the auditor wanted (no GET mutation) already holds
  because all mutation is inside `if request.method == "POST"`. No change.

- **SA-02 / SA-03 (Low) → ACCEPTED as documented defence-in-depth notes.** Admin self-demote /
  self-deactivate remain implicitly blocked by the target-role 404 guard (an admin editing themselves
  targets a role-`admin` user → 404). Adding explicit guards is optional hardening; deferred as a
  backlog note rather than expanded scope for this wave.

**Post-remediation verdict: CLEAR** (no Critical/High/Medium open; SA-02/03 tracked as Low backlog).

---

## Addendum 2 — Role-visibility monotonicity (2026-10-20, user-requested)

New requirement: **a lower role must never see a higher-role user.** The admin list now adds
`.exclude(role=SUPER_ADMIN_ROLE)` (on top of own-org scoping), so a `superAdmin` in the admin's org is not
listed nor counted.

- **SA-08 (Info) — closed.** This is the *visibility* half of a property whose *enforcement* half already
  existed: edit/delete of a `superAdmin` target returns a uniform 404 (target-role guard). The two halves
  are now both regression-tested. Confirmed the hide does not weaken enforcement: a crafted POST to a
  superAdmin id still 404s (`test_admin_cannot_edit_superadmin_target`,
  `test_admin_cannot_delete_superadmin_target`). Only `superAdmin` is hidden (admins still see admins);
  the superAdmin's own view is unchanged. No new attack surface (the excluded rows were already
  uneditable/un-deletable).
