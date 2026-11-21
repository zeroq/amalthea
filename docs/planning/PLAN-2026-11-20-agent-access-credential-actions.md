# PLAN-2026-11-20 — Agent Access Credential Dropdown, Disable, and Delete

**Status:** Implemented — verification READY  
**Owner:** Planning / Tech Lead  
**Target:** Settings → Agent access (`ui-settings-agent-access`)  
**References:** `AGENTS.md` §2/§6, ADR-006 role gate, ADR-009 org scoping, ADR-013 MCP OAuth resource server, `docs/planning/PLAN-2026-11-20-mcp-phase3.md`

## Decisions

Confirmed with the user:

1. Disable is **reversible** (`disabled_at` + Enable action), not a rename of Revoke.
2. Delete is available only for **inactive** records.
3. The superAdmin service-user dropdown uses a **grouped list only**; no client-side filtering.
4. Permanent delete is also added to **External OAuth bindings**.
5. The existing terminal **Revoke** action stays for MCP credentials.

## Summary

The MCP agent credential create modal currently uses a free-text input with a `<datalist>` for **Service user login**. Browsers do not present this as a reliable select control: it accepts arbitrary text and only shows suggestions after typing. Replace it with a real `<select>` populated from active service users, grouped by organisation for superAdmins.

The MCP agent credential table currently exposes only terminal **Revoke** (`fa-ban`). Add:

1. A reversible **Disable** action with a `fa-toggle-off` icon.
2. An **Enable** action with a `fa-toggle-on` icon for disabled credentials.
3. A permanent **Delete** action with a `fa-regular fa-trash-can` icon for inactive credentials.
4. The same permanent **Delete** action for inactive External OAuth bindings.

Disabled credentials fail closed but can be re-enabled. Revoked credentials remain terminal. Revoke is not removed. This mirrors the existing ingestion-token lifecycle in `ui/templates/ui/settings_sources.html`.

## Goals

- Turn **Service user login** into a true, accessible dropdown.
- Keep organisation and tenant scoping intact for admins and superAdmins.
- Add reversible disable/enable for static MCP agent credentials.
- Add audited permanent delete for inactive credentials and inactive OAuth bindings.
- Preserve the existing terminal Revoke workflow.
- Keep the dark, keyboard-driven, TheHive-aligned UX: icon buttons have accessible names, icons are `aria-hidden`, destructive actions use styled `<dialog>` modals, and the page degrades to plain POST forms without JS.

## Non-goals

- No client-side JavaScript filtering for the dropdown; the grouped list is server-rendered.
- No API/DRF endpoints; these are UI-only POST routes under `/settings/agent-access/...`.
- No changes to token minting, plaintext display, or the show-once modal.
- No changes to the OAuth binding create form fields.
- No secrets, token plaintext, or secret hashes in logs, audit rows, templates, or URLs.
- No change to MCP scope validation or proposal approval rules.

## Architecture

Stack remains Django 5.x templates + HTMX/Tailwind-style components, Django auth/session middleware, and PostgreSQL.

Rationale:

- The create modal is already server-rendered via `ui/_modal.html`; a real `<select>` is the smallest robust fix and preserves progressive enhancement.
- A grouped list for superAdmins gives explicit org ownership without introducing JS state.
- Credential lifecycle mutations belong in `mcp_server/credentials.py` beside `mint()`, `verify()`, and `revoke()` so UI views stay thin.
- Disable/enable/delete are POST-only UI routes, guarded by the same `_require_admin_ui` + manual org scoping used by `settings_agent_create`, `settings_agent_revoke`, and `settings_oauth_binding_disable` (ADR-006/ADR-009).
- Existing reference pattern: ingestion tokens distinguish reversible deactivation, terminal revocation, and delete-after-inactive in `ui/templates/ui/settings_sources.html`.

No new ADR is required for the approved design.

## Database Schema

Add one nullable timestamp to `McpCredential`:

```python
disabled_at = models.DateTimeField(null=True, blank=True)
```

Migration: `mcp_server/migrations/0005_mcpcredential_disabled_at.py`.

Model properties in `McpCredential`:

```python
@property
def is_disabled(self) -> bool:
    return self.disabled_at is not None


@property
def is_active(self) -> bool:
    return not (self.is_disabled or self.is_revoked or self.is_expired)
```

No schema change is required for `McpOAuthBinding`; its existing `is_active` flag represents enabled/disabled state.

Notes:

- `is_revoked` and `is_expired` remain unchanged.
- `McpCredential.verify()` already checks `is_active`; adding `is_disabled` there makes disabled tokens fail closed without changing the verifier API.
- Existing FKs from `McpInvocation` and `McpActionProposal` to `McpCredential` use `SET_NULL`, so hard delete does not break history rows.
- `McpInvocation.oauth_binding` uses `SET_NULL`, so hard-deleting an OAuth binding preserves invocation history.
- Audit rows retain `target_type` and `target_id` after deletion.

## API / Routes

Existing routes remain:

- `GET /settings/agent-access` → `ui-settings-agent-access`
- `POST /settings/agent-access/create` → `ui-settings-agent-access-create`
- `POST /settings/agent-access/<uuid:credential_id>/revoke` → `ui-settings-agent-access-revoke`
- `POST /settings/agent-access/oauth-bindings/<uuid:binding_id>/disable` → `ui-settings-oauth-binding-disable`

Add for MCP credentials:

| Method | Path | Name | Purpose |
|---|---|---|---|
| POST | `/settings/agent-access/<uuid:credential_id>/disable` | `ui-settings-agent-access-disable` | Soft-disable; fail closed; reversible |
| POST | `/settings/agent-access/<uuid:credential_id>/enable` | `ui-settings-agent-access-enable` | Clear `disabled_at` |
| POST | `/settings/agent-access/<uuid:credential_id>/delete` | `ui-settings-agent-access-delete` | Hard delete inactive credential |

Add for OAuth bindings:

| Method | Path | Name | Purpose |
|---|---|---|---|
| POST | `/settings/agent-access/oauth-bindings/<uuid:binding_id>/delete` | `ui-settings-oauth-binding-delete` | Hard delete inactive binding |

Contracts:

- All new views: `@not_viewer`, `@login_required`, `@require_POST`, `_require_admin_ui`, and object-scoped lookup helper.
- Wrong role → `404`.
- Cross-org or unknown ID → `404`.
- Success → `302` to `ui-settings-agent-access` with a flash message.
- Idempotent no-op where applicable → `302` with an info message and no duplicate audit row.
- Delete active credential/binding → `302` with an error message; no row removed.
- No plaintext token, secret hash, client secret, or full credential value is ever accepted from or returned to these routes.

Service-layer helpers in `mcp_server/credentials.py`:

- `disable(credential_id) -> bool`
- `enable(credential_id) -> bool`
- `delete(credential_id) -> bool`

OAuth binding deletion can remain in the view using `_scoped_oauth_binding()` plus `binding.delete()` inside a transaction; no new service module is required.

Audit actions:

- `mcp_credential.disabled`
- `mcp_credential.enabled`
- `mcp_credential.deleted`
- `mcp_oauth_binding.deleted`

`record_mcp_credential_change` and `record_mcp_oauth_binding_change` already document `deleted`; extend the credential helper docstring to include disabled/enabled.

## UI / UX

### Create modal

File: `ui/templates/ui/_settings_agent_create_form.html`

Replace the `<input type="text" name="user_login" list="agent-create-users">` and `<datalist>` with:

```html
<select id="agent-create-user" name="user_login" required form="{{ form_id }}">
  <option value="">— select a service user —</option>
  ...
</select>
```

Details:

- For admins: options are active users in the admin’s org.
- For superAdmins: options are grouped by organisation with `<optgroup label="Org name">`; option label is `login`.
- No JS filtering and no `data-org-id` requirement.
- Backend keeps the existing “service user must belong to selected organisation” validation.
- Plain POST still works with JS disabled.

### Credentials table actions

File: `ui/templates/ui/settings_agents.html`

Wrap icons in `div.control-row`.

Action matrix:

| State | Icons |
|---|---|
| Active | Disable (`fa-toggle-off`), Revoke (`fa-ban`) |
| Disabled | Enable (`fa-toggle-on`), Revoke (`fa-ban`), Delete (`fa-regular fa-trash-can`) |
| Revoked | Delete (`fa-regular fa-trash-can`) |
| Expired | Delete (`fa-regular fa-trash-can`) |

Requirements:

- Each icon-only button has `aria-label` and `title`; `<i>` remains `aria-hidden="true"`.
- Disable and Delete use `ui/_modal.html` confirmation dialogs; no native `confirm()` / `alert()`.
- Enable may be a direct POST form (non-destructive).
- Modal IDs are unique per credential, e.g. `disable-agent-<id>`, `delete-agent-<id>`.
- Add `Disabled` pill with a text label; never colour-only.
- Do not render action buttons for credentials the user cannot mutate via org scoping; current list is already scoped.

New modal bodies:

- `ui/templates/ui/_settings_agent_disable_form.html`
- `ui/templates/ui/_settings_agent_delete_form.html`

Both name the credential and explain consequence. Delete copy states that history records keep the credential ID but the credential itself is permanently removed.

### OAuth bindings table actions

File: `ui/templates/ui/settings_agents.html`

Existing active bindings keep the `fa-ban` disable action. Disabled bindings currently render `—`; replace that with a permanent Delete icon:

| State | Icons |
|---|---|
| Active | Disable (`fa-ban`) |
| Disabled | Delete (`fa-regular fa-trash-can`) |

Requirements:

- Delete uses `ui/_modal.html` with unique modal ID `delete-oauth-binding-<id>`.
- Modal body names the binding and states deletion is permanent.
- `aria-label` / `title` identify the binding by name.
- Cross-org and role gates remain identical to the existing disable action.

New modal body:

- `ui/templates/ui/_settings_oauth_binding_delete_form.html`

## Phases and Tasks

### Phase 1 — Replace datalist with real dropdown

**T1.1 Server-render real select**

Files: `ui/templates/ui/_settings_agent_create_form.html`, `ui/views/agents.py`.

AC:
- Rendered HTML contains `<select id="agent-create-user" name="user_login" ...>`.
- No `datalist` remains in this template.
- Admin sees only active users from own org.
- superAdmin sees all active users grouped by organisation via `<optgroup>`.
- No new JS file is added.
- Plain POST still works with JS disabled.
- Backend still rejects mismatched org/inactive users.

### Phase 2 — Reversible disable/enable

**T2.1 Model + service lifecycle**

Files: `mcp_server/models.py`, new migration `0005_mcpcredential_disabled_at.py`, `mcp_server/credentials.py`.

AC:
- `disabled_at` is nullable and migrated.
- `is_disabled` and updated `is_active` behave correctly.
- `verify()` returns `False` for disabled credentials.
- `disable()` / `enable()` are idempotent and return whether a mutation occurred.

**T2.2 UI routes**

Files: `ui/views/agents.py`, `ui/urls.py`, `ui/views/__init__.py`.

AC:
- Disable/enable routes are POST-only, admin-only, and org-scoped.
- Cross-org and unknown IDs return `404`.
- Success redirects to agent access with flash message.
- Audit rows use `mcp_credential.disabled` / `mcp_credential.enabled`.
- No audit/log entry contains token plaintext.

**T2.3 UI controls**

Files: `ui/templates/ui/settings_agents.html`, new disable modal body.

AC:
- Active credentials show a `fa-toggle-off` disable icon.
- Disabled credentials show a `Disabled` pill and a `fa-toggle-on` enable icon.
- Disable uses a styled modal with unique ID and accessible button labels.
- Enable is a direct POST form with CSRF.
- Terminal Revoke remains visible for active and disabled credentials.

### Phase 3 — Delete inactive records

**T3.1 MCP credential delete route + service**

Files: `mcp_server/credentials.py`, `ui/views/agents.py`, `ui/urls.py`, `ui/views/__init__.py`.

AC:
- Delete route is POST-only, admin-only, and org-scoped.
- Active credentials cannot be deleted.
- Disabled, revoked, and expired credentials can be deleted.
- Row is removed and an audit row is written in the same transaction.
- Audit action is `mcp_credential.deleted` and does not include token material.

**T3.2 MCP credential delete UI**

Files: `ui/templates/ui/settings_agents.html`, new delete modal body.

AC:
- Inactive credentials show a `fa-regular fa-trash-can` delete icon.
- Active credentials do not show delete.
- Delete uses a styled modal with unique ID, accessible name, and explicit “permanent” warning.
- Deleted rows disappear from the list after redirect.

**T3.3 OAuth binding delete route**

Files: `ui/views/agents.py`, `ui/urls.py`, `ui/views/__init__.py`.

AC:
- Delete route is POST-only, admin-only, and org-scoped via `_scoped_oauth_binding`.
- Active bindings cannot be deleted.
- Inactive bindings can be deleted.
- Row is removed and an audit row is written in the same transaction.
- Audit action is `mcp_oauth_binding.deleted`.
- No client secret or token material is logged.

**T3.4 OAuth binding delete UI**

Files: `ui/templates/ui/settings_agents.html`, new binding delete modal body.

AC:
- Disabled bindings show a `fa-regular fa-trash-can` delete icon.
- Active bindings do not show delete.
- Delete uses a styled modal with unique ID and accessible name.
- Deleted rows disappear from the list after redirect.

### Phase 4 — Tests and docs

**T4.1 Extend conformance tests**

Files:
- `tests/conformance/test_settings_agent_access.py`
- `tests/conformance/test_mcp_oauth_bindings_ui.py`
- `tests/conformance/test_ui_urls_smoke.py`
- `tests/conformance/test_tenant_scoping.py`

AC:
- Dropdown is a `<select>` with correct scoping/grouping; no datalist.
- Backend still rejects mismatched org/inactive user.
- Disable makes `credential.verify(plaintext)` return `False`; enable restores it.
- Credential audit actions and no-op idempotency are asserted.
- Credential delete removes only inactive credentials; cross-org/analyst/viewer attempts return `404`.
- OAuth binding delete removes only inactive bindings; cross-org/analyst/viewer attempts return `404`.
- New routes are discovered by URL/conformance tests and classified as admin-only.
- Icons and modals are asserted in rendered HTML.

**T4.2 Documentation/deviations**

Files: this plan, `TODO.md` only if an existing checklist item applies.

AC:
- Plan status updated to Implemented after verification.
- Any deviation from the approved decisions is recorded in the plan before merge.

## Security

- Role gate: admin/superAdmin only via `_require_admin_ui`; analysts/viewers receive `404`.
- Tenant gate: non-superAdmins can only mutate credentials or bindings in their own org via scoped lookup helpers.
- CSRF: all mutations are POST with Django CSRF token from rendered forms.
- Fail closed: disabled credentials must be rejected by `McpCredential.verify()` and therefore by MCP bearer auth.
- Secret hygiene: no plaintext token, hash, client secret, or full credential value in URL, audit details, logs, flash messages, or templates after the show-once modal.
- Destructive confirmation: disable and delete use styled `<dialog>` modals, never native dialogs.
- History: `SET_NULL` FKs preserve invocation/proposal rows; audit rows preserve deleted IDs.
- Logging: log credential/binding ID, org ID, and actor ID only.

## Testing

Targeted suites:

- `tests/conformance/test_settings_agent_access.py`
- `tests/conformance/test_mcp_oauth_bindings_ui.py`
- `tests/conformance/test_mcp_scopes_phase2.py`
- `tests/conformance/test_ui_urls_smoke.py`
- `tests/conformance/test_tenant_scoping.py`
- `tests/conformance/test_mcp_deployment.py` if auth-mode behaviour is touched

Static gates:

- `.venv/bin/ruff check .`
- `.venv/bin/ruff format --check .`
- `.venv/bin/mypy`
- `.venv/bin/python manage.py makemigrations --check --dry-run`
- `scripts/secret-scan.sh --tracked`

Full pytest only if explicitly requested (the last full run was ~23 minutes).

## Risks

- **Superadmin mismatch:** a grouped select removes free text, but superAdmin could still choose an org and a user from another org; backend validation must remain authoritative.
- **Grouped list length:** the existing 500-user cap could make the grouped select long; keep the cap and rely on native select keyboard navigation.
- **Icon clutter:** active credentials show two actions; disabled credentials show three. Use `control-row` and tooltips.
- **Audit clarity:** deleted records no longer exist as rows; audit UI must remain meaningful with only `target_id`.
- **Migration/backfill:** `disabled_at` defaults to `NULL`, so existing revoked/active states are unchanged.

## Decisions Record / Open Questions

None outstanding. Confirmed decisions:

- Reversible disable, not revoke rename.
- Delete inactive records only.
- Grouped service-user list only.
- Delete inactive OAuth bindings too.
- Keep terminal Revoke functionality.

## Verification Record

- Verifier final status: **READY**; all acceptance criteria met after the action-matrix fixes.
- Initial verifier findings (active Enable/Delete, disabled Disable, missing modal wiring) were resolved.
- QA: `134/134` targeted conformance tests passed; `35/35` markup-focused tests passed.
- Security: no blocking findings; role/org gating, CSRF/POST-only, fail-closed verification, inactive-only delete, and secret hygiene confirmed.
- Static gates: ruff check/format clean; `makemigrations --check --dry-run` clean; `scripts/secret-scan.sh --tracked` clean.

## Implementation Brief

**Primary files**

- `ui/templates/ui/_settings_agent_create_form.html`
- `ui/templates/ui/settings_agents.html`
- `ui/templates/ui/_settings_agent_disable_form.html` (new)
- `ui/templates/ui/_settings_agent_delete_form.html` (new)
- `ui/templates/ui/_settings_oauth_binding_delete_form.html` (new)
- `ui/views/agents.py`
- `ui/urls.py`
- `ui/views/__init__.py`
- `mcp_server/models.py`
- `mcp_server/migrations/0005_mcpcredential_disabled_at.py` (new)
- `mcp_server/credentials.py`
- `identity/audit.py` (docstring only)
- `tests/conformance/test_settings_agent_access.py`
- `tests/conformance/test_mcp_oauth_bindings_ui.py`
- `tests/conformance/test_ui_urls_smoke.py`
- `tests/conformance/test_tenant_scoping.py`

**Dependencies**

- This plan is approved.
- No upstream schema changes beyond the new migration.
- Existing `_modal.html`, `control-row`, icon, and role/org guard patterns must be reused.
- No new JS file.

**Deviations**

Any deviation from the approved decisions must be recorded here before merge.
