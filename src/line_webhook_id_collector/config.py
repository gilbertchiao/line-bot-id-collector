"""從環境變數載入設定。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    """必要設定缺少或格式錯誤。"""


@dataclass(frozen=True)
class Settings:
    table_name: str
    secret_name_prefix: str = "line-webhook-id-collector/"
    secret_cache_ttl_seconds: int = 300
    name_lookup_limit: int = 50
    name_lookup_budget_seconds: float = 8.0
    log_level: str = "INFO"
    log_target_ids: bool = False


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """讀取環境變數；`env` 未給時使用 `os.environ`。"""
    source = os.environ if env is None else env
    table_name = source.get("DYNAMODB_TABLE_NAME", "").strip()
    if not table_name:
        raise ConfigError("DYNAMODB_TABLE_NAME is required")
    try:
        return Settings(
            table_name=table_name,
            secret_name_prefix=source.get("SECRET_NAME_PREFIX", "line-webhook-id-collector/"),
            secret_cache_ttl_seconds=int(source.get("SECRET_CACHE_TTL_SECONDS", "300")),
            name_lookup_limit=int(source.get("NAME_LOOKUP_LIMIT", "50")),
            name_lookup_budget_seconds=float(source.get("NAME_LOOKUP_BUDGET_SECONDS", "8")),
            log_level=source.get("LOG_LEVEL", "INFO").upper(),
            log_target_ids=_as_bool(source.get("LOG_TARGET_IDS", "false")),
        )
    except ValueError as exc:
        raise ConfigError(f"invalid numeric setting: {exc}") from exc
