# Implementation Brief — Section 6 Frontend: Scoped HTMX + Live UI

**Plan:** [`PLAN-2026-11-26-section6-realtime-and-search.md`](./PLAN-2026-11-26-section6-realtime-and-search.md)
**Status:** Approved — handoff to `django-frontend`
**Scope:** §6.16 P7 UI work only
**Approved defaults:** OQ-1 through OQ-6 in the plan are accepted.

## Ownership

### You own
- `ui/views/**` for partial response branches only
- `ui/templates/ui/alerts/**`, `ui/templates/ui/cases/**`, `ui/templates/ui/observables/**`, `ui/templates/ui/dashboard.html`, `ui/templates/ui/case_detail.html`, and new partial templates under those areas
- `ui/templates/ui/base.html` only for websocket config/meta and no-inline-asset compliance
- `ui/static/ui/live.js` (new or refactored from existing live JS)
- frontend tests: `tests/conformance/test_ui_htmx_scope.py` and narrowly scoped extensions to existing UI tests that are not owned by backend

### Do not touch
- `realtime/**`, `query/**`, `core/**`, `alerts/models.py`, `cases/models.py`, migrations
- `TODO.md`, `COMPLETED.md`
- `.env`, CI config, secret-scan scripts, git hooks
- git history; do not commit or push

## Constraints

- Do **not** run full pytest unless the user explicitly asks.
- Do **not** commit or push.
- No inline `<script>`, `style=`, or `on*=` attributes; ADR-003 still applies.
- All primary actions remain keyboard reachable and labelled. Icons are `aria-hidden="true"`; icon-only controls need accessible names.
- HTMX is progressive enhancement: ordinary GET/POST must keep working.
- Any deviation must be recorded in `docs/spec/deviations.md`.

## Shared realtime contract from backend

Org socket: `/ws/org/<org_id>/`

```json
{
  "type": "event",
  "event_type": "refresh",
  "payload": {
    "surface": "alerts | cases | dashboard",
    "reason": "alert-created | alert-updated | case-created | case-updated | case-closed",
    "object_id": "<uuid-or-null>",
    "occurred_at": "<iso8601>"
  }
}
```

Case socket already exists at `/ws/case/<case_id>/` and gains:

```json
{"type": "presence", "count": 2}
```

Client may send `{"type":"presence_ping"}` to refresh presence TTL.

## Tasks and acceptance criteria

### F1 — Live status and org socket wiring
Add static JS to connect the org socket on alert list, case list, and dashboard.

AC-F1.1: JS reads org id and case id from server-rendered data attributes/meta, not from globals containing secrets.
AC-F1.2: Live chip states are `Live`, `Reconnecting…`, and `Offline`, with `aria-live="polite"`.
AC-F1.3: Reconnect uses bounded backoff; no reconnect storm.
AC-F1.4: No refresh while `document.hidden`; one refresh on visibility return.
AC-F1.5: All JS is static and passes `test_ui_inline_assets.py`.

### F2 — Refresh targets and HTMX partials
Reuse existing URLs with `HX-Request: true` and `partial=...`.

| Surface | Target | URL query |
|---|---|---|
| Alert list rows | `#alert-list-rows` | `partial=rows` plus current filters |
| Case list rows | `#case-list-rows` | `partial=rows` plus current filters |
| Observable list rows | `#observable-list-rows` | `partial=rows` plus current filters |
| Dashboard stats | `#dashboard-stats` | `partial=stats` |
| Dashboard recent alerts | `#dashboard-recent-alerts` | `partial=recent-alerts` |
| Dashboard recent cases | `#dashboard-recent-cases` | `partial=recent-cases` |

AC-F2.1: On org refresh, debounce 250 ms then issue `htmx.ajax('GET', url, {target, swap:'innerHTML'})`.
AC-F2.2: Refresh URL preserves current `q`, filters, `page`, and `per_page`.
AC-F2.3: Client never constructs result rows from event payloads.
AC-F2.4: Partial responses render only the target fragment for HTMX requests.
AC-F2.5: Without `HX-Request`, the full page renders exactly as before.

### F3 — List filter HTMX
Convert filters for alert list, case list, and observable list.

AC-F3.1: Alert list `scope`, `severity`, `status`, `q`, `page`, `per_page` update rows only.
AC-F3.2: Case list `status`, `severity`, `q`, `page`, `per_page` update rows only.
AC-F3.3: Observable list `q`, `type`, `page`, `per_page` update rows only.
AC-F3.4: `hx-push-url` keeps the browser URL in sync.
AC-F3.5: Focus remains usable after filter updates; keyboard users are not trapped.
AC-F3.6: Plain GET renders the full page and is tested per list.
AC-F3.7: Viewer and cross-org access produce the same partial/no-access semantics as full pages.

### F4 — Case presence UI
AC-F4.1: Case detail header contains `#case-presence` with `aria-live="polite"`.
AC-F4.2: Text is `1 analyst viewing` or `N analysts viewing`; icon supplements the label.
AC-F4.3: Presence is never clickable and never displays identities.
AC-F4.4: Offline state shows `Live updates unavailable`, not a stale count.
AC-F4.5: Duplicate tabs for the same user do not visually double-count because backend sends unique counts.

### F5 — Case-detail mutation HTMX
Convert at least one mutation in each group:

- General: status, severity, assignee quick actions
- Tasks: create task and toggle task
- Observables: add observable and remove observable link
- Comments: keep existing HTMX form and add refresh-trigger integration

AC-F5.1: Each converted form uses `hx-post` with the correct Django CSRF token.
AC-F5.2: Each response returns a small partial or `204` with `HX-Trigger`.
AC-F5.3: Plain POST fallback redirect/render behaviour is unchanged.
AC-F5.4: Viewer submissions are rejected with the same status as before.
AC-F5.5: No inline JS/CSS is introduced.
AC-F5.6: Labels and visible text remain present; icons never replace primary action labels.

### F6 — Frontend tests and handoff report
AC-F6.1: Add `tests/conformance/test_ui_htmx_scope.py` covering partials, no-JS fallback, CSRF, viewer, same-org, and cross-org matrices.
AC-F6.2: Keep `tests/conformance/test_ui_search.py` green; do not re-add the header search form.
AC-F6.3: `make check` passes.
AC-F6.4: Targeted UI conformance tests pass.
AC-F6.5: No full pytest run.
AC-F6.6: Return a final report listing files changed, commands run, test results, and any deviations.

## Suggested verification commands

```bash
make check
.venv/bin/pytest -q \
  tests/conformance/test_ui_htmx_scope.py \
  tests/conformance/test_ui_search.py \
  tests/conformance/test_ui_inline_assets.py \
  tests/conformance/test_ui_urls_smoke.py \
  tests/conformance/test_t2_p2_surface.py
```
