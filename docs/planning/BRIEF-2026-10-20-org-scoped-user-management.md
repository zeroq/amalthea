# Implementation Brief — Org-Scoped User Management

**Plan:** `docs/planning/PLAN-2026-10-20-org-scoped-user-management.md`
**ADR:** `docs/decisions/ADR-010-org-scoped-user-management.md`
**Status:** APPROVED — ready to implement. No schema migration. No API change.
**Agents:** `django-backend` (Phase A–B views/context + Phase D tests) · `django-frontend` (Phase B2–B4 template + Phase C nav). One agent may own both; coordinate on `ui/templates/ui/settings_users.html`.

> Any deviation from this brief must be **recorded in the plan §14 Deviations** and reported back. Do not
> silently diverge.

---

## 0. Context (read these first)

- `ui/views.py` — `settings_users` (`~4140-4224`), `settings_users_edit` (`~4240-4336`),
  `settings_users_delete` (`~4349+`); `_require_superadmin_ui`/`_require_admin_ui` (`~3934-3950`);
  the precedent `settings_token_create` (`~4888-4917`); `_settings_sources_context` (`~4400`,
  `show_org_column` + org scoping).
- `identity/models.py` — `User` (`~73`), `role` (`~92-97`), `login` (`~91`), `org` FK PROTECT (`~98`).
- `identity/scoping.py` — `is_admin()`, `org_q`, `resolve_*` helpers (model the org resolver on these).
- `identity/audit.py` — `record_user_change(request, actor, action, target_id, target_org)` (`~170`).
- `core/enums.py:117-138` — `USER_ROLE_CHOICES`, `ADMIN_ROLE`, `SUPER_ADMIN_ROLE`, `VIEWER_ROLE`.
- `ui/templates/ui/_settings_nav.html` — tab strip (`{% if role == "admin" or role == "superAdmin" %}`).
- `ui/templates/ui/base.html:101-128` — rail Settings submenu; `aria-current` route lists at lines 103/107.
- Taxonomy/tests to mirror: `tests/conformance/test_settings_ingestion_tokens.py`
  (`TestRoleGates`, `TestSuperadminOrgHandling`, `TestAdminOrgBinding`).

**Invariants you must not break:** one org per user (single FK, ADR-007); role global (no per-org roles);
self-lockout + last-active-superAdmin guards; uniform 404 for every scope refusal (no existence oracle).

---

## 1. Phase A — backend views (`ui/views.py`)

### A1 — widen the gate  → AC-1
Change the decorator on `settings_users`, `settings_users_edit`, `settings_users_delete` from
`_require_superadmin_ui` to `_require_admin_ui`. Keep `not_viewer` if present.
- [ ] `analyst`/`viewer` → 404 on all three.
- [ ] `admin` and `superAdmin` reach all three.

### A2 — scope the list  → AC-2
In `settings_users` GET: `is_superadmin = request.user.role == SUPER_ADMIN_ROLE`.
- superAdmin: queryset unscoped (as today).
- admin: `qs = qs.filter(org_id=request.user.org_id)`.
- [ ] admin sees only own-org users; superAdmin sees users across ≥2 orgs.

### A3 — pin org on create  → AC-3
POST branch: superAdmin reads `request.POST["organisation"]`; admin **ignores** any posted org and uses
`request.user.org`. (Mirror `settings_token_create`.)
- [ ] admin posting a foreign `org` id creates the user in the admin's own org (silently ignored).
- [ ] superAdmin creates in the posted org.

### A4 — object-scoped resolve  → AC-4
Replace `get_object_or_404(User, pk=user_id)` in edit (GET+POST) and delete with a scoped resolve: for an
admin, filter `org_id=request.user.org_id` **before** `get_object_or_404`, so an out-of-org id yields the
same 404 as a nonexistent one.
- [ ] admin edit (GET/POST) or delete of another org's user → 404.
- [ ] superAdmin resolves any user.

### A5 — escalation guards  → AC-5
Server-side, in both create and edit:
- Posted `role` must be in the allow-list: superAdmin → all four; admin → `{analyst, viewer}` only.
  Reject anything else with a user-facing validation message (re-render form, HTTP 200/400 per existing
  pattern — do not 500, do not silently coerce).
- An admin may not edit/delete a target whose current role is `admin`/`superAdmin` → uniform 404.
- [ ] admin cannot create a user with role admin/superAdmin.
- [ ] admin cannot promote an existing user to admin/superAdmin.
- [ ] admin cannot edit/delete an admin|superAdmin target (404).
- [ ] superAdmin unaffected.

### A6 — org-move restriction  → AC-6
Edit form: superAdmin keeps the editable `org` field (may move a user between orgs). Admin: the field is
absent and a posted `org` change is ignored.
- [ ] admin cannot move a user to another org.
- [ ] superAdmin can.

### A7 — preserve lock guards  → AC-7
Do **not** alter the self-demote / self-deactivate / self-delete / last-active-superAdmin logic or the
`@serializes_superadmin_changes` decoration on edit. Keep existing tests green.
- [ ] existing guard tests unchanged and passing.

### Audit (part of A)  → AC-20
Every create/update/delete still calls `record_user_change(..., target_org=<target user's org>)`.
`actor_org != target_org` in the row **is** the cross-org marker for mutations — do **not** also emit
`record_cross_org_read` for a mutation.
- [ ] audit row carries the target user's org for both admin and superAdmin actors.

---

## 2. Phase B — superAdmin per-org layout

### B1 — per-org context  → AC-8
In the superAdmin branch of `settings_users` GET, build context:
- Organisations in `name` order, each with its users (newest first, capped at `USERS_PER_ORG_CAP = 200`),
  plus `member_count` and a `capped`/`total` pair for the disclosure.
- Avoid N+1: bounded `prefetch_related`/`select_related` or an `annotate(Count("users"))`; do not issue a
  query per org.
- Admin branch keeps the existing flat `paginate(qs)` path (unchanged).

### B2–B4 — `ui/templates/ui/settings_users.html`  → AC-9/AC-10/AC-11
- **superAdmin:** one `.card` per org — heading (org name + member count), a per-org **New user** action
  bound to that org, and a `.data-table`. An org with 0 users shows an honest "No users" line (card stays
  visible). When capped, show "showing latest N of M users".
- **admin:** single flat paginated table (existing markup), **Organisation column omitted**, plus a
  "Users in <Org name>" note.
- Row actions: existing regular-weight icon links (`fa-regular fa-pen-to-square` Edit,
  `fa-regular fa-trash-can` Delete in `.icon-link-err`), each with `aria-label` + `aria-hidden="true"` on
  the icon; delete via `_modal.html` `<dialog>` (no native `confirm()`).
- [ ] one table per org + correct counts; [ ] cap disclosure; [ ] admin single table, no Org column.

---

## 3. Phase C — navigation  → AC-12/AC-13

- `ui/templates/ui/_settings_nav.html`: add a **Users** tab (`admin`+`superAdmin` only), order
  Organisations · **Users** · Sources. Active state uses the same `tab-on`/`aria-current` pattern.
- `ui/templates/ui/base.html`: add a Users entry to the rail Settings submenu (admin+superAdmin); extend
  the `aria-current` route lists at lines 103/107 to include `ui-settings-users` and its `-edit`/`-delete`
  children.
- [ ] Users visible to admin+superAdmin, absent for analyst/viewer, in both tab strip and rail.

---

## 4. Phase D — tests (backend)

Pref: extend `tests/conformance/test_settings_ui.py` **or** add
`tests/conformance/test_settings_users_scoping.py` (mirror `test_settings_ingestion_tokens.py`).

- [ ] **AC-14** permission matrix: admin own-org list; analyst/viewer 404; superAdmin ≥2 orgs.
- [ ] **AC-15** cross-org isolation: admin edit/delete out-of-org → 404; admin create can't place cross-org.
- [ ] **AC-16** escalation: admin can't create/promote admin|superAdmin; can't edit/delete such a target.
- [ ] **AC-17** per-org layout: superAdmin response has one table per org with correct member counts; cap
      disclosure renders.
- [ ] **AC-18** nav: Users tab/rail present for admin+superAdmin, absent for analyst/viewer.
- [ ] **AC-19** update stale pins — `test_ui_pagination_edge_cases.py:648,656`,
      `test_tenant_scoping.py:797-809` (+ `TestUIRouteEnumerationConformance` classification),
      `test_ui_urls_smoke.py:124-136`, `test_settings_ui.py::TestUsersCRUD`. No test may still assert the
      old "superAdmin-only global" behaviour. **Land in the same commit as the behaviour change.**
- [ ] **AC-20** audit `target_org` correctness for admin + superAdmin.
- [ ] **AC-21** `make check` green; run the targeted conformance files (do **not** run the full pytest
      suite — user runs it manually).

Test clients: dev settings reject `testserver` → use
`Client(SERVER_NAME="localhost", HTTP_HOST="localhost")`.

---

## 5. Phase E — docs (planner)

- [ ] **AC-22** `TODO.md` §10.1 → done; note the orphan resolved.
- [ ] **AC-23** ADR-010 (already written — verify it matches shipped behaviour).
- [ ] **AC-24** `docs/spec/deviations.md` if the settings route class changes.

---

## 6. Done = all of

`make check` green · AC-1…AC-24 verifiable · no schema/API change · no secrets · tree clean ·
**`verifier` sign-off on each AC** · **`security-auditor` pass on tenant isolation + escalation**.
Do not commit or push without explicit user approval.
