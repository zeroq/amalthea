# PLAN-2026-10-30 — Playbook tenant scoping + viewer secret access (M3 / §11.2)

## Summary

`Playbook` is currently a global automation definition. It has no `owner_org`, so any authenticated
user in any organisation can list, read, edit, and run another tenant's playbook. HTTP playbooks keep
outbound credentials in `config["headers"]`; the API returns `config` in full and the API read routes
also allow the `viewer` role. This plan closes the cross-tenant and viewer exposure.

The approved approach is **Option A** from `TODO.md` §11.2:

1. Add a required `Playbook.owner_org` FK.
2. Scope all playbook reads, writes, deletes, and manual runs with `identity.scoping.org_q`.
3. Backfill existing playbooks into `AMALTHEA_DEFAULT_ORG_NAME` and change name uniqueness from global
   to `(owner_org, name)`.
4. Bind event-driven automation to the **event subject's organisation**: a playbook owned by org A must
   never run for a case/alert/task owned by org B.
5. Deny the `viewer` role on the playbook collection/detail API reads (the UI already denies playbook
   detail/edit via `not_viewer`; the list remains readable and renders no credentials).
6. Audit superAdmin cross-org **detail** reads using the existing `maybe_record_cross_org_read` helper.

**Accepted residual risk:** same-org `analyst`/`admin`/`superAdmin` users can still see
`config["headers"]` in cleartext. This plan intentionally does **not** add secret references,
encryption, or masked reads. The M3 finding is the cross-tenant and viewer leak; secret storage is a
separate follow-up. See "Residual risks" below.

> **Status:** approved 2026-10-30. Ready for implementation.

## Goals

1. No cross-tenant playbook list, read, create, update, delete, or manual run.
2. A `viewer` cannot read a playbook's `config` through the API. The UI list may show non-secret
   inventory fields; UI detail/edit stay denied.
3. Event-driven dispatch selects only playbooks owned by the event subject's organisation. An event
   whose organisation cannot be resolved produces no runs rather than falling back to all orgs.
4. A manual run requires `playbook.owner_org == subject case.owner_org` (the case resolved from the
   observable when only an observable is supplied).
5. Existing playbooks survive migration and are assigned to the default organisation.
6. Playbook names are unique per organisation; two orgs may both have a playbook named `enrich`.
7. `superAdmin` keeps ADR-004 cross-org visibility, but runtime activation remains org-bound.
8. Update tenant-route conformance, playbook tests, and the documented API/data-model/automation
   contracts in the same change.

## Non-goals

- No secret references, encryption at rest, masked placeholder reads, or write-only header fields.
- No change to the `Playbook.config` shape, executor, SSRF guard, interpolation names, or HTTP
  redirect policy.
- No global/system playbooks. `owner_org` is mandatory.
- No "move playbook to another organisation" operation. `owner_org` is set at create and immutable.
- No superAdmin "create for another org" selector. A superAdmin creates in their own org; cross-org
  visibility applies to existing playbooks. This can be a later admin-UX change.
- No cleanup or re-parenting of historical `AutomationRun` rows that already span orgs. New writes are
  org-bound; old rows remain historical.
- No UI redesign, new JavaScript framework, or inline styles.
- No change to the viewer-readable playbook list; it exposes name, trigger, action type/script path,
  active state, run counts and timestamps, not `config["headers"]`.

## Current state (verified)

- `automation/models.py:17` — `Playbook.name` is globally `unique=True`; there is no `owner_org`.
  `config` is a `JSONField`.
- `automation/views.py:67-74` — `_resolve()` looks up any playbook by UUID or name with no org filter.
  `playbook_collection` lists all playbooks (`:104`); create checks global name uniqueness (`:113`);
  detail PATCH name check is global (`:232`); run resolves globally (`:290`).
- `automation/views.py:17-19` — mutating endpoints require analyst+, but **read endpoints allow any
  authenticated org member**, including `viewer`.
- `core/serializers.py:245-264` — `playbook_json()` inlines the full `config`; it has no owner-org
  field.
- `automation/dispatcher.py:109` — dispatch selects `Playbook.objects.filter(trigger_event=..., is_active=True)`
  globally.
- `automation/registry.py:43-55` — `playbooks_for()` is also global. It is not currently called by
  dispatch, but leaving an unscoped helper is a trap.
- `ui/views.py:2360-2386` — `playbook_list` queries all playbooks; it is viewer-readable.
- `ui/views.py:2392-2462` — `playbook_create` checks global name uniqueness and creates without an org.
- `ui/views.py:2467-2537` and `:2632-2688` — detail/edit resolve a playbook globally by UUID or name.
  Both are `@not_viewer`, so a viewer is already denied; a cross-org analyst is not.
- `ui/views.py:2724-2765` — delete resolves globally; `:2771-2824` — manual run resolves globally.
- `ui/templates/ui/playbook_list.html` — viewer sees the inventory but no detail/create/delete links.
- `ui/templates/ui/playbook_detail.html:62` — renders full `playbook.config|prettyjson`.
- `ui/templates/ui/playbook_form.html:81-85` — renders existing header values into text inputs.
- `tests/conformance/test_tenant_scoping.py:706` and `:776` — playbook API/UI routes are currently
  allow-listed as global read routes.
- `tests/conformance/test_playbook_roundtrip.py:274` — asserts the stored `Authorization` header is
  rendered back; this must remain true under Option A because masking is out of scope.
- `docs/spec/api.md:298-317`, `docs/spec/data-model.md:341-344`, and `docs/spec/automation.md:70-89`
  document the current global/no-owner behaviour and dispatch query.

## Design decisions

### D1 — Option A: scope the row; do not mask in this wave

`Playbook` becomes an org-owned row. Cross-tenant access is removed by scoping. `viewer` API reads of
collection/detail are denied because `config` is inlined. Same-org analysts/admins are trusted to run
automations and may see the configuration they can execute.

This deliberately leaves the D1 round-trip contract intact: the edit form still restores header values,
and `tests/conformance/test_playbook_roundtrip.py` does not need to change its masking expectation.

### D2 — Required `owner_org`, default-org backfill, per-org names

- Final model: `owner_org = models.ForeignKey("identity.Organisation", on_delete=models.PROTECT, related_name="playbooks")`.
- Migration adds the field nullable, assigns every existing row to the organisation named by
  `settings.AMALTHEA_DEFAULT_ORG_NAME` (default `"Default"`), then alters it to `NOT NULL`.
- `name` loses the global `unique=True`; a `UniqueConstraint(fields=["owner_org", "name"], name="playbook_org_name_uniq")`
  replaces it.
- The old `playbook_trigger_idx ("trigger_event", "is_active")` is replaced by
  `playbook_org_trigger_idx ("owner_org", "trigger_event", "is_active")`, because every dispatch now
  filters by owner org first.

### D3 — Event organisation = the subject's organisation

`dispatch()` resolves the org from the event subject, not from the playbook and not from the caller:

| Event | Organisation source |
|---|---|
| `AlertIngested` | `Alert.owner_org` for `alert_id` |
| `CaseStatusChanged` | `Case.owner_org` for `case_id` |
| `ObservableCreated` | `Case.owner_org` for `case_id` (one event per linked case) |
| `TaskCompleted` | `Task.case.owner_org` for `task_id` |

If the row cannot be resolved, log a warning and return `[]`. Never dispatch all playbooks as a
fallback. This keeps `core/events.py` unchanged and avoids touching every emitter just to carry a
second source of truth.

`AutomationRun` gains no new column: its `case` and `playbook` already identify the org. New runs are
guaranteed to have `playbook.owner_org == case.owner_org` by the dispatch filter and the `run_now`
guard.

### D4 — Manual runs are org-bound too

`run_now()` resolves the linked case when only an observable is supplied, then refuses if
`playbook.owner_org_id != case.owner_org_id`. Add a dedicated `PlaybookOrgMismatch(ValueError)` so
views can render the uniform 404 without swallowing ordinary `ValueError`s (e.g. "observable is not
linked to a case").

This means a write-share holder in org B cannot run an org-B playbook against an org-A case. Automation
credentials are org-owned; the run must use the case owner's playbooks.

### D5 — SuperAdmin: visibility yes, runtime no

`org_q("owner_org", user)` already returns all rows for `superAdmin` and own-org rows for everyone
else. Use it everywhere. A superAdmin can list/detail/edit any playbook, but a manual run still
requires the subject case's org to match the playbook's org. Cross-org **detail** reads are audited;
list paths are not audited (consistent with other superAdmin list surfaces).

### D6 — Name lookup is scoped; ambiguous names are 404

`idOrName` resolution first filters by `org_q`, then UUID, then name. For non-superAdmins the per-org
constraint makes the name unique. For a superAdmin, two orgs can hold the same name; a name lookup that
matches more than one row returns the uniform 404 and the superAdmin must use a UUID. This avoids
returning an arbitrary tenant's playbook.

### D7 — Creation org and immutability

Create assigns `request.user.org` on both API and UI. `owner_org` is never accepted in PATCH or the
edit POST and is never reassigned. A superAdmin who needs a playbook in another org can create it as a
member of that org (or a future admin-UX change can add an org selector).

## Data model and migration

### Model changes — `automation/models.py`

```python
class Playbook(UUIDModel, TimeStampedModel):
    owner_org = models.ForeignKey(
        "identity.Organisation",
        on_delete=models.PROTECT,
        related_name="playbooks",
    )
    name = models.CharField(max_length=200)  # no unique=True
    ...

    class Meta:
        db_table = "playbook"
        constraints = [
            models.UniqueConstraint(
                fields=["owner_org", "name"],
                name="playbook_org_name_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["owner_org", "trigger_event", "is_active"],
                name="playbook_org_trigger_idx",
            ),
        ]
```

### Migration — `automation/migrations/0008_playbook_tenant_scope.py`

1. `AddField("playbook", "owner_org", FK(..., null=True))`.
2. `RunPython(backfill_playbook_org, reverse=noop)`:
   - `Organisation.objects.get_or_create(name=settings.AMALTHEA_DEFAULT_ORG_NAME)`.
   - `Playbook.objects.filter(owner_org__isnull=True).update(owner_org=org)`.
3. `AlterField("playbook", "owner_org", FK(..., null=False))`.
4. `AlterField("playbook", "name", CharField(max_length=200))` to drop the old unique flag.
5. `AddConstraint(... UniqueConstraint(...))`.
6. `RemoveIndex("playbook", "playbook_trigger_idx")`.
7. `AddIndex(... playbook_org_trigger_idx ...)`.

Dependencies: `("automation", "0007_rewrite_legacy_action_path")` and the latest `identity` migration
that guarantees the default org exists. The backfill should use historical models only, no signals.

The existing seed migrations `0005_seed_playbook.py` and `0006_seed_example_playbook.py` run before
0008 and are covered by the backfill; they do not need an `owner_org` argument.

## Dispatch and manual-run isolation

### `automation/dispatcher.py`

Add:

```python
class PlaybookOrgMismatch(ValueError):
    """The playbook and the subject case do not belong to the same organisation."""
```

Add `_event_org_id(event) -> UUID | str | None`:

- `AlertIngested` → `Alert.objects.filter(pk=event.alert_id).values_list("owner_org_id", flat=True).first()`
- `CaseStatusChanged` → `Case.objects.filter(pk=event.case_id)...`
- `ObservableCreated` → require `case_id`, then `Case.objects.filter(pk=event.case_id)...`
- `TaskCompleted` → `Task.objects.filter(pk=event.task_id).values_list("case__owner_org_id", flat=True).first()`

In `dispatch()`:

```python
org_id = _event_org_id(event)
if org_id is None:
    logger.warning("automation.dispatch: cannot resolve owner org for %s", type(event).__name__)
    return []
playbooks = list(
    Playbook.objects.filter(
        owner_org_id=org_id,
        trigger_event=event_name,
        is_active=True,
    ).order_by("name")
)
```

Keep the existing idempotency key derivation. A subject belongs to one org, and dispatch only selects
that org's playbooks, so the playbook name remains sufficient in the key.

In `run_now()` after resolving a linked case:

```python
if playbook.owner_org_id != case.owner_org_id:
    raise PlaybookOrgMismatch("playbook and case belong to different organisations")
```

Check this **before** creating the `AutomationRun`. The view layer also pre-resolves/scopes so ordinary
users get a 404 before reaching this guard.

`automation/registry.py::playbooks_for` must accept `owner_org_id` and filter on it, or be removed if
it remains unused. Leaving a global helper is not acceptable.

## API contract — `automation/views.py`, `core/serializers.py`

### Shared helpers

- `_resolve(identifier: str, user) -> Playbook | None`: filter
  `Playbook.objects.select_related("owner_org").filter(org_q("owner_org", user))`; then UUID; then a
  single name match. More than one name match → `None` (uniform 404).
- `_require_analyst(request) -> Response | None`: reuse `IsAnalystOrAbove` and return the existing
  `AuthorizationError` 403 envelope.
- `_can_read_config` is the same analyst+ check. Do not rely on the global `ViewerReadOnly`, which
  permits safe methods.

### Endpoints

| Endpoint | New behaviour |
|---|---|
| `GET /api/v1/playbook` | `viewer` → 403. Otherwise org-scoped, `select_related("owner_org")`, order by owner org/name. `superAdmin` sees all. |
| `POST /api/v1/playbook` | analyst+; create with `owner_org=request.user.org`; duplicate name checked within that org only. |
| `GET /api/v1/playbook/_meta` | Unchanged. Any authenticated org member; no tenant data, no secrets. |
| `GET /api/v1/playbook/<idOrName>` | `viewer` → 403; scoped resolve; cross-org → 404. SuperAdmin cross-org detail read is audited. |
| `PATCH /api/v1/playbook/<idOrName>` | analyst+; scoped resolve; duplicate name within `playbook.owner_org`; `owner_org` immutable. |
| `DELETE /api/v1/playbook/<idOrName>` | analyst+; scoped resolve; existing active-run refusal and terminal-history deletion unchanged. |
| `POST /api/v1/playbook/<idOrName>/run` | analyst+; scoped resolve; case/observable scoped as today; require playbook org == resolved case org; mismatch → 404 and no run. |

`playbook_json()` gains:

```python
"ownerOrg": str(playbook.owner_org_id),
"ownerOrgName": playbook.owner_org.name,
```

Keep `config` inline. Keep the existing `_id`/`id`/`_type` keys and camelCase conventions. Use
`select_related("owner_org")` on list/detail to avoid N+1.

Audit superAdmin cross-org detail reads:

```python
maybe_record_cross_org_read(
    request,
    actor=request.user,
    target_obj=playbook,
    target_type="Playbook",
    target_id=str(playbook.id),
)
```

Call it on successful GET detail (not on collection list, not on writes). It is a no-op for
non-superAdmins and same-org reads.

## UI behaviour — `ui/views.py`, templates

Add a single helper in `ui/views.py`:

```python
def _resolve_playbook_or_404(user, identifier: str) -> Playbook:
    qs = Playbook.objects.select_related("owner_org").filter(org_q("owner_org", user))
    try:
        pk = uuid.UUID(identifier)
    except (TypeError, ValueError):
        pk = None
    if pk is not None:
        return get_object_or_404(qs, pk=pk)
    matches = list(qs.filter(name=identifier)[:2])
    if len(matches) != 1:
        raise Http404()
    return matches[0]
```

- `playbook_list`: filter with `org_q`; add `select_related("owner_org")`; keep viewer access. Add a
  `show_owner_org` context flag for `superAdmin` and an **Organisation** column in the template so
  duplicate names across orgs are distinguishable.
- `playbook_create`: reject an org-less user with 404; duplicate check within `request.user.org`; set
  `owner_org=request.user.org`.
- `playbook_detail`: scoped resolve. GET stays analyst+ via `@not_viewer`; POST stays admin+. Audit
  superAdmin cross-org GET. Do not allow `owner_org` to change.
- `playbook_edit`: scoped resolve. GET analyst+; POST admin+; duplicate name check is not needed
  because the UI name is read-only, but keep the invariant if the form later becomes renameable.
- `playbook_delete`: scoped resolve; role gate unchanged.
- `playbook_run`: scoped resolve; case/observable resolution unchanged; catch
  `PlaybookOrgMismatch` and render 404; ordinary `ValueError` keeps the existing flash-message path.
- `playbook_detail.html`: add an "Owner organisation" row. Keep the full config block because
  analysts/admins are the intended readers; viewers never reach this page.
- `playbook_form.html`: no header masking. Add a read-only owner-org display on edit if it fits the
  existing meta layout; otherwise rely on the detail page.
- No inline styles. If CSS changes are needed, edit `ui/static/ui/app.src.css` and run `make css`.

## Security analysis

- **Cross-tenant read:** closed by `org_q` on list/detail/edit/delete/run and API collection/detail.
- **Cross-tenant write:** closed by scoped resolution and by setting `owner_org` only from the caller.
- **Viewer read:** API collection/detail return 403; UI detail/edit remain `not_viewer` 404; UI list
  remains readable but renders no `config` map.
- **Triggered automation:** closed by filtering playbooks on the event subject's org. Unresolvable
  events fail closed with no run.
- **Manual automation:** closed by the playbook/case org equality guard; write-share holders cannot
  mix another org's credentials with the case.
- **SuperAdmin:** keeps break-glass visibility per ADR-004; detail reads are audited. Runtime remains
  org-bound, so a superAdmin cannot silently run org A credentials against org B data.
- **No existence oracle:** cross-org and missing playbooks both return the uniform 404; client
  validation errors remain 400 only after the row is visible.

### Residual risks (accepted)

1. Same-org `analyst`/`admin`/`superAdmin` can still read `config["headers"]`; this is the trusted
   authoring/execution role set. A future secret-reference or masked-read design is required if that
   trust boundary is too broad.
2. A superAdmin collection GET returns all orgs' inlined configs. It is a break-glass role; list reads
   are not per-row audited anywhere in this codebase. Detail reads are audited.
3. Historical `AutomationRun` rows may reference a playbook and a case from different orgs if they
   predate this change. No new such rows are created; cleanup is out of scope.
4. Secrets embedded in `config["url"]` or `config["body"]` are not addressed by header masking either;
   secret storage is a separate design.

## Implementation phases and acceptance criteria

### Phase 1 — Schema, migration, dispatch (backend / db-postgres)

**B1. Playbook ownership model and migration**

Files: `automation/models.py`, `automation/migrations/0008_playbook_tenant_scope.py`.

AC:
- [ ] `Playbook.owner_org` is a required `PROTECT` FK to `identity.Organisation`.
- [ ] Existing rows are backfilled to the default org; no row is left null.
- [ ] `name` is no longer globally unique; `(owner_org, name)` is unique.
- [ ] The trigger dispatch index starts with `owner_org`.
- [ ] `makemigrations --check` reports no changes.
- [ ] A migration test proves a pre-0008 playbook comes out owned by the default org and duplicate
      names across two orgs are allowed.

**B2. Dispatch and manual-run org binding**

Files: `automation/dispatcher.py`, `automation/registry.py`.

AC:
- [ ] Each of the four event types selects only playbooks whose `owner_org` matches the subject's org.
- [ ] An event whose subject/org cannot be resolved returns `[]` and does not raise.
- [ ] `ObservableCreated` uses the linked case's org, not the observable's org.
- [ ] `TaskCompleted` uses the task's case org.
- [ ] `run_now()` raises `PlaybookOrgMismatch` when `playbook.owner_org_id != case.owner_org_id`
      before creating a run.
- [ ] No unscoped `playbooks_for` helper remains.
- [ ] Existing idempotency behaviour and order are preserved.

**B3. API scoping and viewer denial**

Files: `automation/views.py`, `core/serializers.py`.

AC:
- [ ] `viewer` GET collection/detail → 403; `_meta` remains readable.
- [ ] Analyst/admin list/detail return own-org rows only; cross-org UUID/name → 404.
- [ ] Create sets `owner_org` from the caller; duplicate names are rejected only within that org.
- [ ] PATCH/DELETE/run resolve through the scoped helper; cross-org → 404.
- [ ] `playbook_json` includes `ownerOrg` and `ownerOrgName`; `config` remains inlined.
- [ ] SuperAdmin sees all; a duplicate name is ambiguous and returns 404; detail GET writes an
      `AuditLog` cross-org read.
- [ ] Manual run with a mismatched playbook/case org → 404 and no `AutomationRun`.

### Phase 2 — UI scoping (django-frontend)

**F1. Playbook views**

Files: `ui/views.py`.

AC:
- [ ] List/create/detail/edit/delete/run all use the scoped resolver / scoped queryset.
- [ ] List is still readable by `viewer`; detail/edit remain 404 for `viewer`.
- [ ] Create rejects an org-less user and stamps `request.user.org`.
- [ ] Cross-org detail/edit/delete/run → 404.
- [ ] Manual run with a mismatched org → 404 and no run.
- [ ] SuperAdmin cross-org detail GET writes the audit row.
- [ ] Existing admin-only update and analyst+ run/delete role gates are unchanged.

**F2. Templates**

Files: `ui/templates/ui/playbook_list.html`, `ui/templates/ui/playbook_detail.html`,
`ui/templates/ui/playbook_form.html` (only if needed).

AC:
- [ ] SuperAdmin list has an Organisation column; non-superAdmin lists are unchanged.
- [ ] Detail shows owner organisation.
- [ ] No header masking or new secret field is introduced.
- [ ] No inline styles; CSS source workflow respected if styles change.
- [ ] Viewer list still exposes no authoring links and no config map.

### Phase 3 — Fixtures, tests, docs (qa/verifier)

**T1. Update existing fixtures and route allow-lists**

Files: all `tests/**` files that create `Playbook` without `owner_org`; at minimum:

- `tests/conformance/test_tenant_scoping.py`
- `tests/conformance/test_playbook_roundtrip.py`
- `tests/conformance/test_playbook_authoring.py`
- `tests/conformance/test_playbook_script_ui.py`
- `tests/conformance/test_playbook_delete_ui.py`
- `tests/conformance/test_viewer_affordances.py`
- `tests/conformance/test_mvp_loop_automation.py`
- `tests/conformance/test_realtime.py`
- `tests/conformance/test_enum_contracts.py`
- `tests/conformance/test_integrity.py`
- `tests/conformance/test_fk_audit.py`
- `tests/conformance/test_ui_loop.py`
- `tests/conformance/test_ui_pagination.py`
- `tests/conformance/test_ui_pagination_edge_cases.py`
- `tests/conformance/test_authz.py`

AC:
- [ ] Every `Playbook.objects.create/get_or_create` supplies `owner_org` (prefer the `default_org` or
      module's existing org fixture).
- [ ] Remove `playbook-collection`, `playbook-collection-slash`, `playbook-detail`,
      `playbook-detail-slash` from `_GLOBAL_READ_ROUTES`; keep `playbook-meta*`.
- [ ] Remove `ui-playbook-list`, `ui-playbook-detail`, `ui-playbook-edit` from
      `_UI_GLOBAL_READ_ROUTES`; keep write-only playbook routes allow-listed where appropriate.
- [ ] Add `/api/v1/playbook` to `TENANT_ROUTES` and confirm org-less access is denied/empty.
- [ ] Update stale comments that call playbooks global.

**T2. New conformance coverage**

New file: `tests/conformance/test_playbook_tenant_scope.py` (or split by concern).

AC:
- [ ] Model/migration: required owner; default-org backfill; per-org duplicate allowed; same-org
      duplicate rejected.
- [ ] API: viewer GET 403; own-org list/detail; cross-org 404 for list/detail/patch/delete/run;
      create stamps org; duplicate name per org; superAdmin all; ambiguous name 404; `ownerOrg` fields.
- [ ] API manual run: own-org 202; cross-org playbook 404; playbook/case org mismatch 404/no run.
- [ ] UI: own-org list/detail/edit; cross-org 404; viewer list readable but detail/edit 404; create
      stamps org; superAdmin org column; manual run mismatch 404.
- [ ] Dispatch: each event type runs only the subject org's playbook; same name in two orgs runs the
      correct one; unresolvable org yields no run; observable link uses case org; task uses case org.
- [ ] Audit: superAdmin cross-org detail GET creates one `AuditLog`; same-org GET creates none.

**T3. Docs**

Files: `docs/spec/api.md`, `docs/spec/data-model.md`, `docs/spec/automation.md`,
`docs/spec/deviations.md` (if a deviation note is warranted), `TODO.md` §11.2 status.

AC:
- [ ] Data model documents `owner_org`, per-org unique name, and the new index.
- [ ] API documents read denial for viewers, org scoping, `ownerOrg` fields, ambiguous-name 404, and
      manual-run org equality.
- [ ] Automation spec documents event-org dispatch and the fail-closed missing-org path.
- [ ] TODO §11.2 links this plan and records the accepted same-org cleartext residual.
- [ ] No doc claims playbooks are global.

**T4. Verification gates**

AC:
- [ ] `make check` passes (ruff, mypy, Django check, migrations, CSS drift, playbook discovery).
- [ ] Targeted tests pass for every touched playbook/tenant test module.
- [ ] Full suite passes (verifier runs `.venv/bin/pytest`; PostgreSQL-only test may remain skipped).
- [ ] Verifier writes `docs/planning/VERIFY-2026-10-30-playbook-tenant-scoping.md` and checks every AC
      in this plan.
- [ ] `security-auditor` reviews the viewer denial, cross-org 404 paths, dispatch filter, and manual-run
      guard; residual risks are confirmed accepted.

## Testing matrix

| Concern | Minimal test |
|---|---|
| owner required | `Playbook.objects.create(...)` without org raises; model/migration test |
| backfill | pre-0008 row → default org |
| per-org name | same name in org A/B succeeds; duplicate in A fails |
| API viewer | GET `/api/v1/playbook` and `/{id}` → 403 |
| API scoping | org A analyst cannot GET/PATCH/DELETE/run org B playbook or name |
| API create | `ownerOrg` equals caller org |
| API superAdmin | list all; detail cross-org; audit row |
| API ambiguity | duplicate names across orgs + name lookup → 404 |
| UI scoping | cross-org detail/edit/delete/run → 404 |
| UI viewer | list 200 with inventory, no links; detail/edit 404 |
| dispatch | org A event runs only org A playbook |
| dispatch fail-closed | missing alert/case/task → `[]`, no exception, no run |
| manual run | playbook/case org mismatch → 404, no run |
| idempotency | replay still collapses; org scoping does not change keys |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Large mechanical test churn from required `owner_org` | Add an `owner_org` argument to every factory; use the existing `default_org` fixture; run the targeted modules first. |
| Migration fails on a database with no default org | The default org is guaranteed by `identity/migrations/0006_create_default_org.py`; the data migration still uses `get_or_create` defensively. |
| SuperAdmin name ambiguity returns arbitrary tenant | Resolve only when exactly one name match; otherwise uniform 404 and use UUID. |
| Historical cross-org runs remain | Accepted; no new cross-org runs. Document in the plan and verification report. |
| Same-org cleartext remains | Accepted for M3; record as residual and defer secret references/masking. |
| Event org lookup adds a query | PK/index lookups; dispatch already runs inside a transaction and playbook lookup is indexed. |
| UI list for superAdmin shows duplicate names | Add the Organisation column; non-superAdmin lists unchanged. |
| A scoped helper is bypassed later | Centralise resolution in `_resolve` / `_resolve_playbook_or_404`; add conformance tests for every route. |

## Open questions

None blocking. The decisions above are the approved defaults:

- Option A (scope, no masking).
- Default-org backfill.
- Per-org names.
- Event-org dispatch.
- Viewer API reads denied.
- SuperAdmin visibility with org-bound runtime and audited detail reads.

If any of these change during implementation, record the deviation in this plan and notify the
verifier before merging.

## Rollout / rollback

- Forward migration is data-preserving; existing playbooks land in the default org.
- Rollback of the schema is possible only before the new per-org names exist; once two orgs use the
  same name, reverting to a global unique constraint will fail. Treat the migration as one-way in
  production and restore from backup if a rollback is required.
- Deploy code and migration together. A code-only deploy against the old schema would query
  `owner_org` that does not exist; a migration-only deploy would leave old code global. The release is
  atomic by deployment order (migrate, then restart ASGI/workers).
