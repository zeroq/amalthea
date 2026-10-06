"""The case room: handshake authentication, a three-message sync protocol, live forwarding.

The protocol is deliberately three sentences long, because every branch here runs on every message
from every open case tab:

* **Handshake.** Auth is checked *before* `accept()`, so an anonymous socket never joins the group
  and never learns that the case id exists (AC7.2). Refusal is `4401` — the websocket equivalent of
  401 — which a browser can tell apart from a network drop.
* **`{"type": "sync", "after": <event-id|null>}`** → `{"type": "timeline", "events": [...]}`,
  strictly after `after` in `(date, id)` order, whole ledger when `after` is null (AC7.4). The
  anchor is resolved by `cases.ledger.events_after`, so the server's ordering promise lives with
  the write path rather than being re-derived here.
* **Anything else** closes `4000`. The client contract is small enough to refuse outright; echoing
  unknown shapes back (what this consumer used to do) would make a client bug look like a feature.
* **`{"type": "event", ...}`** is the publisher's message, forwarded unchanged.

The `case_id` in the URL is the case's UUID — the same string the publisher puts in the group name
(brief 2) — so no resolution step can make publisher and consumer disagree about the room.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from cases.ledger import events_after
from core.serializers import timeline_event_json

#: Handshake refused: no authenticated session on the socket. Not a retry — a browser should send
#: its cookie, and a socket without one will be refused again.
UNAUTHENTICATED = 4401
#: The client said something outside the protocol (unparseable frame, unknown type, malformed
#: anchor). Closing beats guessing: a client that is out of sync is more useful debugging loud.
PROTOCOL_ERROR = 4000


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _timeline_payload(case_id: str, after: str | None) -> list[dict[str, Any]]:
    """Query and serialize together, on a sync thread.

    Serializing inside the same call is what keeps `event.actor` a real row: `events_after`
    `select_related`s it, and touching a deferred foreign key from async context would be an
    implicit database query where none is allowed.
    """
    return [timeline_event_json(event) for event in events_after(case_id, after)]


class CaseConsumer(AsyncWebsocketConsumer):
    case_id: str
    room_group_name: str
    joined: bool

    async def connect(self) -> None:
        self.case_id = str(self.scope["url_route"]["kwargs"]["case_id"])
        self.room_group_name = f"case_{self.case_id}"
        self.joined = False

        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=UNAUTHENTICATED)
            return

        # A room name the publisher can never produce (it always uses the case UUID) is refused
        # rather than joined as a group nobody will ever write to.
        if not _is_uuid(self.case_id):
            await self.close(code=PROTOCOL_ERROR)
            return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        self.joined = True
        await self.accept()

    async def disconnect(self, close_code: int) -> None:
        # Guarded because a handshake refused at `connect` never joined, and a discard of a group
        # we are not in is a pointless round trip to the channel layer.
        if self.joined:
            self.joined = False
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        if bytes_data is not None or text_data is None:
            await self.close(code=PROTOCOL_ERROR)
            return

        try:
            message = json.loads(text_data)
        except ValueError:
            await self.close(code=PROTOCOL_ERROR)
            return

        if not isinstance(message, dict) or message.get("type") != "sync":
            await self.close(code=PROTOCOL_ERROR)
            return

        after = message.get("after")
        if after is not None and not _is_uuid(after):
            await self.close(code=PROTOCOL_ERROR)
            return

        events = await sync_to_async(_timeline_payload)(self.case_id, after)
        await self.send(text_data=json.dumps({"type": "timeline", "events": events}))

    async def event(self, event: dict[str, Any]) -> None:
        """A `publish_case_event` message, relayed as-is (brief 3)."""
        await self.send(text_data=json.dumps(event))
