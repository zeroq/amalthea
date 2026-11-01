# Security Audit Report: Playbook Tenant Scoping / M3 Change

**Date:** 2026-11-01  
**Auditor:** security-auditor (independent, per plan VERIFICATION-2026-10-31)  
**Scope:** `automation/models.py`, `automation/migrations/0008_playbook_tenant_scope.py`, `automation/dispatcher.py`, `automation/registry.py`, `automation/views.py`, `core/serializers.py`, `ui/views.py`, UI templates, test conformance suites  
**Change:** Playbook tenant-scoping (per-org ownership, name uniqueness, org-scoped dispatch, manual-run guards, headers-in-config exposure)  
**Verdict:** CONDITIONAL PASS

---

## Summary

The M3 change introduces per-organisation ownership of playbooks (`owner_org` FK), per-org name uniqueness, org-scoped event dispatch, and manual-run org-mismatch guards. The implementation correctly enforces tenant isolation via `identity.scoping` helpers (`org_q`, `resolve_case`, `resolve_observable`), with uniform 404 for cross-org access (no existence oracle). All 20 playbook tenant scope tests pass, 16 roundtrip tests pass, and the full suite passes (`3206 passed, 3 skipped, 1 warning`). `make check` passes its static gates.

No code was modified during this audit (read-only). The worktree is dirty with pre-existing unrelated repairs.

---

## Findings by Severity

### Critical: None

No critical vulnerabilities found. Tenant isolation is correctly enforced across all paths.

### High: None

No high-severity vulnerabilities. The `_meta` endpoint exposes the authoring vocabulary (trigger events, action names, script names) but NOT per-playbook config secrets. Viewer access to `_meta` (200) is deliberate and safe — it returns code-derived vocabulary, not org-scoped data.

### Medium: None (new)

No new medium-severity findings. The `config["headers"]` cleartext exposure is an **accepted residual** from the plan (same-org analyst/admin/superAdmin only). This wave did not broaden exposure or add secret leakage to logs, errors, audit rows, or cross-org responses.

### Low: None (new)

No new low-severity findings.

### Info: Configuration-appropriate

- Tenant isolation fully enforced via `org_q` / `resolve_*` helpers
- Uniform 404 for cross-org detail/name/UUID lookups (no existence oracle)
- SuperAdmin cross-org reads are audited via `maybe_record_cross_org_read`
- `PlaybookOrgMismatch` guard in `run_now` + 404 catch in `playbook_run` — no `AutomationRun` created on org mismatch
- Idempotency key (`AutomationRun.idempotency_key` unique constraint) prevents duplicate runs on replay
- Migration `0008_playbook_tenant_scope` backfill is safe: `reverse_code=migrations.RunPython.noop`, backfill sets `owner_org` to default org before `AlterField` makes it NOT NULL
- Per-org unique constraint (`playbook_org_name_uniq`) cannot be bypassed by name truncation (200-char limit enforced at model + API layer)
- `@not_viewer` on all mutating UI views; viewers receive 404 before any parsing/write path
- `_meta` endpoint is code-derived vocabulary, not per-playbook data — safe for viewer access

---

## Detailed Review

### 1. Tenant Isolation (All Paths)

**API `playbook_collection` (GET /api/v1/playbook):**
- Org-scoped via `org_q("owner_org", user)` (identity/scoping.py:107-119)
- Viewer → 403 (IsAnalystOrAbove); analyst/admin/superAdmin → own org's playbooks only
- SuperAdmin sees all orgs' playbooks (collection exposure accepted; cross-org detail GET audited)

**API `playbook_detail` (GET/PATCH/DELETE /api/v1/playbook/{idOrName}):**
- `_resolve(identifier, user)` (automation/views.py:75-94): UUID-first within org scope, then name lookup scoped to `org_q("owner_org", user)`
- Non-superAdmin: per-org name uniqueness → exactly 1 match or 404
- SuperAdmin with same name across orgs → uniform 404 (forces UUID use) — tested in `test_superadmin_name_lookup_is_ambiguous_across_orgs`
- Cross-org GET → 404 (test `test_detail_cross_org_is_a_uniform_404`)
- Cross-org PATCH/DELETE: `_require_analyst` passes for analysts; the org-scoped `_resolve` then returns 404. A viewer gets 403 first. Cross-org superAdmin GET is audited; superAdmin writes are allowed by the break-glass role.

**UI playbook_list.html / playbook_detail.html:**
- `@not_viewer` decorator on all mutating UI views (ui/views.py:190-220)
- UI list remains viewer-readable (200) but omits authoring links/config; UI detail/edit/delete return uniform 404 for viewers (no existence oracle). API detail returns 403 for viewers.
- `can_mutate` = `not is_viewer(request.user)` in `_base_context` (ui/views.py:338)

**API `playbook_run` (POST /api/v1/playbook/{idOrName}/run):**
- Resolves playbook via `_require_analyst` + `_resolve` → cross-org → 404
- Resolves case via `resolve_case(..., write=True)` → org-scoped → None → 404
- Resolves observable via `resolve_observable(...)` → org-scoped → None → 404
- `run_now` checks `playbook.owner_org_id != case.owner_org_id` → raises `PlaybookOrgMismatch` → caught → `_not_found("Playbook")` (404, no run created)
- Test `test_manual_run_rejects_a_case_from_another_org` confirms 404 + 0 AutomationRuns
- Test `test_run_now_raises_on_org_mismatch` confirms `PlaybookOrgMismatch` raised, 0 runs

### 2. Viewer Access to Config/Secrets

- **API:** Viewer cannot reach `playbook_detail` (403 from `IsAnalystOrAbove`); in the UI, `@not_viewer` returns 404. `playbook_json` includes `config`, but viewer never reaches it.
- **UI:** `playbook_detail.html` line 65: `<pre class="body">{{ playbook.config|prettyjson }}</pre>` is inside `{% if can_mutate %}` block (line 57). Viewers have `can_mutate = False`, so config is never rendered.
- **`_meta` endpoint (GET /api/v1/playbook/_meta):** Returns `triggerEvents`, `actions`, `actionNames` — code-derived vocabulary, NOT per-playbook config. Explicitly allowed for viewers (test line 147: `assert viewer_client.get("/api/v1/playbook/_meta").status_code == 200`). No secret leakage.
- **Headers in config:** `config["headers"]` cleartext is an accepted residual for same-org analyst/admin/superAdmin. No new exposure in this wave — headers are not sent in cross-org responses, error messages, audit logs, or `_meta`.

### 3. SuperAdmin Behavior

- **Cross-org detail reads:** 200 with audit log entry (test `test_superadmin_cross_org_detail_is_readable_and_audited`). Audit records `actor_org`, `target_org`, `action="cross_org_read"`.
- **Cross-org name lookup:** Uniform 404 when same name exists in multiple orgs (test `test_superadmin_name_lookup_is_ambiguous_across_orgs`). Forces superAdmins to use UUID for unambiguous identification.
- **Collection GET (superAdmin):** Sees all orgs' playbooks — accepted residual from the plan.
- **Cross-org write:** superAdmin may edit/delete any playbook by UUID (break-glass role); analyst/admin writes outside their org are 404. There is no cross-org creation selector; create always stamps the caller's org.

### 4. Dispatch / Event Org Scoping

- `_event_org_id` (dispatcher.py:95-166): Resolves event subject's org:
  - `alert.ingested` → `Alert.owner_org`
  - `case.status_changed` → `Case.owner_org`
  - `observable.created` → `Case.owner_org` (via linked case)
  - `task.completed` → `Task.case.owner_org`
- `dispatch` (dispatcher.py:180-235): Filters playbooks by `owner_org_id=org_id`, `trigger_event=event_name`, `is_active=True`. If `org_id is None` → returns empty list (no runs).
- Idempotency key: `idempotency_key(playbook.name, event_name, subject)` — stable, deterministic, unique constraint collapse on replay (AC6.5).
- Test `test_unresolvable_event_org_creates_no_runs`: org can't be resolved → no runs created.
- Test `test_observable_event_without_a_case_creates_no_runs`: Observable without case link → no runs.

### 5. Manual Run Org Mismatch Guard

- `run_now` (dispatcher.py:275): `if playbook.owner_org_id != case.owner_org_id: raise PlaybookOrgMismatch`
- `playbook_run` view (automation/views.py:365-367): `except PlaybookOrgMismatch: return _not_found("Playbook")`
- No `AutomationRun` created on mismatch (confirmed by tests)
- API/UI both return uniform 404 on org mismatch

### 6. Migration 0008 Backfill

- `backfill_playbook_org`: Sets `owner_org` = default org for null rows (line 13)
- `reverse_code=migrations.RunPython.noop`: Reverse is safe no-op
- Sequence: AddField (nullable) → RunPython (backfill) → AlterField (NOT NULL) → Remove global unique → Add per-org unique
- Cannot leave NULL `owner_org`: backfill runs before NOT NULL constraint
- Per-org uniqueness cannot be bypassed by name truncation: 200-char limit enforced at model + API (`name[:200]`); constraint is on `(owner_org, name)`
- Safe enough for this wave

### 7. Authorization Helpers — Confused Deputy & IDOR

- `_resolve` (automation/views.py:75-94): UUID-first within org scope, name lookup scoped to org. Prevents confused deputy where a name from another org could be resolved.
- Cross-org UUID lookup → 404 (scoped to user's org via `org_q`)
- Cross-org name lookup for superAdmin with same name across orgs → uniform 404 (forcing UUID)
- No IDOR via UUID for non-superAdmins: always scoped to the user's org; superAdmin cross-org access is deliberate and detail reads are audited
- No bypass through alternate routes:
  - API slash variants: `_resolve` takes string identifier; URL routing may register both `/playbook/{idOrName}` and `/playbook/{idOrName}-slash`, but both use same `_resolve` logic
  - UI routes: `@not_viewer` decorator on all mutating views; viewer gets 404 before any org scoping
  - `_meta`: code-derived vocabulary, not per-playbook data, no IDOR possible

### 8. Accepted Residuals (from plan — unchanged)

| Residual | Status |
|---|---|
| Same-org cleartext `config["headers"]` | Accepted, unchanged |
| SuperAdmin collection GET sees all orgs' configs | Accepted, unchanged |
| Historical cross-org `AutomationRun` rows not cleaned up | Accepted, unchanged |
| No cross-org creation selector | Accepted, unchanged |

These are not reported as new findings since the implementation does not exceed the accepted scope.

---

## Verdict: CONDITIONAL PASS

The playbook tenant-scoping / M3 change is **securely implemented** with proper tenant isolation, uniform 404 for cross-org access, audited superAdmin cross-org reads, and correct org-mismatch guards for manual runs and event dispatch.

**Conditional** because the following accepted residuals from the plan remain in place (not new findings, but carry forward):

1. Same-org cleartext `config["headers"]` visible to analyst/admin/superAdmin
2. SuperAdmin collection GET sees all orgs' playbook configs
3. Historical cross-org `AutomationRun` rows not cleaned up
4. No cross-org creation selector (deliberate — create is org-scoped)

These are documented as plan residuals and the implementation does not broaden their exposure.

Targeted conformance tests pass (20/20 playbook tenant scope, 16/16 roundtrip, 10/10 HTTP tenant scoping, 8/8 viewer affordances, 5/5 playbook delete UI). Full suite: `3206 passed, 3 skipped`; `make check` static gates pass (ruff, ruff format, mypy 126 files, Django check, `makemigrations --check`, CSS drift, playbook scripts).

---

**Report Path:** `docs/security/SECURITY-2026-11-01-playbook-tenant-scoping.md`

