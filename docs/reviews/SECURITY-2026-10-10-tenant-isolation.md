# Security Review — Tenant Isolation (P2 ingest, P3 read, P4 write)

- **Date:** 2026-10-10
- **Reviewer:** security-auditor (AppSec/IR)
- **Scope:** uncommitted working tree implementing tenant isolation per `docs/planning/PLAN-2026-10-10-tenant-isolation.md` and `docs/decisions/ADR-004-tenant-isolation.md`
- **Mode:** read-only (no code changed)
- **Baseline:** `make check` green (~1059 tests) at time of review

## Threat model tested

A tenant is an organisation. An attacker is an authenticated user (or an `ApiKey` holder) in
organisation A who must not be able to read or mutate organisation B's cases, alerts, observables,
tasks, comments, procedures, shares, tokens, or timeline — and must not be able to tell B's existing
objects apart from non-existent ones (no existence oracle). Ingest is authenticated by
`IngestionToken`; a token binds to exactly one org and one source.

## Summary

| Severity | Count | Findings |
|----------|-------|----------|
| Critical | 2 | C1 (cross-tenant write via unscoped `_resolve_case` / `observable_tag_link`), C2 (ingest dedupe key not org-scoped) |
| High | 3 | H1 (alert-comment authz bypass), H2 (realtime consumer has no org check), H3 (share grant/revoke not owner-org-only) |
| Medium | 3 | M1 (write-route conformance guard not falsifiable), M2 (SSRF DNS-rebinding TOCTOU), M3 (audit gap on observable detail reads) |
| Low | 4 | L1 (legacy plaintext secret copied to `secret_hash`), L2 (merge moves shared-org children), L3 (playbook authoring/run not role-gated), L4 (duplicate ingest triple → 500) |
| **Total** | **12** | |

**Highest-severity finding: C1 — any authenticated tenant user can mutate another tenant's case
sub-resources (tasks, custom events, alert links, tags) and any observable's tags, because four
write routes still call the org-blind `_resolve_case` helper and `observable_tag_link` resolves
`Observable.objects.filter(pk=...)` directly.**

The P1/P2/P3 surfaces (scoping helpers, alert/case/observable read scoping, token routing) are
largely correct and well-tested; the failures are concentrated where the P4 sweep missed call sites
and where the conformance guard was configured not to look (M1).

---

## Critical

### C1 — Cross-tenant writes: `_resolve_case` and `observable_tag_link` bypass org scoping

**Where**
- `cases/views.py:115` — `_resolve_case(identifier)` calls `link_case_from_identifier` with no user/org argument.
- `cases/views.py:448` — `case_task_create` → `POST /api/v1/case/{idOrNumber}/task`.
- `cases/views.py:723` — `case_alert_remove` → `DELETE /api/v1/case/{idOrNumber}/alert/{alertId}`.
- `cases/views.py:771` — `case_custom_event_create` → `POST /api/v1/case/{idOrNumber}/customEvent`.
- `cases/views.py:1336` — `case_tag_link` → `POST|DELETE /api/v1/case/{idOrNumber}/tag`.
- `cases/views.py:1353-1365` — `observable_tag_link` → `POST|DELETE /api/v1/observable/{id}/tag` resolves `Observable.objects.filter(pk=pk).first()` with no org filter.
- `cases/views.py:88` — `_case_org_scope` correctly returns `identity.scoping.org_q("case__owner_org", user)`; the routes above simply do not use it.

**Exploit**
With an `ApiKey` scoped `readwrite` for org A, and a known/guessable UUID (or case number) belonging
to org B:

```
POST /api/v1/case/<B_case_id>/task            {"title": "pwn"}
POST /api/v1/case/<B_case_id>/customEvent     {"title":"x","date":0}
DELETE /api/v1/case/<B_case_id>/alert/<B_alert_id>
POST /api/v1/case/<B_case_id>/tag             {"tags":["owned"]}
POST /api/v1/observable/<B_obs_id>/tag        {"tags":["owned"]}
```

Every request returns success. `case_custom_event_create` also publishes a WebSocket event on org
B's case room (`append_timeline_event`), so org B sees attacker-authored ledger entries live.

**Impact** — Integrity breach across tenants: org A injects tasks, timeline entries and tags into
org B's case, and detaches alerts from org B's case (destroying the case→alert link). Tag mutation
on an org-B observable corrupts shared forensic evidence. This defeats the core promise of ADR-004.
`observable_tag_link` is *documented as deliberately unscoped* (`cases/views.py:1353-1357`) but that
justification is stale: it still cites the pre-ADR-004 "globally deduplicated" observable, while
`observable_detail` (`cases/views.py:996`, fixed at line 1014) and the bulk path are now per-org
scoped — so the observable counterpart is inconsistent with the model.

**Remediation** — Replace `_resolve_case(case_id)` at all four case write sites with
`identity.scoping.resolve_case(request.user, case_id, write=True)`, and resolve the observable in
`observable_tag_link` with `identity.scoping.resolve_observable(request.user, str(pk))` (mirroring
`observable_detail`). Delete or repurpose the now-dead `_resolve_case` helper so the org-blind path
cannot be reused. Update the `observable_tag_link` docstring to state the per-org rule.

---

### C2 — Ingest dedupe key is not org-scoped: cross-tenant alert disclosure and ingest loss

**Where**
- `ingest/pipeline.py:110-124` — `Alert.objects.get_or_create(source=wire_source, type=alert_type, source_ref=source_ref, ...)`; the lookup key omits `owner_org`.
- `alerts/models.py:143-144` — `UniqueConstraint(fields=["source", "type", "source_ref"], name="uniq_alert_source_type_ref")` is still global; `alerts/migrations/0013_tenant_owner_org.py` only adds the `owner_org` FK and does not narrow the constraint.
- `ingest/views.py:281-282` — `wire_source=str(payload.get("source") or source.slug)`; `source` and `source_ref` are caller-controlled, and `alert_type` defaults to `"generic"`.
- `ingest/views.py:300-315` — on a replay (`created=false`) the endpoint returns `200` and the **existing** alert's `_id`, `title`, `severity`, `source`, `type`, `status`, `correlation_key`, `source_ref`, `date`, `created_at`.

**Exploit**
Two paths, both realistic:
1. **Accidental (Q6 multi-tenant source).** A single `IngestionSource` serving org A and org B with
   tokens for each (`test_same_source_different_tenants_by_token` explicitly exercises this). A
   payload without `source` makes `wire_source = source.slug` for *both* orgs; a common
   `sourceRef` (counters starting at 1) then produces the same triple. Org B receives org A's alert
   (200 + A's metadata) and its own alert is silently dropped.
2. **Deliberate.** An attacker with a valid token for org A sets `source` to the victim's wire
   source string and `sourceRef`/`type` to a guessed value, and reads back the victim alert's title,
   severity, status, date and UUID from the response.

**Impact** — Cross-tenant information disclosure (alert metadata + internal UUID) and an ingest
integrity/availability failure (the second tenant's alert is never created). Because the response
is `200` with no warning on the "replay" branch, org B has no signal that its alert was absorbed
into another tenant's row. Higher severity than a pure read because it also corrupts ingest.

**Remediation** — Scope the dedupe key and constraint by `owner_org`:
`get_or_create(owner_org=owner_org, source=wire_source, type=alert_type, source_ref=source_ref, ...)`
and replace the constraint with `UniqueConstraint(fields=["owner_org", "source", "type",
"source_ref"], ...)` in a new migration. Add a conformance test asserting the same triple posted
with two orgs' tokens yields two rows, each readable only by its org.

---

## High

### H1 — Alert comments bypass org authorization in `comment_detail`

**Where** — `cases/views.py:1424-1445`; the guard at line 1434:
```python
if comment.case_id and _case_access(request, str(comment.case_id), write=True) is None:
    return _not_found("Comment")
```
`Comment` has both a `case` FK and an `alert` FK (`cases/models.py:332-360`); comments attached to an
alert have `case_id = None`, so the guard is skipped entirely.

**Exploit** — `PATCH /api/v1/comment/{uuid}` or `DELETE` on any alert comment in any org, by any
authenticated user who can guess the comment UUID.

**Impact** — Cross-tenant write: edit or delete another tenant's alert comments (potentially
destroying evidence).

**Remediation** — Branch on the parent: if `comment.case_id` use `_case_access(..., write=True)`;
elif `comment.alert_id` use `identity.scoping.resolve_alert(request.user, str(comment.alert_id))`
(and require write — see whether alert comments should be write-gated for read-only API keys);
else deny (org-less/orphaned comment → 404). Collapse both to the same uniform 404.

### H2 — Realtime consumer has no org check before `accept()` (deviation S2 still open)

**Where** — `realtime/consumers.py:66-84` (`connect` checks only `user.is_authenticated` and UUID
shape, then `group_add` + `accept()`); `realtime/consumers.py:51-58` (`_timeline_payload` calls
`events_after(case_id, after)` with no org filter); `cases/ledger.py:88` (`events_after`).
`realtime/consumers.py` is **not** in `git status` — P5 was not implemented in this wave.

**Exploit** — An authenticated org-A user opens `ws://…/ws/case/<B_case_uuid>/` and receives the
full replayable ledger of org B's case (comments, automation outputs, observable additions, status
changes) via `_timeline_payload`, then live events via the `case_<uuid>` group.

**Impact** — Cross-tenant read of the most sensitive collaborative content, including automation
`output_log` pulled from external tools.

**Remediation** — In `connect`, resolve the case through `identity.scoping.resolve_case(user,
case_id, write=False)` (or `case_visible_q`) **before** `group_add`/`accept()` and close with
`NOT_FOUND` when it returns `None`. Note the consumer runs sync-to-async; use
`sync_to_async(resolve_case)(...)`. Filter `events_after` server-side as defence in depth.

### H3 — Share grant/revoke is not restricted to the case owner's org (deviation P6 open)

**Where** — `cases/views.py:1594` (`case_share_list`) and `cases/views.py:1656` (`share_detail`) both
gate on `_case_access(request, case_id, write=True)` — i.e. *any* org holding a `write` share, not
the owner org. The `case_share_list` body then creates `Share` rows for arbitrary organisations
(`:1621-1640`) and `share_detail` deletes any share on the case.

**Exploit** — Org B has a `read+write` share on org A's case. A user in B:
```
POST /api/v1/case/<A_case>/share  {"shares": [{"organisation": "<C>", "permissions": {"write": true}}]}
```
grants org C access to A's case (widening the sharing graph beyond what A intended), and
`DELETE /api/v1/case/<A_case>/share/<id>` revokes A's own shares.

**Impact** — Privilege escalation / access-control widening: a share holder can extend access to
third parties and strip the owner's shares. `Share` rows are security-sensitive state.

**Remediation** — Gate both routes on owner-org only, e.g. `request.user.org_id == case.owner_org_id`
(superAdmin break-glass excepted and audited). A write share should permit editing the case body,
not managing its access-control list. Add a regression test: write-share holder `POST`/`DELETE` on
`/share` → 404.

---

## Medium

### M1 — The write-route conformance guard is an allow-list, not a detector (control gap)

**Where** — `tests/conformance/test_tenant_scoping.py:753-791` (`_WRITE_ONLY_ROUTES`) and
`_discover_tenant_read_routes` (`:807-862`), which only collects routes with `GET` in their methods.
The module docstring and the test header claim the enumeration "fails if a newly added unscoped
route exists" (`:679-685`).

**Problem** — Every route listed in the C1 and H1 findings (`case-task-create`, `case-tag-link`,
`case-alert-remove`, `case-custom-event-create`, `observable-tag-link`, `comment-detail`,
`custom-event-detail`, `case-share-detail`, …) is hard-coded into `_WRITE_ONLY_ROUTES` and therefore
never exercised. A newly added unscoped **write** route is invisible to the guard; the conformance
suite cannot fail for the exact class of bug it is advertised to catch. This is why C1/H1 were not
caught.

**Remediation** — Make the guard enumerate *all* `/api/v1/` routes that touch tenant objects and
assert a write-probe per non-GET route (cross-org → uniform 404), rather than excluding a
hand-maintained list. If exclusions are unavoidable, fail the test when a route name is present in
the URLconf but absent from the allow-list, so the allow-list cannot silently grow. Also remove dead
entries (e.g. `case-create`/`alert-create`/`observable-create` are not route names).

### M2 — SSRF guard resolves then re-resolves: DNS-rebinding TOCTOU (pre-existing S6)

**Where** — `automation/executor.py:78-146` (`_resolve_host` + `assert_safe_url`), called at
`:168` immediately before `urllib.request.Request(url, ...)` (`:171`) via `opener.open`.

**Problem** — `assert_safe_url` resolves the hostname and validates the resolved addresses, but the
URL string (hostname) is then handed to `urllib`, which resolves it **again** when dialling. The
docstring at `:79-84` claims "Resolving *before* connecting is what closes DNS rebinding", but
without pinning the validated IP there is a TOCTOU window: a name that answers with a public IP for
the guard and a link-local/loopback IP for the connection defeats the check. The scheme allowlist,
credential rejection, manual redirect handling and per-hop validation are otherwise sound.

**Impact** — A playbook whose URL host is controlled/rebindable (e.g. interpolated from an
observable's `fqdn`) can reach `169.254.169.254` and internal services.

**Remediation** — Pin the connection to a validated address: resolve once, pick a validated IP,
and connect to the IP while sending the original `Host` header (e.g. custom `HTTPConnection` /
`http.client` with `create_connection((ip, port))`), or use a hardened HTTP client that accepts an
`allowed_ips`/resolved-address pin. At minimum, re-assert `assert_safe_url` on the final resolved
peer immediately before the `connect` and document the residual window. Track this as the open S6.

### M3 — superAdmin cross-org read audit gap on bare-observable detail

**Where** — `cases/views.py:996-1033` (`observable_detail`). The diffs added
`maybe_record_cross_org_read` to case/task/alert/procedure/page/timeline/flow/share/export reads,
but `observable_detail` returns the observable payload without an audit call. `observables` are
per-org (ADR-004), and `resolve_observable` allows a superAdmin to resolve any org's row.

**Impact** — A superAdmin break-glass read of another tenant's observable is not recorded, so the
`AuditLog` cannot answer "who looked at this artefact of org B". This is a G6/break-glass
accountability gap rather than a direct cross-tenant leak.

**Remediation** — Call `maybe_record_cross_org_read(request, actor=request.user, target_obj=
observable, target_type="Observable", target_id=str(observable.id), target_org_field="owner_org")`
after resolution (as done for `Task` at `cases/views.py:525-538`). Add a test mirroring
`test_superadmin_cross_org_bare_id_read_writes_auditlog`.

---

## Low

### L1 — Legacy plaintext webhook secret copied verbatim into `IngestionToken.secret_hash`

**Where** — `identity/migrations/0006_create_default_org.py` sets `"secret_hash": source.webhook_secret_hash` and `"prefix": source.webhook_secret_hash[:12]`; `ingest/views.py:99-111` `_verify_secret` falls back to `constant_time_compare` for such plaintext rows, so the migrated secret still authenticates. Test fixtures assert/use literal secrets: `tests/conformance/test_tenant_scoping.py:646,664,672` (`"legacys3cr3t"`) and `:566` (`"invalidtoken123456789012"`).

**Impact** — For any deployment with legacy `IngestionSource.webhook_secret_hash` rows, the secret is
stored in cleartext at rest in the new column (and the 12-char prefix is a `unique` index). Greenfield
deployments have no legacy rows, so this is latent; the literal test tokens may also trip the secret
scanner.

**Remediation** — Re-hash in the migration where possible (the migration cannot see plaintext if the
legacy column already holds a hash — branch on whether it looks like an Argon2 digest; otherwise
require operators to rotate on upgrade and record it in `SECURITY.md`). Replace literal fixture
secrets with `secrets.token_urlsafe`/`uuid4` values so no real-looking secret is committed.

### L2 — Merge can pull a shared org's children into the caller's case

**Where** — `cases/merge.py` (merge resolves each id with `write=True`; shares are not copied — S7
correctly fixed). A caller with a `write` share on org B's case can pass `[own_case, orgB_case]` so
the org-B case is merged (and deleted) as a source, with its alerts/tasks/observables reparented
under the caller's own case.

**Impact** — Children keep their original `owner_org` (`import_alert_to_case`/alert rows), but they
become reachable through the caller's case (e.g. `case_flow`), so org-B data can be surfaced in an
org-A case view. The attacker already had write access to the shared case, so this is a
reachability/consistency concern rather than a fresh disclosure. Silently skipping cross-tenant ids
is intended.

**Remediation** — Either forbid merging a case the caller does not own (require owner-org on the
source), or explicitly reparent/refuse children whose `owner_org` differs from the target. Document
the merge share semantics in `docs/spec/deviations.md`.

### L3 — Playbook authoring and run are not role-restricted (SSRF primitive)

**Where** — `automation/views.py` (`playbook_collection` create and `playbook_run`, `:247-268`).
`playbook_run` correctly scopes `case`/`observable` (T4-d), but the playbook itself is a global,
org-neutral object and any authenticated user may create one with an `http` action and run it
against their own case.

**Impact** — Lowest-privilege analysts can cause the server to make outbound HTTP requests to any
public URL (private/link-local blocked by the guard, modulo M2) and persist the response into a
case ledger. This is a server-side request primitive that should be an operator/administrator
capability; combined with M2 it can reach internal endpoints.

**Remediation** — Require `IsOrgAdminOrAbove` (or an explicit automation permission) for playbook
create/update and for running `http` actions. Record playbook authoring in `AuditLog`. Consider
an outbound egress allow-list for enrichment hosts.

### L4 — Duplicate `(source, type, source_ref)` on the API path can raise 500

**Where** — `alerts/views.py:_create_alert` (`:311`) creates an `Alert` with caller-supplied
`source`/`type`/`source_ref` and `owner_org=org`; the global unique constraint (`alerts/models.py:143`)
is shared across orgs, so an authenticated user can supply a triple that already exists for another
tenant, producing an `IntegrityError` that the DRF exception handler does not map to a contract
response.

**Impact** — 500 instead of 4xx (availability/correctness); same root cause as C2.

**Remediation** — Fixing C2 (org-scoped constraint) resolves the cross-tenant collision; also reject
or upsert duplicate triples within the caller's own org with a clean 409.

---

## Invariants confirmed

The following were verified as correct and are not findings:

- **No existence oracle on read.** `_not_found` (`cases/views.py:101`) returns the same
  `{"type": "NotFoundError", ...}` 404 for cross-tenant and non-existent case/alert/observable/task
  ids; `resolve_case`/`resolve_alert`/`resolve_observable` (`identity/scoping.py:102,143,163`) return
  `None` for both. Tests `test_retrieve_case_cross_tenant_404` / `_alert_` assert 404 + `NotFoundError`.
- **Fail-closed for org-less callers.** `case_visible_q`/`org_q` deny org-less users
  (`identity/scoping.py:47,66`); `test_tenant_routes_require_org_context` covers the top-level
  routes.
- **Ingest secret handling is timing-safe and source-bound.** `_authenticate_token`
  (`ingest/views.py:115-155`) filters `IngestionToken` by the URL's `source` and `revoked_at IS
  NULL`, so a token issued for source A cannot be presented to source B; revoked tokens are
  rejected; no token / no tokens configured → strict 401. Tokenless ingestion is impossible;
  `owner_org=token.organisation` only (`ingest/views.py:289`).
- **SuperAdmin break-glass is audited and does not trust `X-Forwarded-For`.**
  `identity/audit.py:_get_client_ip` (`:17`) reads `REMOTE_ADDR` only; `maybe_record_cross_org_read`
  writes `AuditLog` for cross-org case/task/alert/procedure/page/timeline/flow/share/export reads
  (own-org and failed 404 reads correctly write nothing).
- **Read sides of the write routes that were fixed are scoped.** `case_observable_add/list`,
  `case_timeline`, `case_flow`, `case_export`, `case_page_*`, `procedure_detail`
  (`cases/views.py:2359`), `task_detail`, `alert-*` (`alerts/views.py`), and bulk
  (`alert_bulk_update`, `case_bulk_update`, `task_bulk_update`, `observable_bulk_update` +
  `_bulk_cross_case_guard`, `procedure_bulk_delete`) all resolve every id through
  `identity.scoping` / `_case_access`; cross-tenant ids are uniformly skipped/404.
- **Per-org observable dedupe.** `observable_detail` and bulk use `resolve_observable`
  (`cases/views.py:1014`), and `test_observable_same_value_different_orgs_are_distinct` proves two
  orgs with the same value get two rows.
- **No debug/secret leakage in changed source.** No `print(`/`pdb`/`breakpoint(`/`DEBUG` in the
  changed modules. (Literal test secrets under L1 remain.)
- **`_case_org_scope` returns a real `Q`** via `identity.scoping.org_q`
  (`cases/views.py:151-159`), asserted by `TestCaseOrgScopeRegression`.
- **Ingest token prefix verification** uses `check_password`/Argon2 with a constant-time legacy
  fallback; `Alert.source` is verbatim caller text intentionally independent of
  `Alert.ingestion_source` (`alerts/models.py:55-80`).

## Suggested remediation order

1. **C1** (cross-tenant writes) and **C2** (ingest key) — both are direct isolation breaks.
2. **H1** (alert comments), **H2** (realtime), **H3** (shares).
3. **M1** (make the guard real) — without it, the next wave repeats C1/H1.
4. **M2/M3**, then **L1–L4**.

## Reproduction notes

All findings were confirmed by static trace of the cited call sites against the scoping helpers; the
existing suite (`tests/conformance/test_tenant_scoping.py`) green-lights the tree precisely because
the write routes are excluded from enumeration (M1). A focused regression set — cross-org POST/DELETE
against `case-task-create`, `case-tag-link`, `case-alert-remove`, `case-custom-event-create`,
`observable-tag-link`, `comment-detail`, and `/share`, plus a two-token ingest collision — would fail
against the current tree and pass after remediation.
