# PLAN — Every user belongs to an organisation (`User.org` NOT NULL)

- **Date:** 2026-10-13
- **Status:** Approved 2026-10-13 — decisions recorded in §14; implementation in progress
- **Owner:** planner
- **Supersedes/amends:** `docs/decisions/ADR-004-tenant-isolation.md` (§21, §36 — the promised `User.org` backfill + `NOT NULL` was never written)
- **New ADR:** `docs/decisions/ADR-007-user-org-required.md`
- **Triggered by:** case-list "New case" modal defect investigation — `ui/templates/ui/case_list.html:13` offers the button for any `can_mutate` user, but `:47` renders the dialog only when `form_values` exists, which `ui/views.py:530` supplies only when `request.user.org is not None`. A user with no organisation therefore got a button whose dialog does not exist: `modal.js` (correctly) lets the anchor navigate, so the "modal" silently degraded to the standalone `/cases/new` page. Root cause is an **org-less user being representable at all**.

## 1. Summary

`identity.User.org` is `ForeignKey(Organisation, null=True, blank=True, on_delete=SET_NULL)`. Keeping it nullable was always documented as a workaround for exactly one thing — Django's `createsuperuser` — and ADR-004 §36 already promised the backfill-and-`NOT NULL` step that was never implemented. The org-less state has since leaked into the UI (a "No organisation" rail indicator, an explanatory `case_create` page, a documented API 403, a dead branch in the admin add-form) and produced at least one real broken affordance.

This plan makes organisation membership a **structural invariant**: `org` becomes `NOT NULL` with `on_delete=PROTECT`, existing org-less rows are backfilled to the default organisation, and every supported user-creation path supplies an organisation. The org-less concept is then deleted from the UI, the serializers and the API contract.

## 2. Goals

1. `identity.User.org` is `NOT NULL` — an org-less user cannot exist in the database.
2. `createsuperuser` (and `create_user`) keep working with zero new required flags; they default to the `AMALTHEA_DEFAULT_ORG_NAME` organisation.
3. Existing org-less rows are backfilled by a reversible data migration; the default org is guaranteed to exist first (`identity/0006_create_default_org` already does this).
4. No supported path (UI, API, Django admin, service layer) can create or unset an org-less user.
5. The org-less branches are removed from the UI/API contract and docs, and the case-list button/dialog gates collapse to a single gate — fixing the reported defect by construction.
6. `make check` stays green; the fail-closed tenancy behaviour remains covered by tests (as defence-in-depth, using stub actors).

## 3. Non-goals

- **No new RBAC model.** Roles are unchanged (ADR-006 stands); `superAdmin` still spans all organisations.
- **No org-membership join table / multi-org users.** One user, one org — unchanged by this plan.
- **No change to `owner_org`** on `Case`/`Alert`/`Observable`/`IngestionToken` — already `NOT NULL` where required.
- **No new management command** (unless §12 Q2 is answered "require explicit `--org`").
- **Not fixing** the unrelated docs/README defects found during discovery — `README.md` references `make setup` / `make dev`, which do not exist in the `Makefile`; `AMALTHEA_DEFAULT_ORG_NAME` is absent from `.env.example`. (The latter is folded in as T6-d because it documents this plan's behaviour; the former is recorded in `TODO.md`.)

## 4. Architecture / decision

- **ADR-007 (new)** records the invariant: *every user belongs to exactly one organisation, enforced by the database.*
- **ADR-004 is amended, not replaced:** §36's promised step is delivered here; §21's rationale ("`NOT NULL` is avoided because it breaks `createsuperuser`") is superseded because a custom manager now supplies the org.
- **Bootstrap is already solved.** `identity/migrations/0006_create_default_org.py` `get_or_create`s the organisation named by `settings.AMALTHEA_DEFAULT_ORG_NAME` (default `"Default"`). There is no chicken-and-egg: the org always exists by the time any user is created. This plan adds **no** new bootstrap step.
- **`on_delete` must change.** `SET_NULL` is invalid with `null=False` (Django raises `fields.E320`). Options are `PROTECT` (recommended), `RESTRICT`, or `CASCADE`. See §12 Q1.

## 5. Data model & migration

### 5.1 Model change (`identity/models.py:50-52`)

```python
org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="users")
```

(`null=False, blank=False` implied.) Note `related_name="users"` is preserved — `identity/views.py:170`, the org-delete member counts (`ui/views.py:2577`) and admin rely on it.

### 5.2 Migration `identity/migrations/0009_user_org_required.py`

Dependencies: `("identity", "0008_role_viewer_rename")` only. **Self-contained** — never `apps.get_model` another app, which is the rule `0006` documents (and violates).

Operations, in order:

1. `RunPython(backfill_user_org, migrations.RunPython.noop)`
   - `org, _ = Organisation.objects.get_or_create(name=getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default"))`
   - `User.objects.filter(org__isnull=True).update(org=org)`
   - Count the affected rows and emit them via `print`-less logging (migration `RunPython` has no logger); the AC asserts the count in a test instead.
2. `migrations.AlterField(model_name="user", name="org", field=<PROTECT FK>)`

Reversibility: reverse is `noop` for the data step (the backfill is not un-doable, and un-doing it would recreate the state we are eliminating) plus the inverse `AlterField` back to `SET_NULL`/nullable, matching `0002`.

### 5.3 Default org knob

`settings.AMALTHEA_DEFAULT_ORG_NAME` (`amalthea/settings/base.py:210`) already exists. T6-d adds it to `.env.example`.

## 6. API contract

| Surface | Current | After |
|---|---|---|
| `POST /api/v1/case` with an org-less actor | 403 `AuthorizationError` (two-key body), documented at `docs/spec/api.md:162` | Unreachable. The service-level guard in `cases/create.py:119-120` is **kept** as defence-in-depth but no longer reachable via HTTP. §12 Q3. |
| `core/serializers.py:212` (`user_json`) | `"org": user.org.name if user.org else None` | `"org": user.org.name` |
| `core/serializers.py:576-577` (`user_admin_json`) | `"org"`/`"orgId"` nullable | `"org": user.org.name`, `"orgId": str(user.org_id)` |
| `compat/views.py:126-128` (login `OutputUser`) | `org = user.org  # no query when org_id is NULL` | `user.org_id` used directly, or `select_related("org")` to avoid an N+1 on the login path |
| `PATCH /api/v1/user/<id>` unsetting org (`identity/views.py:528-534`) | silently ignored when `org is None` | must reject an explicit attempt to unset (validation error), not ignore it |

TheHive compatibility is **improved**, not harmed: TheHive users always have an organisation, so a non-null `orgId` is closer to the reference contract. AC: no conformance test asserts a null `orgId`/`org` for a user (to be confirmed in P4).

## 7. UI

| File | Change |
|---|---|
| `ui/views.py:530` | `if not is_viewer(request.user) and request.user.org is not None:` → `if not is_viewer(request.user):`. This is the actual defect fix: `form_values` is now always available for a non-viewer, so `case_list.html:13` and `:47` evaluate identically. |
| `ui/views.py` `case_create` (~:519-528) | Delete the no-org explanatory branch. POST/GET can no longer arrive without an org. |
| `ui/templates/ui/case_list.html:13,47` | Keep both gates but make them literally identical (`can_mutate`), and gate the empty-state "Open one" link (`:44`) the same way. |
| `ui/templates/ui/base.html:107-115` | Restructure the tenant indicator: `{% if request.user.is_authenticated and request.user.org %}{{ request.user.org.name }}{% elif <superAdmin> %}All organisations link{% endif %}`. Must **not** regress the anonymous case: `ui/views.py:1922 handler500` renders `500.html` (which extends `base.html`) with no `_base_context`, and `request.user` is then `AnonymousUser` with no `org` attribute. |
| `ui/templates/ui/settings_users.html:34` | `{{ user.org.name }}` (drop `|default:"—"`). |
| `ui/static/ui/keys.js` | No change needed: the `n` handler already falls back to the button when the dialog is absent, and the dialog is now always present. |

## 8. Phases & tasks (with acceptance criteria)

### P1 — Schema, migration, user creation (django-backend, **db-postgres review**)

- **T1-a** `identity/models.py`: `org` → `PROTECT`, non-null.
  - **AC:** `python manage.py makemigrations --check` reports no drift after T1-b; `manage.py check` clean; `fields.E320` not raised.
- **T1-b** Add `identity/migrations/0009_user_org_required.py` per §5.2.
  - **AC:** migrate forward on SQLite **and** verify the generated SQL for Postgres (`sqlmigrate identity 0009`); migration is reversible (`migrate identity 0008` succeeds); an org-less user row created before the migration ends up in the default org.
- **T1-c** Custom manager: `create_user`/`create_superuser` default `org` to the `AMALTHEA_DEFAULT_ORG_NAME` organisation when it is not supplied.
  - **AC:** `createsuperuser --noinput` with `DJANGO_SUPERUSER_PASSWORD`/`_EMAIL` succeeds and the new superuser's `org` is the default org; `User.objects.create_user(username=…)` (no org) also lands in the default org.
- **T1-d** `identity/admin.py:18-21`: make `org` a required field in `add_fieldsets`.
  - **AC:** the admin add-user form rejects a blank org.
- **T1-e** Service/API can no longer unset an org: `identity/services.py:374-376`, `identity/views.py:528-534`, `ui/views.py:2835-2836`.
  - **AC:** a request that tries to null the org is rejected with a validation error (not silently ignored); a legitimate reassignment to another existing org still works and is audited.

### P2 — Remove the org-less concept (django-backend)

- **T2-a** `core/serializers.py:212,576-577` and `compat/views.py:126-128` per §6.
  - **AC:** `user_json`/`user_admin_json` always emit a non-null `org`/`orgId`; the TheHive login response is unchanged for a user with an org; no extra query on the login path (assert with `CaptureQueriesContext`).
- **T2-b** `identity/permissions.py`: `IsAuthenticatedAndOrgMember` is defined, exported in `__all__`, and **unused**. Either delete it or wire it into `DEFAULT_PERMISSION_CLASSES`. §12 Q5.
  - **AC:** no dead exported symbol, and `DEFAULT_PERMISSION_CLASSES` is unchanged unless Q5 says otherwise.
- **T2-c** Decide the fate of the remaining defensive `org_id is None` branches (`identity/scoping.py:80-81,95-96,116-117`, `identity/audit.py:115-116`, `query/views.py:78-80`, `cases/create.py:119-120`). §12 Q3.
  - **AC:** each kept branch is covered by a stub-actor test; each deleted branch is noted in `docs/spec/deviations.md`.

### P3 — UI (django-frontend)

- **T3-a** `ui/views.py:530` gate simplification + `case_create` no-org branch removal (§7).
  - **AC:** for every user shape (superAdmin / admin / analyst), `/cases` renders the button **and** `id="new-case-modal"`; `/cases?new=1` renders `data-autoopen`; clicking no longer navigates.
- **T3-b** `base.html` tenant indicator + `settings_users.html`.
  - **AC:** an authenticated user sees their org name; a `superAdmin` still gets the "All organisations" link; the anonymous 500 rendering does not show a stray "No organisation" string; no template raises on `request.user.org`.
- **T3-c** Keep the `n` shortcut and the cheat-sheet row consistent with the button now always being present.
  - **AC:** `tests/conformance/test_ui_nav.py` rail/ROUTES/cheat-sheet parity stays green; `_NON_NAV_KEYS` unchanged.

### P4 — Tests (qa-tester)

- **T4-a** Fix every test that constructs an org-less **user**:
  - explicit `org=None`: `tests/conformance/test_ui_case_create.py:80-85` (+ its consumers incl. `:774`), `tests/conformance/test_tenant_scoping.py:160-164,245-249,299-303,2091-2092`, `tests/conformance/test_ui_p1_wiring.py:670-678`, `tests/test_roles.py:104`;
  - **no `org` argument at all** (would now hit `IntegrityError`): `tests/conformance/test_enum_contracts.py:262`, `tests/conformance/test_ui_loop.py:165`, `tests/conformance/test_unknown_fields.py:441`, `tests/conformance/test_viewer_readonly_api.py:66`, `tests/conformance/test_viewer_readonly_ui.py:103`.
  - Note: `tests/conformance/test_observable_rehash.py:63`, `test_ui_loop.py:83,584`, `test_t2_p4_surface.py:53`, `test_query_api.py:88,97,117`, `test_realtime.py:109` use `org=None` for **Case/Alert/Observable** helpers and must **not** be touched.
  - **AC:** no test constructs an org-less `User`; the `make_user` fixture (already `org=default_org`) is used instead of ad-hoc `User.objects.create`.
- **T4-b** Preserve the fail-closed tenancy truth-table rows using an **unsaved stub actor** (e.g. `User(id=uuid4(), org_id=None, role="analyst")` with `is_authenticated=True`) rather than a persisted row.
  - **AC:** `case_visible_q`/`org_q`/`org_filtered`/`resolve_case` still return deny/no-access for an actor with `org_id is None`.
- **T4-c** New tests:
  - `User.objects.create(..., org=None)` raises `IntegrityError`;
  - `create_user`/`create_superuser` without `org` → default org (T1-c);
  - the `0009` backfill moves an org-less row into the default org (exercise the migration function via `apps`);
  - deleting an org that still has members raises `ProtectedError`, and the UI reassign/delete flow still succeeds;
  - the admin add-form requires org;
  - `/cases` button+dialog parity for all three roles (regression for the reported defect).
  - **AC:** all new tests fail against the pre-change schema and pass after it.
- **T4-d** Confirm no conformance test asserts a null `orgId`/`org` for a user.
  - **AC:** grep-verified; any counter-example is listed as a deviation rather than silently deleted.

### P5 — Verification & security (verifier, security-auditor)

- **T5-a** `verifier`: AC-by-AC report in `docs/verification/VERIFY-2026-10-13-non-null-user-org.md` against the final diff.
- **T5-b** `security-auditor`: review of (i) the **backfill** — assigning pre-existing org-less users to the default org grants them *visibility of that org's cases*, a genuine privilege change that must be called out; (ii) `PROTECT` preventing accidental user deletion; (iii) the removal of the API's org-less 403 and whether any endpoint now trusts an implicit org it should not; (iv) `manage.py check --deploy`.
  - **AC:** 0 Critical / 0 High; any Medium/Low recorded in `docs/spec/deviations.md`.

### P5b — `on_delete` and Postgres review (db-postgres)

- **T5b-a** Confirm `PROTECT` vs the existing org-delete flow (`ui/views.py:2645` `settings_organisations_delete`, `identity/services.py` `delete_organisation`, `members=reassign|delete`) and the org-delete conformance tests.
  - **AC:** deleting a non-empty org still returns the existing friendly error, never a 500; reassign and delete member flows still pass.
- **T5b-b** Review the Postgres `ALTER … SET NOT NULL` for lock/timeout implications on `identity_user`.
  - **AC:** documented statement + recommended runbook note in the ADR.

### P6 — Documentation (planner / ir-docs)

- **T6-a** `docs/decisions/ADR-007-user-org-required.md` (new).
- **T6-b** Amend ADR-004 §21/§36 with a "delivered in ADR-007" note.
- **T6-c** Update `docs/spec/api.md:162` (drop the org-less 403), `docs/spec/identity-auth.md:6-9`, `docs/spec/data-model.md:26-28`.
- **T6-d** `.env.example`: add `AMALTHEA_DEFAULT_ORG_NAME`.
- **T6-e** Historical docs (`docs/planning/PLAN-2026-10-10-tenant-isolation.md:69`, `PLAN-2026-10-13-ui-case-create.md:147,163,208-209`, `docs/verification/VERIFY-2026-10-13-ui-case-create.md:73`) are **not rewritten** — append a superseding note only.
- **T6-f** `docs/spec/deviations.md` wave entry; `TODO.md` + `COMPLETED.md` bookkeeping.
- **AC:** after every `.md` edit, `.venv/bin/ruff format <file.md>` (the repo formats Python fences inside Markdown); `make check` green; `scripts/lock.sh --check` green **after** the commit.

## 9. Security

- **Backfill = privilege change (must be reviewed).** An org-less user today sees nothing (`case_visible_q` → `pk__in=[]`). After backfill to the default org they see/act on that org's cases. For a fresh greenfield deployment this is a no-op (no org-less rows exist, T1-b AC); for any deployment that has one it is a *deliberate* grant. T5-i covers it; the ADR must state it.
- **`PROTECT` is a safety improvement:** today `SET_NULL` can silently orphan every member of a deleted org. `PROTECT` makes an unsafe delete impossible at the database level, and the UI already prefers an explicit reassign/delete choice.
- **No new trust boundary.** `owner_org` continues to come from the server-side actor's org, never from client input (confirmed for `create_case` in `REVIEW-2026-10-13-ui-case-create.md`).
- **Fail-closed guards stay** (T2-c): removing them would make the system depend on an invariant that a future migration/bulk-`update()` could bypass.
- **Secret hygiene:** no secrets touched. `scripts/secret-scan.sh` unchanged.

## 10. Testing

- Gate: `make check` (ruff + format check + mypy + pytest). Current baseline: **1689 passed / 3 skipped**.
- Tests run on in-memory SQLite (`amalthea/settings/test.py`, `NAME=":memory:"`), so parallel pytest invocations are safe; the SQLite table-rebuild path for `AlterField` is exercised there, and the Postgres SQL is reviewed via `sqlmigrate` (T1-b).
- Explicitly out of scope: extending coverage of `ui/static/ui/*.js` (no JS test harness exists; the defect is fixed server-side by construction).

## 11. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | `createsuperuser` becomes broken for operators (the single reason the column was nullable) | T1-c manager default + T1-c AC runs `createsuperuser --noinput` |
| R2 | `on_delete` change alters org-delete semantics; a non-empty delete now raises | T5b-a; existing org-delete conformance tests must stay green |
| R3 | Postgres `ALTER SET NOT NULL` takes an `ACCESS EXCLUSIVE` lock on `identity_user` | T5b-b statement review + runbook note; table is tiny |
| R4 | TheHive-compat clients expecting `orgId: null` | Non-null is closer to TheHive's own contract; T4 confirms no test asserts null; noted in ADR-007 |
| R5 | Data migration on a DB that *does* contain org-less users silently grants access | Explicitly documented (§9) + T6-a; the migration is reversible up to the point of `AlterField` |
| R6 | A test-suite-wide fixture sweep (T4-a touches ~11 sites) introduces noise in the diff | Scope tightly: only user construction; Case/Alert/Observable `org=None` helpers untouched, listed explicitly in T4-a |
| R7 | Django admin or a future bulk `update()` bypasses the invariant | Non-null is a DB constraint, not a form rule — `bulk_update`/`QuerySet.update()` cannot write NULL |

## 12. Open questions (all resolved 2026-10-13 — answers in §14)

1. **`on_delete`:** `PROTECT` (recommended — an org with members cannot be deleted; the UI already forces reassign-or-delete), `RESTRICT`, or `CASCADE` (deleting an org silently deletes its users)?
2. **`createsuperuser` ergonomics:** auto-assign the `AMALTHEA_DEFAULT_ORG_NAME` organisation when `org` is omitted (recommended; zero friction, no new flags, `--noinput` still works) — or require an explicit organisation via a new management command (`--org`), which would make `createsuperuser` unusable unless every operator passes it?
3. **Defence-in-depth:** keep the now-unreachable `org_id is None` guards in `identity/scoping.py` / `identity/audit.py` / `query/views.py` / `cases/create.py` (recommended — cheap, fail-closed, testable with stub actors), or delete them as dead code?
4. **API contract:** is it acceptable to **remove** the documented "actor with `org=None` → 403" behaviour from `docs/spec/api.md:162` (it becomes unreachable), or should it stay documented as a defensive guarantee?
5. **`IsAuthenticatedAndOrgMember`** (`identity/permissions.py`, exported but never wired): delete it, or add it to `DEFAULT_PERMISSION_CLASSES` now that every authenticated user is guaranteed to be an org member?
6. **Backfill target:** confirm that any pre-existing org-less user should be assigned to the `AMALTHEA_DEFAULT_ORG_NAME` organisation (not, say, deactivated instead). And confirm the backfill should **not** change their `role`.

## 13. Acceptance criteria (wave-level)

1. `identity.User.org` is `NOT NULL`; `manage.py check` and `makemigrations --check` are clean.
2. `createsuperuser --noinput`, `create_user` without `org`, and the Django admin add-form all yield a user in a real organisation.
3. No UI, API, admin or service path can create or unset an org-less user; a request that tries is rejected with a validation error.
4. The case-list button and its modal share exactly one gate for every user shape; `/cases` and `/cases?new=1` are verified for superAdmin/admin/analyst.
5. `identity/0009` backfills org-less rows, is reversible to `0008`, and the Postgres SQL has been reviewed.
6. The fail-closed tenancy behaviour is still covered by tests, using stub actors.
7. `make check` green (≥ 1689 passed / 3 skipped, plus the new tests); mypy file count unchanged or updated in the `Makefile` banner.
8. ADR-007 written; ADR-004 amended; `docs/spec/api.md`, `identity-auth.md`, `data-model.md`, `.env.example` updated; `docs/spec/deviations.md`, `TODO.md`, `COMPLETED.md` updated.
9. Verification report records every AC as met/not-met/deviated; security review records 0 Critical / 0 High.

## 14. Decisions (approved 2026-10-13)

| # | Question | Decision |
|---|---|---|
| 1 | `on_delete` | **`PROTECT`** — an organisation that still has members cannot be deleted; the UI already forces an explicit `members=reassign\|delete` choice (`ui/views.py:2645`), so this only turns an implicit rule into a database constraint. T5b-a confirms the existing flow and tests still pass. |
| 2 | `createsuperuser` ergonomics | **Auto-assign** the `AMALTHEA_DEFAULT_ORG_NAME` organisation when `org` is omitted, from a custom manager. `org` is *not* added to `REQUIRED_FIELDS`, so `createsuperuser` and `createsuperuser --noinput` keep working unchanged. |
| 3 | Defence-in-depth | **Keep** every now-unreachable `org_id is None` guard (`identity/scoping.py`, `identity/audit.py`, `query/views.py`, `cases/create.py:119-120`). Each is covered by a stub-actor test (T4-b); none is deleted. |
| 4 | API contract | **Remove** the org-less 403 from `docs/spec/api.md:162` — it becomes unreachable. The *behaviour* of the guard in `cases/create.py` is unchanged (Q3); only the documented contract is retracted. |
| 5 | `IsAuthenticatedAndOrgMember` | **Wire it into `DEFAULT_PERMISSION_CLASSES`** (`amalthea/settings/base.py:149`), as a global fail-closed guard for present and future endpoints — the same rationale ADR-006 used for registering `ViewerReadOnly` globally. Placed **after** `IsAuthenticated` so an anonymous caller is still a 401. |
| 6 | Backfill target | Assign pre-existing org-less users to the org named by `AMALTHEA_DEFAULT_ORG_NAME`; **do not** change their `role` and **do not** deactivate them. |

### 14.1 Safety check performed for decision 5

Adding a global permission class is the one decision that can break unrelated surfaces, so it was verified before approval:

- **The ingest webhook is not affected.** It is a plain (non-DRF) Django view mounted by `ingest/urls.py` at `api/v1/`, so `DEFAULT_PERMISSION_CLASSES` never evaluates it. This is stated in `amalthea/settings/base.py:162` and was independently confirmed by `docs/verification/VERIFY-2026-10-12-viewer-role-enforcement.md` (P3) and `test_viewer_readonly_api.py:149`. The MVP loop (AGENTS.md §4 step 1) therefore keeps working.
- **The only `permission_classes` overrides in non-test code are `compat/views.py:70,142`** (`AllowAny` on login/logout, intentional — an explicit list replaces the defaults outright for those views).
- **`IsAuthenticatedAndOrgMember` is already implemented** (`identity/permissions.py:100-116`) as "authenticated **and** `org_id is not None`", is exported in `__all__`, and is currently unused. After this wave the invariant makes it a no-op for real users and a deny for anything that ever bypasses the constraint.
- **Ordering:** it must sit after `IsAuthenticated` so anonymous callers still receive 401, not 403.
- **Residual effect to document:** an authenticated user with no org would now receive 403 on *every* API endpoint rather than producing empty scoped results. That is the intended fail-closed behaviour and is recorded in ADR-007.

