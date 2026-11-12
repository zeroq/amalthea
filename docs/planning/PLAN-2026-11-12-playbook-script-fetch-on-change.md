# PLAN — Load playbook scripts on Action type = PYTHON (on-change fetch)

**Date:** 2026-11-12
**Status:** Implemented (2026-11-12) — recommended answers adopted; see §8 and §10
**Supersedes (in part):** `PLAN-2026-11-12-playbook-script-picker-refresh.md` (removes the
`refresh=True` on every form GET in favour of an on-change fetch)
**Area:** Automation · Playbook authoring UI
**Related:** `ui/views/automation.py`, `ui/urls.py`, `ui/templates/ui/playbook_form.html`,
`ui/static/ui/playbook_form.js`, `automation/scripts_loader.py`

---

## 1. Summary

Today the Script dropdown is server-rendered on form GET, and the previous change made that GET
re-import every script (`discover_scripts(refresh=True)`). That re-imports on **every** form load,
including HTTP-action playbooks that never use a script.

Switch to a **fetch on demand**: when Action type becomes `PYTHON` (including on load for an
existing python playbook), the page calls a small same-origin JSON endpoint that runs
`discover_scripts(refresh=True)` and repopulates the Script `<select>`. HTTP-only forms never
re-import.

CSP already permits same-origin fetch (`connect-src 'self'`), and the JS is already an external
file (`ui/static/ui/playbook_form.js`, no inline script).

## 2. Goals

- G1: Selecting Action type = PYTHON fetches the current script list from disk and fills the
  dropdown (no page reload).
- G2: Editing a playbook whose action is already `python` refreshes the dropdown on page load.
- G3: HTTP-action forms do **not** trigger script discovery at all.
- G4: No-JS fallback still works (server renders the cached list; deploy is rare).
- G5: Keep the deploy-only model and server-side POST validation unchanged.

## 3. Non-goals

- No new cache/TTL architecture; `discover_scripts(refresh=True)` is the freshness mechanism.
- No upload control.
- No change to `playbook_detail` or `GET /api/v1/playbook/_meta` (they stay cache-only).
- No innerHTML — options built with DOM APIs.

## 4. Proposed changes

### T1 — JSON endpoint (`ui/views/automation.py`, `ui/urls.py`)
New view `playbook_scripts` — `GET`, `@login_required`, admin/superAdmin only:

```python
@login_required
@require_GET
def playbook_scripts(request: HttpRequest) -> JsonResponse:
    if request.user.role not in ("admin", "superAdmin"):
        return JsonResponse({"detail": "Not found"}, status=404)
    scripts = discover_scripts(refresh=True)
    data = [
        {"name": i.name, "description": i.description, "triggerHint": i.trigger_hint}
        for i in sorted(scripts.values(), key=lambda i: i.name)
        if i.load_error is None
    ]
    return JsonResponse({"scripts": data})
```

Route `automation/playbooks/scripts` → `ui-playbook-scripts`, registered **before**
`automation/playbooks/<str:playbook_id>` so the literal path wins.

**AC**
- AC1: `GET` returns `{"scripts": [...]}` for admin/superAdmin, re-scanned per request.
- AC2: analyst/viewer get a 404 (uniform with other authoring endpoints).
- AC3: Response is loadable-only, name-sorted.

### T2 — Revert per-GET refresh (`ui/views/automation.py`)
`playbook_create` / `playbook_edit` GET go back to `discover_scripts()` (the cheap cached baseline
used for the no-JS render). Remove the "refresh on every form load" comments.

**AC**
- AC4: A form GET does not re-import scripts; the list is the cached baseline.

### T3 — Template hooks (`ui/templates/ui/playbook_form.html`)
- Put the endpoint URL on the container:
  `<div id="python-fields" class="hidden" data-scripts-url="{% url 'ui-playbook-scripts' %}">`
- Give the description hint and empty message stable ids:
  `<p class="hint" id="script-description">…</p>`, `<div class="empty" id="script-empty">…</div>`
  (keep the existing conditional select/hidden-input structure for the no-JS baseline).

**AC**
- AC5: `#python-fields` carries `data-scripts-url`; `#script-description` / `#script-empty` exist
  when rendered.

### T4 — JS (`ui/static/ui/playbook_form.js`)
- `loadScripts()`: `fetch(url, {credentials: "same-origin"})`, parse JSON, rebuild
  `#id_action_path` options with `document.createElement("option")` (no innerHTML), preserving the
  current value when still present; update `#script-description`; toggle `#script-empty`.
- If the current control is the empty-state `<input type="hidden" name="action_path">`, replace it
  with a `<select id="id_action_path" name="action_path">` when scripts arrive.
- `toggleActionFields()` calls `loadScripts()` when action === `"python"` (so it also fires on load
  for an edit form whose action is already python).
- Guard against overlapping requests (a request sequence number; last response wins).
- On network error: keep the existing options, show a short inline error (do not wipe the select).

**AC**
- AC6: Changing Action type to PYTHON populates the select from the endpoint.
- AC7: HTTP action triggers no request.
- AC8: Edit form with `action=python` fetches on load; configured `action_path` stays selected if
  still present.
- AC9: Empty response shows the empty-state message; error keeps prior options.

### T5 — Tests
- Replace the two GET-refresh tests added previously with endpoint tests:
  - warm cache, add a script, GET `ui-playbook-scripts` → new name present (admin).
  - analyst/viewer → 404.
- Template assertion: `#python-fields` has `data-scripts-url` and the JS script tag is present.
- Keep/adjust `test_form_shows_empty_state_when_no_scripts` for any markup changes.

**AC**
- AC10: Endpoint tests pass; existing playbook script/loader suites stay green.

## 5. Testing / verification

- `pytest tests/conformance/test_playbook_script_ui.py tests/conformance/test_playbook_scripts_loader.py -q`
- `pytest tests/conformance/test_playbook_authoring.py tests/conformance/test_playbook_script_ui.py -q`
- `make check`
- Manual: create form → pick PYTHON → dropdown fills; deploy a new `.py` → toggle away/back → it
  appears; HTTP action → no network request in devtools.

## 6. Risks

- No-JS users get the cached list (pre-existing behaviour; deploy is rare).
- Dynamic `<select>` rebuild can lose the selection if the script vanished from disk; we preserve
  only when present (intended).
- If the endpoint is hit while a script is mid-write, an import error is logged and that script is
  omitted until the next fetch.

## 8. Decisions (resolved)

1. **Replace vs. add:** **replace** — `playbook_create` / `playbook_edit` GETs are back to
   `discover_scripts()`; freshness comes from the on-change fetch.
2. **Edit load:** **yes** — `toggleActionFields()` runs on `DOMContentLoaded`, so an edit form with
   `action=python` fetches immediately.
3. **Endpoint auth/status:** **admin/superAdmin only**, **404** for everyone else (uniform with the
   authoring pages).
4. **Error/empty UX:** empty response → show the empty-state block; network error → keep the
   existing options and show an inline hint.
5. **No-JS baseline:** **yes** — keep the server-rendered cached list.

## 9. Implementation notes

- Endpoint: `playbook_scripts` in `ui/views/automation.py`, exported from `ui/views/__init__.py`,
  routed at `automation/playbooks/scripts` (`ui-playbook-scripts`) **before** the dynamic
  `<str:playbook_id>` route.
- Payload: `{"scripts": [{"name", "description", "trigger_hint"}]}`, loadable-only, name-sorted.
- Template: `#python-fields` carries `data-scripts-url`; `#script-description`,
  `#script-load-error`, and `#script-empty` are the JS hooks. The select is always rendered
  (hidden when the baseline is empty) so JS can populate it after an empty server render.
- JS: `loadScripts()` fetches when action becomes `python` (and on load); options are built with
  `document.createElement`, the previous selection is preserved, and a request sequence number
  makes the last response win.
- Tests: endpoint refresh/empty-check/authz + template hooks. The previous GET-refresh tests were
  replaced accordingly.

## 10. Deviations / follow-ups

- The empty-state markup changed from a hidden `action_path` input to a hidden `<select>` with the
  placeholder option; a no-JS empty submit still sends `action_path=""` and is rejected the same
  way. One assertion in `test_form_shows_empty_state_when_no_scripts` was updated for this.
- **Local artifact:** an untracked `automation/scripts/test_playbook_2.py` (a manual copy reusing
  `NAME = "test_playbook"`) currently makes `make check`'s script scan fail with a duplicate
  identity error. It is not part of this change; delete it or give it a distinct `NAME`.
- JS syntax was reviewed but not machine-checked (`node` is not installed in this environment).
