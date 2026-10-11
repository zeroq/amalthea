# Security Audit — Organisation & User Management (superAdmin Settings)

**Date:** 2026-10-11
**Auditor:** security-auditor (read-only; no code/tests/docs modified — this report is the only artefact written)
**Scope:** the O1–O4 delivery of `PLAN-2026-10-10-org-management.md` (superAdmin-only Settings area: Organisations + Users, REST + UI + guarded org deletion), on top of the verified tenant-isolation baseline (`SECURITY-2026-10-10-tenant-isolation-final.md`, ADR-004). Working tree vs `4c4c5f8`; everything reviewed is uncommitted on `main`.
**Files read (primary):** `identity/views.py`, `identity/services.py`, `identity/permissions.py`, `identity/audit.py`, `identity/urls.py`, `identity/models.py`, `identity/admin.py`, `identity/migrations/0006_create_default_org.py`, `core/serializers.py`, `ui/views.py` (settings region), `ui/urls.py`, `ui/templates/ui/settings_organisations.html`, `ui/templates/ui/settings_users.html`, `ui/templates/ui/settings_organisation_form.html`, `ui/templates/ui/settings_user_form.html`, `ui/templates/ui/_modal.html`, `ui/templates/ui/base.html`, `amalthea/settings/base.py`, plus the new tests.
**Method:** source review; targeted test run; targeted dynamic repros in a throwaway directory outside the repo (no repo files created/modified).

**Gate observed:**
- `tests/conformance/test_org_user_management.py tests/conformance/test_settings_ui.py -q` → **106 passed**.
- `scripts/secret-scan.sh --tracked` → **clean** (317 tracked files).
- Live repros (temp, out-of-repo) for the XSS sink, the org-delete lockout/audit bug, the enumeration oracle, and the UI 500s — all reproduced (details inline below).

## Verdict

**CHANGES REQUIRED.**

The role gating is sound at the HTTP layer (uniform 404, in most cases) and the serializers/validators are reasonable, but the delivery has **two High/Critical correctness-and-security defects in the org-deletion path** (self-lockout / last-superAdmin bypass; a non-atomic audit write that turns a successful destructive delete into a 500 with no audit row), **a stored-XSS sink in the Settings JS** (currently masked only by the CSP), and an **audit-log gap for UI create/update**. Do not build on this until F1–F5 are addressed.

---

## Findings

| # | Severity | Area | Summary |
|---|----------|------|---------|
| F1 | **Critical** | Org deletion | `members=delete` deletes the actor and the last active superAdmin → permanent lockout |
| F2 | **High** | Org deletion / audit | `organisation.deleted` audit written *outside* the transaction and references deleted FKs → 500 after commit, audit lost |
| F3 | **High** (CSP-masked) | XSS / output encoding | `escapejs` + `innerHTML` in Settings templates → stored XSS reachable by any tenant user |
| F4 | **Medium** | Audit | UI create/update of orgs/users writes no `AuditLog` row (API does) |
| F5 | **Medium** | AuthZ / TOCTOU | Last-active-superAdmin guard is a check-then-act with no lock → concurrent demotions can zero the admins |
| F6 | **Low** | Enumeration | `PATCH/DELETE /api/v1/user/{idOrLogin}` leaks user existence via a differing 404 `message` |
| F7 | **Low** | Input validation | Malformed UUID / 201-char-name collision in Settings UI POSTs → unhandled 500 |
| F8 | **Low** | Input validation | API `is_active` uses `bool()`; the string `"false"` is treated as **True** |
| F9 | **Info** | Admin | `IngestionToken.secret_hash` is exposed read-only in the Django admin |
| F10 | **Info** | CSP / UI | Settings modals rely on inline `<script>`, which the shipped CSP blocks (also the only thing blocking F3) |
| F11 | **Info** | Enumeration | `GET /api/v1/user/{idOrLogin}` returns any user to any authenticated caller (documented, pre-existing) |

---

### F1 — Critical — Org deletion `members=delete` bypasses the self / last-superAdmin guards (permanent lockout)

**Where**
- `identity/services.py:100-115` — the `members=delete` branch deletes **all** of the org's users via `members_qs.delete()` with no self/last-admin guard.
- `identity/views.py:301-349` — `_delete_organisation` calls the service directly; the `_delete_user` guards (`identity/views.py:571-590`) are never consulted.
- `ui/views.py:1501-1546` — the UI delete path calls the same service.

**Exploit / trigger.** A superAdmin `A` is a member of organisation `X`, and `X` owns no cases/alerts/observables/tokens (so the pre-flight passes). A calls:

```
DELETE /api/v1/organisation/{X}
{"members": "delete"}
```

The service deletes every member account — **including `A`** — and then the org. If `A` was the only active superAdmin, the platform is left with **zero** administrators and nobody can ever open Settings/tokens/tenants again. The plan explicitly calls this out as risk R1 (§9) and requires self/last-admin protection (§5.3, §7); the user endpoints enforce it, the org-delete path does not.

**Reproduced (temp test, `transaction=True`):** status **500**, `actor still exists: False`, `active superadmins left: 0`, org gone. (The 500 is F2, but the account deletion is F1 and is not rolled back.)

**Remediation.** In the `members=delete` branch (or before member deletion) refuse to delete the acting user and refuse to remove the last active superAdmin (reuse the `delete_user` guard logic, or block the org delete when the org's members include the last active superAdmin). Add a conformance test asserting `DELETE /organisation/{own}` with `members=delete` cannot remove the actor/last admin. Note this also applies when the actor is an ordinary member being deleted alongside — treat "actor is a member of the deleted org" as a guarded case.

---

### F2 — High — Non-atomic `organisation.deleted` audit write references deleted FKs (500 after commit, audit lost)

**Where**
- `identity/services.py:100-115` (`with transaction.atomic():` … `org.delete()`), then `identity/services.py:121-123` — `record_organisation_change(...)` is called **after** the atomic block.
- `identity/audit.py:136-166` — `record_organisation_change` inserts `actor_org=getattr(actor, "org", None)`.

**Exploit / trigger.** A superAdmin deletes an organisation they belong to (either `members=reassign` or `members=delete`). After the transaction commits, `record_organisation_change` resolves the actor's cached `org` relation to the now-deleted organisation and inserts an `AuditLog` with a dangling `actor_org` FK (and, for `members=delete`, a dangling `actor` FK). The insert raises `IntegrityError: FOREIGN KEY constraint failed`; because it runs **after** the commit and there is no request-level transaction (`ATOMIC_REQUESTS` unset → Django default `False`), the deletion is already permanent.

**Reproduced (temp tests):**
- reassign, actor is a member → **500**, org gone, `AuditLog(action="organisation.deleted") == 0`.
- `members=delete`, actor is the sole superAdmin → **500**, actor deleted, 0 active superAdmins, org gone, audit count 0.
- Direct service call with a cached `actor.org` → `IntegrityError: FOREIGN KEY constraint failed`.

This also contradicts plan §5.6 ("the delete audit row is written **inside** the same `transaction.atomic()`").

**Remediation.** Capture `target_id` **and** `actor_org_id` before deletion, write the `organisation.deleted` audit row **inside** the same atomic block with `target_org=None` and an `actor_org_id` that still exists (or `None`), and make the whole delete+audit a single atomic unit. Wrap the service in a transaction that also owns the audit insert, and handle `IntegrityError` explicitly rather than letting it surface as a 500.

---

### F3 — High (currently masked by CSP) — Stored XSS via `escapejs` + `innerHTML` in the Settings templates

**Where**
- `ui/templates/ui/settings_organisations.html:85-89` (`name: "{{ org.name|escapejs }}"`), `:137-141` (`html += '<option value="' + opt.value + '">' + opt.name + '</option>'`), `:147` (`body.innerHTML = html`).
- `ui/templates/ui/settings_users.html:74-78` (`orgOptions`), `:86-105` (template literal), `:155` (`body.innerHTML = html`).

**Exploit / trigger.** `escapejs` escapes the value for a **JavaScript string literal**, not for an HTML/`innerHTML` context. The JS engine decodes `\u003C`/`\u003E` back to `<`/`>` at runtime, and the value is then concatenated into an HTML string assigned to `innerHTML` — so markup injection is possible.

Injection source is not privileged: **any authenticated member of an organisation (including `read-only`) can rename their own org** via `PATCH /api/v1/organisation/{own}` (`identity/views.py:240-258`, no role check — confirmed by `test_analyst_own_org_patch_works`). A superAdmin then loads Settings → Organisations or Settings → Users and the poisoned name is parsed as HTML in their session.

Proof (temp, out-of-repo):
```
python -c "from django.utils.html import escapejs; print(escapejs('</option><img src=x onerror=alert(1)>'))"
# \u003C/option\u003E\u003Cimg src\u003Dx onerror\u003Dalert(1)\u003E   ← decoded by JS to raw HTML
```
And the rendered page contains that escaped-then-decoded payload in the `innerHTML` sink (`test_xss_render` → `FOUND escapejs payload in JS sink: True`).

**Current mitigation — and why it is not enough.** The app ships a strict CSP (`amalthea/settings/base.py:65-78`): `script-src 'self'`, no `unsafe-inline`, no nonce (verified on the live response header). That blocks inline event handlers (`onerror=…`) and the inline `<script>` that performs the assignment, so the classic payload does **not** fire in a compliant browser today. However:
1. It is a defence-in-depth control, not a fix — the sink remains, and it becomes a **full superAdmin session-takeover (Critical)** the moment CSP is relaxed or bypassed.
2. The very feature needs inline JS to work, and that same CSP blocks it (F10) — creating direct pressure to add `unsafe-inline`, which would make F3 immediately exploitable.
3. Any environment/client that does not enforce CSP is exposed; HTML injection into the admin UI (UI redressing, injected links/forms) exists even under CSP.

**Remediation.** Never build HTML by concatenating server data. Pass data with `{{ ...|json_script:"id" }}` and read it with `JSON.parse`, then create nodes with `document.createElement`/`textContent`; or, if an HTML string is unavoidable, HTML-escape (`django.template.defaultfilters.escape`) the value before concatenation — `escapejs` is the wrong escaper for `innerHTML`. Prefer moving the modal JS into an external `ui/static/ui/*.js` (see F10).

*(Note: the plain table cells `{{ org.name }}` / `{{ org.email }}` use Django auto-escaping and are safe; only the JS sinks are affected.)*

---

### F4 — Medium — UI create/update mutations write no `AuditLog`

**Where**
- `ui/views.py:1451` — org create (`Organisation.objects.create`), no `record_organisation_change`.
- `ui/views.py:1488-1490` — org edit (`org.save`), no audit.
- `ui/views.py:1604-1605` — user create (`user.save`), no `record_user_change`.
- `ui/views.py:1711` — user edit (`user.save`), no audit.

Only the **delete** paths go through `identity.services` (which audits). `grep` confirms `ui/views.py` never imports or calls the audit helpers.

**Impact.** The Settings UI is the primary operator surface, yet tenant/personnel changes made there leave no forensic trail, while the equivalent API calls do. This violates plan goal G7 ("Every org and user mutation writes an `AuditLog` row") and §7. It is not directly exploitable, but it materially undermines accountability for the exact high-impact actions this feature introduces.

**Remediation.** Route UI mutations through `identity.services` (`create_user`/`update_user` already exist; add `create_organisation`/`update_organisation` wrappers or call `record_*` directly), and add a UI conformance test asserting one audit row per UI mutation.

---

### F5 — Medium — Last-active-superAdmin guard is check-then-act (TOCTOU / race)

**Where**
- `identity/views.py:499-504` (demote), `:528-534` (deactivate), `:578-581` (delete).
- `identity/services.py:222-225`, `:240-243`, `:298-303`.

Each guard does `User.objects.filter(role="superAdmin", is_active=True).count()`, compares to `1`, then saves/deletes in a **separate** statement — no `select_for_update`, no transaction, no DB constraint.

**Exploit.** Two concurrent requests (two active superAdmins `A`,`B`; `A` demotes/deactivates `B` while `B` demotes/deactivates `A`, or two delete requests) can both observe `count()==2` and both commit, leaving **zero** active superAdmins → lockout. Same class of bug as F1 but via concurrent user operations.

**Remediation.** Serialize these mutations: `with transaction.atomic():` + `User.objects.select_for_update().filter(role="superAdmin", is_active=True)` (or a single conditional `UPDATE … WHERE (SELECT count(*) …) > 1`), or a DB trigger/constraint that refuses to leave zero active superAdmins. Add a concurrency regression test where practical.

---

### F6 — Low — User-existence oracle on `PATCH/DELETE /api/v1/user/{idOrLogin}`

**Where** `identity/views.py:451-463` — the user is resolved **before** the superAdmin guard; a missing id returns `_not_found("User")` ("User not found") while an existing user returns the guard's `_not_found("Resource")` ("Resource not found"). Both are 404, but the JSON `message` differs.

**Reproduced (temp):** non-super PATCH existing user → `404 {"message":"Resource not found"}`; non-super PATCH random UUID → `404 {"message":"User not found"}`. (The org equivalent is uniform: both → "Organisation not found".)

**Impact.** Any authenticated user can confirm whether a given user id/login exists, violating the plan's "uniform 404 / no enumeration" intent (§7). Low: needs an account and only leaks existence.

**Remediation.** Run `_require_superadmin` before resolving/reporting the target for mutating verbs, or return the same generic body for "missing target" and "not permitted".

---

### F7 — Low — Settings UI input paths return unhandled 500s

**Where**
- `ui/views.py:1513-1517` — `Organisation.objects.get(pk=target_org_id)`; a non-UUID string raises `ValidationError` (only `DoesNotExist` is caught).
- `ui/views.py:1580-1583`, `:1643-1646` — same pattern for the create/edit `org` field.
- `ui/views.py:1448-1451`, `:1485-1490` — uniqueness is checked on the **untruncated** name but the stored value is `name[:200]`; two distinct >200-char names (or one 201-char name colliding with an existing 200-char name) pass the pre-check and then hit the DB unique constraint → `IntegrityError`.

**Reproduced (temp):** `POST /settings/users` with `org=not-a-uuid` → 500; `POST /settings/organisations/{id}/delete` with `target_org=not-a-uuid` → 500; 201-char name collision → `IntegrityError: UNIQUE constraint failed: identity_organisation.name`.

**Impact.** superAdmin-only, so self-inflicted; but it is inconsistent with the API (which deliberately uses `_as_uuid` — see `identity/views.py:66-75`), produces 500s/possible stack traces with `DEBUG=True`, and the UI does not fail closed cleanly.

**Remediation.** Reuse `_as_uuid`/`_resolve_org` in the UI, validate/truncate-compute the stored name before the uniqueness check, and catch `IntegrityError` to render a form error.

---

### F8 — Low — API `is_active` coercion accepts the string `"false"` as `True`

**Where** `identity/views.py:524` — `new_active = bool(payload["is_active"])`. `bool("false") is True`, so a JSON body `{"is_active":"false"}` silently leaves the account active (and could mislead an operator using a string-based client). Not a privilege escalation; a validation/robustness bug. **Remediation:** accept only real JSON booleans (or `"true"/"false"` explicitly) and 400 on anything else.

---

### F9 — Info — `IngestionToken.secret_hash` shown (read-only) in Django admin

**Where** `identity/admin.py` (`IngestionTokenAdmin.readonly_fields = ("prefix", "secret_hash", "last_used_at")`). The value is an Argon2 hash (not the plaintext, not directly usable), but plan §7 asked for `secret_hash` to remain "excluded everywhere". **Remediation:** drop `secret_hash` from `readonly_fields`/display; keep only `prefix` (non-sensitive) and `revoked_at`.

---

### F10 — Info — Settings modals depend on inline `<script>` that the shipped CSP blocks

**Where** the create/delete modal logic is inline in `ui/templates/ui/_modal.html:58-133` and in `settings_organisations.html` / `settings_users.html`; the CSP is `script-src 'self'` with **no nonce** (`amalthea/settings/base.py:65-78`). ADR-003 states "we have zero inline scripts/styles", but the repo contains inline scripts in 8 templates (including this feature). Verified: the live header is `…; script-src 'self'; …` with no nonce/hash, and the page contains `<script>`.

**Impact.** In a CSP-honouring browser the Settings create/delete modals (and their client-side guards) do not run; the tests only assert markup, so this is invisible to the suite. Security-relevant because it means (a) the client-side confirmation UX is inert, and (b) the same CSP is the *only* thing preventing F3 — relaxing it (a likely "fix" for the broken modals) would activate F3.

**Remediation.** Move the modal JS into external `ui/static/ui/` assets (CSP-compliant, and aligns with ADR-003); then F3's remediation (DOM APIs / `json_script`) removes the CSP dependency entirely.

---

### F11 — Info — `GET /api/v1/user/{idOrLogin}` is an intentional cross-user read

**Where** `identity/views.py:441-457`. Any authenticated caller can fetch any user's `user_json` (login, name, org) by id/login; this preserves TheHive assignee resolution and is documented in the plan. It is an existence/enumeration oracle by design; note it as an accepted residual and ensure it stays read-only (PATCH/DELETE are superAdmin-gated).

---

## Explicit XSS assessment (as requested)

- **Are the values escaped?** In the plain HTML tables, yes — Django auto-escaping (`{{ org.name }}`) is correct.
- **In the JS**, the values are passed through the **wrong escaper**: `{{ org.name|escapejs }}`. `escapejs` only protects a JS *string literal*; it does not neutralise HTML metacharacters once the JS engine decodes the escapes.
- **Are they inserted via `innerHTML`?** Yes — the decoded value is concatenated into an HTML string (`'<option …>' + opt.name + '</option>'`) and assigned to `body.innerHTML` in both Settings templates. That is an HTML-parsing sink, so `</option><img …>`/`<script>`/`<svg onload>` payloads are injected as markup.
- **Net:** a genuine **stored XSS / HTML-injection** flaw, reachable by any authenticated tenant user (they can rename their own org) and triggered in a superAdmin's browser. **It is currently neutralised by the app's CSP** (`script-src 'self'`, no `unsafe-inline`), which blocks inline event handlers and the inline script itself. The correct fix is output encoding for the sink (or DOM APIs), not reliance on CSP.

**Do client-side guards get server-side enforcement?** **Yes — but the client guards are currently inert.**
- Org-delete confirm-disabled-on-blockers, and the reassign/delete member choice, are re-validated server-side in `identity/services.py::delete_organisation` and mirrored in `ui/views.py:1519-1523`; the API returns 409/400 and the UI redirects with an error. Verified by the passing tests plus the temp repros.
- User self-delete/last-admin and self-role/self-deactivate guards are enforced server-side in `identity/views.py:494-537, 571-590` and `identity/services.py:218-303`. The UI modal only disables for self-delete; the last-admin case is server-enforced.
- However, because the modal JS is inline and the CSP blocks it (F10), these client-side guards do not execute in a compliant browser — server-side enforcement is doing all the real work, which is the correct design but hides the broken client UX.

---

## Positive findings / controls that held

- **Uniform 404 status** on the management routes for non-superAdmin is enforced by `_require_superadmin` (`identity/views.py:94-100`) and `_require_superadmin_ui` (`ui/views.py:1429-1433`), with the guard checked **before** object resolution on the UI edit/delete views (no id-existence oracle there). The one body-level exception is F6.
- **No directory for non-superAdmin**: `GET /organisation` returns only the caller's own org; the superAdmin directory is role-gated (`identity/views.py:148-172`).
- **Server-side re-validation** of org deletion: pre-flight protects against `ProtectedError` with a 409 carrying **counts only** (no cross-tenant names/ids); reassign with a missing/same target is refused by both the service (`identity/services.py:84-91`) and the UI (`ui/views.py:1519-1523`); the D4 atomic-rollback-on-race tests pass.
- **Password validators** run via `validate_password` on create and reset, in both API (`identity/views.py:428,544`) and UI (`ui/views.py:1599,1702`); `set_password` is used; passwords are never logged or placed in audit rows.
- **Role/`login` integrity**: role is validated against `USER_ROLE_VALUES`; `login` is derived from `username` on save and cannot be supplied by the client.
- **CSRF**: every UI POST form and modal includes `{% csrf_token %}`; `CsrfViewMiddleware` is enabled and no settings view is `csrf_exempt`. API session requests are CSRF-protected by DRF `SessionAuthentication`; read-only API keys still fail closed on mutating verbs (`compat.auth.ScopePermission`).
- **Secrets**: `scripts/secret-scan.sh --tracked` is clean; no credentials/keys/.env introduced. No PII/passwords in logs or audit rows.
- **Query-API / WS / share ACL** tenant scoping from the prior verification remains intact (not modified in a way that weakens them).

## Residuals accepted (non-blocking, pre-existing)

- F11 (`GET /user/{id}` any-user read) — documented, required for assignee resolution.
- `AuditLog.ip` reads `REMOTE_ADDR` only (does not trust `X-Forwarded-For`) — safe as written.
- Org directory aggregates use four `Count(..., distinct=True)` joins; correct but O(joins) (plan R3) — performance, not security.

## Recommended remediation order

1. **F1** then **F2** (same code path: guard the member-delete set and move/stabilise the audit write inside the transaction).
2. **F3** (fix the sink with DOM/`json_script`; do not "fix" it by relaxing CSP) and **F10** (externalise the modal JS).
3. **F4** (route UI mutations through audited services) and **F5** (lock the last-superAdmin invariant).
4. **F6/F7/F8/F9** hardening and tests; add a UI conformance test that asserts one `AuditLog` row per UI mutation and that `members=delete` cannot remove the actor/last admin.

*End of report.*
