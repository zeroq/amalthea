# PLAN-2026-10-10 — Organisation & User Management (SuperAdmin Settings)

**Status:** APPROVED 2026-10-10 — decisions locked (§4). **O1–O4 implemented 2026-10-11** (`make check` ✓ — 1218 passed / 3 skipped; see §12 for corrections D1–D4); O5 (docs + independent verification) in progress.
**Owner:** planner (design) → django-backend (API/permissions/deletion guard) + django-frontend (Settings UI) → verifier / security-auditor / qa-tester.
**Scope:** a `superAdmin`-only **Settings** area with two pages — **Organisations** and **Users** — (list / add / edit / delete) plus the matching API, on top of the tenant-isolation work (ADR-004).
**Non-scope (backlog):** Ingestion-token management and an Audit-log viewer — recorded in `TODO.md`.
**Related:** `identity/views.py` (`organisation_collection`/`organisation_detail`, `user_current`/`user_detail`, `_own_org`), `identity/permissions.py` (`IsSuperAdmin`), `core/serializers.py` (`organisation_json`/`user_json`), `identity/audit.py`, `ui/urls.py`, `ui/templates/ui/base.html` (rail), `ui/static/ui/keys.js`, TODO §6.12.

---

## 1. Summary

Amalthea now enforces strict multi-tenant isolation (ADR-004), but:
- an organisation can only be created in a `manage.py shell` or via the TheHive-compat register path, and the only API surface (`GET|PATCH /api/v1/organisation/{idOrName}`) is scoped to the **caller's own** tenant — deliberately, so it can never become a tenant directory;
- there is no product surface to create/read/update/delete tenants or users.

This plan adds a `superAdmin`-only **Settings** area with two pages — **Organisations** and **Users** — reusing the existing tenancy model (no new tenant concept). It extends the org serializer with the counts an operator needs, routes every mutation through the role guard and the audit log, and — critically — defines **safe deletion** given that `Case`/`Alert`/`Observable`/`IngestionToken` now `PROTECT` their organisation and `User.org` is `SET_NULL`.

## 2. Goals

- **G1.** A `superAdmin` can list **all** organisations with a per-org summary (member / case / alert / observable counts, created date).
- **G2.** A `superAdmin` can **create** an organisation (name + description), with duplicate-name handling.
- **G3.** A `superAdmin` can **edit** an organisation (rename / description) with the same validation as `PATCH` today.
- **G4.** A `superAdmin` can **delete** an organisation — only when it owns no tenant data, and when its members are either **reassigned** to another org or **deleted** with it. Blocked deletions explain exactly why.
- **G5.** A `superAdmin` can **list all users** and **create / edit / delete** them (email, role, org, active, optional password reset).
- **G6.** The Settings area is reachable from the rail (superAdmin-only, and server-enforced), dark-theme, keyboard-driven, with `<dialog>` modals for destructive actions.
- **G7.** Every org and user mutation writes an `AuditLog` row; every management route is `superAdmin`-only, failing **closed** with the uniform **404** for anyone else (no tenant/user enumeration).

## 3. Non-goals

- **N1.** Per-org branding/quotas/billing/domain routing (ADR-004 N1). Multi-org **membership** (a user has one org).
- **N2.** Postgres RLS / DB-level enforcement. Tenant encryption / key separation.
- **N3.** TheHive **wire** parity for these operations — per decision **Q4**, this is **UI + Amalthea API only**; no `compat/` routes are added or changed.
- **N4.** **Ingestion-token management** and an **Audit-log viewer** — deferred to the backlog (decision **Q1/Q8**), tracked in `TODO.md`.
- **N5.** TheHive `taskRule`/`observableRule`/`locked` org columns (decision **Q5** = name/description + timestamps only).
- **N6.** Self-service registration / password-reset email flows (no mailer in scope).

## 3a. Current state (what exists today)

| Piece | State |
|---|---|
| `Organisation` model | `UUIDModel`-only: `name` (unique, ≤200), `description`. **No timestamps, no creator.** |
| `User` model | `username`, unique `login` (derived from username on save), `role ∈ {superAdmin, admin, analyst, read-only}` (default `analyst`), `org` FK (nullable, `SET_NULL`), `is_active`. |
| Org API | `GET /api/v1/organisation` → caller's own org (0-or-1); `GET|PATCH /api/v1/organisation/{idOrName}` → **caller's own only** (`_own_org`, uniform 404 otherwise). |
| User API | `GET /api/v1/user/current`; `GET /api/v1/user/{idOrLogin}` → **any** user (used to resolve assignees). No collection, no mutation. |
| Serializers | `organisation_json` = `{_id,id,_type,name,description,createdAt:null,updatedAt:null,…}`; `user_json`. |
| Permissions | `IsSuperAdmin`, `IsOrgAdminOrAbove`, `IsAnalystOrAbove`, `IsAuthenticatedAndOrgMember` exist but **no org/user view uses `IsSuperAdmin`**. |
| Audit | `identity/audit.py::maybe_record_cross_org_read` (reads only). No create/update/delete helper. |
| UI | Rail nav = Dashboard/Alerts/Cases/Automation/Playbooks/Sources. **No Settings.** `keys.js` ROUTES = `g/a/c/p/r/s`. |
| FK semantics → `Organisation` | **PROTECT:** `Case.owner_org`, `Alert.owner_org`, `Observable.owner_org`, `IngestionToken.organisation`. **SET_NULL:** `User.org`, `AuditLog.actor_org`/`target_org`. **CASCADE:** `Share.organisation`. |
| FK semantics → `User` | **SET_NULL:** case/task assignee, timeline actor, `Share.created_by`, `AutomationRun` actor, etc. **CASCADE:** `ApiKey.user`. So deleting a user is largely safe (no tenant data is destroyed). |

## 4. Decisions (locked 2026-10-10)

| # | Question | Decision |
|---|---|---|
| Q1 | Settings scope | **Organisations + Users** now. Ingestion tokens and Audit-log viewer → **backlog**. |
| Q2 | Who manages tenants/users | **`superAdmin`-only.** An `admin` keeps the existing *own-org* `GET`/`PATCH` only; no directory, no user management. |
| Q3 | Deletion of a non-empty org | **(a) Block** with a 409 + counts when the org owns cases/alerts/observables/ingestion-tokens; operator must empty/move first. |
| Q4 | TheHive API parity | **UI + Amalthea API only.** No `compat/` changes. |
| Q5 | Org fields | Add **`created_at`/`updated_at`** (`TimeStampedModel`); no slug, no `taskRule`/`observableRule`/`locked`. |
| Q6 | Org members on delete | Either **reassign** members to a target org **or delete the member accounts with the org** — chosen explicitly per delete; block otherwise. |
| Q7 | Placement + shortcut | **Rail item** in the primary nav; shortcut **`,`**; both Settings pages listed in the `?` cheat-sheet. |
| Q8 | Audit-log viewer | **Deferred** (backlog). |

## 5. Proposed design

### 5.1 Data model
- **No new tenant/user concept.** Add `TimeStampedModel` mixin to `Organisation` (`created_at`/`updated_at`) — additive migration; existing rows get a backfilled `created_at` (data migration or `auto_now_add` default handled by Django's add-column-with-default path).
- No change to the FK on_delete matrix (that *is* the deletion safety net).
- Register `Organisation` in `identity/admin.py` (list/search by `name`) alongside `User`/`IngestionToken`/`AuditLog`.

### 5.2 API — Organisations
| Method | Path | Auth | Behaviour |
|---|---|---|---|
| `GET` | `/organisation` | any | **Non-superAdmin unchanged** (own org). `superAdmin`: **all** orgs with counts. |
| `POST` | `/organisation` | `IsSuperAdmin` | Create `{name, description}` → 201 `OutputOrganisation`; duplicate name → 400 `{"name":["already exists"]}`; **audited**. |
| `GET` | `/organisation/{idOrName}` | any | Own org (404 otherwise). `superAdmin`: any org (+ counts). |
| `PATCH` | `/organisation/{idOrName}` | `IsSuperAdmin` any / own-org legacy | Update `name`/`description` → 204; duplicate name → 400; **audited**. |
| `DELETE` | `/organisation/{idOrName}` | `IsSuperAdmin` | Guarded delete (§5.6). Query/body: `members ∈ {reassign, delete}` and `target_org` (required when `reassign`). 204 on success; **409** with counts/blockers; **audited**. |

- Extend the serializer with an **optional `counts` block** (`members`, `cases`, `alerts`, `observables`) populated only for the superAdmin directory/detail, keeping the recorded `OutputOrganisation` keys stable (or a sibling `organisation_admin_json` — implementation choice).
- Error envelope stays `{"type","message","fields"}`.

### 5.3 API — Users
| Method | Path | Auth | Behaviour |
|---|---|---|---|
| `GET` | `/user` | `IsSuperAdmin` | **New collection**: all users (`username`,`login`,`email`,`role`,`org`,`is_active`,`createdAt`, last login). |
| `GET` | `/user/current` | any | Unchanged. |
| `GET` | `/user/{idOrLogin}` | any | Unchanged (assignee resolution). |
| `POST` | `/user` | `IsSuperAdmin` | Create `{username, email, role, org, password[, is_active]}`; `login` derived from `username`; run Django password validators; duplicate username/login → 400; **audited**. |
| `PATCH` | `/user/{idOrLogin}` | `IsSuperAdmin` | Update `email`, `role`, `org` (reassign), `is_active`, optional `password` (reset). Guard: cannot demote/deactivate the **last superAdmin**; cannot change your own role away from superAdmin; **audited** (esp. role/org changes). |
| `DELETE` | `/user/{idOrLogin}` | `IsSuperAdmin` | Delete; guard: cannot delete **yourself** or the **last superAdmin**; **audited**. (FKs are `SET_NULL`, so tenant data survives.) |

- Reuse `user_json`; add an admin list serializer if counts/fields differ. Errors in the standard envelope; cross-role → **uniform 404**.

### 5.3a Deletion of an org with members (Q6)
`DELETE /organisation/{id}` with members present is **blocked (409)** unless one of:
- `members=reassign&target_org=<idOrName>` → every member's `org` is moved to the target (same transaction), then the org is deleted;
- `members=delete` → the member accounts are deleted with the org (their FKs are `SET_NULL`, so no tenant data is lost), then the org is deleted.
Independently, if the org owns any case/alert/observable/ingestion-token → **409 blocked** (PROTECT), and no member option overrides that. The UI disables the confirm and states the reason in text.

### 5.4 UI / navigation (django-frontend)
- **Rail** (`base.html`): add a **Settings** item rendered only when `request.user.role == "superAdmin"` (cosmetic; the views enforce the role regardless).
- **Routes:** `ui-settings-organisations` (`/settings/organisations`) and `ui-settings-users` (`/settings/users`), plus POST endpoints:
  - `/settings/organisations` (create), `/settings/organisations/<uuid>/edit`, `/settings/organisations/<uuid>/delete`
  - `/settings/users`, `/settings/users/<uuid>/edit`, `.../delete`
  HTMX where it helps; plain-POST fallback (§1/§6 progressive enhancement).
- **Organisations page:** table (name · members · cases · alerts · observables · created · actions) + **Add organisation**; per-row **Edit**, **Delete**.
- **Users page:** table (username · email · role · org · active · last login), **Add user**, per-row **Edit** / **Delete**.
- **Modals:** add/edit via a `<dialog>` form; **delete** via a `<dialog>` confirm. The org-delete modal shows counts and, when members exist, a **radio choice: reassign to ⟨select⟩ / delete members with the org**; the confirm is **disabled** (with the reason) when the org owns protected data. Esc closes, Tab is trapped, focus returns to the trigger. Never native `confirm()`.
- **Keyboard:** add Settings (`Settings` → `,`) to `keys.js` ROUTES + cheat-sheet.

### 5.5 Permissions
- New `IsSuperAdmin` wiring on every management API view; UI views use `@login_required` + a superAdmin check rendering the standard **404** (not 403) for anyone else, matching T7-a.
- The existing own-org `GET`/`PATCH /organisation/{idOrName}` stays available to any authenticated member for **their own** org.

### 5.6 Audit
- Add helpers in `identity/audit.py`: `record_organisation_change(...)` and `record_user_change(...)` →
  `AuditLog(action ∈ {"organisation.created","organisation.updated","organisation.deleted","user.created","user.updated","user.deleted"}, target_type ∈ {"Organisation","User"}, target_id, target_org, actor, ip=_get_client_ip(request))`.
- Never log passwords, tokens, or secret hashes. Reassignment records both source and target org.
- The delete audit row is written **inside** the same `transaction.atomic()` as the delete, capturing `target_id` before removal.

### 5.7 Deletion semantics (the important part)
Because `Case`/`Alert`/`Observable`/`IngestionToken` **PROTECT** their org, `Organisation.delete()` raises `ProtectedError` whenever the tenant owns data.
- **Pre-flight** counts; if cases/alerts/observables/tokens ≠ 0 → **409** / disabled-confirm modal naming what blocks it.
- **Members** handled per §5.3a (reassign or delete) inside the same transaction.
- **Shares** to the org (`CASCADE`) are removed; `AuditLog` rows are preserved (`SET_NULL`).
- Wrap delete + member handling + audit in one `transaction.atomic()`; never a partial state.

## 6. Phases

- **O1 — Organisations API + permissions (django-backend).** `TimeStampedModel` on `Organisation`; `IsSuperAdmin` on POST/PATCH/DELETE; superAdmin directory `GET`; counts; audit helpers; admin registration. **AC:** O1-a non-superAdmin → uniform 404 on every mutating route and no directory; O1-b create/edit/duplicate-name happy+error paths; O1-c exactly one `AuditLog` row per mutation; O1-d own-org `GET`/`PATCH` unchanged for a plain member.
- **O2 — Organisation deletion guard (django-backend).** Pre-flight counts; 409 shape; member reassign/delete; atomic delete+audit. **AC:** O2-a blocked-409 with counts when the org owns a case/alert/observable/token (no member option overrides); O2-b empty-org delete succeeds, shares removed, audit preserved; O2-c `members=reassign` moves every member then deletes; O2-d `members=delete` removes the accounts then deletes; O2-e no partial state on failure.
- **O3 — Users API (django-backend).** `GET /user` collection (superAdmin); create/edit/delete; password validators; last-superAdmin/self guards; audit. **AC:** O3-a non-superAdmin → uniform 404; O3-b create with validators + duplicate rejection; O3-c cannot delete self or the last superAdmin; O3-d role/org changes audited; O3-e `login` derived from `username` (no blank login).
- **O4 — Settings UI (django-frontend).** Rail item (superAdmin-only), Organisations + Users pages, add/edit/delete `<dialog>` modals, blocked-delete + member-choice state, `,` shortcut + cheat-sheet. **AC:** O4-a nav hidden for non-superAdmin and the routes 404; O4-b full CRUD on both pages via UI (HTMX and no-HTMX); O4-c destructive actions use `<dialog>` (Esc/Tab-trap), never native `confirm()`; O4-d org-delete modal shows counts, disables confirm when blocked, and offers reassign-or-delete for members; O4-e keyboard + cheat-sheet updated.
- **O5 — Docs + admin (planner + django-backend).** Update `docs/spec/identity-auth.md`, `data-model.md`, `api.md`, `deviations.md` (the Amalthea extension for the superAdmin directory/list + user collection), `README.md` bootstrap (point operators at Settings). Backlog entries for tokens + audit viewer. **AC:** O5-a docs match behaviour with code pointers; O5-b no stale "deferred" text.

## 7. Security
- **Enumeration:** the superAdmin directory and user list are the *only* multi-tenant/multi-user reads; role-gated in depth (view + serialization), uniform 404 for everyone else. Preserve `user_detail` semantics for assignee resolution (unchanged).
- **Fail closed:** blocked deletes; no cascading of tenant data; `PROTECT` is the backstop.
- **Privilege:** guards against deleting/demoting the **last superAdmin** and against self-lockout; role/org changes are audited.
- **Secrets:** passwords set via `set_password` + Django validators; never echoed, logged, or put in audit rows; `IngestionToken.secret_hash` remains excluded everywhere.
- **CSRF/session** on all UI POSTs; DRF auth on the API. No org/user identity accepted from a client for authorization decisions.

## 8. Testing
- **Conformance:** extend the route-enumeration guard (`tests/conformance/test_tenant_scoping.py`) to cover the new `/settings/...` and `/api/v1/organisation` + `/api/v1/user` mutations — non-superAdmin and cross-tenant both → uniform 404.
- **API:** org CRUD + duplicate names + blocked/successful deletes + member reassign/delete + audit assertions; user CRUD + validators + last-superAdmin/self guards + audit assertions.
- **UI:** nav visibility, route 404 for non-superAdmin, HTMX and plain-POST paths, `<dialog>` keyboard behaviour, blocked-delete UX, cheat-sheet entry.
- **Regression:** existing own-org `GET`/`PATCH` and `user_current`/`user_detail` unchanged.

## 9. Risks
- **R1 — Lock-out:** a superAdmin deleting/demoting the last superAdmin (or themselves) bricks tenant management. Mitigation: O3 guards + tests.
- **R2 — Directory leakage:** the whole point of `_own_org` was to avoid a tenant directory; the superAdmin path must be the *only* lister and role-gated in depth.
- **R3 — Heavy counts:** 4 aggregates × N orgs. Mitigation: single annotated query; paginate if large.
- **R4 — Orphaning on delete:** handled by the explicit reassign/delete choice (Q6); still, reassigning to the wrong org is possible — the UI must show the target org clearly and the audit records both sides.
- **R5 — Serializer width:** adding `counts` could break consumers — keep it additive/optional or split the serializer.
- **R6 — Password handling:** weak initial passwords from an admin tool; run Django validators and document the policy.

## 10. TODO entry
Recorded as **TODO §6.12** and priority row #6, with the backlog items (ingestion-token management, audit-log viewer) noted separately.

## 11. References
- `docs/decisions/ADR-004-tenant-isolation.md`, `docs/planning/PLAN-2026-10-10-tenant-isolation.md`.
- `identity/views.py`, `identity/permissions.py`, `identity/audit.py`, `identity/models.py`, `core/serializers.py`, `ui/urls.py`, `ui/views.py`, `ui/templates/ui/base.html`, `ui/static/ui/keys.js`, `docs/spec/api.md`, `AGENTS.md` §6.

## 12. Implementation corrections (O1–O3 review, 2026-10-11)
Two issues were found in the O1–O3 implementation during review and corrected before O4; recorded per the "deviations must be recorded" rule:
- **D1 — `user/<idOrLogin>` was registered twice.** `identity/urls.py` stacked `user_detail_management` and then the legacy `user_detail` on the same pattern, so the second was dead and the **live** `GET /api/v1/user/{idOrLogin}` returned `user_admin_json` — dropping `hasKey`/`hasPassword`/`hasMFA`/`locked` from the documented `OutputUser` contract (the existing surface test only asserted `_id`, so it did not catch this). Corrected: one route (`user/<idOrLogin>`) → `user_detail_management`, whose GET returns `user_json` (contract preserved); the unused `user_detail` view was removed; the public route name stays `user-detail`.
- **D2 — guard 404 body.** `_require_superadmin` built `"Not found not found"`; changed to a neutral `{"message": "Resource not found"}` uniform 404.
Both fixed and re-verified (`make check` ✓, 176 targeted tests green). No other AC deviations were found.

### O4 review corrections (2026-10-11)

The first `django-frontend` O4 pass left the suite red (14 failures in `test_settings_ui.py`, 1 in `test_ui_urls_smoke.py`) and contained functional bugs that the tests did not exercise. Corrected (agent re-run + planner fixes):

- **D3 — Settings templates.** (a) `{% url %}` was nested inside a quoted `{% include %}` argument (`form_action="{% url … %}"`) → `TemplateSyntaxError` / HTTP 500 on both pages; resolved with `{% url … as … %}`. (b) `modal_id="delete-org-"|add:org.id|stringformat:"s"` produced an **empty** id because `add` cannot concatenate `str`+`UUID`; stringified first (`{% with sid=org.id|stringformat:"s" %}`). (c) `data-is-superadmin` used a non-existent `is_superadmin` field and `data-is-self` compared a UUID to `"1"`/`"0"` (filter precedence); replaced with `{% if %}`. (d) **Create dialogs could not submit**: inputs injected into `.modal-body` were outside `_modal.html`'s footer `<form>`; fixed by associating them via `form="org-create-form"` / `form="user-create-form"` and setting the form id in JS. (e) `test_ui_urls_smoke.py` now asserts the superAdmin-only Settings list routes return **404** for a plain analyst (`_SUPERADMIN_ONLY`) rather than 200/302.
- **D4 — shared `identity/services.py::delete_organisation` (planner fix).** The O4 refactor that extracted the deletion guard from `identity/views.py` (now used by **both** the API and the UI) introduced two latent defects the AC tests missed:
  - **Not atomic.** Member reassignment (`members_qs.update(org=target)`) and member-account deletion ran **outside** the `transaction.atomic()` block (contrary to the docstring and AC **O2-e**). A raced `ProtectedError` on `org.delete()` would leave members already moved or their accounts already removed. Fixed: member handling and `org.delete()` now share one atomic block; added regression tests `test_delete_atomic_reassign_not_partial_on_race` and `test_delete_atomic_member_delete_not_partial_on_race`.
  - **Orphaned members.** The service trusted callers for target validation, but the UI view did not validate (it only resolved a missing target to `None`), so a crafted/stale `members=reassign` with no or unknown target ran `members_qs.update(org=None)` → members silently orphaned. Fixed: the service refuses `reassign` without a real, different target (defence in depth) and `ui/views.py::settings_organisations_delete` mirrors the API's up-front validation; tests `test_service_reassign_without_target_never_orphans_members` and `test_delete_organisation_reassign_invalid_target_blocked`.
- Re-verified: `make check` ✓ **1218 passed / 3 skipped**.

### Security-review corrections (2026-10-11)

`security-auditor` reviewed O1–O4 (`docs/reviews/SECURITY-2026-10-11-org-user-management.md`) and returned **CHANGES REQUIRED**. The Critical/High/Medium correctness items are fixed here; the UI/CSP items are split out to **Wave 6.3** (§13).

- **D5 — org deletion lock-out + audit atomicity + UI audit gap (planner fix).**
  - **F1 (Critical) — lock-out via `members=delete`.** `delete_organisation` deleted every member without consulting the self/last-superAdmin guards, so a superAdmin deleting their own org (or the org holding the last active superAdmin) could brick tenant management. Fixed: `members=delete` now raises `OrganisationDeleteBlockedError(reason="self_lockout"|"last_superadmin")`; the API returns 409 with `blockers.reason` and the UI shows a "reassign members instead" message. Test: `test_delete_self_via_members_delete_blocked`.
  - **F2 (High) — audit written outside the transaction.** `organisation.deleted` (and, on the actor's own org, `actor_org`) was written **after** the commit against rows that no longer existed → `IntegrityError` → HTTP 500 with **zero** audit rows after the delete had already committed. Fixed: all audit rows are now written **inside** the same `transaction.atomic()` and **before** the referenced rows disappear (matches plan §5.7). Tests: `test_delete_race_leaves_no_audit_rows`, `test_delete_own_org_by_reassign_audits_without_error` (F2 regression).
  - **F4 (Medium) — UI create/update wrote no audit.** Added `record_organisation_change`/`record_user_change` to the four Settings UI mutations (`ui/views.py`). Tests: `TestSettingsUIAudit` (4).
  - Re-verified: `make check` ✓ **1225 passed / 3 skipped**.

### UI fix (independent, 2026-10-11) — data-table class collision

Not part of O1–O5; found while reviewing the tables. `ui/static/ui/app.css` styled data tables with
`.grid`, but Tailwind v4 also emits a `.grid { display: grid }` **utility** (the `grid` token appeared
in the table markup) and the `utilities` cascade layer outranks `components` — so `<table class="grid">`
became a grid container and its `<thead>`/`<tbody>` were laid out as two independent grid items, giving
each its own column widths (**column headers no longer lined up with the rows**). Fixed by renaming the
component class to `.data-table` / `.data-table-tight` across the 17 templates and the compiled
stylesheet (which also drops the now-dead `.grid` utility). Guard: `tests/conformance/test_ui_table_markup.py`.
`make check` ✓ **1228 passed / 3 skipped**.

## 13. Wave 6.3 — Settings UI hardening (closed 2026-10-11)

Split into **two scopes**, each tracked as its own backlog item:

- **§13A (scope A) — Settings-scoped fix** → **TODO §6.14** (scheduled; fixes the org-management AC + F3/F5/F6/F7/F8/F10).
- **§13B (scope B) — repo-wide ADR-003 compliance** → **TODO §6.15** (backlog; pre-existing shipped surfaces, not scheduled).

### 13A — Scope (A): Settings UI hardening (TODO §6.14)

Deferred from the O4 security review. Root cause: **ADR-003 ("zero inline scripts/styles") is not actually satisfied** — `script-src 'self'` with no nonce **blocks** the inline `<script>` in `ui/templates/ui/_modal.html`, `settings_organisations.html` and `settings_users.html` (verified on the live header). Consequences:

- **F10 (Info → functional):** the modal open/close/guard JS does not execute in a CSP-honouring browser, so the Settings create/delete dialogs are inert; and because the create inputs are *only* injected by that JS, **AC O4-b ("HTMX and no-HTMX") is not met** — there is no JS-free create form.
- **F3 (High, currently CSP-masked):** `{{ org.name|escapejs }}` → string-concat → `body.innerHTML` is a **stored-XSS sink** (any tenant user can rename their own org, which a superAdmin then renders). `escapejs` protects a JS string literal, not an HTML sink. Only the CSP prevents it; **externalising the JS (F10) would activate F3**, so both must be fixed together.
- **F5 (Medium):** last-active-superAdmin guards are check-then-act → concurrent operations can zero the admins (TOCTOU). Mitigate with `select_for_update` around the guard+mutate.
- **F6/F7/F8 (Low):** non-uniform user 404 exists as an existence oracle (F6); malformed UUID in UI POSTs → 500 (F7); `bool("false") is True` for `is_active` in the JSON API (F8).

**Plan (A):** externalise all modal/interaction JS into `ui/static/ui/*.js` (CSP-compliant) and render create/edit as real server-side forms inside `<dialog>` (degrades to plain POST); replace the `innerHTML` sinks with DOM APIs / `json_script`; remove inline `style=""` attributes too (same `style-src` rule); add `select_for_update` guards. Re-run `security-auditor` afterwards. **Scope (A) = `ui/templates/ui/_modal.html` + the two Settings templates only.**

### 13B — Scope (B): repo-wide ADR-003 compliance (TODO §6.15)

The same inline-script / inline-style violation exists in **pre-existing shipped templates** (case-detail `live.js` bootstrap, playbook forms, the cheatsheet modal, …). Scope (B) audits the whole `ui/templates/` tree and externalises every remaining inline `<script>`/`<style>`/`style="…"` so `script-src 'self'` / `style-src 'self'` hold without `unsafe-inline`. Tracked as **TODO §6.15**; **not scheduled** — do after (A) lands.

### 13A — Outcome (implemented 2026-10-11; verifier 17/17 met)

Delivered on top of the org-management work (all still uncommitted at time of writing):

- **F10/F3:** `ui/static/ui/modal.js` + `modal.css` externalised (loaded from `base.html`); `ui/templates/ui/_modal.html`
  rewritten as markup-only (`form_id`, `form_method`, `body_template`; no `{% block %}`, no `innerHTML`, no inline
  `<script>`/`<style>`); the four modal bodies are server-rendered partials (`_settings_org_create_form.html`,
  `_settings_org_delete_form.html`, `_settings_user_create_form.html`, `_settings_user_delete_form.html`). Settings
  create/edit/delete work as plain CSRF POSTs with HTMX **and** without JS (O4-b met).
- **F5:** `identity/services.py` gained `lock_active_superadmins()` plus the `locks_active_superadmins` (service) and
  `serializes_superadmin_changes` (HTTP, mutating verbs only) decorators; applied to `update_user`/`delete_user`. The
  **org-delete** path (`delete_organisation`, the F1 `members="delete"` guard) is locked too. Lock semantics are
  Postgres-only; SQLite no-ops `SELECT … FOR UPDATE` (dev/test).
- **F6:** `user_detail_management` runs the superAdmin guard *before* target resolution for PATCH/DELETE → uniform 404 body.
- **F7:** `as_uuid`/`resolve_organisation`/`resolve_user` prevent `ValidationError`→500 on malformed ids; names truncated
  to 200 **before** the uniqueness check with `IntegrityError` caught. Also fixed the same-class `case_share_list` 500.
- **F8:** `parse_bool` accepts real bools + explicit `"true"/"false"`, rejects anything else with 400 (`bool("false")` bug).
- **F9:** `IngestionTokenAdmin.secret_hash` is **excluded** (not merely read-only — removing it from `readonly_fields`
  alone would have made the Argon2 hash editable); add is disabled (tokens are minted by `create_token`). No migration.

**Deviations (recorded):** edit remains a standalone page, not a `<dialog>` (`D1`, Low — no AC violated, still plain
CSRF POST); `D2` (org-delete lock) and `D3` (security re-audit) are now closed.

**Residual risk (accepted):** no DB-level ≥1-active-superAdmin invariant; Postgres lock semantics not exercised by the
SQLite-only suite; the share form does not validate the target against `available_orgs`.

**Evidence:** `docs/reviews/SECURITY-2026-10-11-wave63a.md` (all F3/F5–F10 resolved, security sign-off),
`docs/planning/VERIFY-2026-10-11-wave63a.md` (17 Met / 0 Not Met / 1 Deviated), `tests/conformance/test_org_user_hardening.py`,
`TestSettingsUICspAndEscaping` in `tests/conformance/test_settings_ui.py`.

## 14. References (addenda)
- `docs/reviews/SECURITY-2026-10-11-org-user-management.md`, `docs/planning/VERIFY-2026-10-11-org-management.md`.
- `docs/reviews/SECURITY-2026-10-11-wave63a.md`, `docs/planning/VERIFY-2026-10-11-wave63a.md` (§13A outcome).
- `identity/services.py` (shared `delete_organisation`/`create_user`/`update_user`/`delete_user`), `identity/audit.py`, `amalthea/settings/base.py` §CSP, `docs/decisions/ADR-003-frontend-assets.md`.


