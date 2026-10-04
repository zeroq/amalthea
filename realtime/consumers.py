import json
from typing import Any

from channels.generic.websocket import AsyncWebsocketConsumer


class CaseConsumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        self.case_id = self.scope["url_route"]["kwargs"]["case_id"]
        self.room_group_name = f"case_{self.case_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code: int) -> None:
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        if text_data:
            await self.send(text_data=json.dumps({"echo": text_data}))

    async def event(self, event: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(event))
