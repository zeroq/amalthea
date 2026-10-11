# Verification Report: PLAN-2026-10-10 — Tenant Isolation (P3 Read + P4 Write Enforcement)

Plan: `docs/planning/PLAN-2026-10-10-tenant-isolation.md` (§6b, §7, §9 P3/P4, §10, §15)
ADR: `docs/decisions/ADR-004-tenant-isolation.md`
Prior report: `docs/planning/VERIFY-2026-10-10-tenant-isolation-P1P2.md`
Verified: 2026-10-10 16:27 UTC (branch `main` @ `4c4c5f8`, working tree **uncommitted**)
Scope: Phases **P3** (read enforcement, ACs **T3-a…T3-d**) and **P4** (write enforcement, ACs **T4-a…T4-d**).
Automated gate: `make check` → **1059 passed, 3 skipped, 0 failed** (re-run by verifier); `tests/conformance/test_tenant_scoping.py` alone → 57 passed.

**Verdict: FAIL (partial).** T3-b, T3-d, T4-c and T4-d are met. **T3-a, T4-a and T4-b are not met**, and **T3-c is a partial guard**: the Query API (`POST /api/v1/query`) reads across tenants unrestricted, and **five cross-tenant write endpoints still succeed**, contradicting §15's "All read/write surfaces now enforce org scoping". These are behavioural defects, not coverage gaps. The route-enumeration test is genuine (empirically fails on a new unscoped GET route) but explicitly skips the Query API.

## Summary

- Total ACs: 8
- Met: 4 (T3-b, T3-d, T4-c, T4-d)
- Not Met: 3 (T3-a, T4-a, T4-b)
- Deviated / partial: 1 (T3-c)
- Unrecorded behavioural deviations: **yes** (§15 claims full enforcement; five write paths leak and the Query API read surface is unscoped).

## Criterion-by-Criterion

### T3-a: cross-tenant GET → uniform 404 (same status + error type/shape as non-existent id)
**Status: Not Met** — the GET-reachable detail routes are correct, but a documented read surface (search/query, §7.2) leaks cross-tenant data.

Evidence (GET routes — correct):
- `tests/conformance/test_tenant_scoping.py::TestHTTPTenantScoping::test_retrieve_case_cross_tenant_404` (:334) asserts cross-tenant `GET /api/v1/case/{id}` → `404` and `resp.json()["type"] == "NotFoundError"`.
- `TestRouteEnumerationConformance::test_cross_org_read_returns_uniform_404` (:990) drives every enumerated GET route and asserts cross-org and fake-id both `404` + `NotFoundError` (cross vs fake share `_not_found("Case")` → identical shape by construction).

Evidence (Query API — **leak**):
- `query/views.py::query_view` (:64-92) never passes `request.user` into the engine.
- `query/engine.py::_start` (:552-557) seeds `listCase`/`listAlert`/`listObservable` from `_case_qs()`/`_alert_qs()`/`_observable_qs()` (:729-748) which are **unfiltered**; `_get_case` (:575-587) resolves any case via unscoped `link_case_from_identifier` (:582).
- Verifier probe (authenticated org-1 user, org-2 data seeded):
  - `POST /api/v1/query {"query":[{"_name":"listCase"}]}` → `200` with org-2's case JSON.
  - `POST /api/v1/query {"query":[{"_name":"getCase","idOrName":"~<org-2-case-id>"}]}` → `200` with org-2's case.
- The conformance test does not catch this: it explicitly `continue`s on `query-api`/`query-api-slash` (`test_tenant_scoping.py:1017` and `:1083`), and no cross-org query test exists (`tests/conformance/test_query_api.py` seeds a single org only).

Notes: The literal AC verb is "GET"; the query surface is POST with read semantics. §7.2 lists "search/query" as a P3 read surface and §15 claims all read surfaces are scoped, so this is in-scope and unrecorded.

### T3-b: shared-org read works (a `read` share grants read)
**Status: Met**

Evidence:
- `identity/scoping.py::case_visible_q` (def :47; share OR at :63) → `Q(owner_org_id=org_id) | Q(shares__organisation_id=org_id)`; `resolve_case` (def :102) applies `shares.filter(permissions__write=True)` when `write=True` (:138-139).
- `TestHTTPTenantScoping::test_shared_case_read_works` (:353) creates `Share(case=case1, organisation=org2, permissions={"read": True})` and asserts the shared org gets `200` on `GET /api/v1/case/{case1.id}` (:369-370).
- `test_shared_read_visible` (:125) and `test_shared_read_resolves_for_get` (:214) cover the truth-table rows; `resolve_case(..., write=True) is None` (:226) proves read-only shares do not grant write.

### T3-c: route-enumeration conformance test exists and fails on an unscoped route (enumerated from URLConf; global routes allow-listed)
**Status: Deviated (guard is real but excludes a currently-unscoped read route)**

Evidence (the guard is genuine — it *does* fail):
- `TestRouteEnumerationConformance` (`test_tenant_scoping.py:879`); `_discover_tenant_read_routes` (:807) walks `get_resolver()` → `URLResolver`/`URLPattern`, filters to `api/v1/`, subtracts the allow-lists, and keeps GET/POST-read routes.
- Verifier empirical proof (temporary pytest plugin appended one deliberately-unscoped `GET /api/v1/unscoped-probe` returning all cases, inserted before the compat catch-all; no repo file touched):
  ```
  E  AssertionError: Conformance failures:
  E    unscoped-probe: cross-org got 200, expected 404
  E    unscoped-probe: fake-id got 200, expected 404
  1 failed
  ```
  (A first attempt appended the route *after* `compat.urls` and failed only on an own-org 404 — route shadowing — demonstrating ordering sensitivity, not a guard weakness.)

Evidence (the loophole):
- The test hard-codes `if name in ("case-attachment-download", "query-api", "query-api-slash"): continue` (`:1017`, repeated `:1083`). `query-api` is exactly the unscoped read surface in T3-a, so the guard cannot fail on the one read route that is currently broken.
- `_BARE_ID_READ_ROUTES` (:795-804) is defined but **never referenced** — dead scaffolding.
- The allow-lists are reasonable but unverified: nothing asserts an allow-listed route is actually global. `case-template-*`/`playbook-*` (definitions), vocabularies, identity and webhook are defensible; the `query-api` skip is not (it is tenant-touching).

### T3-d: superAdmin cross-org read succeeds and writes `AuditLog`; own-org read writes none
**Status: Met**

Evidence:
- Hook: `identity/audit.py::record_cross_org_read` (def :37) writes `AuditLog(..., ip=...)` (create :78, `ip=` :85); `_get_client_ip` reads `REMOTE_ADDR` only (:17-27). `maybe_record_cross_org_read` (def :89) fires only for a superAdmin whose org differs from the target's (:112-124).
- Call sites are in request scope (`cases/views.py` `case_detail` :271, `case_task_list` :490, `case_timeline` :854, `case_observable_list` :699, `case_page_list` :1465, `case_page_detail` :1515, `case_flow` :1565, `case_share_list` :1607, `case_export` :2540, `task_detail` :535, `procedure_detail` :2347; `alerts/views.py` `alert_detail` :83, `alert_raw` :127, `alert_comment_list` :678).
- Tests: `test_superadmin_cross_org_read_writes_auditlog` (:1141) asserts `log.action == "cross_org_read"`, actor/actor_org/target_org/target_type/target_id, and `log.ip is not None` (:1166); `test_superadmin_own_org_read_no_auditlog` (:1186) asserts count 0; `test_analyst_cross_org_read_no_auditlog` (:1205) asserts an analyst's 404 writes nothing; bare-id (`:1168`) and alert (`:1218`) variants pass.

Notes (minor gap): the Query API cross-org read (above) is neither scoped nor audited, so a superAdmin's cross-org query is an unaudited break-glass path. This is a coverage/behaviour gap of T3-d's spirit but is dominated by the T3-a finding.

### T4-a: cross-tenant mutate (PATCH/DELETE/merge) → uniform 404 and row unchanged
**Status: Not Met** — five cross-tenant write endpoints succeed.

Evidence (the tested paths are correct):
- `TestP4WriteEnforcement`: `test_cross_tenant_observable_patch_returns_404` (:1383) asserts `404` + `NotFoundError` + row unchanged; `test_cross_tenant_observable_delete_returns_404` (:1409); `test_cross_tenant_comment_patch_returns_404` (:1428); `test_cross_tenant_task_patch_returns_404` (:1449); `test_cross_tenant_case_merge_returns_404` (:1470).

Evidence (the leaks — verifier probe, org-1 user mutating org-2 resources):
| # | Endpoint | Result | Cause |
|---|---|---|---|
| 1 | `POST /api/v1/case/{org2}/task` `{"title":"pwn"}` | **201**, task created on the victim case | `cases/views.py:448` `case = _resolve_case(case_id)` (unscoped, `:115-119`) |
| 2 | `POST /api/v1/case/{org2}/customEvent` `{"title":"pwn","date":1}` | **201**, timeline event written to victim case | `cases/views.py:771` bare `_resolve_case` |
| 3 | `POST /api/v1/case/{org2}/tag` `{"tags":["pwn"]}` | **200**, victim case tags mutated | `cases/views.py:1336` bare `_resolve_case` |
| 4 | `DELETE /api/v1/case/{org2}/alert/{org2-alert}` | **204**, alert unlinked from victim case (and a ledger entry appended to the victim's timeline, `:731-737`) | `cases/views.py:723` bare `_resolve_case` |
| 5 | `POST /api/v1/observable/{org2}/tag` `{"tags":["pwn"]}` | **200**, victim observable tags mutated | `cases/views.py:1361` — `Observable.objects.filter(pk=pk).first()` with **no** org filter (def :1353) |

The bare resolver is unscoped:
```python
# cases/views.py:115
def _resolve_case(identifier: str) -> Case | None:
    try:
        return link_case_from_identifier(identifier)   # no org filter
```
Route registration confirms these are reachable (`cases/urls.py:22` task, `:33` tag, `:40` alert-remove, `:45` customEvent, `:154` observable tag). None is covered by `TestP4WriteEnforcement`; all are POST/DELETE so the read conformance test skips them.

### T4-b: a `read`-only share denies mutation; a `readwrite` share permits
**Status: Not Met** — the tested surfaces are correct, but read-only shares (and non-shared tenants) can mutate via the unscoped routes.

Evidence (correct):
- `test_readonly_share_allows_get_denies_patch` (:1508) asserts shared-read org gets `200` GET and `404` on case/task PATCH.
- `test_readwrite_share_allows_mutate` (:1550) asserts `200`/`204` for a `{"read":true,"write":true}` share.

Evidence (violation): the routes in T4-a do not consult `resolve_case` at all, so a `read`-only (or entirely unrelated) org can `POST` a task / customEvent / tag and `DELETE` an alert link on the case. The read-only-share denial is therefore only enforced on the paths that happen to call `resolve_case(write=...)`.

### T4-c: merge neither widens shares (no new `(case, organisation)` grant) nor drops `AutomationRun` links
**Status: Met**

Evidence:
- `cases/merge.py::_absorb`: `AutomationRun.objects.filter(case=source).update(case=target)` (:74); `Procedure.objects.filter(case=source).update(case=target)` (:76) (required because `Procedure.case` is CASCADE); shares are **not** copied, with rationale at :87-88.
- `test_merge_does_not_copy_shares` (:1586) asserts org2 does not gain access via merge while org3's pre-existing share survives; `test_merge_preserves_automation_runs` (:1624) asserts `run.case_id == target.id`; `test_merge_preserves_procedures` (:1667) asserts `proc.case_id == target.id`.

### T4-d: `playbook_run` requires visibility of the named case/observable (cross-tenant → 404, no run)
**Status: Met**

Evidence:
- `automation/views.py::playbook_run`: case via `identity.scoping.resolve_case(request.user, str(raw_case), write=True)` (:247); observable via `identity.scoping.resolve_observable(request.user, str(pk))` (:255); cross-tenant → `_not_found("Case")`/`_not_found("Observable")` (:249, :257) before `run_now` (:260).
- `test_playbook_run_cross_tenant_case_returns_404` (:1691) asserts `404` + `NotFoundError` + `AutomationRun.objects.count() == 0`; `test_playbook_run_cross_tenant_observable_returns_404` (:1720) likewise; `test_playbook_run_own_org_returns_202` (:1749) asserts one run linked to the case.
- No existence leak: a non-existent case yields the same `_not_found("Case")` response as a cross-tenant one (both `resolve_case → None`).

## Implementer claims scrutinised

1. **`_case_org_scope` refactor — CONFIRMED.** `cases/views.py:151-158` now returns `identity.scoping.org_q("case__owner_org", request.user)` (a real `Q`), not a `WhereNode`. Call sites `_lookup_task` (:554) and `_lookup_custom_event` (:899) still carry `select_related("case")` but no longer depend on it: `TestCaseOrgScopeRegression` (:1268) applies the `Q` to bare `Task`/`TimelineEvent` querysets and passes (`test_task_list_without_select_related_scoped_to_org` :1276; `..._cross_tenant_denied...` :1327/:1348). The P1/P2 latent risk is closed.
2. **Route-enumeration test — GENUINE, but skips `query-api`.** Empirically fails on a new unscoped GET route (see T3-c). The `continue` on `query-api` is a material loophole because that route is currently unscoped. `_BARE_ID_READ_ROUTES` is dead code.
3. **`playbook_run` — CONFIRMED.** Both subject kinds resolved; 404 shape uniform; no run on cross-tenant (T4-d).
4. **`_absorb` — CONFIRMED.** `AutomationRun` and `Procedure` re-parented; share-copy widening removed (T4-c).
5. **superAdmin audit hook — CONFIRMED request-scoped with `ip`; fired only for superAdmin cross-org reads** (T3-d). Not fired for analysts or own-org reads. It does **not** cover the query path.

## Gaps / recommended follow-ups (ordered by severity)

1. **[Critical — T4-a/T4-b] Scope the five unscoped write endpoints.** Replace the bare `_resolve_case` with `identity.scoping.resolve_case(..., write=True)` in `cases/views.py::case_task_create` (:448), `case_alert_remove` (:723), `case_custom_event_create` (:771), `case_tag_link` (:1336), and use `identity.scoping.resolve_observable` in `observable_tag_link` (:1361). Add a cross-tenant + read-only-share test per endpoint (expected: 404, row unchanged). Closing test name suggestion: `test_cross_tenant_task_create_404`, `test_cross_tenant_custom_event_404`, `test_cross_tenant_case_tag_link_404`, `test_cross_tenant_case_alert_remove_404`, `test_cross_tenant_observable_tag_link_404`, plus a read-only-share variant. The stale "globally deduplicated" docstring at `cases/views.py:1357` must be corrected (observables are per-org per ADR-004 §3).
2. **[Critical — T3-a] Org-scope the Query API.** Thread `request.user` into `QueryEngine` and filter `_case_qs`/`_alert_qs`/`_observable_qs` (and gate `_get_case` via `resolve_case`) through `identity.scoping`. Remove the `query-api` `continue` in `TestRouteEnumerationConformance` (`test_tenant_scoping.py:1017`) so the guard covers it, and add a cross-org query test (a missing sub-case: `test_query_api_cross_org_list_and_get_case_404`). Without this, P3's "all read surfaces scoped" is false.
3. **[High — T3-c] Tighten the guard's coverage.** Delete or implement the dead `_BARE_ID_READ_ROUTES`; assert the allow-list contains no route that touches a tenant-owned model (e.g. a meta-test that every allow-listed view is org-neutral); do not blanket-skip routes.
4. **[Medium — T3-d] Audit query break-glass.** If the Query API remains, route superAdmin cross-org query results through `identity.audit` (or document the exemption).
5. **[Low] Remove dead unscoped helpers** `alerts/views.py::_lookup_alert` (:95) / `_resolve_alert_or_404` (:470), `cases/views.py::_lookup_task` (:547) / `_procedure_for_write` (:2323) — each is an unscoped resolver looking for a future caller.
6. **[Low] Record the deviations.** §15's P3/P4 notes ("All read/write surfaces now enforce org scoping"; "No new deviations") are inaccurate and should be corrected to list the gaps above, or the gaps fixed.

## Blockers

- **Yes** for building P5 on P3/P4 as-is: P5 (Realtime) will resolve visibility via `identity.scoping.resolve_case`, but P3/P4 leave the HTTP read/write surface partially unscoped, so a WebSocket fix would give a false sense of a closed boundary. The Query API leak and the five write leaks must be closed (or explicitly recorded) first.

## Is P3/P4 safe to build P5 on?

**No — not yet.** The scoping *mechanism* (`identity/scoping.py`), the `_case_org_scope` fix, the audit hook, the merge fixes and `playbook_run` are solid and reusable, and the route-enumeration test is a real guard. But three of eight ACs fail on behavioural grounds (T3-a query leak; T4-a five write leaks; T4-b read-only-share bypass via those routes). Fix gaps 1–3 (or record them as accepted deviations with owners), then P5 can proceed. T3-b/T3-d/T4-c/T4-d are ready.

---
Verifier note: no code, migrations, or tests were modified. Two throwaway probes were written under the OS temp dir (`tenant_probe_plugin.py`, `probe_writes.py`) and never touched the repository; `git status` is unchanged by this verification.
