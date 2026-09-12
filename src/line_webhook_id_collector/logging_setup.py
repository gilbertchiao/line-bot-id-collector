"""JSON 格式的 CloudWatch 日誌設定。"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

LOGGER_NAME = "line_webhook_id_collector"
_QUIET_LOGGERS = ("boto3", "botocore", "urllib3", "linebot")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {"level": record.levelname}
        if isinstance(record.msg, dict):
            payload.update(record.msg)
        else:
            payload["message"] = record.getMessage()
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> logging.Logger:
    """設定本專案 logger；第三方 logger 固定 WARNING 以免外洩 HTTP 內容。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return logger


def log_event(logger: logging.Logger, result: str, **fields: Any) -> None:
    """輸出一筆結構化事件；值為 None 的欄位省略。"""
    payload = {"result": result}
    payload.update({k: v for k, v in fields.items() if v is not None})
    logger.info(payload)
