"""以 bot_id 讀取 Secrets Manager 中的 Bot 憑證，含 TTL 快取與負快取。"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError


class SecretNotFound(Exception):
    """對應 bot_id 的 secret 不存在。"""


class SecretConfigError(Exception):
    """secret 存在但內容不合法（非 JSON 或缺 channel_secret）。"""


@dataclass(frozen=True)
class BotSecret:
    """單一 LINE Bot 的憑證內容。"""

    channel_secret: str
    channel_access_token: str | None = None
    bootstrap_admin_user_id: str | None = None


def _parse(raw: str) -> BotSecret:
    """將 Secrets Manager 回傳的原始字串解析成 BotSecret。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretConfigError("secret is not valid JSON") from exc
    if not isinstance(data, dict):
        raise SecretConfigError("secret must be a JSON object")
    channel_secret = data.get("channel_secret")
    if not isinstance(channel_secret, str) or not channel_secret:
        raise SecretConfigError("channel_secret is required")
    return BotSecret(
        channel_secret=channel_secret,
        channel_access_token=data.get("channel_access_token") or None,
        bootstrap_admin_user_id=data.get("bootstrap_admin_user_id") or None,
    )


class SecretCache:
    """bot_id -> BotSecret 的快取。找不到的結果也快取（負快取），避免被隨機 bot_id 打爆。"""

    def __init__(
        self,
        client: Any,
        prefix: str,
        ttl_seconds: int,
        max_entries: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._prefix = prefix
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        # value: (expires_at, BotSecret | SecretConfigError | None)。
        # None 代表負快取（查無此 secret）；SecretConfigError 代表該 secret 存在但
        # 內容不合法（非 JSON 或缺 channel_secret），同樣以 TTL 快取，避免格式錯誤的
        # secret 在每次請求都重新打 Secrets Manager。
        self._entries: OrderedDict[str, tuple[float, BotSecret | SecretConfigError | None]] = (
            OrderedDict()
        )

    def get(self, bot_id: str) -> BotSecret:
        """取得指定 bot_id 的憑證，優先讀取尚未過期的快取。"""
        now = self._clock()
        cached = self._entries.get(bot_id)
        if cached is not None and cached[0] > now:
            self._entries.move_to_end(bot_id)
            value = cached[1]
            if value is None:
                raise SecretNotFound(bot_id)
            if isinstance(value, SecretConfigError):
                raise value
            return value

        try:
            response = self._client.get_secret_value(SecretId=f"{self._prefix}{bot_id}")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                self._store(bot_id, None, now)
                raise SecretNotFound(bot_id) from exc
            raise

        try:
            secret = _parse(response.get("SecretString") or "")
        except SecretConfigError as exc:
            self._store(bot_id, exc, now)
            raise
        self._store(bot_id, secret, now)
        return secret

    def _store(self, bot_id: str, secret: BotSecret | SecretConfigError | None, now: float) -> None:
        """寫入快取項目，並在超過上限時淘汰最舊的項目（LRU）。"""
        self._entries[bot_id] = (now + self._ttl, secret)
        self._entries.move_to_end(bot_id)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)
