# ADR-006: Role Semantics — the Viewer Role is Read-Only

**Status:** Accepted (2026-10-12) — implemented in full; verified and security-reviewed
**Date:** 2026-10-12
**Owner:** planner
**Extends:** ADR-004 decision 4 (`User.role ∈ {superAdmin, admin, analyst, viewer}`, "enforced by DRF permission classes")
**Supersedes:** nothing
**Related:** `docs/planning/PLAN-2026-10-12-viewer-role-enforcement.md` (§13 implementation log),
`docs/verification/VERIFY-2026-10-12-viewer-role-enforcement.md`,
`docs/reviews/REVIEW-2026-10-12-viewer-role-enforcement.md`,
`docs/reviews/REVIEW-2026-10-12-ui-completeness-p3.md` (M1)

## Context

ADR-004 chose four roles and stated they were "enforced by DRF permission classes". In practice only
three enforcement sites ever existed:

- `identity/permissions.py` — `IsSuperAdmin` / `IsOrgAdminOrAbove` / `IsAnalystOrAbove`, wired into
  `automation/views.py` **only**;
- an inline check in `ui/views.py` on eight playbook/template views
  (`request.user.role not in ("analyst", "admin", "superAdmin") → 404`);
- `_require_superadmin_ui()` for the settings area.

Everything else was unprotected. `cases/views.py` declares no `permission_classes` at all, so the JSON
API trusted `IsAuthenticated` + `ScopePermission` (which governs **API-key scope**, not user role). In the
UI, roughly 38 POST-capable views existed and 6 were role-gated. Concretely, a `read-only` user could
comment on cases, change status/severity, create and assign tasks, extract observables, merge cases,
escalate/merge/dismiss alerts, create tags, author case templates, and manage case **shares** — the last
because `is_case_owner` (the share-ACL predicate, `identity/scoping.py:189`) is a pure *organisation*
predicate and consults no role.

Two things made this fixable cheaply and made the status quo untenable:

1. **`resolve_case(…, write=True)` is a chokepoint.** 35 mutation call sites already funnel through it
   (`ui/views.py`, `alerts/views.py`, `automation/views.py`), and `cases/views.py::_case_access` delegates
   to it, so the case API inherits any rule added there.
2. **A drift-proof test is possible.** The UI route table and the DRF URLconf can be enumerated, so
   "every mutating route denies a viewer" is machine-checkable rather than a review checklist.

`Share.permissions.profileName` on the TheHive-compat wire (`"write"` / `"read-only"`) is a **different
vocabulary** — share ACL profiles, not user roles — and must not be conflated with this decision.

## Decision

**A viewer can read; a viewer cannot write. Enforced server-side, fail-closed, in three layers.**

1. **One predicate, one constant.** `core.enums.VIEWER_ROLE` names the role value; `identity.scoping.is_viewer`
   is the sole predicate. It is **deny-only**: anonymous callers, `None`, `IngestionToken` and other
   non-`User` identities are never viewers, so the guard is additive and cannot turn a machine-to-machine
   path into a denial.
2. **Layer 1 — resource write-resolution.** `resolve_case(user, id, write=True)` returns `None` for a
   viewer; likewise `resolve_alert(..., write=True)` and `resolve_observable(..., write=True)`. Callers
   already translate `None` into a uniform 404.
3. **Layer 2 — fail-closed nets.** A DRF `ViewerReadOnly` permission (denies unsafe methods to a viewer;
   never denies anyone else) is added to `DEFAULT_PERMISSION_CLASSES`, covering every present and future
   DRF endpoint. A `not_viewer` decorator is applied to every mutating UI view, gate-first (before parsing),
   returning `ui/404.html`.
4. **Layer 3 — affordances.** `_base_context` exposes `can_mutate`; templates and the keyboard-shortcut
   layer hide mutation controls for a viewer. Affordances are UX, never the security boundary.
5. **Precedence: role beats share.** A viewer holding a read-write case share is still refused. Otherwise
   "read-only" would be advisory.
6. **superAdmin bypasses.** A superAdmin is not a viewer; break-glass behaviour stays as ADR-004 §5
   (cross-tenant, audited).
7. **Self-service auth is exempt.** `login`, `logout` and password change remain reachable by a viewer.
   The exemption list is asserted by the conformance test, not merely documented.
8. **Uniform denial.** UI → 404 (no existence oracle, matching ADR-004's scoping rule). API → 403
   (`PermissionDenied`, matching the existing `ScopePermission` behaviour). A read-through-POST
   (`POST /api/v1/query`) is denied for a viewer, consistent with the standing read-scope-key decision.
9. **No audit row for denials.** They are logged (`logger.warning`); they are not state changes.
10. **Rename `read-only` → `viewer`** (plan Phase 5, pending Q1) so the stored value, the label, the API
    representation and the docs agree. The TheHive share literal `"read-only"` is out of scope.

## Rationale

- **The chokepoint beats 40 guard clauses.** Putting the rule in `resolve_case(write=True)` covers the UI
  and the case API in one line; the decorator and the DRF permission cover what the resolver never sees
  (tags, templates, settings, shares, alert/observable routes).
- **Deny-only composition.** An allow-list permission (`role in {analyst, admin}`) would break ingestion
  tokens, `AllowAny` login/logout and service identities. "Deny if and only if the caller is a viewer"
  composes with `IsAuthenticated` and `ScopePermission` without ordering hazards.
- **Fail-closed over review.** The invariant test enumerates routes from the URLconf, so a new mutating
  view without a gate fails CI instead of shipping. This is the same posture as the tenant-isolation
  route-enumeration test (ADR-004 §"Consequences").
- **404 over 403 in the UI** preserves ADR-004's decision that existence is not leaked; **403 in the API**
  keeps the DRF contract (`ScopePermission` precedent) and is safe because the endpoint already exists
  publicly.
- **The rename removes a lie.** `read-only` reads like a *share* profile and appears next to TheHive's
  `profileName`; `viewer` states the semantics.

## Consequences

- A viewer's `GET` on a GET+POST screen that resolves `write=True` before branching (`case_merge`,
  `case_apply_template`) becomes a 404 — intended; those screens exist only to write.
- `user_json.role` changes value for these users (from `read-only` to `viewer`, Phase 5). The field is an
  Amalthea extension to the TheHive user shape → recorded in `docs/spec/deviations.md`.
- Two conformance suites become mandatory: every mutating `ui-*` route must return 404 for a viewer
  (allow-list asserted), and every DRF endpoint must deny unsafe verbs to a viewer.
- Any future role must be added to the predicate *and* the invariant tests; a new mutating route is
  covered automatically.
- `Share` semantics are unchanged, but their precedence versus role is now defined and tested.
- Resolves TODO §11.1 (M1). Related deferrals (M3, L1, §11.4) are unaffected.

## Implementation outcome (2026-10-12)

Shipped as described, with three additions worth noting for anyone reading this later:

1. **A pre-existing 500 was found and fixed on the way.** The UI attachment-download route uses a
   `<uuid:attachment_id>` converter, so the API helper it delegates to received a `UUID` and called
   `UUID(value)` on it — an `AttributeError` (a 500) for *every* user, viewer or not, because no test had
   ever followed that link. The role work's read-inventory test caught it.
2. **`resolve_alert`/`resolve_observable` were made role-strict.** They had skipped the viewer denial for a
   superAdmin. Decision 4 makes that combination unreachable, but leaving the exception in would have meant
   the guarantee was "role unless superuser" in two of the three resolvers — so the exception is gone and
   the invariant is stated the same way everywhere.
3. **`not_viewer` logs the resolver's `url_name`, not `request.path`.** `PATH_INFO` is percent-decoded and
   the `<str:case_id>` converter accepts `%0A`, so the original line allowed an authenticated viewer to
   forge log entries (CWE-117). The security review caught it before merge.
