# PLAN-2026-10-22 — Case Template defaults and source-alert payload

## Summary

Case templates are useful only if a new template ships a runbook, not an empty task list. This
change gives the **UI create form** a default `Triage/Analysis` task and an `Example Field` custom
field, and teaches template application to resolve `{{ alert.raw_payload }}` in a task description
from the earliest alert linked to the case. The task copy is capped at 64 KiB; a task-detail modal
lazy-loads the complete payload for users who may read the source alert.

The API stays explicit: `POST /api/v1/case/template` does not inject defaults. Defaults are a UI
authoring aid, not a wire contract.

## Goals

- UI-created templates start with a useful default task and custom-field example.
- Applying a template replaces the payload token with the earliest linked alert's pretty JSON.
- The task excerpt is bounded and visibly marked when truncated.
- The full payload remains reachable from the task page without embedding it in every request.
- Tenant rules are unchanged: a sharee may read the case/task, but alerts have no shares.

## Non-goals

- No schema or migration changes.
- No automatic template application on alert escalation.
- No changes to API-created template semantics (explicit defaults remain explicit).
- No new custom-field creation from a template; the example field is copied only when a global
  `CustomField` with that name already exists.
- No background job, playbook, or automation changes.

## Decisions

1. **Defaults only on apply.** Creating a template stores what the author submitted. The UI create
   form pre-fills defaults; if the submission has an empty `tasks`/`custom_fields` field, the UI
   substitutes the defaults so a no-edit submission still seeds a runbook. API creation never does.
2. **Initial alert = earliest linked alert**, ordered by `date`, `created_at`, `id`, and filtered to
   the case owner's organisation.
3. **64 KiB JSON cap.** The cap applies to the pretty-printed JSON inside the fenced block, measured
   in UTF-8 bytes without splitting a character. The visible truncation marker is outside the fence.
4. **Modal for the complete payload.** The task page links to a lazy-loaded fragment; the fragment
   is tenant-checked against `resolve_alert` and is never cached.
5. **Custom fields are best-effort.** `DEFAULT_TEMPLATE_CUSTOM_FIELDS` contains `Example Field` /
   `example`; application skips it unless a global field with that name exists.

## Architecture

- `cases/template_defaults.py` — constants and pure helpers:
  `SOURCE_ALERT_PAYLOAD_TOKEN`, `SOURCE_ALERT_PAYLOAD_SENTINEL`,
  `SOURCE_ALERT_PAYLOAD_TRUNCATION_MARKER`, `MAX_SOURCE_ALERT_PAYLOAD_BYTES`,
  `MAX_FULL_PAYLOAD_BYTES`, `DEFAULT_TEMPLATE_TASKS`, `DEFAULT_TEMPLATE_CUSTOM_FIELDS`,
  `earliest_alert_for_case`, `has_source_payload_sentinel`, `render_source_alert_payload`,
  `format_full_payload`.
- `cases/views.py::_apply_template` — resolves the token on each copied task description. Both the
  API bulk apply and the UI apply call this one service.
- `ui/views.py` — create/edit defaults, `task_detail` context, `task_source_payload` fragment.
- `ui/urls.py` — `ui-task-source-payload`.
- `ui/templates/ui/case_template_form.html` — `form_tasks` / `form_custom_fields` textareas + help.
- `ui/templates/ui/task_detail.html`, `_task_source_payload.html`,
  `_task_source_payload_loading.html` — trigger, lazy modal body, no-JS alert-page fallback.

### Rendering contract

````
<blank line>
```json
{ ... pretty payload, capped ... }
```
<blank line>
````

- The fence is dynamically longer than any backtick run in the payload, so payload backticks do not
  break the block.
- The truncation marker is outside the fence and only appears when the 64 KiB cap was hit.
- The invisible sentinel is appended exactly once at the end, on its own line. It is a Markdown
  link-reference definition: `[//]: # (amalthea:source-alert-payload)`.
- No alert linked: readable fallback text, no sentinel, no marker.
- No token: byte-for-byte unchanged.

### Full-payload display

`format_full_payload` serialises compact JSON first. If compact exceeds 8 MiB, it returns a
"too large to display" message. Otherwise it pretty-prints; if pretty exceeds the cap, it returns
the compact JSON (complete, less readable). The 8 MiB bound prevents a 5 MiB webhook payload from
expanding into a much larger pretty-printed response; normal payloads remain complete.

## Data model

No migration. `CaseTemplate.tasks` remains a JSONB list; a task item may contain `description` with
the literal token. `Task.description` stores the rendered Markdown plus the sentinel. No new
columns or indexes.

## API

- `POST /api/v1/case/template` — unchanged, explicit `tasks`/`customFields`; no injected defaults.
- `POST /api/v1/case/_bulk/caseTemplate` — applies a template through `_apply_template`, so the
  token is resolved from each case's earliest same-org alert.
- `GET /api/v1/case/template/<idOrName>` — unchanged.
- New UI-only route: `GET /tasks/<uuid:task_id>/source-payload` (`ui-task-source-payload`).

The case-template REST route is `case/template`; the older `caseTemplate` spelling is only the
apply route and remains unchanged.

## UI / UX

- Create form pre-fills the default task and example custom field. The tasks help text documents the
  token using `&#123;&#123; alert.raw_payload &#125;&#125;` so Django does not evaluate it.
- Task detail shows **View full alert payload** only when the sentinel is present, an alert exists,
  and the caller may read that alert. The control is an anchor: with HTMX it loads the modal body;
  without it, it navigates to the alert page.
- The modal is a normal `<dialog>` with Esc/backdrop close; the fragment has a loading/fallback body.
- Dark theme and semantic controls only; no inline script or native dialogs.

## Security

- **Tenant isolation:** `earliest_alert_for_case` filters `owner_org_id=case.owner_org_id`.
  `task_source_payload` calls `resolve_alert`; a case sharee gets 404. `task_detail` hides the
  trigger for sharees.
- **XSS:** payloads go through the task description's Markdown sanitizer (`nh3`); the modal renders
  JSON in an autoescaped `<pre>`. No `mark_safe` on raw payload.
- **DoS:** task excerpt is 64 KiB; modal is lazy-loaded, compact-fallback capped at 8 MiB, and
  `never_cache` prevents shared-cache retention. The raw alert page remains the unbounded source of
  truth (existing behavior).
- **Spoofing:** sentinel detection requires an exact standalone line, so an inline quote cannot make
  the trigger appear.

## Testing

`tests/conformance/test_case_template_defaults.py` (31 tests) covers:
- token at end/trailing prose/two tokens/existing sentinel;
- sentinel invisibility in Markdown and own-line detection;
- no-token/no-alert/empty-payload cases;
- 64 KiB cap, closed fence, multibyte boundary, tail reachable in the full fragment;
- earliest-alert tie-break and same-org filter;
- UI create prefill and empty-submission defaults;
- API create does not inject defaults;
- UI apply renders and writes exactly one `template-applied` ledger entry;
- non-UUID template id returns 404;
- trigger count, hide-without-sentinel, edit-removes-trigger;
- sharee 404/no trigger, owner/superadmin access, anonymous redirect, POST 405;
- `never_cache`; script payload escaped in Markdown;
- full-payload compact fallback, oversize message, and pre-allocation 2x cap heuristic;
- superAdmin cross-org task read/fragment audit rows (and no rows for same-org reads).

`tests/conformance/test_tenant_scoping.py` includes the new route in its task param map.

## Risks / accepted tradeoffs

- The 8 MiB modal cap can refuse an extreme API-created payload; the alert page still exposes it.
- UI create cannot express "zero tasks" when both fields are empty (defaults are restored); edit can
  clear them. This asymmetry is deliberate for the authoring aid and documented here.
- The example custom field is only useful when a global field named `Example Field` exists; this is
  best-effort by design.
- The sentinel is stored in user-editable Markdown; own-line detection limits but cannot eliminate
  intentional spoofing.

## Open questions / follow-ups

None blocking. Future work could make defaults configurable per organisation. Security follow-ups:
M1 (audit superAdmin cross-org UI task reads) and M2 (pretty-print allocation before the 8 MiB check)
are remediated with regression tests; L1 (rate-limit the lazy-load fragment) is accepted for MVP as
deployment/edge-control work. See
[`AUDIT-2026-10-22-case-template-defaults.md`](../security/AUDIT-2026-10-22-case-template-defaults.md).

## Acceptance criteria

- **AC1 — Authoring defaults:** `GET /case-templates/new` pre-fills a `Triage/Analysis` task and
  `Example Field`; submitting both fields empty keeps those defaults. API `POST
  /api/v1/case/template` with `tasks: []` stores no defaults.
- **AC2 — Token rendering:** Applying a template replaces every `{{ alert.raw_payload }}` with the
  earliest same-org linked alert (date, created_at, id). A missing alert gives a readable fallback
  with no sentinel; a description without the token is byte-identical.
- **AC3 — Bounded excerpt:** The JSON block is capped at 64 KiB UTF-8 bytes without splitting a
  character, the fence is always closed, the truncation marker appears only when capped, and the
  sentinel appears once on its own line, is invisible in Markdown, and is not fooled by an inline
  quote.
- **AC4 — Full payload access:** Task detail shows exactly one trigger only when the sentinel,
  source alert, and `resolve_alert` permission are all present. The fragment returns the complete
  payload, is `never_cache`, returns 404 to a case sharee, and works for the owner and superAdmin.
- **AC5 — Safety edges:** A non-UUID template id is 404, a payload containing `<script>` is escaped
  in Markdown, anonymous fragment access redirects, and an authenticated POST gets 405.
- **AC6 — Quality gates:** No migration is generated; `ruff`, `mypy`, Django checks, CSS drift, and
  the full test suite pass.

## Implementation record

- Implemented `cases/template_defaults.py`, `_apply_template` token resolution, UI defaults,
  lazy modal route/templates, and the 31-test suite.
- Post-audit hardening: UI task detail/fragment now audit superAdmin cross-org reads; the
  full-payload formatter skips pretty-printing when a 2x estimate exceeds the cap.
- Deviations from the original sketch: sentinel is a Markdown link-reference (HTML comments rendered
  visibly); the fence length is dynamic; the full payload is returned as raw JSON rather than a
  fenced block; the modal has an 8 MiB compact/pretty safety cap; the sharee and same-org guards were
  added after QA/security review; the fragment is `never_cache`.
- No migration generated.
- `make check` passes (ruff, format, mypy, Django check, migrations, CSS drift, playbook scripts).
- Full suite: **3099 passed, 3 skipped** (after post-audit M1/M2 hardening).
- Independent verification: [`VERIFY-2026-10-22-case-template-defaults.md`](./VERIFY-2026-10-22-case-template-defaults.md)
  — all six ACs met.
- Security review: [`AUDIT-2026-10-22-case-template-defaults.md`](../security/AUDIT-2026-10-22-case-template-defaults.md)
  — no blocking issues; Medium/Low hardening follow-ups recorded above.
