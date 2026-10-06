# Implementation Brief — Phase 7: Realtime Ledger

Plan: `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` §Phase 7 (tasks 1–3, AC7.1–AC7.4).
TODO: `TODO.md` 5.4 (NEXT). Record deviations in `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` §12 when done.

## Current state (verified)

- `realtime/` scaffold exists: `consumers.py` (echo-only, no auth check, no sync), `publisher.py`
  (`publish_case_event(case_id, event_type, payload)` — the choke point, but **zero call sites**),
  `routing.py` (`ws/case/{case_id}/`), app registered; `amalthea/asgi.py` already wraps with
  `AuthMiddlewareStack`.
- The ledger is `cases.models.TimelineEvent` (uuid, case FK, date, title, description, kind, actor,
  metadata). Created at exactly these sites:
  1. `cases/views.py` `case_create` (~line 115): `kind="case-created"`
  2. `cases/views.py` `_update_case` (~line 158): `kind="status-changed"` (only when status changes)
  3. `automation/dispatcher.py` `record_result` (~line 160): `kind="automation-run"`
  4. `ui/views.py` `case_set_status`: `kind="status-change"`
  5. `ui/views.py` `case_comment`: `kind="comment"`
- Serializer: `core/serializers.timeline_event_json(event)` → `{_id, id, _type, date, endDate,
  title, description, kind, actor, metadata}`.
- UI: `ui/templates/ui/case_detail.html` renders the timeline as `<ol class="timeline">` of `<li>`s;
  comment form is a plain POST → redirect (`ui-case-comment`). No WS client, no HTMX on the form.
- Channels/InMemory in test+dev settings; `daphne` in requirements. Channels `WebsocketCommunicator`
  available for tests.
- `ui/views.py` uses `@login_required`, `_resolve_case(case_id)` resolves UUID **or** number.

## Design decisions (fixed)

1. **Single choke point = `cases/ledger.py` service.** New module `cases/ledger.py` exposing
   `append_timeline_event(case, *, title, description="", kind, actor=None, metadata=None) ->
   TimelineEvent` which creates the row **and** `transaction.on_commit(lambda:
   publish_case_event(str(case.id), kind, {"event": timeline_event_json(event)}))`. All five
   creation sites above switch to it. A scan test guards the choke point (below). No signals — this
   repo's culture is explicit calls, and a guard can enforce them.
2. **Room key = case UUID**: group `case_{str(case.id)}`. The WS URL embeds the **UUID**
   (`case.id`), not the number, so publisher and consumer agree without resolution.
3. **Wire protocol (JSON):**
   - Client → server: `{"type": "sync", "after": "<event-id-or-null>"}`. On connect the client
     sends `after` = the last event id it has rendered (from the initial page or prior receives).
   - Server → client (sync reply): `{"type": "timeline", "events": [timeline_event_json...]}`
     ordered by `("date", "id")` strictly **after** `after` (all events if `after` is null).
   - Server → client (live): `{"type": "event", "event_type": ..., "payload": {...}}` — forward the
     publisher message unchanged (group message type `event` already routes here).
4. **Auth refusal = 4401.** Consumer checks `self.scope["user"].is_authenticated` **before**
   `accept()`; if unauthenticated → `await self.close(code=4401)` and return. Disconnect always
   group_discards.
5. **AC7.4 no-duplicates** is a server contract: sync returns only events with `id > after`
   (compare by `("date", "id")` keyset, i.e. filter `(date, id) > (after.date, after.id)`). The
   client dedupes by injecting only entries with a `data-event-id` it has not rendered.
6. **HTMX fallback**: the comment form keeps its plain POST action; add `hx-post` (same URL),
   `hx-target="#timeline-events"`, `hx-swap="beforeend"`, `hx-disabled-elt`. When the request
   carries `HX-Request`, `ui.views.case_comment` returns the **rendered single `<li>`** (new partial)
   instead of a redirect. Non-JS clients fall back to the redirect naturally.
7. **New partial** `ui/templates/ui/_timeline_entry.html`: exactly the current `<li>` markup from
   `case_detail.html`, plus `data-event-id="{{ event.id }}"` on the `<li>`. The existing timeline
   loop in `case_detail.html` switches to `{% include %}` so rendered and live entries share one
   template (no duplication drift).
8. **Assignment event** (plan names it): in `cases/views.py` `_update_case`, when `assignee`
   changes, also `append_timeline_event(... kind="assigned", metadata={"assignee": login})` with
   title `Assigned to <login>` (or `Unassigned`). Small, matches the plan.

## Tasks (acceptance criteria per task)

### T1 — `cases/ledger.py` service + call-site migration
- Create `cases/ledger.py` with `append_timeline_event` (per design 1) and a keyset query helper
  `events_after(case_id, after_event_id) -> QuerySet` used by the consumer.
- Switch all 5 sites (list above) to the helper; add the `assigned` event in `_update_case`.
- **AC:** `TimelineEvent.objects.create(` appears in app code **only** inside `cases/ledger.py`
  (grep `cases/ ui/ automation/` — excluding `__pycache__`); every switched site keeps its exact
  existing title/kind/metadata semantics (diff-check nothing else changed).

### T2 — Consumer hardening (`realtime/consumers.py`)
- 4401 on unauthenticated handshake (design 4); `group_add` only after auth.
- `receive`: handle `sync` (`after` uuid or null); send `{"type":"timeline","events":[...]}`;
  ignore/close on unknown message shapes (4000).
- Keep the `event` handler forwarding the publisher payload as-is.
- **AC:** unauthenticated `WebsocketCommunicator` open→`receive_output` yields a close with code
  4401; authenticated connect succeeds; `sync` with `after=X` returns exactly the events after X
  ordered `(date, id)`; unknown client message types are refused, not echoed.

### T3 — Publisher wiring + automation/API/UI emission
- `cases/ledger.py` calls `publish_case_event` on commit (design 1). Verify `record_result` in
  `automation/dispatcher.py` runs outside a wrapping transaction so `on_commit` actually fires in
  the worker (autocommit is fine).
- **AC:** a POST to `ui-case-comment` with `HX-Request` returns one `<li>` (partial, with
  `data-event-id`) and its TimelineEvent is published to group `case_{uuid}` (assert via
  `django_capture_on_commit_callbacks(execute=True)` + a `WebsocketCommunicator` receiving the
  `event` message). Same for status change and automation `record_result`.

### T4 — Frontend: partial + HTMX + live client
- Extract `_timeline_entry.html` (design 7); `case_detail.html` `<ol>` gets `id="timeline-events"`
  and uses the include; comment form gets the HTMX attributes (design 6).
- New `ui/static/ui/live.js`: connect to `ws(s)://<host>/ws/case/<case.id>/` (path from a
  `data-ws-url` attribute on the `<ol>` or a small inline config); on open send
  `{"type":"sync","after":<last rendered event id>}`; render received `events` and `event` messages
  by appending the entry with `data-event-id` (dedupe by id); exponential-backoff reconnect
  (1s→2s→4s→… cap 15s) re-sends sync on reopen. No inline event handlers; defer; dark-theme CSS
  already styles `.tl-*`.
- Also make `ui/views.py` `case_comment` HX-aware (design 6).
- **AC:** in the Django test client, GET `/ui/cases/{uuid}/` (as `analyst`) includes
  `ui/static/ui/live.js` and a `data-ws-url` pointing at `ws/case/{uuid}/`; the comment form
  carries `hx-post`+`hx-target="#timeline-events"`+`hx-swap="beforeend"`.

### T5 — Conformance tests `tests/conformance/test_realtime.py`
Use `channels.testing.WebsocketCommunicator` over `amalthea.asgi.application` (AuthMiddlewareStack
included) or the consumer's `as_asgi()` with a session-authenticated scope for the auth-positive
cases; InMemoryChannelLayer in test settings.
- **AC7.2 (auth):** anonymous communicator → close 4401, no group join (no event received after a
  publish).
- **AC7.3 (isolation):** two authenticated communicators, cases A and B; publish to A → B receives
  nothing within a bounded `receive_nothing`-style wait; A receives it.
- **AC7.4 (sync/reconnect):** seed 3 events; connect, `sync` with `after` = 2nd id → exactly the
  3rd; reconnect with `after` = 3rd id → empty list; no duplicates when the same event arrives via
  sync then live (dedupe is client-side; assert the server never re-sends an already-synced id).
- **AC7.1 (publish chain):** `django_capture_on_commit_callbacks(execute=True)`; POST a comment via
  the UI client; both of two connected communicators on that case receive the `event` message with
  the comment payload.
- **Choke-point scan:** `TimelineEvent.objects.create` only in `cases/ledger.py` (walk
  `cases/ ui/ automation/` source files, exclude `__pycache__`; assert the literal does not appear
  elsewhere). This is the mutation guard: reverting any call site to a direct create fails it.
- Keep the file's guard style consistent with existing conformance files (docstring referencing
  the relevant review/plan §, module-scoped markers if needed).

### T6 — Gate
- Postgres suite: `DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg .venv/bin/python -m pytest -q`
  (was 408 passed / 24 skipped before this work — must stay green + new tests).
- SQLite: `make check` (was 429 passed / 3 skipped).
- `ruff check .` + `ruff format` clean on every new/edited file.

## Dependencies / constraints
- Do not touch models/migrations — `TimelineEvent` schema is final.
- `publish_case_event` signature stays `(case_id: str, event_type: str, payload: dict)`; send
  `{"type": "event", ...}` is already correct for the consumer's `event` handler.
- The WS URL pattern stays `re_path(r"ws/case/(?P<case_id>[^/]+)/$", ...)`; pass the UUID.
- Keep `transaction.on_commit` — never publish before the row is durable (same rule as the
  automation dispatch on_commit in AC6.6).
- Reuse `core.serializers.timeline_event_json` — do not hand-roll a second serialization.

## Deliverables
- `cases/ledger.py` (new), edits to `cases/views.py`, `automation/dispatcher.py`,
  `realtime/consumers.py`, `ui/views.py`, `ui/templates/ui/case_detail.html`,
  `ui/templates/ui/_timeline_entry.html` (new), `ui/static/ui/live.js` (new),
  `tests/conformance/test_realtime.py` (new).
- Record deviations (if any) in plan §12 and report them back.