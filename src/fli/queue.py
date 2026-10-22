"""The ``ingest`` queue (spec §7.1): Service Bus with sessions, ``SessionId = libraryId``.

``MemoryQueue`` records messages for tests.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

Message = dict[str, Any]


class Queue(Protocol):
    async def send(self, message: Message, *, session_id: str) -> None: ...


class ServiceBusQueue:
    def __init__(self, sender: Any) -> None:  # azure.servicebus.aio.ServiceBusSender
        self._sender = sender

    async def send(self, message: Message, *, session_id: str) -> None:
        from azure.servicebus import ServiceBusMessage

        await self._sender.send_messages(
            ServiceBusMessage(json.dumps(message), session_id=session_id, content_type="application/json")
        )


class MemoryQueue:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Message]] = []

    async def send(self, message: Message, *, session_id: str) -> None:
        self.sent.append((session_id, message))
