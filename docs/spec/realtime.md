# Amalthea — Realtime / WebSocket protocol (implemented)

Stack: Django Channels (ASGI, Daphne in prod), `AuthMiddlewareStack` around the case-room and org-room router.

ASGI entrypoint: `amalthea/asgi.py` initialises Django, then calls `mcp_server.asgi.build_application()`.
`mcp_server/asgi.py::build_application` returns a `ProtocolTypeRouter` whose websocket branch is:

```python
AuthMiddlewareStack(URLRouter(realtime.routing.websocket_urlpatterns))
```

MCP Streamable HTTP is mounted before Django for `/mcp` and `/mcp/`; the WebSocket router is unchanged.

## Routes

| Route | Consumer | Purpose |
|---|---|---|
| `/ws/case/<case_id>/` | `realtime.consumers.CaseConsumer` | Case ledger sync, live timeline events, counts-only presence |
| `/ws/org/<org_id>/` | `realtime.consumers.OrgConsumer` | Organisation-scoped refresh signals for alert list, case list, dashboard |

`realtime/routing.py` also exposes HTTP fallback views at the same paths so a plain HTTP request receives a helpful error instead of a bare 404.

## Connection & close codes (`realtime/consumers.py`)

All authorization checks run **before `accept()`**. Refused sockets never join a group.

Shared close codes:

- `UNAUTHENTICATED = 4401` — anonymous socket refused before `accept()`; the client never learns whether the target exists.
- `FORBIDDEN = 4404` — authenticated but the target is not visible to the caller's org (and not `superAdmin`). A non-existent id and an existing-but-other-org id both yield `4404` (no existence oracle, ADR-004).
- `PROTOCOL_ERROR = 4000` — non-UUID id, non-dict frame, unparseable JSON, unknown `type`, malformed `after`, or any client frame in the org room.

Case room: `resolve_case(user, case_id, write=False)` gates visibility; SuperAdmin is allowed via `resolve_case`.

Org room: `_org_access(user, org_id)` checks org membership. SuperAdmin may join any **existing** org room; a cross-org join writes an `AuditLog` row with action `realtime_org_join` and the client IP. Non-existent orgs yield `4404`.

## Case room request frame

Only these message types are accepted:

```json
{"type": "sync", "after": "<timeline-event-id|null>"}
```

```json
{"type": "presence_ping"}
```

- `sync` with `after: null` → full ledger.
- `sync` with `after: <uuid>` → keyset page strictly after that event (`cases/ledger.py::events_after`). The anchor is looked up inside the case's own queryset, so a foreign/absent anchor falls back to the whole ledger; no cross-case leak.
- `presence_ping` refreshes the caller's TTL and broadcasts the updated count.

## Case room response frames

Timeline response:

```json
{"type": "timeline", "events": [<timeline_event_json>, ...]}
```

Events are serialized by `core.serializers.timeline_event_json` (ISO-8601 `date`, `_id`/`id`, body `description`, `kind`, `actor`, `metadata`).

Presence response:

```json
{"type": "presence", "count": 2}
```

Presence is counts-only. Joining does not broadcast a frame; the joining socket and existing sockets first receive their ledger sync response. A `presence_ping` or disconnect broadcasts the updated count, excluding the originating channel where appropriate.

## Publisher relay

Case events reach the room via:

```
group_send(f"case_{case_id}", {"type": "event", "event_type": <kind>, "payload": {...}})
```

Org refresh events reach the room via:

```
group_send(f"org_{org_id}", {"type": "event", "event_type": "refresh", "payload": {...}})
```

`realtime/publisher.py::publish_case_event` and `publish_org_event` are no-ops when the channel layer is `None` (tests/dev). Both derive the room from server-side object state, never from a caller-supplied room name.

Org refresh payload contract:

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

Payloads contain no event bodies, comments, raw alert payloads, or user identities.

## Current org refresh emitters

| Mutation | Surface / reason | Source |
|---|---|---|
| Alert create | `alerts` / `alert-created` | `alerts/views.py` on-commit callback |
| Timeline events with canonical kinds | `cases` / `case-updated` | `cases/ledger.py::append_timeline_event` on-commit callback |
| Comment on a linked alert | `cases` / `case-updated` | `alerts/views.py` on-commit callback |

Canonical timeline kinds mapped to `case-updated`: `comment`, `status-changed`, `status-change`, `verdict-changed`, `automation-run`, `alert-imported`, `alert-merged`. Custom events publish to the case room but intentionally do not emit an org refresh body.

Known deviation: generic alert update/triage and bulk alert update do not currently emit org refresh frames. The original P7 AC expected those paths; the limitation is recorded in `docs/spec/deviations.md`. Alert creation still keeps the alert-list surface live, and case-related changes keep the case-list/dashboard surfaces live.

## Presence store (`realtime/presence.py`)

- Contract: `join(case_id, user) -> int`, `touch(case_id, user) -> int`, `leave(case_id, user) -> int`.
- Production: Redis sorted set `presence:case:<case_id>` with score = expiry timestamp and member = HMAC-SHA256(user.pk, `SECRET_KEY`)[:16]. Expired members are pruned before counting.
- Dev/test: locked in-memory store with the same TTL behaviour.
- Only the integer count is broadcast. The hashed member never leaves the server.
- If `SECRET_KEY` is unavailable, the dev fallback `amalthea-dev-only` is used only for HMAC opacity; production settings fail closed on a strong `DJANGO_SECRET_KEY`.

## The ledger — single write path (`cases/ledger.py`)

`append_timeline_event(case, *, title, kind, description="", actor=None, metadata=None, date=None, end_date=None)` is the **only** way `TimelineEvent` rows are created (a conformance test forbids `TimelineEvent.objects.create` elsewhere). It:

1. creates the row,
2. registers `transaction.on_commit(partial(publish_case_event, str(case.id), kind, {"event": timeline_event_json(event)}))`,
3. for canonical kinds, registers `transaction.on_commit(partial(publish_org_event, str(case.owner_org_id), "refresh", {...}))`.

A rolled-back ledger never publishes either event. `kind` doubles as the WebSocket `event_type`.

## Event kinds

| kind | emitted by |
|---|---|
| `comment` | timeline comments (default) |
| `case-created` | `case_collection` POST (`cases/views.py`) |
| `status-changed` / `assigned` | case PATCH (`cases/views.py`) |
| `status-change` | the UI task toggle / status edits (`ui/views.py`) — the UI spelling, distinct from the API's `status-changed`; both map to TheHive `custom` in `_TIMELINE_KIND_MAP` |
| `alert-imported` / `alert-merged` | `alerts/escalation.py` |
| `alert-removed` | `case_alert_remove` |
| `custom` | customEvent POST/PATCH/DELETE |
| `automation-run` | `automation/dispatcher.py::record_result` |

## Keyset ordering (`events_after`)

Anchor-based range with a negated tie-break to stay deterministic under equal `date`s:

```
Q(date__gte=anchor.date) & ~Q(date=anchor.date, id__lte=anchor.id)
```

ordered `("date", "id")`. Index `timeline_case_date_idx ("case","-date","-id")` exists precisely for this shape.

## Client behaviour

- `ui/static/ui/live.js` maintains one org socket per eligible page and the case socket on case detail.
- Status chips show `Live`, `Reconnecting…`, or `Offline` with `aria-live="polite"`.
- Org `refresh` frames debounce 250 ms and issue a partial GET via `htmx.ajax`, preserving current filters, `page`, and `per_page`.
- Case timeline frames call the pinned `insertBefore` path; presence frames are handled inline on case detail.
- Standalone presence is only initialised where no case timeline exists, so a case socket never produces duplicate presence handlers.
- Hidden tabs suppress refresh; visibility change triggers one resume refresh.

## Evidence

- `tests/conformance/test_realtime.py` — consumer protocol, tenant scoping, ledger event frames.
- `tests/conformance/test_realtime_org.py` — org auth matrix, invalid UUID, frame schema, on-commit publisher.
- `tests/conformance/test_realtime_presence.py` — unique-user count, duplicate tabs, disconnect, TTL, no identity in frames.
- `tests/conformance/test_mcp_asgi.py` — combined ASGI application regression.
- `tests/conformance/test_ui_htmx_scope.py` — partials, no-JS fallback, CSRF, viewer/same-org/cross-org matrix.
- `tests/conformance/test_ledger_keyset.py` — keyset and sole-write-path guard.

See also [`api.md`](./api.md) for case-timeline REST rendering and [`automation.md`](./automation.md) for `automation-run`. Tenant isolation: [`../decisions/ADR-004-tenant-isolation.md`](../decisions/ADR-004-tenant-isolation.md).
