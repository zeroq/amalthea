from __future__ import annotations

from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def publish_case_event(case_id: str | Any, event_type: str, payload: dict[str, Any]) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    group_name = f"case_{case_id}"
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "event",
            "event_type": event_type,
            "payload": payload,
        },
    )
