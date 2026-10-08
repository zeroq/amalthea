# Amalthea — Realtime / WebSocket protocol (implemented)

Stack: Django Channels (ASGI, Daphne in prod), `AuthMiddlewareStack` around the case-room router.
`amalthea/asgi.py`: `ProtocolTypeRouter({"websocket": AuthMiddlewareStack(URLRouter(
realtime_routing.websocket_urlpatterns))})`.

## Route

`re_path(r"ws/case/(?P<case_id>[^/]+)/$", consumers.CaseConsumer.as_asgi())` → **`/ws/case/<id>/`**.

## Connection & close codes (`realtime/consumers.py`)

- `UNAUTHENTICATED = 4401` — anonymous socket refused **before `accept()`**; the client never learns
  whether the case exists (AC7.2).
- `PROTOCOL_ERROR = 4000` — any protocol violation (non-UUID `case_id`, non-dict frame, unparseable
  JSON, unknown `type`, malformed `after`).

## Request frame

Only one message type is accepted:

```json
{"type": "sync", "after": "<timeline-event-id|null>"}
```

- `after: null` → full ledger.
- `after: <uuid>` → keyset page strictly after that event (`cases/ledger.py::events_after`).
- The anchor is looked up **inside the case's own queryset** — a foreign/absent anchor falls back to
  the whole ledger; no cross-case leak.

## Response frame

```json
{"type": "timeline", "events": [<timeline_event_json>, ...]}
```

Events are serialized by `core.serializers.timeline_event_json` (ISO-8601 `date`, `_id`/`id`, body
`description`, `kind`, `actor`, `metadata`).

## Publisher relay

Server-originated events reach the room via the group send:

```
group_send(f"case_{case_id}", {"type": "event", "event_type": <kind>, "payload": {...}})
```

`realtime/publisher.py::publish_case_event` — a no-op when the channel layer is `None` (tests/dev).
The consumer's `event(event)` handler relays the message unchanged.

## The ledger — single write path (`cases/ledger.py`)

`append_timeline_event(case, *, title, kind, description="", actor=None, metadata=None, date=None,
end_date=None)` is the **only** way `TimelineEvent` rows are created (a conformance test forbids
`TimelineEvent.objects.create` elsewhere). It:

1. creates the row,
2. registers `transaction.on_commit(partial(publish_case_event, str(case.id), kind,
   {"event": timeline_event_json(event)}))` — so a rolled-back ledger never publishes.

`kind` doubles as the WebSocket `event_type`.

## Event kinds

| kind | emitted by |
|---|---|
| `comment` | timeline comments (default) |
| `case-created` | `case_collection` POST (`cases/views.py`) |
| `status-changed` / `assigned` | case PATCH (`cases/views.py`) |
| `status-change` | the UI task toggle / status edits (`ui/views.py:305`) — the UI spelling, distinct from the API's `status-changed`; both map to TheHive `custom` in `_TIMELINE_KIND_MAP` (`cases/views.py:569-579`) |
| `alert-imported` / `alert-merged` | `alerts/escalation.py` |
| `alert-removed` | `case_alert_remove` |
| `custom` | customEvent POST/PATCH/DELETE |
| `automation-run` | `automation/dispatcher.py::record_result` (title `Automation <status>: <label>`, description `<output_log|error>[:4000]`, metadata `{runId, playbook, triggerEvent, status}`) |

## Keyset ordering (`events_after`)

Anchor-based range with a negated tie-break to stay deterministic under equal `date`s:

```
Q(date__gte=anchor.date) & ~Q(date=anchor.date, id__lte=anchor.id)
```

ordered `("date", "id")`. (Index `timeline_case_date_idx ("case","-date","-id")` exists precisely
for this shape — M9.)

## Evidence

`tests/conformance/test_realtime.py` (consumer protocol), `test_ledger_keyset.py` (keyset + the
choke-point test guarding `ledger.py` as the sole write path). See also [`api.md`](./api.md) for the
case-timeline REST rendering (ms-epoch on the wire, P8-1) and [`automation.md`](./automation.md) for
the `automation-run` kinds.