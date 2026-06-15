from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SlackMessage:
    destination_id: str
    text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)


class SlackMessageClient(Protocol):
    def post_message(self, message: SlackMessage) -> str:
        raise NotImplementedError

    def update_message(self, message: SlackMessage, ts: str) -> str:
        raise NotImplementedError


class RecordingSlackMessageClient:
    def __init__(self) -> None:
        self.messages: list[SlackMessage] = []
        self.updates: list[tuple[SlackMessage, str]] = []
        self._counter = 0

    def post_message(self, message: SlackMessage) -> str:
        self.messages.append(message)
        self._counter += 1
        return f"1700000000.{self._counter:06d}"

    def update_message(self, message: SlackMessage, ts: str) -> str:
        self.updates.append((message, ts))
        return ts


class NoopSlackMessageClient:
    def post_message(self, message: SlackMessage) -> str:
        return ""

    def update_message(self, message: SlackMessage, ts: str) -> str:
        return ts
