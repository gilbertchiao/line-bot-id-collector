"""開發者指令：解析、授權、執行、產生回覆文字。

不直接呼叫 AWS 或 LINE SDK；透過 CommandContext 注入 repository 與 client。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from line_webhook_id_collector.config import Settings
from line_webhook_id_collector.events import USER_ID_RE
from line_webhook_id_collector.secrets import BotSecret
from line_webhook_id_collector.text_utils import pack_blocks

HELP_TEXT = "\n".join(
    [
        "Commands",
        "",
        "/id - show your user ID",
        "/list - list active targets",
        "/list groups|users|rooms - list one type",
        "/list all - include inactive targets",
        "/admin list - list admins",
        "/admin add <userId> - grant admin",
        "/admin remove <userId> - revoke admin",
        "/help - this message",
    ]
)

_LIST_USAGE = "Usage: /list [groups|users|rooms|all]"
_ADMIN_USAGE = "Usage: /admin list | /admin add <userId> | /admin remove <userId>"
_SECTION_ORDER = (("group", "Groups"), ("room", "Rooms"), ("user", "Users"))


@dataclass
class CommandContext:
    bot_id: str
    settings: Settings
    secret: BotSecret
    repo: Any
    client: Any
    sender_id: str
    now_ms: int
    clock: Callable[[], float] = field(default=time.monotonic)


def is_admin(repo: Any, bot_id: str, secret: BotSecret, user_id: str) -> bool:
    """種子開發者或 DB 中 role=admin；status 不影響授權。"""
    if secret.bootstrap_admin_user_id and user_id == secret.bootstrap_admin_user_id:
        return True
    item = repo.get(bot_id, user_id)
    return bool(item) and item.get("role") == "admin"


def execute_command(text: str, ctx: CommandContext) -> list[str] | None:
    """回傳要回覆的訊息清單；None 代表靜默。呼叫端須先確認 sender 是開發者。"""
    tokens = text.strip().split()
    if not tokens:
        return None
    keyword = tokens[0].lower()
    args = tokens[1:]

    if keyword == "/id":
        return [f"LINE Target\n\nType: user\nUser ID:\n{ctx.sender_id}"]
    if keyword == "/help":
        return [HELP_TEXT]
    if keyword == "/list":
        return _list(args, ctx)
    if keyword == "/admin":
        return _admin(args, ctx)
    return None


# ---------- /list ----------


class _NameResolver:
    """受筆數上限與時間預算限制的名稱查詢。"""

    def __init__(self, ctx: CommandContext) -> None:
        self._client = ctx.client
        self._limit = ctx.settings.name_lookup_limit
        self._budget = ctx.settings.name_lookup_budget_seconds
        self._clock = ctx.clock
        self._started = ctx.clock()
        self._count = 0

    def _allowed(self) -> bool:
        if self._client is None or self._count >= self._limit:
            return False
        return (self._clock() - self._started) < self._budget

    def group(self, group_id: str) -> str | None:
        if not self._allowed():
            return None
        self._count += 1
        return self._client.get_group_name(group_id)

    def user(self, user_id: str) -> str | None:
        if not self._allowed():
            return None
        self._count += 1
        return self._client.get_user_name(user_id)


def _list(args: list[str], ctx: CommandContext) -> list[str]:
    mode = args[0].lower() if args else ""
    if len(args) > 1 or mode not in ("", "groups", "users", "rooms", "all"):
        return [_LIST_USAGE]
    type_filter = {"groups": "group", "users": "user", "rooms": "room"}.get(mode)
    include_inactive = mode == "all"

    items = [
        item
        for item in ctx.repo.list_targets(ctx.bot_id)
        if (include_inactive or item.get("status") == "active")
        and (type_filter is None or item.get("target_type") == type_filter)
    ]
    if not items:
        return ["No targets found."]

    items.sort(key=lambda i: int(i.get("last_event_ts", 0)), reverse=True)
    resolver = _NameResolver(ctx)
    blocks: list[str] = []
    for target_type, title in _SECTION_ORDER:
        section = [i for i in items if i.get("target_type") == target_type]
        if not section:
            continue
        if blocks:
            blocks.append("")
        blocks.append(f"{title} ({len(section)})")
        for item in section:
            blocks.append(_format_item(item, resolver))

    def trailer(remaining: int) -> str:
        return (
            f"... and {remaining} more. Use:\n"
            f"aws dynamodb query --table-name {ctx.settings.table_name} "
            f'--key-condition-expression "bot_id = :b" '
            f'--expression-attribute-values \'{{":b":{{"S":"{ctx.bot_id}"}}}}\''
        )

    return pack_blocks(blocks, trailer=trailer)


def _format_item(item: dict[str, Any], resolver: _NameResolver) -> str:
    target_id = item["target_id"]
    target_type = item.get("target_type")
    if target_type == "room":
        label = None
    elif target_type == "group":
        label = resolver.group(target_id)
    else:
        label = resolver.user(target_id)

    suffixes = []
    if item.get("role") == "admin":
        suffixes.append("[admin]")
    if item.get("status") == "inactive":
        suffixes.append("[inactive]")
    suffix = (" " + " ".join(suffixes)) if suffixes else ""

    if target_type == "room":
        return f"- {target_id}{suffix}"
    return f"- {label or '(unknown)'}{suffix}\n  {target_id}"


# ---------- /admin ----------


def _admin(args: list[str], ctx: CommandContext) -> list[str]:
    sub = args[0].lower() if args else ""
    if sub == "list" and len(args) == 1:
        return _admin_list(ctx)
    if sub in ("add", "remove") and len(args) == 2:
        user_id = args[1]
        if not USER_ID_RE.match(user_id):
            return ["Invalid user ID format."]
        return _admin_add(user_id, ctx) if sub == "add" else _admin_remove(user_id, ctx)
    return [_ADMIN_USAGE]


def _admin_list(ctx: CommandContext) -> list[str]:
    bootstrap = ctx.secret.bootstrap_admin_user_id
    db_admins = [i for i in ctx.repo.list_targets(ctx.bot_id) if i.get("role") == "admin"]
    ordered: list[str] = []
    if bootstrap:
        ordered.append(bootstrap)
    for item in db_admins:
        if item["target_id"] not in ordered:
            ordered.append(item["target_id"])

    resolver = _NameResolver(ctx)
    blocks = [f"Admins ({len(ordered)})"]
    for user_id in ordered:
        name = resolver.user(user_id) or "(unknown)"
        marker = " (bootstrap)" if user_id == bootstrap else ""
        blocks.append(f"- {name}{marker}\n  {user_id}")
    return pack_blocks(blocks)


def _admin_add(user_id: str, ctx: CommandContext) -> list[str]:
    if user_id == ctx.secret.bootstrap_admin_user_id:
        return ["Already bootstrap admin."]
    existing = ctx.repo.get(ctx.bot_id, user_id)
    if existing and existing.get("role") == "admin":
        return [f"Already admin: {user_id}"]
    ctx.repo.add_admin(ctx.bot_id, user_id, ctx.now_ms)
    return [f"Added admin: {user_id}"]


def _admin_remove(user_id: str, ctx: CommandContext) -> list[str]:
    if user_id == ctx.secret.bootstrap_admin_user_id:
        return ["Cannot remove bootstrap admin."]
    if user_id == ctx.sender_id:
        return ["Cannot remove yourself."]
    if ctx.repo.remove_admin(ctx.bot_id, user_id):
        return [f"Removed admin: {user_id}"]
    return [f"Not an admin: {user_id}"]
