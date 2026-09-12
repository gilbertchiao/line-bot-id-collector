"""AWS Lambda 進入點：驗證、收集、（可選）回覆。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import boto3

from line_webhook_id_collector.commands import CommandContext, execute_command, is_admin
from line_webhook_id_collector.config import Settings, load_settings
from line_webhook_id_collector.events import CommandCandidate, analyze_event
from line_webhook_id_collector.line_client import LineApiError, LineClient
from line_webhook_id_collector.logging_setup import configure_logging, log_event
from line_webhook_id_collector.repository import TargetRepository
from line_webhook_id_collector.secrets import (
    BotSecret,
    SecretCache,
    SecretConfigError,
    SecretNotFound,
)
from line_webhook_id_collector.signature import (
    BodyDecodeError,
    decode_body,
    is_plausible_signature,
    verify_signature,
)

BOT_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")

__all__ = ["LineApiError", "lambda_handler"]


@dataclass
class Dependencies:
    """跨呼叫重複使用的相依物件（AWS client、cache 等）。"""

    settings: Settings
    logger: Any
    secrets: SecretCache
    repo: TargetRepository


_deps: Dependencies | None = None


def _make_secrets_client() -> Any:
    """建立 Secrets Manager client；獨立成函式方便測試 monkeypatch。"""
    return boto3.client("secretsmanager")


def _make_line_client(token: str) -> LineClient:
    """建立 LINE Messaging API client；獨立成函式方便測試 monkeypatch。"""
    return LineClient(token)


def build_dependencies(settings: Settings) -> Dependencies:
    """依設定建立本次 Lambda 執行環境需要的所有相依物件。"""
    return Dependencies(
        settings=settings,
        logger=configure_logging(settings.log_level),
        secrets=SecretCache(
            _make_secrets_client(), settings.secret_name_prefix, settings.secret_cache_ttl_seconds
        ),
        repo=TargetRepository(settings.table_name),
    )


def _get_deps() -> Dependencies:
    """取得（並視需要初始化）模組層級的相依物件，跨 Lambda 呼叫重複使用。"""
    global _deps
    if _deps is None:
        _deps = build_dependencies(load_settings())
    return _deps


def _response(status: int, message: str) -> dict[str, Any]:
    """組出 API Gateway HTTP API（payload v2）格式的回應。"""
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"message": message}),
    }


def _header(headers: Mapping[str, Any] | None, name: str) -> str | None:
    """不分大小寫地從 headers 找出指定標頭值。"""
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == name:
            return value if isinstance(value, str) else None
    return None


def _target_id_for_log(settings: Settings, target_id: str) -> str | None:
    """依設定決定 log 中是否輸出 target_id（預設不輸出，避免外洩個資）。"""
    return target_id if settings.log_target_ids else None


def _command_label(text: str) -> str:
    """組出可安全寫入 log 的指令標籤（第一個 token，必要時加上子指令）。

    僅在第一個 token 是 `/admin` 或 `/list`，且第二個 token 全為小寫英文字母
    （例如 add/remove/list/groups/users/rooms/all）時才附加第二個 token；
    使用者 ID（`U`/`C`/`R` 開頭接 32 碼英數混合）一定含數字，不會符合
    `^[a-z]+$`，因此保證 ID 或其他個資不會外洩到 log。空字串回傳 `""`。
    """
    tokens = text.strip().split()
    if not tokens:
        return ""
    keyword = tokens[0].lower()
    if keyword in ("/admin", "/list") and len(tokens) > 1:
        second = tokens[1].lower()
        if re.match(r"^[a-z]+$", second):
            return f"{keyword} {second}"
    return keyword


def _process_event(
    deps: Dependencies,
    bot_id: str,
    raw_event: Any,
    now_ms: int,
    request_id: str | None,
) -> tuple[CommandCandidate | None, bool]:
    """分析並寫入單一事件；回傳（指令候選（若有且非重送）, 是否發生 DynamoDB 寫入失敗）。

    呼叫端須自行捕捉本函式可能拋出的例外（主要來自 `analyze_event` 對非預期結構的輸入），
    確保單一畸形事件不會讓整個 webhook 失敗。
    """
    settings, logger = deps.settings, deps.logger
    outcome = analyze_event(raw_event, now_ms)
    if outcome is None:
        log_event(logger, "ignored", request_id=request_id, bot_id=bot_id, reason="invalid")
        return None, False

    had_write_failure = False
    command = outcome.command if not outcome.is_redelivery else None

    for status, targets in (
        ("active", outcome.active_targets),
        ("inactive", outcome.inactive_targets),
    ):
        for target in targets:
            try:
                result = deps.repo.upsert(
                    bot_id, target, status, outcome.event_ts, outcome.event_type
                )
            except Exception:
                had_write_failure = True
                logger.exception(
                    {
                        "result": "write_failed",
                        "request_id": request_id,
                        "bot_id": bot_id,
                        "event_type": outcome.event_type,
                        "target_type": target.target_type,
                    }
                )
                continue
            log_event(
                logger,
                result,
                request_id=request_id,
                bot_id=bot_id,
                event_type=outcome.event_type,
                source_type=outcome.source_type,
                target_type=target.target_type,
                target_id=_target_id_for_log(settings, target.target_id),
            )
    if not outcome.active_targets and not outcome.inactive_targets:
        log_event(
            logger,
            "ignored",
            request_id=request_id,
            bot_id=bot_id,
            event_type=outcome.event_type,
            source_type=outcome.source_type,
        )
    return command, had_write_failure


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda 進入點：驗證簽章、收集事件、執行至多一個開發者指令。"""
    deps = _get_deps()
    logger = deps.logger
    request_id = (event.get("requestContext") or {}).get("requestId")

    bot_id = (event.get("pathParameters") or {}).get("bot_id", "")
    if not isinstance(bot_id, str) or not BOT_ID_RE.match(bot_id):
        log_event(logger, "unknown_bot", request_id=request_id)
        return _response(404, "unknown bot")

    signature = _header(event.get("headers"), "x-line-signature")
    if not is_plausible_signature(signature):
        log_event(logger, "invalid_signature", request_id=request_id, bot_id=bot_id)
        return _response(401, "invalid signature")

    try:
        body = decode_body(event)
    except BodyDecodeError:
        log_event(logger, "bad_request", request_id=request_id, bot_id=bot_id, reason="body")
        return _response(400, "bad body encoding")

    try:
        secret = deps.secrets.get(bot_id)
    except SecretNotFound:
        log_event(logger, "unknown_bot", request_id=request_id, bot_id=bot_id)
        return _response(404, "unknown bot")
    except SecretConfigError as exc:
        log_event(logger, "config_error", request_id=request_id, bot_id=bot_id, reason=str(exc))
        return _response(500, "bot misconfigured")
    except Exception:
        logger.exception({"result": "secret_error", "request_id": request_id, "bot_id": bot_id})
        return _response(500, "secret unavailable")

    if not verify_signature(secret.channel_secret, body, signature or ""):
        log_event(logger, "invalid_signature", request_id=request_id, bot_id=bot_id)
        return _response(401, "invalid signature")

    try:
        payload = json.loads(body)
    except ValueError:
        log_event(logger, "bad_request", request_id=request_id, bot_id=bot_id, reason="json")
        return _response(400, "invalid json")
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        log_event(logger, "bad_request", request_id=request_id, bot_id=bot_id, reason="events")
        return _response(400, "events must be a list")

    now_ms = int(time.time() * 1000)
    had_write_failure = False
    pending: CommandCandidate | None = None

    for raw_event in events:
        try:
            command, write_failed = _process_event(deps, bot_id, raw_event, now_ms, request_id)
        except Exception:
            # 單一事件的分析或處理不應讓整個 webhook 失敗（避免 API Gateway 502）；
            # 不記錄原始事件內容，避免將未知結構的資料寫進 log。
            logger.exception({"result": "event_failed", "request_id": request_id, "bot_id": bot_id})
            continue
        if write_failed:
            had_write_failure = True
        if pending is None and command is not None:
            pending = command

    if pending is not None:
        _handle_command(deps, bot_id, secret, pending, now_ms, request_id)

    if had_write_failure:
        return _response(500, "partial failure")
    return _response(200, "ok")


def _handle_command(
    deps: Dependencies,
    bot_id: str,
    secret: BotSecret,
    candidate: CommandCandidate,
    now_ms: int,
    request_id: str | None,
) -> None:
    """在所有事件收集完成後執行指令；任何失敗只記 log，不影響 HTTP 回應。"""
    logger, settings = deps.logger, deps.settings
    if not secret.channel_access_token:
        return
    try:
        if not is_admin(deps.repo, bot_id, secret, candidate.user_id):
            return
        client = _make_line_client(secret.channel_access_token)
        ctx = CommandContext(
            bot_id=bot_id,
            settings=settings,
            secret=secret,
            repo=deps.repo,
            client=client,
            sender_id=candidate.user_id,
            now_ms=now_ms,
        )
        messages = execute_command(candidate.text, ctx)
        if messages is None:
            return
        client.reply(candidate.reply_token, messages)
        log_event(
            logger,
            "replied",
            request_id=request_id,
            bot_id=bot_id,
            command=_command_label(candidate.text),
        )
    except LineApiError as exc:
        log_event(logger, "reply_failed", request_id=request_id, bot_id=bot_id, reason=str(exc))
    except Exception:
        logger.exception({"result": "command_failed", "request_id": request_id, "bot_id": bot_id})
