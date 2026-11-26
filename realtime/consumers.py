"""WebSocket consumers for case rooms and organisation-scoped refresh rooms.

Both consumers authenticate before ``accept()``.  A refused socket never joins a
group, and non-existent/forbidden targets both close ``4404`` so the socket cannot
be used as an existence oracle.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

import identity.scoping
from cases.ledger import events_after
from core.enums import SUPER_ADMIN_ROLE
from core.serializers import timeline_event_json
from realtime.presence import get_presence_store

UNAUTHENTICATED = 4401
FORBIDDEN = 4404
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
    """Query and serialize together on a sync thread."""
    return [timeline_event_json(event) for event in events_after(case_id, after)]


def _org_access(user: Any, org_id: str) -> tuple[bool, bool]:
    """Return ``(allowed, is_cross_org_superadmin)`` for an org room join."""
    from identity.models import Organisation

    if not Organisation.objects.filter(pk=org_id).exists():
        return False, False
    if getattr(user, "role", "") == SUPER_ADMIN_ROLE:
        return True, str(getattr(user, "org_id", "")) != org_id
    return str(getattr(user, "org_id", "")) == org_id, False


def _audit_cross_org_org_join(user: Any, org_id: str, client_ip: str | None) -> None:
    """Record a superAdmin's cross-org realtime subscription."""
    from identity.models import AuditLog, Organisation

    target = Organisation.objects.filter(pk=org_id).first()
    if target is None:
        return
    actor_org = getattr(user, "org", None)
    if actor_org is not None and str(actor_org.id) == str(target.id):
        return
    AuditLog.objects.create(
        actor=user,
        actor_org=actor_org,
        target_org=target,
        action="realtime_org_join",
        target_type="Organisation",
        target_id=str(target.id),
        ip=client_ip,
    )


class CaseConsumer(AsyncWebsocketConsumer):
    """The case room: sync protocol, live ledger events, and counts-only presence."""

    case_id: str
    room_group_name: str
    joined: bool

    async def connect(self) -> None:
        self.case_id = str(self.scope["url_route"]["kwargs"]["case_id"])
        self.room_group_name = f"case_{self.case_id}"
        self.joined = False
        self.presence_store = get_presence_store()

        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=UNAUTHENTICATED)
            return
        if not _is_uuid(self.case_id):
            await self.close(code=PROTOCOL_ERROR)
            return

        allowed = await sync_to_async(identity.scoping.resolve_case)(
            user,  # type: ignore[arg-type]
            self.case_id,
            write=False,
        )
        if allowed is None:
            await self.close(code=FORBIDDEN)
            return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        self.joined = True
        await self.accept()
        # Do not broadcast on join: another tab's first frame must remain its ledger event,
        # and the joining socket must not receive a presence frame it did not ask for.
        await sync_to_async(self.presence_store.join)(self.case_id, user)

    async def disconnect(self, close_code: int) -> None:
        if not self.joined:
            return
        self.joined = False
        user = self.scope.get("user")
        if user is not None and user.is_authenticated:
            count = await sync_to_async(self.presence_store.leave)(self.case_id, user)
            await self._broadcast_presence(count)
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
        if not isinstance(message, dict):
            await self.close(code=PROTOCOL_ERROR)
            return

        if message.get("type") == "presence_ping":
            user = self.scope.get("user")
            if user is not None and user.is_authenticated:
                count = await sync_to_async(self.presence_store.touch)(self.case_id, user)
                await self._broadcast_presence(count)
            return

        if message.get("type") != "sync":
            await self.close(code=PROTOCOL_ERROR)
            return
        after = message.get("after")
        if after is not None and not _is_uuid(after):
            await self.close(code=PROTOCOL_ERROR)
            return
        events = await sync_to_async(_timeline_payload)(self.case_id, after)
        await self.send(text_data=json.dumps({"type": "timeline", "events": events}))

    async def event(self, event: dict[str, Any]) -> None:
        """A ``publish_case_event`` message, relayed as-is."""
        await self.send(text_data=json.dumps(event))

    async def presence(self, event: dict[str, Any]) -> None:
        """Counts-only presence frame; a joiner does not receive its own join broadcast."""
        if event.get("exclude_channel") == self.channel_name:
            return
        await self.send(text_data=json.dumps({"type": "presence", "count": int(event["count"])}))

    async def _broadcast_presence(self, count: int, *, exclude_channel: str | None = None) -> None:
        event: dict[str, Any] = {"type": "presence", "count": count}
        if exclude_channel is not None:
            event["exclude_channel"] = exclude_channel
        await self.channel_layer.group_send(self.room_group_name, event)


class OrgConsumer(AsyncWebsocketConsumer):
    """Organisation-scoped refresh room for list and dashboard live updates."""

    org_id: str
    room_group_name: str
    joined: bool

    async def connect(self) -> None:
        self.org_id = str(self.scope["url_route"]["kwargs"]["org_id"])
        self.room_group_name = f"org_{self.org_id}"
        self.joined = False

        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=UNAUTHENTICATED)
            return
        if not _is_uuid(self.org_id):
            await self.close(code=PROTOCOL_ERROR)
            return

        allowed, cross_org = await sync_to_async(_org_access)(user, self.org_id)
        if not allowed:
            await self.close(code=FORBIDDEN)
            return
        if cross_org:
            client_list = self.scope.get("client") or (None, None)
            await sync_to_async(_audit_cross_org_org_join)(user, self.org_id, client_list[0])

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        self.joined = True
        await self.accept()

    async def disconnect(self, close_code: int) -> None:
        if self.joined:
            self.joined = False
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        # The org room is server-push only; a client message is always a protocol error.
        await self.close(code=PROTOCOL_ERROR)

    async def event(self, event: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(event))
