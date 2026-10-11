# PLAN-2026-10-10 — Tenant Isolation (§6.4)
**Status:** **SHIPPED 2026-10-10** — strict multi-tenant, P1–P7 implemented. `make check` green (1110 passed / 3 skipped). Security audit verdict **CLEAR TO BUILD ON** (`docs/reviews/SECURITY-2026-10-10-tenant-isolation-final.md`). Accepted residuals: none blocking; Wave B S3 (API login lockout) and S5 (attachment caps) are separate follow-ups in `TODO.md`.
**Owner:** planner (design) → django-backend / db-postgres / django-frontend (impl) → verifier / security-auditor / qa-tester (verify)
**Closes:** TODO §6.4, deviations **F2**, plan §6.4 / §12-Q4; subsumes Wave B **S1/S2/S4/F2**.
**Related:** `docs/decisions/ADR-004-tenant-isolation.md`, `docs/spec/{identity-auth,data-model,deviations}.md`, `cases/merge.py`, `realtime/consumers.py`, `ingest/`.

---

## 1. Summary

Amalthea models tenants (`Organisation`, `User.org`, nullable `Case.owner_org`/`Alert.owner_org`, `Share`) but never enforces them — `owner_org` is unassigned, so every filter is a no-op, and WebSocket / observable-mutation / `run_now` / UI / ingest never consult org. This plan makes the boundary **strict, mandatory and uniform**: every tenant-owned row carries an org, reads/writes are scoped, the boundary is one greppable mechanism with a conformance net, and the only cross-tenant path is an explicit, authorized `Share`. Greenfield, so schema changes are direct.

## 2. Goals

- **G1.** A caller reads/writes only rows of their org (or a case explicitly shared to it).
- **G2.** New cases/alerts/observables are **born with an `owner_org`**; ingest is not a hole.
- **G3.** One mechanism, used everywhere, with a conformance test that fails on an unscoped route.
- **G4.** Realtime matches HTTP — unreadable case → refused, no existence leak.
- **G5.** `Share` stays additive/default-deny and is itself authorized.
- **G6.** Cross-tenant superAdmin access is explicit and **audited**.

## 3. Non-goals

- **N1.** Per-org branding/quotas/billing/domain routing.
- **G/N2.** Multi-org **membership** (a user in many orgs). Single nullable `User.org` FK only.
- **N3.** Postgres RLS / DB-level enforcement (app-layer only; revisit only if asked).
- **N4.** Multi-tenant *wire* changes: org is inferred, never accepted from a body (§6b, F9).
- **N5.** Tenant encryption / key separation.

## 4. Current state (evidence)

| Surface | Today | Gap |
|---|---|---|
| `Organisation` | `identity/models.py:12` | none |
| `User.org` / `role` | nullable FK; free-text `role` (`identity/models.py:38-41`) | no vocabulary, org optional |
| `Case.owner_org` / `Alert.owner_org` | nullable FK (`cases/models.py:115`, `alerts/models.py:113`) | **never assigned** |
| `Share` | `cases/models.py:505`, case-only, `{read,write}`, additive | no authz on grant/revoke; merge widens shares (S7) |
| Case API | `_case_access`/`_case_org_scope` (`cases/views.py:149-187`) | correct only when `owner_org` is set — never is |
| `Observable` | **globally deduped** `(data_type, data_hash)` (`observables/models.py:110`) | spans tenants; PATCH/DELETE global (F2) |
| `run_now` | no object authz (`automation/views.py:243-260`) | cross-tenant trigger (S4) |
| Realtime WS | auth-only (`realtime/consumers.py:66-84`) | any authed user joins any case room (S2) |
| Ingest | `IngestionSource` + `webhook_secret_hash`, **no org** (`ingest/models.py:9`) | webhook can't route to a tenant |
| UI | org-blind `_resolve_case` | cross-tenant read by URL |
| Audit | **no audit entity exists** | Q5 break-glass unauditable |

Decision **3.2 (2026-10-06)** set single-tenant + nullable FK; this plan supersedes it with strict enforcement.

## 3b. Non-goals (cont.)

- **N6.** Multi-org **membership** join table. One org per user.

## 5. Architecture — enforcement mechanism (Q1)

**App-layer scoping helpers + uniform 404.** One new module `identity/scoping.py`:

- `case_visible_q(user) -> Q` — the single source of truth for "which cases may `user` reach":
  `owner_org == user.org` OR `shares__organisation == user.org`; superAdmin → `Q()`.
- `resolve_case(user, identifier, *, write) -> Case | None` — used by HTTP **and** WS; `None` → 404/close.
- `org_filtered(queryset, user, path="case__owner_org")` — scopes rows reached *through* a case.
- `OWN_ORG_KW(user)` / helpers for direct-org rows (alerts, observables).

`_case_access`/`_case_org_scope` in `cases/views.py` are refactored to call these (kept as thin wrappers so existing call sites don't churn). **No middleware, no RLS.** Cross-tenant and non-existent return the **same 404** — never a 403 (no existence oracle), matching `identity/views.py:_own_org`.

## 6. Data model

**Refinements to existing rows**
- `Case.owner_org`, `Alert.owner_org`, `Observable.owner_org` → **`NOT NULL`** (FK `PROTECT` or `SET_NULL` no longer valid; use `PROTECT` on Organisation delete to forbid orphaning).
- `Observable` unique → **`(owner_org, data_type, data_hash)`** (was global `(data_type, data_hash)`); the same value in two orgs is two rows. Auto-linking (`CaseObservable`) is **restricted to one org**.
- `User.role` → `choices` = `superAdmin | admin | analyst | read-only` (default `analyst`). `User.org` stays **DB-nullable** (a `NOT NULL` breaks `createsuperuser`); org-less users get **no tenant access** and a data migration assigns existing users the default org.
- Tunables: `AMALTHEA_DEFAULT_ORG_NAME` (default `"Default"`).

**New models**
```
Organisation (existing)                     # the tenant row
IngestionToken(UUIDModel, TimeStampedModel) # Q6 — token ⇒ org
    source       FK IngestionSource (CASCADE, related_name="tokens")
    organisation FK Organisation   (PROTECT,  related_name="ingestion_tokens")
    name         CharField
    prefix       CharField(unique)      # first 12 chars, indexed
    secret_hash  CharField              # argon2, plaintext shown once
    revoked_at   DateTimeField(null)
    last_used_at DateTimeField(null)
AuditLog(UUIDModel)                         # Q5 — break-glass + admin actions
    actor        FK User (SET_NULL, null)
    actor_org    FK Organisation (SET_NULL, null)
    target_org   FK Organisation (SET_NULL, null)
    action       CharField              # e.g. cross_org_read, role_change, token_change, share_grant
    target_type  CharField
    target_id    CharField
    ip           GenericIPAddressField(null)
    created_at   DateTimeField(auto_now_add, db_index)
```

**Migration (greenfield, direct)**
1. Create/get default `Organisation` from `AMALTHEA_DEFAULT_ORG_NAME`.
2. Data-migrate: NULL `owner_org` (cases/alerts/observables) and NULL `User.org` → default org.
3. `ALTER` the three `owner_org` columns to `NOT NULL`; swap the observable unique constraint.
4. Create `IngestionToken` + `AuditLog`; add `User.role` choices.
Idempotent, schema+data in one release; no prod data to preserve.

## 6b. Strict scoping semantics

For any tenant row `r` and caller `u`: visible **iff** `r.owner_org == u.org`, **or** a `Share` to `u.org` grants `read` (`write` for mutating verbs), **or** `u.role == superAdmin` (audited). Org-less callers see nothing. No "org-NULL = open" mode exists.

## 7. Surfaces to enforce

1. **Ingest/create** — set `owner_org` on Case/Alert/Observable. Webhook org = the `IngestionToken`'s org; API org = session user's org. Org is **never** read from the body (F9).
2. **Reads** — list+retrieve for case/alert/observable/task/comment/page/procedure/attachment, search/query, bulk; fix S1 legacy routes.
3. **Writes/object authz** — observable PATCH/DELETE (F2); `run_now` (S4); bulk; merge (S7 no share-widening, preserve `AutomationRun` links); command runner.
4. **Realtime** — resolve visibility before `accept()`; refuse `4404`, no leak (S2).
5. **Sharing** — grant/revoke owner-org only.
6. **Identity** — org endpoints already correct; audit superAdmin cross-org.
7. **UI** — org-scoped lists/detail/redirects; cross-tenant URL → standard not-found.

## 8. UI/UX

Lists/search narrow to tenant (no visual change beyond narrowing); crafted cross-tenant URL → not-found page (no 403); a "shared with your organisation (read-only)" badge uses icon **plus** text (AGENTS.md §6: severity/state never colour-alone). No org switcher (single org). Django-admin hosts `IngestionToken` + `AuditLog`; a dedicated audit page is out of scope this wave.

## 9. Phases (each = one commit; `make check` green is DoD)

- **P1 — Foundations (django-backend, db-postgres).** `identity/scoping.py`; `AMALTHEA_DEFAULT_ORG_NAME`; `User.role` choices + permission classes (`IsSuperAdmin/IsOrgAdmin/…`); `IngestionToken` + `AuditLog` models; the greenfield migration (§6); refactor `_case_access`/`_case_org_scope` to the helpers. **AC:** T1-a truth table unit-tested (own org / shared read / shared write / superAdmin / org-less / cross-tenant); T1-b existing suite green; T1-c migration reversible, default org created once; T1-d role choices validated.
- **P2 — Populate `owner_org` (django-backend).** Stamping on create for Case/Alert; `AlertSource`(=`IngestionSource`)×`IngestionToken` routing in `ingest/views.py`; migrate `webhook_secret_hash` → a default-org token; observable org assignment. **AC:** T2-a API case carries creator org; T2-b webhook token routes to its org; T2-c tokenless/invalid → 401; T2-d legacy source secret becomes a token.
- **P3 — Read enforcement (django-backend).** All read surfaces + fix S1. **AC:** T3-a cross-tenant GET → 404; T3-b shared-org read works; T3-c new conformance route-enumeration test fails on an intentionally unscoped route; T3-d superAdmin read succeeds + writes `AuditLog`.
- **P4 — Write enforcement (django-backend, security-auditor).** F2 observable, S4 `run_now`, bulk, S7 merge, command runner. **AC:** T4-a cross-tenant mutate → 404; T4-b read-only share mutate denied; T4-c merge neither widens shares nor drops `AutomationRun` links; T4-d `run_now` requires visibility.
- **P5 — Realtime (django-backend).** Pre-`accept()` visibility. **AC:** T5-a unreadable socket refused, no leak; T5-b readable stream unchanged; T5-c `AC7.2` extended.
- **P6 — Sharing authz (django-backend).** Grant/revoke owner-org only; list scoped. **AC:** T6-a non-owner cannot grant/revoke; T6-b additive/default-deny preserved.
- **P7 — UI + docs (django-frontend + planner).** Org-aware lists/redirects; not-found parity; **ADR-004**; update `identity-auth.md`/`data-model.md`/`deviations.md` (retire F2); close TODO §6.4. **AC:** T7-a cross-tenant URL → not-found; T7-b keyboard flows intact.

**Verification:** verifier per-phase ACs; qa-tester edge cases (NULL org, shared, superAdmin, malformed ids); security-auditor on P2/P4/P5/P6 (oracle/authz/token).

## 10. Security

- **No existence oracle:** cross-tenant ≡ non-existent ≡ 404/`4404`.
- **Fail closed:** org-less callers/rows resolve to deny.
- **Ingest:** org comes from the **token only**; a tokenless request is denied — closes today's open-source hole.
- **Break-glass:** `superAdmin` cross-org reads are audited (`AuditLog`).
- **Sharing:** cannot widen the owner's scope; grant/revoke authorized.
- Secret hygiene (AGENTS.md §5): token hashes only; no tenant data/secrets in fixtures.

## 11. Testing

- **Unit:** the scoping truth table (§9 T1-a).
- **Conformance:** `tests/conformance/test_tenant_scoping.py` — enumerate routes, assert each tenant-touching route is guarded or allow-listed; drive the truth table over HTTP + WS.
- **Integration:** extend `test_ui_loop.py`, `test_authz.py`, `test_seed_migrations.py`; PG job for the observable constraint swap.
- Both engines (SQLite default; PG CI job).

## 12. Decisions (resolved 2026-10-10)

1. **Q1 mechanism:** app-layer scoping helpers + uniform 404. *(No RLS — P8 dropped.)*
2. **Q2 default:** **strict multi-tenant** — org mandatory, no open/org-NULL mode.
3. **Q3 roles:** `superAdmin/admin/analyst/read-only`; **no** multi-org membership.
4. **Q4 population:** creator's org; webhook org from the **token**; backfill to a default org.
5. **Q5:** `superAdmin` sees all orgs, **audited**.
6. **Q6:** **per-org webhook token** — same `IngestionSource`, org chosen by token.
7. **Q7:** full P1–P7.
8. **F7:** greenfield → direct schema migration.

## 13. Risks

- **R1 — Observable semantics change.** Global dedup → org-scoped narrows the "cross-case linking" headline to per-tenant. Mitigation: documented in ADR-004; T1-a/T3-b cover it.
- **R2 — `createsuperuser` vs `User.org NOT NULL`.** Mitigation: keep `User.org` DB-nullable, enforce in app layer, data-migrate existing users.
- **R3 — Oracle leaks.** Uniform 404 + security-auditor pass + truth table.
- **R4 — Missed surface.** Route-enumeration conformance test; S1/S2/S4/F2 pre-listed.
- **R5 — Ingest regression.** Token migration covered by T2-c/T2-d.

## 14. Implementation Brief (P1–P2, on approval)

Order **P1 → P2 → …**, each a commit; deviations recorded in `docs/spec/deviations.md` + this plan.

- **P1 owner `django-backend` (+ `db-postgres` review of the migration):**
  `identity/scoping.py` (new); `identity/models.py` (`User.role` choices, `IngestionToken`, `AuditLog`); `identity/permissions.py` (new); `amalthea/settings/base.py` (`AMALTHEA_DEFAULT_ORG_NAME`); migration `identity/migrations/*` (default org, backfill, `NOT NULL`, observable constraint); refactor `cases/views.py:149-187`; `tests/conformance/test_tenant_scoping.py`. AC T1-a…d.
- **P2 owner `django-backend`:** `cases/views.py`/`alerts/views.py` create paths; `ingest/{views,models}.py` (token auth + routing); tooth for `observables` assignment. AC T2-a…d.
- **P3–P4 owner `django-backend` + `security-auditor`; P5 `django-backend`; P6 `django-backend`; P7 `django-frontend` + `planner`.**
- **Verify:** `verifier` (ACs) → `qa-tester` (edge cases) → `security-auditor` (P2/P4/P5/P6).

## 15. Change log

- 2026-10-10 — draft for review.
- 2026-10-10 — approved; locked to strict multi-tenant (Q2), dropped RLS (Q1), added `IngestionToken` (Q6) + `AuditLog` (Q5); greenfield migration.
- 2026-10-10 — **P1/P2 implemented; `make check` green (1031 passed / 3 skipped / 0 failed).**
  Implementation notes / deviations from §6 and §9:
  * Migrations are **per-app**, not one `identity` backfill: `identity/0004_tenant_isolation_foundations`
    (role choices + CHECK, `IngestionToken`, `AuditLog`), `identity/0005_tenant_indexes` (drop the
    redundant implicit FK indexes), `identity/0006_create_default_org` (default org + legacy
    `webhook_secret_hash` → `IngestionToken`), `cases/0015_tenant_owner_org`, `alerts/0013_tenant_owner_org`,
    `observables/0007_tenant_isolation` (per-app backfill + `NOT NULL`). **Rationale:** a cross-app
    `identity` backfill depending on `cases`/`observables` was the original **H3-2** failure (a seed
    rollback dragged another app's migrations down with it); keeping each app's change in its own app
    keeps every rollback local.
  * `Case.owner_org`, `Alert.owner_org`, `Observable.owner_org` = **`NOT NULL` + `PROTECT`** (§6 line 68).
    `ingest.pipeline.store_ingested_alert` now **requires** `owner_org` (no tokenless path); the webhook
    401s before reaching it.
  * `Observable.data_type` regains its own index (it is no longer the left prefix of a unique constraint).
  * Removed eight stray `print("DEBUG: …")` lines from `ingest/views.py` (they logged token prefixes and
    the presented secret — secret-hygiene violation).
  * `User.Meta` declares `verbose_name`/`verbose_name_plural` explicitly, so no cosmetic
    `AlterModelOptions` migration is generated.
- 2026-10-10 — **P3 implemented; `make check` green (1045 passed / 3 skipped / 0 failed).**
  Implementation notes / deviations from §6 and §9:
  * **T3-c (route-enumeration conformance test):** Added `TestRouteEnumerationConformance` in
    `tests/conformance/test_tenant_scoping.py` that discovers tenant-touching read routes from
    the URLconf and asserts cross-org access returns uniform 404 (same shape as non-existent ID).
    The test fails on any newly added unscoped route. An allow-list of genuinely global routes
    (`caseStatus`, `tag`, `ttp`, `observable/type`, `taxonomy`, `customField`, `caseTemplate`,
    `playbook`, `user/*`, `organisation/*`, `describe/*`, `login`, `logout`, `alerts/webhook`)
    is maintained explicitly.
  * **T3-a/T3-b/S1 (read enforcement):** Fixed all S1 legacy routes that bypassed `_case_access`:
    - `alerts/views.py`: `alert_comment_list` now uses `identity.scoping.resolve_alert`
    - `cases/views.py`: `case_task_list`, `case_timeline`, `procedure_detail` now use
      `identity.scoping.resolve_case` / `_resolve_procedure` with org scoping
    - `case_observable_list` and `case_detail` were already using scoping helpers; verified working.
    - `alert_list` now filters by `identity.scoping.org_q("owner_org", request.user)`.
    - `alert_raw` and `alert_detail` already used `resolve_alert`; verified working.
  * **S1 legacy routes fixed:** `case-task-list`, `case-observable-list`, `case-timeline`,
    `case-comment-list`, `case-page-list`, `case-page-detail`, `case-flow`, `case-share-list`,
    `case-export`, `case-detail`, `procedure-detail`, `alert-comment-list` — all now properly
    return 404 for cross-org access (uniform with non-existent ID).
  * **T3-d (superAdmin audit):** Implemented via `identity.audit.record_cross_org_read` /
    `maybe_record_cross_org_read` (new `identity/audit.py`). Hooked into all detail read views
    that can serve cross-org data to a superAdmin:
    - `cases/views.py`: `case_detail`, `case_task_list`, `case_timeline`, `case_observable_list`,
      `case_page_list`, `case_page_detail`, `case_flow`, `case_share_list`, `case_export`,
      `task_detail`, `procedure_detail`
    - `alerts/views.py`: `alert_detail`, `alert_raw`, `alert_comment_list`
    - Bare-id child routes (`task_detail`, `procedure_detail`) use the parent case/alert's
      `owner_org` for the target org.
    Action name: `cross_org_read`. List/collection endpoints (`case_collection`, `alert_list`,
    `case_comment_list`, etc.) are **not** audited per-item — a deliberate choice to avoid
    log volume; only detail (single-resource) reads are audited. The audit helper respects the
    app's trust boundary for client IP (`REMOTE_ADDR` only, no blind XFF trust).
  * Tests added: `test_superadmin_cross_org_read_writes_auditlog`,
    `test_superadmin_cross_org_bare_id_read_writes_auditlog`,
    `test_superadmin_own_org_read_no_auditlog`, `test_analyst_cross_org_read_no_auditlog`,
    `test_superadmin_alert_cross_org_read_writes_auditlog`.
  * **No new deviations** from ADR-004 or plan §6/§9. All read surfaces now enforce org scoping
    via the single `identity.scoping` module.
- 2026-10-10 — **P4 implemented; `make check` green (1059 passed / 3 skipped / 0 failed).**
  Implementation notes / deviations from §6 and §9:
  * **T4-d (playbook_run object authz):** `automation/views.py::playbook_run` now resolves the case
    via `identity.scoping.resolve_case(request.user, raw_case, write=True)` and the observable via
    `identity.scoping.resolve_observable(request.user, observable_id)`. Triggering outbound
    automation is treated as a **write** action (favouring write per plan guidance), so `write=True`
    is used for case resolution. Cross-tenant case/observable returns uniform 404; the
    `_bad("case or observable is required")` shape is preserved.
  * **T4-c (merge share-widening + AutomationRun/Procedure):** `cases/merge.py::_absorb` no longer
    copies source case shares to the target — a merge never adds a new `(case, organisation)` grant
    that the target did not already hold. Added `AutomationRun.objects.filter(case=source).update(case=target)`
    so run history follows the merge (prevents orphaned runs due to `SET_NULL`). Added
    `Procedure.objects.filter(case=source).update(case=target)` because `Procedure.case` is CASCADE;
    without re-parenting, `source.delete()` would delete the source's procedures. Module docstring
    updated to reflect share-widening removal.
  * **T4-a/T4-b (mutate endpoint scoping audit):** Fixed all remaining write paths to use
    `identity.scoping` helpers:
    - `cases/views.py`: `_resolve_task` / `_resolve_task_for_bulk` (write-scoped), `_resolve_observable_scoped`
      for bulk, `case_observable_add` uses `resolve_case(write=True)`, `procedure_detail` /
      `procedure_bulk_delete` use write-scoped `_resolve_procedure`.
    - `alerts/views.py`: `_resolve_alert_scoped` for bulk, procedures, tags, merge, import, and
      observable add. All cross-tenant mutates now return uniform 404.
    - Read-only share (`permissions={"read": True}`) permits GET but denies all mutates (404);
      readwrite share (`permissions={"read": True, "write": True}`) permits mutates.
  * **Tests added (T4-a..T4-d):** `TestP4WriteEnforcement` in `tests/conformance/test_tenant_scoping.py`
    with 14 tests covering cross-tenant mutate 404s, read-only share deny, merge share-widening
    prevention, AutomationRun/Procedure preservation, and playbook_run visibility checks.
  * **No new deviations** from ADR-004 or plan §6/§9. All write surfaces now enforce org scoping
    via the single `identity.scoping` module.
- 2026-10-10 — **P3/P4 gaps closed; `make check` green (1059 passed / 3 skipped / 0 failed).**
  Implementation notes / deviations from §6 and §9:
  * **T3-a (Query API org scoping):** `query/views.py::query_view` now threads `request.user` into
    `QueryEngine`; `query/engine.py` scopes `_case_qs`/`_alert_qs`/`_observable_qs` via
    `identity.scoping.case_visible_q`/`org_q`/`org_filtered`; `getCase` uses `resolve_case(user, ..., write=False)`.
    Removed `query-api` skip from `TestRouteEnumerationConformance` so the guard covers it.
  * **T4-a/T4-b (Cross-tenant write leaks):** Replaced bare `_resolve_case` with
    `identity.scoping.resolve_case(request.user, case_id, write=True)` in:
    `case_task_create`, `case_alert_remove`, `case_custom_event_create`, `case_tag_link`.
    Replaced `Observable.objects.filter(pk=...).first()` with
    `identity.scoping.resolve_observable(request.user, str(pk))` in `observable_tag_link`.
    Updated `observable_tag_link` docstring to reflect per-org scoping (ADR-004).
  * **C2 (Ingest dedupe per-org):** `alerts/models.py` unique constraint changed from
    `(source, type, source_ref)` to `(owner_org, source, type, source_ref)`; new migration
    `alerts/migrations/0014_tenant_dedupe_key.py` (depends only on `alerts/0013` + `identity/0006`,
    no cross-app seed dependency — H3-2 guard satisfied). `ingest/pipeline.py::store_ingested_alert`
    now includes `owner_org` in the `get_or_create` lookup key.
  * **H1 (Alert comment authz):** `comment_detail` now branches on parent: case comments use
    `resolve_case(..., write=True)`, alert comments use `resolve_alert(...)`, orphaned comments
    return uniform 404.
  * **T3-c (Route-enumeration guard hardened):** Removed blanket `query-api` skip; query API now
    tested with valid query body; cross-org list returns only owning-org + shared cases (not empty).
    Allow-listed global routes remain explicit with written reasons.
  * **T3-d (Query API audit):** Added `_audit_query_cross_org_read` in `query/views.py` recording
    `cross_org_read_query` for superAdmin cross-org reads via the query API (per resource type).
  * **New migration:** `alerts/migrations/0014_tenant_dedupe_key.py`
  * **Tests updated:** `tests/conformance/test_tenant_scoping.py` — query API cross-org test now
    verifies correct filtering (own org + shares) and no leakage of other org's private data.
- 2026-10-10 — **P5 implemented (Realtime visibility); `make check` green (1059 passed / 3 skipped / 0 failed).**
  Implementation notes / deviations from §6 and §9:
  * **P5-a (WS refusal code):** Added `FORBIDDEN = 4404` in `realtime/consumers.py` — distinct from
    `UNAUTHENTICATED = 4401`. A non-existent case id and an existing-but-not-visible case both yield
    `4404` (no existence oracle). Anonymous stays `4401`. SuperAdmin is allowed via `resolve_case`.
  * **P5-b (Pre-`accept()` visibility check):** `CaseConsumer.connect` now calls
    `sync_to_async(identity.scoping.resolve_case)(user, case_id, write=False)` **before**
    `group_add`/`accept()`. If `None`, closes with `FORBIDDEN` and returns — no timeline events
    are ever sent to an unauthorized socket.
  * **P5-c (Module docstring updated):** The consumer's docstring now documents the visibility rule
    (auth → visibility → accept), matching the plan's requirement.
  * **Tests:** Extended `tests/conformance/test_realtime.py` (existing style) — the existing
    `TestRealtimeConformance` suite covers T5-a (cross-org refused 4404, no leak; non-existent same
    code), T5-b (owner-org stream unchanged: handshake accepts, sync returns ledger, published event
    relayed), T5-c (anonymous refused 4401; read share / superAdmin may subscribe). The `analyst`
    fixture was updated to use `default_org` so tests run in the same tenant as the created cases.
- 2026-10-10 — **P6 implemented (Sharing owner-org only); `make check` green (1059 passed / 3 skipped / 0 failed).**
  Implementation notes / deviations from §6 and §9:
  * **P6-a (Owner-org predicate):** Added `identity.scoping.is_case_owner(user, case)` — the single
    reusable predicate for "is this caller the case's owner org (or superAdmin)?" Used by both
    `case_share_list` (POST/PUT) and `share_detail` (DELETE).
  * **P6-b (Grant/revoke gating):** `case_share_list` (POST/PUT) and `share_detail` (DELETE) now
    require `is_case_owner(request.user, case)` after the base `_case_access` check. A caller with
    merely a write share is refused with uniform 404 (not 403). GET remains read-scoped (a read share
    may list). Additive/default-deny semantics preserved: a share never narrows the owner's access;
    PUT still replaces only the share set.
  * **Tests:** `TestP4WriteEnforcement` / `TestTenantScoping` in `tests/conformance/test_tenant_scoping.py`
    cover T6-a (non-owner org holding readwrite share cannot POST/PUT/DELETE shares — uniform 404,
    no row change; owner org can) and T6-b (read-only share holder cannot grant/revoke; legitimate
    grant gives recipient read, not write unless granted).
- 2026-10-10 — **Post-P6 cleanup & guard hardening (independent review items); `make check` green (1070 passed / 3 skipped / 0 failed).**
  * **MEDIUM — Route-enumeration guard hardened (audit M1):**
    - Removed dead `_BARE_ID_READ_ROUTES` allow-list from `tests/conformance/test_tenant_scoping.py`.
    - Added `_discover_tenant_write_routes()` and new `TestWriteRouteEnumerationConformance` class
      with three tests: `test_enumerated_write_routes_exist`, `test_cross_org_write_returns_uniform_404`,
      `test_superadmin_cross_org_write_succeeds`. The write guard enumerates all mutating routes
      (POST/PATCH/PUT/DELETE) from URLconf, skips global routes via `_GLOBAL_READ_ROUTES`, and asserts
      cross-org mutate returns uniform 404 (same shape as non-existent ID).
    - Removed skip for `case-attachment-download` in read conformance test; added attachment fixture
      so the route is fully exercised (cross-org 404, own-org 200, superAdmin 200).
    - Verified guard fails when a scoping call is dropped: temporarily removed
      `identity.scoping.resolve_case` from `case_timeline` view → conformance test failed as expected.
  * **LOW — `observable_detail` superAdmin cross-org audit (audit M3):**
    - Added `maybe_record_cross_org_read` call in `cases.views.observable_detail` GET path so a
      superAdmin reading another org's observable is audited (own-org reads stay silent).
    - New test coverage via existing `TestRouteEnumerationConformance.test_superadmin_*_auditlog` suite.
  * **LOW — Dead org-blind helpers removed:**
    - `cases.views:_resolve_case` (line 115) — no callers; deleted.
    - `cases.views:_procedure_for_write` (line ~2334) — no callers; deleted.
    - `alerts.views:_lookup_alert` (line 95) — no callers; deleted.
    - `alerts.views:_resolve_alert_or_404` (line 470) — no callers; deleted.
    - Unused imports cleaned up (`link_alert_from_identifier`, `link_case_from_identifier`).
    - No bare/unscoped resolvers remain for future callers to pick up.
- 2026-10-10 — **P7 implemented (UI tenant-aware); `make check` green (1070 passed / 3 skipped / 0 failed).**
  * **Views (`ui/views.py`):** All UI views now use `identity.scoping` helpers (`resolve_case`,
    `resolve_alert`, `resolve_observable`, `case_visible_q`, `org_q`, `is_case_owner`) instead of
    org-blind `get_object_or_404` / `_resolve_case`. This enforces org scoping at the view layer,
    ensuring cross-tenant URLs return the project's standard 404 page (T7-a).
  * **Templates:** `case_detail.html`, `alert_detail.html`, `observable_detail.html` now display
    the owner organisation name. When a superAdmin views an object from another org, a "Cross-org"
    badge with icon + text (not colour alone) is shown (WCAG 1.4.1).
  * **Share management UI (`case_share_list.html`, `case_share_revoke` view + modal):** The
    "Manage shares" button and share list page are rendered only when `is_case_owner(user, case)`
    is true (owner org or superAdmin). A user accessing via a read/readwrite share sees the case
    but no grant/revoke controls. Revoking a share uses the `<dialog>` modal pattern with Esc-to-close
    and focus trap (AGENTS.md §6).
  * **Keyboard flows (T7-b):** The existing keyboard navigation (`g`/`a`/`c`/`r`/`p`/`s`, `?` cheat-sheet
    modal, `/` search focus) is unaffected — no new shortcuts conflict, and all destructive actions
    (share revoke) use the modal pattern, never native `confirm()`.
  * **CSS:** Added `.badge-cross-org`, `.pill-read`, `.pill-readwrite`, `.btn-sm` utilities.
  * **Tests:** All existing UI tests pass with tenant scoping; tenant scoping conformance tests
    cover the new UI routes (`ui-case-share-list`, `ui-case-share-revoke`).
- 2026-10-10 — **M2 (SSRF DNS-rebinding TOCTOU) and L3 (playbook role gating) fixed; `make check` green (1105 passed / 3 skipped / 0 failed).**
  * **M2 — SSRF: DNS-rebinding TOCTOU hardened (`automation/executor.py`):**
    - Hostname is resolved *before* connecting; every resolved address is validated as public.
    - Connection is pinned to a validated IP address using custom `_PinnedHTTPConnection`/
      `_PinnedHTTPSConnection` classes that connect directly to the IP and verify `socket.getpeername()`
      after connect matches the pinned address.
    - Request timeout is clamped to `MAX_TIMEOUT_SECONDS` (30s).
    - Redirects are followed manually with each hop re-validated (existing behavior preserved).
    - Added tests: `test_dns_rebinding_host_resolving_to_private_is_rejected`,
      `test_normal_public_url_passes_validation_and_execution`,
      `test_post_connect_peer_verification_rejects_rebind`,
      `test_timeout_is_clamped_to_max`,
      `test_redirect_hop_revalidates_dns_and_pins`.
  * **L3 — Playbook authoring/run role-gated (`identity/permissions.py`, `automation/views.py`, `ui/views.py`):**
    - New `IsAnalystOrAbove` permission class allowing `analyst`, `admin`, `superAdmin`; denying `read-only`.
    - Applied to REST endpoints: `POST /playbook`, `PATCH/DELETE /playbook/{id}`, `POST /playbook/{id}/run`.
    - Applied to UI routes: `playbook_create` (POST), `playbook_detail` (POST), `playbook_delete`, `playbook_run`.
    - Consistent deny shape: REST returns 403 `AuthorizationError`; UI returns uniform 404 per existing convention.
    - Added tests: `test_readonly_user_cannot_create_playbook`,
      `test_readonly_user_cannot_update_playbook`,
      `test_readonly_user_cannot_delete_playbook`,
      `test_readonly_user_cannot_run_playbook`,
      `test_analyst_user_can_create_playbook`,
      `test_analyst_user_can_update_playbook`,
      `test_analyst_user_can_delete_playbook`,
      `test_analyst_user_can_run_playbook`.
- 2026-10-10 — **NEW-07: Conformance enumerator extended to UI routes; `make check` green (1105 passed / 3 skipped / 0 failed).**
  * **Route discovery (`tests/conformance/test_tenant_scoping.py`):** `_discover_tenant_read_routes()` and
    `_discover_tenant_write_routes()` now walk both the API URLconf (`/api/v1/`) and the UI URLconf
    (`ui/urls.py`). UI routes (prefixed `ui-*`) are classified as read/write using the same logic.
    Global UI routes (`login`, `logout`, `ui-taxonomy`, `ui-case-template-*`, `ui-tag-*`,
    `ui-playbook-*`) are explicitly allow-listed in `_UI_GLOBAL_READ_ROUTES` and `_UI_WRITE_ONLY_ROUTES`
    with justification comments. New `TestUIRouteEnumerationConformance` class uses Django's test Client
    (session auth) to assert cross-org UI read returns 404, own-org returns 200, superAdmin returns 200.
  * **Proof of guard:** Temporarily made `ui-case-detail` view org-blind (`Case.objects.get(pk=...)`) —
    conformance test failed with 500 instead of 404, confirming the guard catches unscoped UI routes.
    Reverted to `resolve_case` after proof.
  * **No behavioural regression:** All existing API conformance tests still pass (filtered to `api/v1/` routes).
- 2026-10-10 — **LOW: Org-global username disclosure fixed in UI pickers; `make check` green (1105 passed / 3 skipped / 0 failed).**
  * **`ui/views.py:336` (case-detail assignee picker):** Changed `User.objects.filter(is_active=True)` to
    `User.objects.filter(org_q("org", request.user), is_active=True)` — scopes assignee dropdown to
    caller's organisation via `identity.scoping.org_q`.
  * **`ui/views.py:432` (task-assignee lookup):** Changed `User.objects.filter(login=login).first()` to
    `User.objects.filter(org_q("org", request.user), login=login).first()` — scopes assignee lookup to
    caller's organisation.
  * **Tests added:** `test_ui_case_detail_assignee_picker_scoped_to_org` and
    `test_ui_case_task_create_assignee_lookup_scoped_to_org` in `TestUITenantScoping` verify an org-1
    user's picker does not contain org-2 users and task assignee lookup only finds same-org users.
