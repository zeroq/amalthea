# PLAN-2026-10-20 — Org-scoped User Management (admin: own org; superAdmin: all orgs)

**Status:** APPROVED (2026-10-20) — all §12 questions resolved via the recommended options. Ready for implementation.
**Source:** user request 2026-10-20 (next item to implement).
**Related:** TODO §10.1 (role-scoped user management), §11.5-L2 (Django admin outside the role model),
ADR-006 (viewer role), ADR-007 (single org FK on User — **file missing**, see Q7), ADR-004 Q5 (cross-org
read auditing), ADR-009 (org-owned sources — the hybrid-scoping precedent), ADR-010 (new, this plan).

---

## 1. Summary

Settings → Users already exists and is **fully built** (`ui-settings-users`, create/edit/delete, audit,
guards) — but it is deliberately **orphaned** (no nav link) and is **superAdmin-global-only**: an `admin`
gets a uniform 404, and the list shows every user of every org with no org scoping.

This plan turns it into the feature the user asked for:

- **`admin`** — sees and manages **only their own org's users**: list, add, edit, delete.
- **`superAdmin`** — sees and manages **all users in all orgs**, with a **per-org navigation** layout
  (one table per organisation) rather than one flat global table.
- **A real Settings → Users tab** so the surface is reachable (un-orphan it).

The API (`identity/views.py` `/api/v1/user`, `/api/v1/organisation`) is **out of scope** — it stays
superAdmin-only as today (non-goal §3).

---

## 2. Current state (evidence, from exploration 2026-10-20)

| Fact | Location |
|---|---|
| `User.role` ∈ `superAdmin, admin, analyst, viewer`; default `analyst` | `core/enums.py:117-138`, `identity/models.py:92-97` |
| One org per user — **no membership/through model**; `User.org` FK `PROTECT` | `identity/models.py:98` (ADR-007) |
| Role is orthogonal to `is_staff`/`is_superuser` | `TODO.md:601-608` (§11.5-L2) |
| `settings_users` is superAdmin-only (`_require_superadmin_ui`) | `ui/views.py:4140-4224` |
| Its queryset is **global, unscoped, unfiltered** (all users) | `ui/views.py:4207-4211` |
| Create re-implements validation inline; edit/delete carry self-lockout + last-superAdmin guards | `ui/views.py:4150-4205`, `:4240-4336`, `:4349+` |
| UI is **orphaned** — no rail or tab links to `ui-settings-users` | exploration; `_settings_nav.html` has only Organisations + Sources |
| Closest hybrid precedent: Sources/Tokens (admin→own org, superAdmin→all + `show_org_column`) | `ui/views.py:4407-4416`, `:4902-4917`; `settings_sources.html` |
| Permission idioms: `_require_superadmin_ui` / `_require_admin_ui` (return-a-404), `not_viewer`, `is_admin()` | `ui/views.py:3934-3950`, `identity/scoping.py:67` |
| Audit helper `record_user_change(request, actor, action, target_id, target_org)` | `identity/audit.py:170` |
| Tests that **pin the current global-only behaviour** (must be updated) | `test_ui_pagination_edge_cases.py:648,656`; `test_tenant_scoping.py:797-809`; `test_settings_ui.py` `TestUsersCRUD`; `test_ui_urls_smoke.py:124-136` |
| `serializes_superadmin_changes` lock decorator (superAdmin rows) | `identity/services.py:113-127` |

---

## 3. Goals

- **G1** — `admin` can list/add/edit/delete users **in their own org only**; cross-org access is a
  uniform 404 (no existence oracle), matching ADR-009's token model.
- **G2** — `superAdmin` can list/add/edit/delete users **in any org**, and the list is **grouped per
  organisation** for navigation.
- **G3** — Settings → Users is **reachable** via the Settings tab strip (and the rail submenu if
  consistent), visible to `admin` and `superAdmin` only.
- **G4** — An `admin` **cannot** create a `superAdmin` (or otherwise escalate privilege), cannot act on
  a superAdmin, and cannot move a user to another org.
- **G5** — All mutations stay audited (`identity/audit.py::record_user_change`) with the correct
  `target_org`, and the existing self-lockout / last-superAdmin guards keep holding.
- **G6** — No regression in viewer denial (`not_viewer`), CSP (no inline JS/style), or the modal/dialog
  conventions.

## 4. Non-goals

- **N1** — No API change: `/api/v1/user` and `/api/v1/organisation` stay superAdmin-only.
- **N2** — No new membership model. Membership remains the single `User.org` FK (ADR-007). A user is in
  exactly one org.
- **N3** — No per-org **roles** (a user's `role` stays global, as today).
- **N4** — No change to `is_staff`/Django admin (`/admin/`) — §11.5-L2 stays a separate, open item.
- **N5** — No "move user between orgs" affordance **for admins** (superAdmin keeps it — see Q3).
- **N6** — No invitation/email flow, no self-service signup, no password-reset-by-email.

---

## 5. Architecture & design decisions

### 5.1 Access model (mirrors ADR-009, the token model)

| Viewer | List scope | Create | Edit / Delete target |
|---|---|---|---|
| `superAdmin` | all users, grouped by org | any org (chooses org) | any user |
| `admin` | own org's users only | own org only (org **pinned**) | own org's users only |
| `analyst` / `viewer` | **404** (surface not theirs) | 404 | 404 |

- Gate: replace `_require_superadmin_ui` with `_require_admin_ui` on all three views.
- Scoping: `if request.user.role != "superAdmin": qs = qs.filter(org_id=request.user.org_id)`; on create,
  `organisation = request.user.org` for an admin (never read `POST["org"]`), exactly like
  `settings_token_create` (ui/views.py:4902-4917).
- Object resolve for edit/delete: an `admin` resolving a user outside their org returns the **uniform
  404** (`render(request, "ui/404.html", status=404)`), not 403 — consistent with the rest of the app.

### 5.2 superAdmin per-org layout

The user explicitly asked for a **separate table per org**. Design:

- The superAdmin view builds `orgs = Organisation.objects.order_by("name")` with, for each, its users
  (`select_related`), and renders **one `.card` + `.data-table` per org**, each with a heading (org name,
  member count) and its own "New user" action pre-bound to that org.
- **Pagination interaction:** per-org tables are **not** paginated individually (bounded by PICKER/rail
  caps for sanity). The existing global `paginate()` is used for the **admin** flat list. For superAdmin,
  a per-org layout is inherently grouped — we cap each org's table at `RAIL_CAP`-style limit with an
  honest "showing latest N of M" line, or paginate the *org list* (one org per page) and render that org's
  full user table. **See Q2 — this is the main open design question.**
- The superAdmin create modal offers an `org` select (all orgs) — as today.
- The `admin` view keeps the **existing flat paginated table** for their single org, with the
  Organisation column **hidden** (there is only one org) — reuse the `show_org_column` pattern.

### 5.3 Privilege-escalation guardrails (G4)

Enforced server-side in the create/edit views (not just UI):

- **Create:** if actor is `admin`, the allowed role set is a **restricted subset** — `{analyst, viewer}`
  (see **Q1** for whether `admin` may create another `admin`). If the posted role is outside the subset,
  reject with an error. The org is pinned (ignores any posted `org`).
- **Edit:** if actor is `admin`:
  - may not edit a user whose role is `superAdmin` or `admin` (unless Q1 permits admin↔admin) → reject;
  - may not set a user's role to `superAdmin` (or `admin`, per Q1);
  - may not change `org` (the org select is not rendered; a posted org change is ignored/rejected).
- **Delete:** if actor is `admin`, may not delete a `superAdmin`/`admin` (per Q1) or any user outside
  their org → uniform 404.
- **Invariant preserved:** can never delete/deactivate/demote the **last active superAdmin** (existing
  guards, ui/views.py:4280-4309), nor self-lockout.

### 5.4 Navigation

- **Tab strip** (`_settings_nav.html`): add a **Users** tab, visible to `admin` + `superAdmin`
  (`{% if request.user.role == "admin" or request.user.role == "superAdmin" %}`). Order: Organisations
  (superAdmin) · Users · Sources. The file's own comment says a tab is "a one-line change".
- **Rail submenu**: check `base.html` — the Sources/Organisations submenu; add Users if the pattern
  matches (Q5).

### 5.5 Audit

- Every create/update/delete keeps calling `record_user_change` with `target_org` = the **target user's**
  org (not the actor's). For an `admin` this is always their own org; for `superAdmin` it may be cross-org
  (which is already the model — the audit row records `actor_org` + `target_org`).
- Consider (Q4) whether superAdmin cross-org mutations should *also* write a `cross_org_read`-style marker
  — the app currently only records cross-org **reads**.

---

## 6. Data model

**No schema change.** `User.org` (FK, PROTECT), `User.role`, `User.is_active` suffice. No migrations.

The superAdmin per-org layout needs only a queryset annotate (`.annotate(member_count=Count("users"))`)
— still no schema change.

---

## 7. API contracts

**No API change (N1).**

UI routes (unchanged names; only the **gate** widens):

| URL name | Path | New gate |
|---|---|---|
| `ui-settings-users` | `settings/users` | admin (own org) / superAdmin (all) |
| `ui-settings-users-edit` | `settings/users/<uuid:user_id>/edit` | idem + target in scope |
| `ui-settings-users-delete` | `settings/users/<uuid:user_id>/delete` | idem + target in scope |

---

## 8. UI / UX

### 8.1 admin view (`/settings/users`)
- Heading **Users**. The **New user** button lives in the card header, next to the org name (the page-head
  actions block is intentionally empty — a single Create action, in context, not duplicated at the top).
- Optional org filter row: **none** (single org). A note: "Users in <Org name>".
- Flat paginated table (reuse existing markup): Username · Email · Role · Active · Last login · actions.
  **Organisation column omitted.**
- Row actions (already icon-based from the just-shipped wave): `fa-pen-to-square` Edit, `fa-trash-can`
  Delete (red), guarded by the same `{% if can_mutate %}` and delete modal.
- **Role select** in create/edit limited to the admin-allowed subset.

### 8.2 superAdmin view (`/settings/users`)
- Heading **Users**. No page-head actions block; each org card owns its **New user** button (org chosen in
  the create modal).
- **Per-org layout (Q2):** one `.card` per organisation, in name order, each with an `<h2>` (org name +
  member count) and a `.data-table` of that org's users. Each card's header carries its own
  **New user** button bound to that org.
- Each org's table is capped at `USERS_PER_ORG_CAP = 200` (newest first), with an honest
  "showing latest N of M users" line when capped. No per-org pager in this wave.
- An org with zero users still renders its card with an honest "No users" empty line (so the org is
  visible and its New-user action is reachable) — **not** omitted.
- No search/filter affordance (Q6).
- The row actions and delete modals are identical to today's (icon links + `<dialog>`).

### 8.3 Accessibility & conventions
- `aria-label` on every icon-only control (carried over from the layout wave; AC-15 guard applies).
- Modals via `_modal.html` (`<dialog>`), no native `confirm()`; CSP-safe (no inline JS).
- Keyboard reachable; `?` cheat-sheet unaffected.
- A 10px gap between adjacent in-cell icon actions (already shipped).

---

## 9. Phases & tasks (with Acceptance Criteria)

### Phase A — Access widening + org scoping (backend, `ui/views.py`)

| Task | Files | Acceptance Criteria |
|---|---|---|
| A1 — Widen the gate | `ui/views.py` (`settings_users`, `settings_users_edit`, `settings_users_delete`) | **AC-1** `analyst`/`viewer` → uniform 404 on all three; `admin` and `superAdmin` reach them. `not_viewer` decoration retained. |
| A2 — Scope the list | `ui/views.py` (`settings_users` GET) | **AC-2** `admin`'s queryset is `filter(org_id=request.user.org_id)`; `superAdmin`'s is unscoped. A superadmin test user sees users of ≥2 orgs (positive control, mirrors `test_settings_users_page_2_for_superadmin`). |
| A3 — Pin org on create | `ui/views.py` (`settings_users` POST) | **AC-3** an `admin` posting `org=<other org id>` creates the user in the **admin's own** org (posted org ignored, no error oracle); a `superAdmin` creates in the posted org (as today). |
| A4 — Object-scoped resolve | `ui/views.py` (`get_object_or_404` → scoped resolve) for edit + delete | **AC-4** an `admin` editing/deleting a user id outside their org → uniform 404 (both GET edit and POST edit/delete); `superAdmin` resolves any. |
| A5 — Escalation guards | `ui/views.py` (create + edit) | **AC-5** an `admin` cannot create or promote a user to `admin`/`superAdmin` (posted role outside `{analyst, viewer}` is rejected with a message); an `admin` cannot edit/delete a target whose role is `admin`/`superAdmin` (uniform 404). Enforced **server-side**. |
| A6 — Org-move restriction | `ui/views.py` (edit) | **AC-6** an `admin` cannot change a user's `org` (org field omitted from admin form; a posted org change is ignored); a `superAdmin` still can (Q3). |
| A7 — Preserve lock guards | `ui/views.py` | **AC-7** self-demote / self-deactivate / self-delete / last-active-superAdmin guards behave identically for admins and superAdmins (existing tests stay green). |

### Phase B — superAdmin per-org layout (backend context + template)

| Task | Files | Acceptance Criteria |
|---|---|---|
| B1 — Per-org context | `ui/views.py` (`settings_users` GET, superAdmin branch) | **AC-8** context exposes orgs (name-ordered) each with its users (≤ `USERS_PER_ORG_CAP`, newest first) + `member_count` + `capped`/`total` flags; no N+1 (bounded prefetch/select_related). |
| B2 — Per-org template | `ui/templates/ui/settings_users.html` | **AC-9** `superAdmin` renders **one `.card` per org**, each with heading (org name + member count), its own New-user action, and a `.data-table`; an org with 0 users shows an honest "No users" line (card present, not omitted). |
| B3 — Capped disclosure | `ui/templates/ui/settings_users.html` | **AC-10** when an org exceeds the cap, the card states "showing latest N of M users". |
| B4 — admin flat table | `ui/templates/ui/settings_users.html` | **AC-11** `admin` renders a single flat paginated table (existing `paginate()`), **Organisation column omitted**; "Users in <Org name>" note shown. |

### Phase C — Navigation / un-orphan

| Task | Files | Acceptance Criteria |
|---|---|---|
| C1 — Settings tab | `ui/templates/ui/_settings_nav.html` | **AC-12** a **Users** tab appears for `admin` + `superAdmin` only, with `tab-on`/`aria-current` when active. Order: Organisations · Users · Sources. |
| C2 — Rail submenu | `ui/templates/ui/base.html` | **AC-13** the rail Settings submenu links Users (admin+superAdmin); the `aria-current` route lists (lines 103/107) include `ui-settings-users*`. |

### Phase D — Tests

| Task | Files | Acceptance Criteria |
|---|---|---|
| D1 — Permission matrix | `tests/conformance/test_settings_ui.py` (extend) or new `test_settings_users_scoping.py` | **AC-14** admin own-org list; analyst/viewer 404; superAdmin sees ≥2 orgs. |
| D2 — Cross-org isolation | idem | **AC-15** admin edit/delete of an out-of-org user → 404; admin create cannot place a user cross-org. |
| D3 — Escalation guards | idem | **AC-16** admin cannot create/promote admin|superAdmin; cannot edit/delete an admin|superAdmin target. |
| D4 — Per-org layout | idem | **AC-17** superAdmin response contains one table per org with correct member counts; cap disclosure renders. |
| D5 — Nav/un-orphan | idem + `test_ui_urls_smoke.py` | **AC-18** Users tab/rail present for admin+superAdmin, absent for analyst/viewer. |
| D6 — Update stale pins | `test_ui_pagination_edge_cases.py:648/656`, `test_tenant_scoping.py:797-809`, `test_ui_urls_smoke.py:124-136`, `test_settings_ui.py::TestUsersCRUD` | **AC-19** no test still asserts the old "superAdmin-only global" behaviour; the route-enumeration matrix reclassifies these routes as hybrid. |
| D7 — Audit | idem | **AC-20** create/update/delete write `record_user_change` with `target_org` = target user's org, for both admin and superAdmin actors. |
| D8 — Regression | run the targeted files | **AC-21** `make check` stays green; the enumerated conformance files pass (full suite is user-run). |

### Phase E — Docs

| Task | Files | Acceptance Criteria |
|---|---|---|
| E1 — TODO | `TODO.md` | **AC-22** §10.1 marked done; the orphan-resolved note added; §12.9-style cross-reference. |
| E2 — ADR | `docs/decisions/ADR-010-org-scoped-user-management.md` | **AC-23** ADR records the hybrid model; notes the missing ADR-007 file as context. |
| E3 — deviations/spec | `docs/spec/deviations.md` (if the route class changes) | **AC-24** any deviation from this plan recorded; route-classification change noted. |

**Traceability:** AC-1…AC-24 map 1:1 to the tasks above; the verifier checks each against the plan.

---

## 10. Security

- **Tenant isolation** is the core risk: every read and write must be org-scoped for `admin`; a missing
  filter would leak another tenant's users. Mitigate with the uniform-404 resolver and cross-org tests.
- **Privilege escalation**: an `admin` must not mint a `superAdmin` (or another `admin` unless Q1 allows).
  Enforce **server-side**, not via a hidden form field.
- **Last-superAdmin / self-lockout** guards must be preserved verbatim.
- **Audit**: every mutation writes `record_user_change` with the correct `target_org`.
- **No secret leakage** (the exploration confirmed `test_admin_does_not_display_secret_hash` — passwords
  are never rendered).
- **CSP**: no inline JS/style in new markup.
- Route enumeration tests (`test_tenant_scoping.py` `TestUIRouteEnumerationConformance`) classify these
  routes; moving them from "superAdmin-only global" to "hybrid" must be reflected so the enumeration
  matrix still asserts correctly.

---

## 11. Testing

- Extend `tests/conformance/test_settings_ui.py` (`TestUsersCRUD`) with the admin matrix, or add
  `tests/conformance/test_settings_users_scoping.py`.
- Model the admin-vs-superAdmin scoping tests on `test_settings_ingestion_tokens.py`
  (`TestRoleGates`, `TestSuperadminOrgHandling`, `TestAdminOrgBinding`).
- **Must update** (currently pin global-only): `test_ui_pagination_edge_cases.py:648/656`,
  `test_tenant_scoping.py:797-809`, `test_ui_urls_smoke.py:124-136`.
- Run: targeted conformance files via pytest (user runs the full suite). `make check` must stay green.

---

## 12. Decisions (RESOLVED 2026-10-20 — all recommended options accepted)

**Q1 — Role subset an `admin` may assign → DECIDED: `{analyst, viewer}` only.** An `admin` may **not**
create or promote a user to `admin` or `superAdmin`. Server-side allow-list; drives AC-5/AC-13.

**Q2 — superAdmin layout paging → DECIDED: one card+table per org on a single page, with a per-org row
cap + honest "showing latest N of M" line.** Revisit if org count grows. Drives AC-6/AC-7/AC-14.
- The cap is a named constant (mirror `RAIL_CAP` naming, e.g. `USERS_PER_ORG_CAP = 200`), and the honest
  count line reuses the existing disclosure wording. Each org card's "New user" binds that org.
- If an org has more users than the cap, the card shows the newest `USERS_PER_ORG_CAP` and states
  "showing latest N of M users". (No per-org pager in this wave — keeps the page a navigation overview.)

**Q3 — superAdmin may still move a user between orgs → DECIDED: yes.** The existing editable `org` field
stays for superAdmin only; the admin edit form omits it and a posted org change from an admin is ignored.

**Q4 — Cross-org mutation audit → DECIDED: yes.** When a superAdmin creates/edits/deletes a user whose org
differs from the actor's org, the mutation still writes `record_user_change` (with `target_org` = target
user's org). Additionally, the existing cross-org **read** marker semantics are extended: a cross-org
mutation is already distinguishable in `AuditLog` by `actor_org != target_org`, so no new action string
is required — the plan records that `actor_org`/`target_org` divergence **is** the cross-org marker for
mutations. (`record_cross_org_read` stays for reads; do not emit it for mutations to avoid double rows.)

**Q5 — Rail submenu → DECIDED: yes, tab strip AND left-rail submenu.** Add a Users entry to
`base.html`'s rail submenu (admin+superAdmin) and extend the `aria-current` route lists in `base.html`
(lines 103/107) to include the users routes.

**Q6 — Filters/search on the Users list → DECIDED: keep plain this wave.** No `?q` filter; `filtered_from`
stays `None`. (Search is §6.16 P6, a separate feature.)

**Q7 — ADR → DECIDED: write a short ADR-010** (`docs/decisions/ADR-010-org-scoped-user-management.md`)
recording the hybrid admin-org / superAdmin-global user-management model. Note: **ADR-007 was planned but
never written** (the "one org per user" invariant shipped in `identity/migrations/0009_user_org_required.py`
without its ADR file) — flag this as a documentation gap in ADR-010's context; do not create ADR-007 here.

**Q8 — admin sees/edits self → DECIDED: yes.** An admin appears in their own org's list and may edit their
own non-role fields (email/password), with the existing self-lockout protections. The self-demote /
self-deactivate / self-delete guards (`ui/views.py:4272-4309`) apply unchanged to admins.

### Locked behaviour summary (for implementation)

| Actor | List | Create org | Assignable roles | Edit/Delete target |
|---|---|---|---|---|
| superAdmin | all users, per-org cards | any org (modal select) | all four | any user; may move org |
| admin | own org only, flat table (no Org column) | own org (pinned, hidden) | `{analyst, viewer}` | own org only; cannot move org |
| analyst/viewer | 404 | 404 | — | 404 |

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| Missing org filter leaks users across tenants | uniform-404 resolver + cross-org tests + review |
| Privilege escalation (admin mints superAdmin) | server-side role subset guard; test |
| Stale tests pinning global-only behaviour fail CI | enumerated in Phase D5; update in same PR |
| Route-enumeration matrix mis-classifies the routes | update `test_tenant_scoping.py` classification |
| Large superAdmin page with many orgs | Q2 paging strategy; per-org cap |
| Breaking the existing self-lockout / last-superAdmin invariants | preserve guards verbatim; keep their tests |

## 14. Deviations

Recorded during the Phase A/B1/D implementation (2026-10-20):

1. **Template (Phase B2–B4) was implemented by the backend agent**, not deferred to the frontend agent —
   the backend tests needed the rendered template to assert against. Benign; template-only changes, no
   logic moved. Coordinated on `ui/templates/ui/settings_users.html`.
2. **`settings_users_delete` delegates its audit to `identity.services.delete_user`** rather than writing
   `record_user_change` in the view as well. Verified: `delete_user` already captures
   `target_org = user.org` before `.delete()` and writes `user.deleted` (`identity/services.py:466-471`),
   so AC-20 coverage is preserved and the view's call was a duplicate. No coverage lost.
3. **Self-review corrections after independent verification** (planner, not the agent):
   - `tests/conformance/test_ui_urls_smoke.py` reclassification (AC-19) was **claimed by the agent but not
     performed**; fixed by the planner — `ui-settings-users`/`-edit` moved from `superadmin_only` to
     `admin_or_superadmin`.
   - The template's new `.cap-disclosure` class had no stylesheet rule (would have added a **new** entry to
     the AC-9 undefined-class backlog, 42→43). Replaced with the existing `.dim` utility — no new class,
     backlog back to 42, no `app.css` recompile needed.
4. **Phase C nav test hardened after review** (planner): the frontend agent's negative nav tests only
   asserted a 404 on settings pages (vacuous for the rail, which renders from `base.html` on every page).
   Strengthened to render `ui-case-list` as analyst/viewer and assert the Users entry is genuinely absent;
   also dropped a redundant assertion duplication. `make check` green; 261 conformance tests pass.
5. **Addendum — a lower role never sees a higher-role user** (user-requested refinement, 2026-10-20):
   the admin list now adds `.exclude(role=SUPER_ADMIN_ROLE)` so a superAdmin sharing the admin's org is not
   shown (nor counted). Edit/delete of a superAdmin was **already** 404'd and remains so — the addendum
   only changes *visibility*, not enforcement. Only `superAdmin` is hidden (an admin still sees other
   admins); the superAdmin's own all-org view is unchanged. Recorded in ADR-010 (Addendum). Three new
   regression tests (`test_admin_does_not_see_superadmin_in_own_org`,
   `test_admin_does_not_see_superadmin_in_any_org`, `test_superadmin_still_sees_superadmins`), falsified
   manually (they fail without the filter). See the matching security-report addendum (SA-08).

---

## 15. Implementation readiness

**Status: READY FOR HANDOFF.** All questions resolved (recommended options), ADR-010 written, ACs locked
(AC-1…AC-24), no schema migration, no API change.

**Suggested split:**
- **Phase A–B (views + context)** and **Phase D (tests)** → `django-backend`.
- **Phase C (nav) + Phase B2–B4 (template)** → `django-frontend` (or backend, since it is server-rendered
  Django templates — a single agent can own both; coordinate on `settings_users.html`).
- **Phase E (docs)** → planner.

**Recommended execution order:** A1→A2→A3→A4→A5→A6→A7 (views), then B1 (context), then B2–B4 + C1–C2
(templates), then D1–D8 (tests), then E1 (TODO). Note D6 (update stale pins) should land **in the same
commit** as the behaviour change or CI/`make check` will be red between them.

**Verification:** hand to `verifier` with this plan + ADR-010; each AC checked individually. Hand to
`security-auditor` for the tenant-isolation + escalation surface (the two highest-risk areas).


---

## 16. Post-plan UI refinements (2026-10-20, user-requested)

These landed **after** the plan was approved and verified; recorded here because they change UI surfaces the
plan touches. No AC was invalidated — the refinements are layout/IA only, no model, API, or authz change.

1. **Sources — "New token" button moved into the card.** `ui/templates/ui/settings_sources.html`: the
   page-head `{% block actions %}` was removed; the button now sits in a `.card-header` next to
   `<h2>Ingestion tokens</h2>`, matching the Users page's in-card "New user" placement. Live-render checked
   (card-header/heading/button/`token-create-modal` present); `test_settings_ingestion_tokens.py` +
   `test_settings_ui.py` + `test_ui_inline_assets.py` green; `make check` green.

2. **Cases + Alerts — scope filters moved into their own filter card.** Both list templates now render their
   scope control as a real `<form method="get" class="inline-form filter-bar">` inside a dedicated
   `<div class="card">` **above** the table card (the convention established by `observable_list.html`). The
   filter card is: `<select class="filter-type">` + **Filter** + **Clear**. The Cases card additionally hosts
   the right-aligned **New case** button (`.btn-end`, `margin-left:auto`) mirroring Users' right-aligned
   "New user"; the table card gained a `.card-header` reading **Open cases / All cases / Closed cases**.

   - New utility: `.filter-bar .btn-end { margin-left: auto }` in `app.src.css` (right-pushes the trailing
     primary action in a filter row) — defined, so the AC-9 undefined-class backlog stays at **42**.
   - `app.css` regenerated (`make css`); `css-check` green.

3. **Cases scope filter is now tri-state: `?scope=open|all|closed`.** Previously `?closed=1` meant
   *closed-only* while the tab was **mislabeled "All"** — the union set was unreachable. The view now reads
   `scope` (default `open`; unrecognised → `open`, never a 500); `has_filter = scope != "all"` so `all`
   shows no "(filtered from N)" disclosure while `open`/`closed` do. Tests referencing `?closed=1` updated to
   `?scope=closed` (`test_ui_pagination.py`, `test_ui_pagination_edge_cases.py` DEF-2/DEF-4 + pager
   preservation, `test_ui_trailing_slash.py`). New conformance file
   `tests/conformance/test_case_scope_filter.py` (10 tests: default-open, per-scope counts, `all`-not-a-filter
   vs `open`/`closed`-are-filters, hostile-scope fallback ×6, three-options-with-correct-`selected`,
   heading-per-scope, pagination preserves `scope=closed`). Non-vacuity proven by sabotaging the `all` branch
   (the union test failed `5 == 8`).

**Not a deviation from the style spec:** new filter markup uses `.filter-bar` (the one-wave alias of
`.control-row`) to match the existing `observable_list.html`; the spec's target class is `.control-row`, but a
site-wide rename of the alias is out of scope for this change.
