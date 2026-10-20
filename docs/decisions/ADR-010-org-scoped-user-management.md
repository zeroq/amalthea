# ADR-010: Organisation-Scoped User Management (admin own-org, superAdmin all-orgs)

**Status:** Accepted (2026-10-20) — implemented with `PLAN-2026-10-20-org-scoped-user-management.md`
**Date:** 2026-10-20
**Owner:** planner
**Extends:** ADR-009 (org-owned resources — the hybrid "admin owns their org, superAdmin owns all"
scoping model and the uniform-404 resolver), ADR-006 (role semantics — a role gate answers before an org
gate, both as 404), ADR-004 (tenant isolation — cross-org divergence in the audit row).
**Related:** `docs/planning/PLAN-2026-10-20-org-scoped-user-management.md`,
`docs/planning/PLAN-2026-10-13-non-null-user-org.md`.

> **Numbering note:** ADR-007 is *claimed* by the "every user belongs to exactly one organisation" ADR
> (referenced from `PLAN-2026-10-13-non-null-user-org.md`), but **that ADR file was never written** — the
> invariant shipped directly in `identity/migrations/0009_user_org_required.py`. This ADR restates that
> invariant in its context so the record is not lost, and uses the next free number (010).

## Context

Settings → Users (`ui-settings-users`) was fully built (list, create, edit, delete; audit; self-lockout
and last-active-superAdmin guards) but deliberately **orphaned** and **superAdmin-global-only**:
`_require_superadmin_ui` returned a 404 to an `admin`, and its queryset (`User.objects...order_by("username")`)
was unscoped — one flat table of every user in every organisation. The rail and Settings tab strip linked
only Organisations and Sources.

The product needs the middle rung: an **`admin` manages their own organisation's users**, while a
**`superAdmin` manages every organisation's users**. This is the same shape ADR-009 shipped for ingestion
sources (an `admin` reaches only their org's feeds; a `superAdmin` reaches all), so the precedent — and
the resolver/hybrid idiom — already exists. The user also asked that a `superAdmin`'s view be **grouped
per organisation** (one table per org) for navigability, rather than one undifferentiated global table.

Three constraints frame the decision:

1. **Membership is a single FK, not a through model** (`User.org`, non-null, `PROTECT`; the ADR-007
   invariant). A user is in exactly one organisation, and `role` is global across the site — there are no
   per-organisation roles.
2. **`role` is orthogonal to `is_staff`/`is_superuser`** (TODO §11.5-L2): `/admin/` is outside the role
   model, so "an admin manages users" must be enforced in the app's own gates, not assumed from Django
   admin.
3. **User creation is a privilege-escalation surface.** An actor who can set `role` can mint a
   `superAdmin`. A scoped user manager must therefore be constrained in what roles it may assign, not
   merely in which rows it may see.

## Decision

1. **The user-management routes are gated by role *and* org, on the ADR-009 model.**
   `ui-settings-users`, `ui-settings-users-edit`, `ui-settings-users-delete` widen from
   `_require_superadmin_ui` to `_require_admin_ui` (admin + superAdmin; everyone else a uniform 404), and
   each then applies the org gate:
   - **`admin`** — list is `filter(org_id=request.user.org_id)`; a target outside that org resolves to the
     **same 404** as a nonexistent id (no existence oracle); the org is **pinned** on create (a posted
     `org` is ignored) and cannot be changed on edit.
   - **`superAdmin`** — list is unscoped; create/edit may name any org; a user may be moved between orgs.
2. **An `admin` may assign only `{analyst, viewer}`.** Creating or promoting a user to `admin` or
   `superAdmin` is rejected **server-side** with a validation message; an `admin` may not edit or delete a
   target whose role is `admin` or `superAdmin` (uniform 404). This is the privilege-escalation guard: the
   role allow-list is enforced in the view, never via a hidden form field.
3. **The `superAdmin` list is grouped per organisation.** The view renders **one card + `.data-table` per
   organisation** (name order), each with the org name and member count and its own "New user" action
   bound to that org. Each org's table is capped at `USERS_PER_ORG_CAP = 200` (newest first) with an
   honest "showing latest N of M users" line. An org with no users still renders its card and an empty
   line, so it stays reachable. The `admin` view keeps the single flat paginated table (Organisation
   column omitted — there is one org).
4. **All mutations keep writing `record_user_change`** with `target_org` = the **target** user's org (not
   the actor's). For a cross-org superAdmin mutation this means `actor_org != target_org` in the audit row
   — which **is** the cross-org marker; no separate action string is emitted (a distinct marker is
   reserved for *reads*, via `record_cross_org_read`, to avoid double rows).
5. **The existing lock guards are preserved verbatim**: an actor cannot demote or deactivate *themselves*
   away from superAdmin, and cannot delete/deactivate/demote the **last active superAdmin**. These apply
   unchanged to admins.
6. **The surface is un-orphaned**: a **Users** tab in the Settings strip and a Users entry in the left-rail
   Settings submenu, both visible to `admin` and `superAdmin` only.
7. **The API is unchanged.** `/api/v1/user` and `/api/v1/organisation` stay superAdmin-only; this ADR
   governs the UI surface only.

### Addendum (2026-10-20) — a lower role never sees a higher-role user

A refinement decided after the initial implementation: **an `admin` must not see `superAdmin` users in
Settings → Users.** The admin list queryset therefore adds `.exclude(role=SUPER_ADMIN_ROLE)` (on top of
the own-org filter), so a `superAdmin` who belongs to the admin's organisation is hidden — from the table,
and therefore from the visible count (`page_obj.paginator.count` derives from the filtered queryset).

- **Only `superAdmin` is hidden.** An `admin` still sees other `admin`s in their org (as before) — this
  addendum does **not** hide equal-or-lower roles from each other.
- **Edit/delete of a `superAdmin` was already refused** with a uniform 404 (`ui/views.py`, the target-role
  guard shared by edit and delete). The hide rule is the *visibility* half; the 404s remain the
  *enforcement* half and are independently regression-tested, so a crafted id cannot reach a superAdmin
  even though the id is no longer discoverable through the UI.
- **The superAdmin's own view is unchanged** — a `superAdmin` still sees every user, including other
  `superAdmin`s, across all orgs.

This keeps the principle that role visibility is monotonic: a user may never *see* a user whose role
outranks their own through this surface.

## Consequences

- **Positive:** the "who may create/edit/delete a user" question now has a scoped, reusable answer; an
  admin is self-sufficient for their org's onboarding without superAdmin involvement; the escalation
  surface is closed by an explicit server-side role allow-list; the hybrid scoping is consistent with
  ADR-009, so there is one mental model for org-owned admin surfaces.
- **Negative / accepted:** a `superAdmin` page that renders one table per organisation grows with the org
  count (mitigated by the per-org cap and the honest disclosure; revisit if org count grows). `role`
  remains global, so an `admin` cannot be given org-local rights over a role — accepted, since
  per-organisation roles are explicitly out of scope.
- **Unchanged by this ADR:** membership stays the single `User.org` FK (no through model); `is_staff` /
  `/admin/` remain outside the role model (§11.5-L2 stays a separate open item); the API stays
  superAdmin-only.
- **Documentation debt surfaced:** ADR-007's file is missing (the invariant lives only in migration
  `0009`). Recorded here rather than silently re-created; restoring ADR-007 is a separate small task.

## Alternatives

- **Leave Settings → Users superAdmin-global** (the status quo). Rejected: it makes every onboarding an
  escalation to a superAdmin and leaves the surface orphaned.
- **Per-organisation roles** (a membership/through model with a role per org). The "correct" multi-tenant
  model for some products, but a large schema/authorisation change (ADR-007 deliberately chose a single
  FK) and out of scope; the single global `role` is retained.
- **Enforce the role allow-list in the template only** (render a restricted `<select>`). Rejected: a
  client can post any role; the guard must be server-side.
- **Keep the superAdmin view as one flat global table** with a filter. Rejected by the product request:
  per-org tables are the requested navigation.
