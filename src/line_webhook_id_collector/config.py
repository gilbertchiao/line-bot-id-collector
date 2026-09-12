"""從環境變數載入設定。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    """必要設定缺少或格式錯誤。"""


_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True)
class Settings:
    table_name: str
    secret_name_prefix: str = "line-webhook-id-collector/"
    secret_cache_ttl_seconds: int = 300
    name_lookup_limit: int = 50
    name_lookup_budget_seconds: float = 8.0
    log_level: str = "INFO"
    log_target_ids: bool = False


_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off"})


def _as_bool(name: str, value: str) -> bool:
    """將環境變數字串解析為布林值；無法辨識的值視為設定錯誤，而非靜默視為 False。

    `name` 是環境變數名稱，僅用於組出錯誤訊息，方便定位是哪個變數設定錯誤。
    """
    normalized = value.strip().lower()
    if normalized in _TRUE_STRINGS:
        return True
    if normalized in _FALSE_STRINGS:
        return False
    raise ConfigError(
        f"invalid {name}: {value!r} (must be one of {sorted(_TRUE_STRINGS | _FALSE_STRINGS)})"
    )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """讀取環境變數；`env` 未給時使用 `os.environ`。"""
    source = os.environ if env is None else env
    table_name = source.get("DYNAMODB_TABLE_NAME", "").strip()
    if not table_name:
        raise ConfigError("DYNAMODB_TABLE_NAME is required")
    log_level = source.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in _VALID_LOG_LEVELS:
        raise ConfigError(
            f"invalid LOG_LEVEL: {log_level!r} (must be one of {sorted(_VALID_LOG_LEVELS)})"
        )
    try:
        return Settings(
            table_name=table_name,
            secret_name_prefix=source.get("SECRET_NAME_PREFIX", "line-webhook-id-collector/"),
            secret_cache_ttl_seconds=int(source.get("SECRET_CACHE_TTL_SECONDS", "300")),
            name_lookup_limit=int(source.get("NAME_LOOKUP_LIMIT", "50")),
            name_lookup_budget_seconds=float(source.get("NAME_LOOKUP_BUDGET_SECONDS", "8")),
            log_level=log_level,
            log_target_ids=_as_bool("LOG_TARGET_IDS", source.get("LOG_TARGET_IDS", "false")),
        )
    except ValueError as exc:
        raise ConfigError(f"invalid numeric setting: {exc}") from exc
