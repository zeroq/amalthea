# PLAN — Viewer role enforcement (make `read-only` actually read-only)

- **Date:** 2026-10-12
- **Status:** Implemented (P1–P6) — see §13 Implementation log; verified by
  `docs/verification/VERIFY-2026-10-12-viewer-role-enforcement.md` and reviewed by
  `docs/reviews/REVIEW-2026-10-12-viewer-role-enforcement.md`
- **Owner:** planner
- **Related:** `AGENTS.md` §1, §6; `docs/decisions/ADR-003-frontend-assets.md`,
  `ADR-004-tenant-isolation.md`; `docs/planning/PLAN-2026-10-11-ui-completeness.md`;
  `docs/reviews/REVIEW-2026-10-12-ui-completeness-p3.md` (M1); `TODO.md` §11.1

---

## 1. Summary

`core/enums.py` defines four `User.role` values — `superAdmin`, `admin`, `analyst`,
`read-only` — but the fourth is not enforced anywhere except a handful of hand-written
gates. There is no server-side rule that a `read-only` user cannot mutate. Concretely,
today a `read-only` user **can**:

- comment on, edit, close, merge, tag, template and attach to any case their org owns
  (UI: `case_comment`, `case_edit`, `case_set_status`, `case_merge`, `case_tags`, …);
- escalate/merge/close alerts, create/delete/edit observables and tasks, create tags,
  create case templates, manage case shares;
- perform **every** mutating API call — `cases/views.py` declares **zero**
  `permission_classes`; the only API-level role-adjacent gate is `ScopePermission`,
  which constrains **API keys** (`scope == "read"`), not users, and only fires on
  non-safe verbs.

The only role checks that exist today are: `identity/permissions.py`
(`IsSuperAdmin` / `IsOrgAdminOrAbove` / `IsAnalystOrAbove`, wired **only** into
`automation/views.py`), an inline role test repeated at 8 sites in `ui/views.py`
(`request.user.role not in ("analyst","admin","superAdmin") → 404`), and
`_require_superadmin_ui()` for the settings area.

This plan makes the role a real, server-enforced constraint: a `viewer` (the value
today is `read-only`, see Q1) can read everything they can already see, and can mutate
nothing. Enforcement is **fail-closed**: adding a new mutating route without a gate
breaks a conformance test rather than silently opening a hole.

## 2. Goals

- **G1** A user whose role is the viewer role can never cause a state mutation through
  the UI or the JSON API — regardless of case shares, org membership, or API-key scope.
- **G2** Fail-closed: the guarantee is enforced by a machine-checked invariant that
  enumerates the whole surface, so new routes/endpoints cannot drift out of coverage.
- **G3** A viewer keeps full read access: cases/alerts/observables/tasks they may already
  see, attachments (list + download), case export, markdown rendering, WebSocket
  live-update subscription, and the query API **via its safe verbs**.
- **G4** A viewer keeps self-service auth: login, logout, password change.
- **G5** Zero behaviour change for `analyst`/`admin`/`superAdmin`.
  `superAdmin` bypasses (consistent with `_is_superadmin` in `identity/scoping.py`).
- **G6** A viewer cannot escalate their own role (user administration + role field are
  already `superAdmin`-only in the UI; the API surface must be confirmed closed).
- **G7** Terminology is coherent: role named `viewer` in the DB, the API, the admin
  and the docs (subject to Q1), with the TheHive-compat share vocabulary
  (`share.profileName: "read-only"`) deliberately **untouched**.

## 3. Non-goals

- No new roles, no per-object permission matrix, no RBAC admin UI.
- No change to `Share` semantics or to the case-share `/share` API vocabulary; only
  *precedence* is settled (viewer beats a write share — Q2).
- No change to API-key scopes (`read` / `readwrite`); the new rule **composes** with
  `ScopePermission` rather than replacing it.
- No role-scoped org user management (TODO §10.1) — but the predicate introduced here
  is reusable for it.
- No audit-log row for denied attempts (nice-to-have; see §8 — logged via `logger.warning`
  only).
- No rate limiting, no session-management changes, no `/admin/` hardening.
- No change to the **share** literal `"read-only"` in `core/serializers.py:415`
  (`profileName`) — that is TheHive's wire vocabulary for share permissions.

## 4. Architecture

Stack is unchanged (Django 5.x + DRF + Channels/ASGI + HTMX/Tailwind; ADR-001, ADR-003).
The work is an *authorization* change in three layers plus one invariant test.

### 4.1 Single predicate (rename-proof)

New in `core/enums.py`:

```python
VIEWER_ROLE: Final[str] = "read-only"  # becomes "viewer" in Phase 5
```

Predicate, in `identity/scoping.py` (where `_is_superadmin` already lives):

```python
def is_viewer(user: User | None) -> bool:
    """True only for an authenticated user whose role is the viewer role."""
```

`is_viewer` is **deny-only**: anonymous, `None`, token, and service identities are not
viewers. This is what keeps the webhook receiver, `AllowAny` login/logout and
`IngestionToken` requests unaffected.

### 4.2 Layer 1 — resource write-resolution (defence in depth, covers 35 call sites)

`identity/scoping.py::resolve_case(user, case_id, *, write=False)` already returns the
case only when the caller may write it (`is_case_owner` / share grant). Add: when
`write=True` **and** `is_viewer(user)` → `None` (uniform 404/403 downstream).

Blast radius is deliberately wide and already audited: **35 `write=True` call sites**
across `ui/views.py`, `alerts/views.py`, `automation/views.py`; every one is a mutation
path (verified 2026-10-12). `cases/views.py::_case_access` delegates to `resolve_case`,
so the JSON case API inherits the rule for free.

Mirror the same `write` flag onto the two other org-owned resolvers so alert and
observable mutations cannot be reached with only a role-blind lookup:
`resolve_alert(user, alert_id, *, write=False)` and
`resolve_observable(user, observable_id, *, write=False)`, then pass `write=True` from
their mutating call sites (UI + API). Read call sites are untouched.

Deliberate consequence: a viewer's `GET` on a GET+POST view that resolves with
`write=True` **before** branching (today `case_merge` at `ui/views.py:1935`,
`case_apply_template` at `:1062`) returns 404. That is correct — those screens exist only
to perform a write — and their entry points are hidden in Layer 3.

### 4.3 Layer 2 — fail-closed nets (the actual guarantee)

**JSON API** — additive DRF permission in `identity/permissions.py`:

```python
class ViewerReadOnly(BasePermission):
    """Deny unsafe methods to a viewer. Additive: never denies a non-viewer."""

    message = "Your role is read-only."

    def has_permission(self, request, view) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return not is_viewer(getattr(request, "user", None))
```

Registered in `amalthea/settings/base.py` `DEFAULT_PERMISSION_CLASSES` **after**
`IsAuthenticated` (which keeps `401` semantics for anonymous, `403` for a viewer).
Notes:
- `compat/views.py` login/logout already set `@authentication_classes([]) @permission_classes([AllowAny])`
  → unaffected.
- `ingest/views.py::webhook_alerts` is a plain Django view (`@csrf_exempt @require_POST`)
  → DRF permissions do not apply.
- `POST /api/v1/query` (a read-through-POST) is denied for a viewer. This is **intentional**
  and consistent with the standing `ScopePermission` decision (a read-scope API key is
  likewise denied there); viewers use the safe verbs.

**UI** — a decorator for every mutation view, applied *before* any parsing:

```python
def not_viewer(view):           # returns ui/404.html, status=404, logs a warning
```

Applied to every mutating UI view, including those already covered by Layer 1, so that
the gate is local, visible and uniform (and so GET+POST views are gate-first).
Reuse `render(request, "ui/404.html", status=404)` — same shape as the existing
`ui/views.py:656` playbook gate, i.e. **no existence oracle** for viewers.

### 4.4 Layer 3 — affordances (UX, not security)

`ui/views.py::_base_context` gains `"can_mutate": not is_viewer(request.user)`. Templates
hide mutation controls for viewers (case status/severity/cycle buttons, note form, task
form, tag toggles, extract/re-extract, escalate/merge, apply-template, share management,
procedure form, playbook authoring links, observable edit/delete, attachment upload).
`ui/static/ui/keys.js` must not advertise mutation shortcuts to viewers.
The server gate, never the template, is the security boundary.

### 4.5 ADR

New `docs/decisions/ADR-006-role-semantics.md`: four roles; viewer = read-only, enforced
server-side and fail-closed; precedence viewer > share; self-service auth exempt;
TheHive share vocabulary out of scope.

## 5. DB schema

No new tables, no new columns, no new indexes (role is not a query predicate anywhere).

Phase 5 (rename) only:

1. `RemoveConstraint("identity_user_role_valid")`
2. `RunPython` — `User.objects.filter(role="read-only").update(role="viewer")`
   (reversible: viewer → read-only)
3. `AddConstraint` regenerated from `USER_ROLE_VALUES` (now containing `viewer`)

`DEFAULT_USER_ROLE` stays `"analyst"` (`identity/models.py:15`). The constraint is already
derived from `core/enums.USER_ROLE_VALUES` via `in_values(...)`, so only the enum change +
migration regeneration is required. Migration must be applied inside the same release as
the code change (constraint refuses the new value until step 3, and refuses the old value
after it).

## 6. API contract

| Surface | Viewer | Note |
|---|---|---|
| Any unsafe verb on any DRF endpoint | **403** | from `ViewerReadOnly`, before scoping ⇒ uniform |
| Any safe verb | unchanged (200/404/…) | shares/scoping still apply |
| `POST /api/v1/login`, `/logout` | allowed | `AllowAny` |
| `POST /api/v1/alerts/webhook/<id>` | unaffected | non-DRF, token-authenticated |
| `POST /api/v1/query` | **403** | read-through-POST; see §4.3 |
| `share.profileName` (`write`/`read-only`) | unchanged | TheHive vocabulary, not roles |
| `user_json.role` | value changes to `viewer` (Q1) | Amalthea extension field → deviation record |

## 7. UI behaviour

- Every mutating `ui-*` route returns **404** to a viewer (uniform, no oracle).
- Allow-list: `login`, `logout`, `ui-password-change` (302/200 as today).
- Read routes unchanged (200), including `ui-case-attachment-download` and case export.
- Mutation controls are hidden for viewers (§4.4); shortcut cheatsheet omits them.

## 8. Security

- **Threat:** direct request forgery / affordance bypass — covered, since the gate is
  server-side and precedes parsing (`not_viewer` runs before `request.POST` is read).
- **Threat:** privilege escalation via share grants (`ui/views.py:1832::case_share_list`
  currently allows an owning-org `read-only` member to *grant* shares, because
  `is_case_owner` is an org predicate) — closed by the decorator + Layer 1.
- **Threat:** privilege escalation via user admin/role field — already `superAdmin`-only
  in the UI; Phase 3 test asserts a viewer POST to the settings routes is denied.
- **Threat:** API-key composition — a viewer may hold a `readwrite` key; the role rule
  still denies writes (role ∧ scope both required). Tested.
- **Threat:** machine identities — `IngestionToken` is not a `User`; `is_viewer` returns
  False for it. Webhook path is non-DRF (unaffected), asserted by an existing test.
- **Fail-closed invariant** (§9) is the control that prevents future drift.
- **Observability:** denied UI attempts log `logger.warning` with the route + user id
  (no payload, no secrets). No `AuditLog` row (a non-mutation); revisit if needed.

## 9. Testing

1. `tests/test_roles.py` (unit) — `is_viewer` truth table (viewer / analyst / admin /
   superAdmin / anonymous / `None` / API-key owner / service identity).
2. `tests/conformance/test_viewer_readonly_ui.py` (**the invariant**) — for **every**
   `ui`-namespace route, `POST` as a viewer (dummy body) must **never** return 2xx/3xx;
   allow-list `{login, logout, ui-password-change}` asserted explicitly. Gate-first is
   what makes this assertion meaningful (a gate-less view would 302 via its own
   validation path).
3. `tests/conformance/test_viewer_readonly_api.py` (**the invariant**) — for every
   registered JSON route, unsafe verbs as a viewer ⇒ 403 (405 when the verb is
   unsupported); no unsafe verb may 2xx/3xx.
4. `tests/test_viewer_reads.py` — a viewer's reads still work (case list/detail, alert
   detail, observable detail, attachment list+download, export, WS subscribe handshake).
5. `tests/conformance/test_viewer_precedence.py` — viewer **with** a read-write case
   share still gets 404/403 on mutations (share cannot override role); and a *viewer*
   cannot grant a share.
6. Regression: existing suites must stay green — notably `test_authz.py`
   (API-key matrix), `test_tenant_scoping.py` (`_UI_WRITE_ONLY_ROUTES`,
   l.835-865), `test_ui_p1_wiring.py`, `test_playbook_authoring.py`.
7. Phase 5: migration test (forward/reverse on a `read-only` row), plus authn test that
   `role="viewer"` satisfies the constraint and `role="read-only"` no longer does.

Gate target: `make check` green, expected ≈ **1538 → ~1590 passed / 3 skipped**.

## 10. Phases

| Phase | Deliverable | ACs |
|---|---|---|
| **P1** | `VIEWER_ROLE` in `core/enums.py`; `is_viewer` in `identity/scoping.py`; `tests/test_roles.py`; ADR-006 | AC1 `is_viewer` truth table passes. AC2 no import cycle (`core` ← `identity`); mypy clean. AC3 ADR-006 published. |
| **P2** | Layer 1: viewer check in `resolve_case(write=True)`; `write` flag on `resolve_alert`/`resolve_observable` + mutating call sites updated | AC1 viewer → `None` on write resolution for case/alert/observable. AC2 all `write=True` call sites are mutation paths (audited list in the plan). AC3 reads unaffected (test 4 green). |
| **P3** | Layer 2: `ViewerReadOnly` in `DEFAULT_PERMISSION_CLASSES`; `not_viewer` decorator on all mutating UI views | AC1 tests 2+3 green. AC2 login/logout/password-change still reachable. AC3 webhook + login `AllowAny` unaffected. AC4 `POST /api/v1/query` → 403 documented. |
| **P4** | Layer 3: `can_mutate` context + template/`keys.js` affordances | AC1 viewer sees no mutation controls on case/alert/observable/playbook/settings pages. AC2 `aria-hidden`/labels per §6 style rules; no CSP violations (`test_ui_inline_assets.py` green). AC3 keyboard shortcuts do not advertise mutations. |
| **P5** | Rename `read-only` → `viewer` (gated on Q1): `core/enums.py`, migration, `identity/admin.py`, docs | AC1 migration forward/reverse tested. AC2 no `"read-only"` **role** literal remains in code (only the share `profileName` literal). AC3 `share_json` unchanged. |
| **P6** | Docs: `TODO.md` §11.1 → resolved; `COMPLETED.md`; `docs/spec/deviations.md`; plan status → Implemented + verification/review reports | AC1 every deviation recorded. AC2 independent `verifier` + `security-auditor` pass, reports stored under `docs/verification/` and `docs/reviews/`. |

Sequencing: P1 → P2 → P3 → P4 are answer-invariant and can be implemented now; P5 is
gated on Q1; P6 after verification.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Machine/service account inadvertently carrying the viewer role, breaking automation | `is_viewer` is deny-only and never matches anonymous/token identities; webhook is non-DRF; superAdmin bypass; documented in ADR-006 |
| Template hiding treated as the control | Explicit: server gate is the boundary; tests 2–5 bypass templates entirely |
| Enumeration test rots (routes added, test not updated) | The test *derives* the route list from the URLconf (`reverse`/resolver walk), so a new route is auto-covered; the allow-list is asserted, not silently tolerated |
| Rename is an API-visible value change (`user_json.role`) | Documented as a deviation; `share.permissions`/"profileName" untouched; single release |
| Over-blocking reads (GET paths resolving `write=True`) | Audited: only `case_merge`/`case_apply_template` GETs are affected — write-only screens, intentional; test 4 pins viewer reads |
| `POST /api/v1/query` denial surprises a viewer client | Documented in §4.3/§6 + deviation; consistent with the existing read-scope-key decision |
| Constraint/migration ordering window | Single release; migration order drop → update → add; tested forward and reverse |

## 12. Open Questions (need answers before P5; P1–P4 proceed with defaults)

1. **Rename the stored value?** Set the role value (and label) to `viewer` — 1 enum constant
   + 1 data migration + docs, and `user_json.role` returns `"viewer"` (Amalthea extension
   field) — or keep `read-only` as the stored value and only relabel in the UI?
   *Default: rename; TheHive share vocabulary left alone.*
2. **Precedence:** does the role win over an explicit read-write case share?
   *Default: yes (role wins) — otherwise "read-only" is not a guarantee.*
3. **Denial shape:** UI **404** (existing playbook precedent, no oracle) + API **403**
   (`ViewerReadOnly`, consistent with `ScopePermission`)? *Default: yes, as specified.*
3b. **superAdmin bypass** a viewer-role user who is also superAdmin?
   *Default: yes (superAdmin wins, matching `_is_superadmin`).*
3b-alt. If superAdmin should **not** bypass, `is_viewer` must be checked before
   `_is_superadmin` in `resolve_case`. (Cheap either way.)
4. **Carve-outs:** confirm **no** writes at all for a viewer — no comments, no tasks, no
   attachments, no extractions — and that `{login, logout, ui-password-change}` is the
   complete self-service exemption.
   *Default: no carve-outs, exemption list as stated.*
4b. **Reads to keep?** Confirm a viewer may download attachments and export a case (pure
   reads). *Default: yes.*
5. **API keys:** confirm a viewer holding a `readwrite`-scoped key is still denied writes.
   *Default: yes.*

---

## 13. Implementation log (2026-10-12)

All six phases landed in one uncommitted batch on top of `a3478d0`. Gate: `make check` green —
**1617 passed / 3 skipped** (3 pre-existing Postgres-only skips; 1620 collected), ruff + ruff format +
mypy (105 files) + `makemigrations --check` clean. The workstream adds **73 tests across 7 files**
(`tests/test_roles.py` + the six `test_viewer_*` / `test_viewer_readonly_*` suites), against a
1547-test baseline.

### Answers to §12 (binding)

| Q | Answer |
|---|---|
| 1 | Rename the **stored value and label** to `viewer`. TheHive *share* `profileName` untouched. |
| 2 | The **role wins** over a write share — no mechanism may assign write to a viewer. |
| 3 | UI **404**, API **403**. SuperAdmin is **never** a viewer (no bypass needed). |
| 3b | N/A by decision 4: an account cannot be both. The code is nonetheless stated positively — the viewer deny is the **first** statement in all three resolvers, so it holds ahead of the superAdmin branch (see `test_the_viewer_deny_precedes_the_privileged_branch`). |
| 4 | **No carve-outs.** Exemption list is exactly `{login, logout, ui-password-change}`. |
| 4b | Reads kept, including attachment download and case export. |
| 5 | A viewer with a `readwrite` key is still denied (key creation is admin-only). |

### Phase results

| Phase | Result | Evidence |
|---|---|---|
| P1 | **Met.** `VIEWER_ROLE` (`"viewer"`), `is_viewer` (deny-only), truth table. | `tests/test_roles.py` (16), `identity/scoping.py` |
| P2 | **Met.** `resolve_case(write=True)`/`resolve_alert`/`resolve_observable` deny a viewer; 5 UI mutation sites pass `write=True`. | `tests/test_roles.py`, `tests/conformance/test_viewer_precedence.py` |
| P3 | **Met** (AC4 deviation). `ViewerReadOnly` global; `not_viewer` on 38 UI views; route-enumeration sweeps prove it. | `tests/conformance/test_viewer_readonly_ui.py` (6), `test_viewer_readonly_api.py` (10) |
| P4 | **Met.** `can_mutate` in `_base_context`; 7 templates guarded; read affordances preserved. | `tests/conformance/test_viewer_affordances.py` (8) |
| P5 | **Met.** Enum + `identity/migrations/0008_role_viewer_rename.py` (drop CHECK → `RunPython` → `AlterField` → re-add CHECK), reversible. | `tests/conformance/test_viewer_role_rename.py` (3) |
| P6 | **Met.** This log, `docs/spec/deviations.md` "Wave 6.16-P5.1", `TODO.md` §11.1/§11.4, `COMPLETED.md`; ADR-006 → Accepted. | this section + the two reports |

### Added beyond the plan

- `tests/conformance/test_viewer_reads.py` (22) — the read inventory §9.4 asked for (every read page,
  case export, attachment list/download, the API GETs, the case-room subscribe + `sync` replay).
- `tests/conformance/test_viewer_precedence.py` (8) — §9.5 (role beats share, in both directions, with
  a non-viewer control that keeps the denial from passing vacuously), plus the monkeypatched ordering
  test that keeps the viewer deny above every privileged branch.
- `tests/conformance/test_viewer_role_rename.py` (3) — the forward/reverse migration + constraint
  boundary test §9.7 asked for.
- The viewer deny is now the **first** statement of all three resolvers (`resolve_case` had it below the
  superAdmin branch; `resolve_alert`/`resolve_observable` skipped it for a superAdmin). Decision 4 makes
  the combination unreachable, so this is ordering discipline rather than a behaviour change for real
  accounts — but it is the ordering the ADR claims, and it is now pinned (see R2-L1 above).

### Pre-existing bugs found and fixed by the new tests

- `cases/views.py::_as_uuid` raised `AttributeError` for a `UUID` instance. The UI attachment-download
  route uses a `<uuid:attachment_id>` converter, so **every** UI download 500'd while the API
  (`<str:...>`) worked — invisible because no test fetched the link. Fixed (`isinstance` fast path +
  `AttributeError` in the except); pinned by `test_a_viewer_can_list_attachments_and_reach_the_download`.
- `ui/views.py::not_viewer` logged `request.path` (percent-decoded, and `<str:case_id>` accepts `%0A`)
  — CWE-117 log forgery, found by the security review. Now logs the resolver's `url_name` + method.

### Post-verification remediation

- Verification (`VERIFY-2026-10-12-viewer-role-enforcement.md`) found §9.4/§9.5/§9.7 were implemented
  but not *covered*; the three files above close that gap rather than downgrading the claim.
- Security review (`REVIEW-2026-10-12-viewer-role-enforcement.md`, rounds 1–2) — no Critical/High.
  L1 (CWE-117 log forging) fixed as above. Round 2 added **R2-L1** (the V6b ordering test was vacuous —
  fixed by the deny-first change and a monkeypatched ordering test) and **R2-I1** (`django.request`'s
  404 logging still writes `request.path`). L2 (Django admin sits outside the role model) and R2-I1 stay
  open at Low/Info and are tracked in `TODO.md §11.5` — not merely described in
  `docs/spec/deviations.md` "Wave 6.16-P5.1". L3 (template guards are UX; the route-enumeration sweeps are
  the CI-enforced guarantee) accepted.
