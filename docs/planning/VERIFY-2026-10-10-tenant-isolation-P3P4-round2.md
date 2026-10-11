# Verification Report (round 2): PLAN-2026-10-10 — Tenant Isolation (P3/P4 remediation)

Plan: `docs/planning/PLAN-2026-10-10-tenant-isolation.md` (§6b, §9, §15 newest bullets)
ADR: `docs/decisions/ADR-004-tenant-isolation.md`
Prior reports: `docs/planning/VERIFY-2026-10-10-tenant-isolation-P3P4.md`,
`docs/reviews/SECURITY-2026-10-10-tenant-isolation.md`
Verified: 2026-10-10 17:13 UTC (branch `main` @ `4c4c5f8`, working tree **uncommitted**)
Automated gate: `make check` → **1059 passed, 3 skipped, 0 failed** (re-run by verifier).

**Verdict: PASS (with residual, non-blocking cleanup items).** All six §15 remediation claims
that this round was asked to falsify are **FIXED and independently reproduced**. The two
Critical/High behavioural defects from the previous round — the unscoped Query API read and the
five cross-tenant write endpoints — are closed, and the route-enumeration guard now genuinely
fails when Query-API scoping is removed (empirically proven). Remaining items are dead-code
cleanup and guard-coverage tightening (audit M1/M3), not tenant-isolation leaks.

## Summary

- Six fixes requested: **6 / 6 fixed** (item 6 = fixed for its stated scope, partial against the
  broader audit-M1 write-route gap).
- Behavioural exploits reproduced and now denied: **yes** (7 endpoints + Query API list/get).
- Previously-passing structural guards (H3-2 seed rollback, FK audit, index audit): **still green**.
- Unrecorded behavioural deviations: **none found** for the six items.

## The six fixes — status at a glance

| # | Fix | Status | Evidence (path:line / node id) |
|---|-----|--------|--------------------------------|
| 1 | Query API scoping (`query/views.py`, `query/engine.py`) | **FIXED** | see below |
| 2 | Cross-tenant writes on 5 case/observable/alert routes | **FIXED** (residual dead helper) | see below |
| 3 | Per-org webhook dedupe (`alerts` constraint + `ingest/pipeline.py`) | **FIXED** | see below |
| 5 | `comment_detail` scoping (case + alert parent) | **FIXED** | see below |
| 6 | Route-enumeration guard covers `query-api`, falsifiable | **FIXED for query-api** / **PARTIAL** overall | see below |
| 7 | Query-API superAdmin cross-org audit | **FIXED** | see below |

---

## Criterion-by-criterion (reproduction of the original exploits)

### Item 1 — Query API scoping — **FIXED**

The engine is now constructed with the caller and every start-step queryset is scoped:
- `query/views.py:155` — `engine = QueryEngine(payload, user=request.user)`.
- `query/engine.py:558/560/562/568-570` — `listCase`/`listAlert`/`listObservable`/`listAny` build
  their branches from `_case_qs(self._user)`, `_alert_qs(self._user)`, `_observable_qs(self._user)`.
- `query/engine.py:738` — `_case_qs` → `qs.filter(identity.scoping.case_visible_q(user))`.
- `query/engine.py:743` — `_alert_qs` → `qs.filter(identity.scoping.org_q("owner_org", user))`.
- `query/engine.py:753` — `_observable_qs` → `identity.scoping.org_filtered(qs, user, path="observable__owner_org")`.
- `query/engine.py:586` — `getCase` → `identity.scoping.resolve_case(self._user, identifier, write=False)`.

Verifier probe (org-1 user; org-2 has a **private** case `c2priv` and a case `c2shared`
read-shared to org-1):
```
POST /api/v1/query {"query":[{"_name":"listCase"}]}
  → 200; ids == {c1(own), c2shared(share)}; c2priv ABSENT
POST /api/v1/query {"query":[{"_name":"getCase","idOrName":"~<c2priv>"}]}  → 200 body == []
POST /api/v1/query {"query":[{"_name":"getCase","idOrName":"~<c2shared>"}]} → 200 body == [c2shared]
```
Assertion used:
```python
assert str(c2priv.id) not in ids, f"LEAK listCase exposes org2 private case: {ids}"
assert r.json() == [], f"LEAK getCase returned org2 case: {r.json()}"
```
The engine is **not** unbounded. (A temporary removal of the `_case_qs` filter — restored byte-for-byte,
sha256 `22d6ba35…` — made the guard fail loudly; see item 6.)

### Item 2 — Cross-tenant writes — **FIXED** (one residual dead helper)

Every formerly-unscoped write endpoint now resolves through `identity.scoping`:
- `cases/views.py:448` `case_task_create` → `resolve_case(request.user, case_id, write=True)`
- `cases/views.py:723` `case_alert_remove` → `resolve_case(..., write=True)`
- `cases/views.py:771` `case_custom_event_create` → `resolve_case(..., write=True)`
- `cases/views.py:1336` `case_tag_link` → `resolve_case(..., write=True)`
- `cases/views.py:1362` `observable_tag_link` → `resolve_observable(request.user, str(pk))`
  (docstring corrected at `:1354-1358`; the stale "globally deduplicated" note is gone)
- `alerts/views.py:645` `alert_tag_link` → `_resolve_alert_scoped` (`alerts/views.py:465-467` →
  `resolve_alert`); `alerts/views.py:165` `alert_observable_add` → `_resolve_alert_scoped`;
  `alerts/views.py:348/362` merge/import → `_resolve_alert_scoped`.

Verifier probe (org-1 user targeting org-2 ids). **All returned 404 with the target row unchanged**
and the ORG-2 OWNER positive controls returned 201/200 (proving the routes are live, so the 404 is
scoping, not a missing route):
```
POST   /api/v1/case/{c2}/task            {"title":"pwn"}              → 404, Task count(c2)==0
POST   /api/v1/case/{c2}/customEvent     {"title":"pwn","date":0}     → 404, timeline_events==0
POST   /api/v1/case/{c2}/tag             {"tags":["pwn"]}             → 404, c2.tags==0
POST   /api/v1/observable/{obs2}/tag     {"tags":["pwn"]}             → 404, obs2.tags==0
POST   /api/v1/alert/{alert2}/tag        {"tags":["pwn"]}             → 404, alert2.tags==0
POST   /api/v1/alert/{alert2}/observable {"dataType":"ip",...}        → 404, links==0
DELETE /api/v1/case/{c2}/alert/{alert2}                              → 404, alert2.case_id==c2
POST   /api/v1/alert/{alert2}/merge/{c2}                             → 404
```
Uniformity (no existence oracle): cross-tenant and fake-UUID responses are byte-identical,
`{"type":"NotFoundError",...}`:
```python
r = client1.post(f"/api/v1/case/{c2.id}/task", {"title": "x"}, format="json")
r_fake = client1.post("/api/v1/case/00000000-0000-0000-0000-000000000000/task", ...)
assert r.json() == r_fake.json()
```

**Residual (LOW, not exploitable):** the old org-blind `_resolve_case` is **not** gone and does
**not** delegate — it still exists at `cases/views.py:115-119` calling `link_case_from_identifier`
with no user/org. It now has **zero callers** (`grep _resolve_case` finds only the definition and
the unrelated `_resolve_case_status`/`_resolve_case_template`). Three sibling dead unscoped
resolvers also remain: `cases/views.py:2334 _procedure_for_write`, `alerts/views.py:95 _lookup_alert`,
`alerts/views.py:470 _resolve_alert_or_404` — none has a caller. This does not leak today, but it
contradicts the task's "gone or delegates" expectation and is a future footgun (audit follow-up #5).

### Item 3 — Per-org webhook dedupe — **FIXED**

- Model constraint now includes the tenant: `alerts/models.py:142-146` —
  `UniqueConstraint(fields=["owner_org","source","type","source_ref"], name="uniq_alert_source_type_ref")`.
- New migration `alerts/migrations/0014_tenant_dedupe_key.py:16-26` drops the old 3-column
  constraint and adds the 4-column one. Its `dependencies` (`:8-13`) are `alerts/0013`,
  `identity/0006`, `ingest/0002_ingestion_source_hardening`, `AUTH_USER_MODEL` — **no cross-app
  seed dependency**.
- Dedupe lookup is scoped: `ingest/pipeline.py:111-115` —
  `Alert.objects.get_or_create(owner_org=owner_org, source=wire_source, type=alert_type, source_ref=source_ref, ...)`.
- Webhook stamps the token's org only: `ingest/views.py:289` `owner_org=token.organisation`.

Verifier probe — same `IngestionSource`, two tokens (org1/org2), **identical**
`(source, type, source_ref)`:
```
token1 → 201, Alert owner_org=org1
token2 → 201, Alert owner_org=org2        # own row, NOT a replay of org1's
Alert.objects.filter(source="wire", type="generic", source_ref="same-ref").count() == 2
token1 again (same payload) → 200, _id == org1's original alert            # in-org replay
```
The existing suite's `test_same_source_different_tenants_by_token`
(`tests/conformance/test_tenant_scoping.py:571`) uses *different* refs, so it did not exercise the
collision; the probe above closes that gap.

**H3-2 still holds.** `tests/conformance/test_seed_migrations.py` (`-k h3_2`) → **8 passed**; the
full seed/fk/index trio → **73 passed, 1 skipped**. `alerts/0014` depends on schema migrations only.

### Item 5 — `comment_detail` scoping — **FIXED**

`cases/views.py:1440-1448` branches on the parent and collapses to a uniform 404:
```python
if comment.case_id:
    if identity.scoping.resolve_case(request.user, str(comment.case_id), write=True) is None:
        return _not_found("Comment")
elif comment.alert_id:  # the H1 bypass (case_id is None) is closed
    if identity.scoping.resolve_alert(request.user, str(comment.alert_id)) is None:
        return _not_found("Comment")
else:
    return _not_found("Comment")  # orphaned → deny
```
Verifier probe (org-1 user, org-2 case/alert comments): PATCH and DELETE on both a case comment and
an **alert-attached** comment return **404** with the row unchanged (message stays `"orig"`, row
still exists). The alert-comment path — the exact H1 bypass — is closed.

### Item 6 — Route-enumeration guard — **FIXED for query-api; PARTIAL overall**

Coverage of the Query API is real, not a `continue`:
- Discovery admits it: `tests/conformance/test_tenant_scoping.py:857-859`
  (`elif "POST" in methods and name == "query-api"` → appended with methods `["POST"]`).
  Verified live: `_discover_tenant_read_routes()` returns `query-api api/v1/query ['POST']`.
- `query-api`/`query-api-slash` are in `list_routes` (`:1009-1010`) and are driven with a real
  `{"query":[{"_name":"listCase"}]}` body; no blanket `continue` (`:1014-1020` only skips
  `case-attachment-download`). The assertions `:1069-1085` require `len(data)==2` (own + share) and
  that org-1's private case is absent.

**Falsifiability empirically proven.** Temporary removal of Query-API scoping
(`query/engine.py::_case_qs` filter dropped; file restored byte-for-byte afterwards, sha256
`22d6ba351800a6542c50f5d8088ec546c2c6429cb97bc17470483c03c6a7185d`) made the guard **fail**:
```
FAILED tests/conformance/test_tenant_scoping.py::TestRouteEnumerationConformance::test_cross_org_read_returns_uniform_404
E  AssertionError: Conformance failures:
E    query-api: cross-org query returned 3 items, expected 2 (own org + shared)
E    query-api: cross-org query leaked org1's private case
```
So the guard would catch the original T3-a exploit.

**Why PARTIAL:** the guard still enumerates only GET-capable routes plus `query-api`; every write
route is hard-excluded by the hand-maintained `_WRITE_ONLY_ROUTES` allow-list (`:754-791`, 36 names),
so a newly-unscoped **write** route is invisible (audit **M1** remains open). `_BARE_ID_READ_ROUTES`
(`:795-804`) is still defined and never referenced (dead scaffolding), and
`case-attachment-download` is still skipped (`:1019-1020`) even though it is in fact scoped
(`cases/views.py:1866` uses `_case_access(write=False)`).

### Item 7 — Query-API superAdmin audit — **FIXED**

`query/views.py:65-138` `_audit_query_cross_org_read`, invoked at `query/views.py:171-172`. It fires
only for `role == "superAdmin"`, groups results by resource type and target org, and for each
cross-org target calls `maybe_record_cross_org_read`, then rewrites the row's action to
`cross_org_read_query` (`query/views.py:134`) with a representative target id.

Verifier probe:
```
superAdmin(org1) POST /api/v1/query listCase → 200 (includes org2's case)
  AuditLog count == 1; action == "cross_org_read_query"; target_org == org2; actor == superadmin
superAdmin(org2) POST /api/v1/query listCase → 200 (all results own-org)
  AuditLog count == 0                                   # own-org read writes nothing
```

## Previously-passing structural guards (re-confirmed)

| Guard | Command | Result |
|---|---|---|
| H3-2 seed rollback stays in-app | `pytest test_seed_migrations.py -k h3_2` | 8 passed |
| FK audit | `pytest test_fk_audit.py` | 30 passed |
| Index audit | `pytest test_indexes.py` | 22 passed, 1 skipped (PG-only plan test) |
| Full DoD gate | `make check` | 1059 passed, 3 skipped, 0 failed |

## Remaining gaps (ranked)

1. **[Medium — audit M1, guard coverage] The guard does not enumerate write routes.** `_WRITE_ONLY_ROUTES`
   (`test_tenant_scoping.py:754-791`) is a hand-maintained exclusion list; a new unscoped POST/PATCH/DELETE
   route trips nothing. `_BARE_ID_READ_ROUTES` (`:795-804`) is dead; `case-attachment-download` is still
   skipped. Recommend: derive the write set from the URLconf, and fail when an allow-listed name is
   present-but-unclassified.
2. **[Low — audit M3, audit gap] `observable_detail` (GET) is not audited for a superAdmin cross-org
   read.** `cases/views.py:996-1033` resolves via `resolve_observable` (superAdmin-any-org,
   `identity/scoping.py:167`) but never calls `maybe_record_cross_org_read`, unlike
   `case_detail`/`task_detail`/etc. No leak; break-glass accountability only.
3. **[Low — dead unscoped helpers] `cases/views.py:115 _resolve_case`,
   `:2334 _procedure_for_write`, `alerts/views.py:95 _lookup_alert`, `:470 _resolve_alert_or_404`
   remain but have no callers.** Delete or delegate them so an org-blind resolver cannot be reused.
4. **[Low — expected, P5] `realtime/consumers.py` still has no pre-`accept()` org check (audit H2).**
   Not in this remediation's scope; it is P5.
5. **[Low — expected, P6] Share grant/revoke still gated by `write` share rather than owner-org
   (audit H3).** Not in this remediation's scope; it is P6.
6. **[Info] Test-ordering fragility (not a defect):** running `test_seed_migrations.py` **before**
   `test_schema_mutation_guards.py` makes the latter fail (`MUTATION DID NOT LAND`); the canonical
   alphabetical suite order runs mutation guards first and is green. Pre-existing, unrelated to the
   tenant work.

## Blockers

- **None** for the six items. No cross-tenant read/write leak remains among them.

## Is P5 safe to start?

**Yes.** The P3/P4 HTTP boundary is now genuinely closed for the surfaces in questions: the Query API
is org-scoped and its superAdmin break-glass path is audited; the five formerly-unscoped write
endpoints return uniform 404 with rows unchanged; ingest dedupe is per-org; alert comments are
guarded; and the route-enumeration guard demonstrably fails when Query-API scoping is removed. The
scoping mechanism (`identity/scoping.py`) is the single reusable primitive P5's WebSocket consumer
needs. The remaining gaps (write-route guard coverage, the `observable_detail` audit gap, dead
resolvers) are cleanup/hardening that do not reintroduce a tenant boundary hole, and H2 (realtime)
is P5's own deliverable. Start P5; schedule audit M1/M3 and the dead-helper removal as a fast-follow.

---
Verifier note: no repo source was modified. One temporary probe file
(`tests/conformance/test_zz_verifier_probe.py`) was added, run (5 passed), and **deleted**; the
one-line mutation of `query/engine.py` used for the falsifiability test was restored byte-for-byte
(sha256 `22d6ba35…`, unchanged `git status`). Reports are the only artefacts written.
