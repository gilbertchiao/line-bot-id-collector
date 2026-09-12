"""分析單一 LINE webhook 事件，抽出要儲存的 target 與可能的指令。

此模組是純函式，不碰 AWS 或 LINE API。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

USER_ID_RE = re.compile(r"^U[0-9a-f]{32}$")

_SOURCE_ID_FIELDS: tuple[tuple[str, str], ...] = (
    ("groupId", "group"),
    ("roomId", "room"),
    ("userId", "user"),
)

#: PRD 8.2～8.7 明確定義額外動作的事件類型。其餘事件類型（`postback`、`beacon`、
#: `videoPlayComplete`、`unsend`、`memberLeft`、`accountLink`、`things` 等）仍套用
#: 8.1 的抽取規則，但視為「不支援」，須記 log `ignored`（PRD 8.8 / 第 13 節）。
SUPPORTED_EVENT_TYPES = frozenset(
    {"follow", "unfollow", "join", "leave", "memberJoined", "message"}
)


@dataclass(frozen=True)
class Target:
    target_id: str
    target_type: str


@dataclass(frozen=True)
class CommandCandidate:
    user_id: str
    text: str
    reply_token: str


@dataclass(frozen=True)
class EventOutcome:
    event_type: str
    source_type: str | None
    active_targets: tuple[Target, ...]
    inactive_targets: tuple[Target, ...]
    event_ts: int
    is_redelivery: bool
    command: CommandCandidate | None

    @property
    def is_supported(self) -> bool:
        """是否為 PRD 8.2～8.7 明確定義額外動作的事件類型。"""
        return self.event_type in SUPPORTED_EVENT_TYPES


def iso_from_ms(ms: int) -> str:
    """毫秒 epoch → `YYYY-MM-DDTHH:MM:SSZ`。"""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _targets_from_source(source: Any) -> tuple[Target, ...]:
    if not isinstance(source, dict):
        return ()
    found: list[Target] = []
    for field, target_type in _SOURCE_ID_FIELDS:
        value = source.get(field)
        if isinstance(value, str) and value:
            found.append(Target(value, target_type))
    return tuple(found)


def _targets_from_members(event: dict[str, Any]) -> tuple[Target, ...]:
    joined = event.get("joined")
    members = joined.get("members") if isinstance(joined, dict) else None
    if not isinstance(members, list):
        return ()
    found: list[Target] = []
    for member in members:
        if isinstance(member, dict):
            user_id = member.get("userId")
            if isinstance(user_id, str) and user_id:
                found.append(Target(user_id, "user"))
    return tuple(found)


def _command_candidate(event: dict[str, Any], source: Any) -> CommandCandidate | None:
    if not isinstance(source, dict) or source.get("type") != "user":
        return None
    message = event.get("message")
    if not isinstance(message, dict) or message.get("type") != "text":
        return None
    user_id = source.get("userId")
    text = message.get("text")
    reply_token = event.get("replyToken")
    if not (isinstance(user_id, str) and isinstance(text, str) and isinstance(reply_token, str)):
        return None
    return CommandCandidate(user_id=user_id, text=text, reply_token=reply_token)


def analyze_event(event: Any, now_ms: int) -> EventOutcome | None:
    """回傳事件分析結果；事件不是物件或缺少 type 時回 None。"""
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        return None

    source = event.get("source")
    source_type = source.get("type") if isinstance(source, dict) else None
    if not isinstance(source_type, str):
        source_type = None

    timestamp = event.get("timestamp")
    event_ts = (
        timestamp if isinstance(timestamp, int) and not isinstance(timestamp, bool) else now_ms
    )

    delivery = event.get("deliveryContext")
    is_redelivery = isinstance(delivery, dict) and delivery.get("isRedelivery") is True

    source_targets = _targets_from_source(source)
    active: tuple[Target, ...]
    inactive: tuple[Target, ...]
    command: CommandCandidate | None = None

    if event_type in ("unfollow", "leave"):
        active, inactive = (), source_targets
    elif event_type == "memberJoined":
        active, inactive = source_targets + _targets_from_members(event), ()
    elif event_type == "message":
        active, inactive = source_targets, ()
        command = _command_candidate(event, source)
    else:
        active, inactive = source_targets, ()

    return EventOutcome(
        event_type=event_type,
        source_type=source_type,
        active_targets=active,
        inactive_targets=inactive,
        event_ts=event_ts,
        is_redelivery=is_redelivery,
        command=command,
    )
