"""Mailbox message model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MessageType(StrEnum):
    TEXT = "text"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_REQUEST = "plan_approval_request"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"


@dataclass
class Message:
    from_: str
    text: str
    timestamp: str = ""
    read: bool = False
    type: MessageType = MessageType.TEXT
    request_id: str = field(default="", metadata={"json": "requestId"})
    approve: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "from": self.from_,
            "text": self.text,
            "timestamp": self.timestamp,
            "read": self.read,
        }
        if self.type != MessageType.TEXT:
            data["type"] = str(self.type)
            data["requestId"] = self.request_id
            data["approve"] = self.approve
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        raw_type = data.get("type", MessageType.TEXT)
        try:
            message_type = MessageType(raw_type)
        except ValueError:
            message_type = MessageType.TEXT
        return cls(
            from_=str(data.get("from", "")),
            text=str(data.get("text", "")),
            timestamp=str(data.get("timestamp", "")),
            read=bool(data.get("read", False)),
            type=message_type,
            request_id=str(data.get("requestId", "")),
            approve=data.get("approve"),
        )


__all__ = ["Message", "MessageType"]
