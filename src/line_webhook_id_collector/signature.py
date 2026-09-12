"""LINE webhook 簽章驗證與 request body 還原。

刻意不使用 SDK 的 SignatureValidator：它接受 str 再重新編碼，
這裡直接以 bytes 驗證，保證使用逐位元組一致的原始 body。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from typing import Any


class BodyDecodeError(Exception):
    """API Gateway 標示 base64 但內容無法解碼。"""


def decode_body(event: Mapping[str, Any]) -> bytes:
    """從 API Gateway HTTP API (payload v2) 事件取出原始 body bytes。"""
    raw = event.get("body")
    if raw is None:
        return b""
    if not isinstance(raw, str):
        raise BodyDecodeError("body is not a string")
    if event.get("isBase64Encoded"):
        try:
            return base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BodyDecodeError("body is not valid base64") from exc
    return raw.encode("utf-8")


def is_plausible_signature(signature: str | None) -> bool:
    """在讀 secret 之前先便宜地過濾明顯不合法的簽章標頭。"""
    if not signature:
        return False
    try:
        return len(base64.b64decode(signature, validate=True)) == hashlib.sha256().digest_size
    except binascii.Error, ValueError:
        return False


def verify_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    """HMAC-SHA256(channel_secret, body) 的 base64 是否等於 signature（constant-time）。"""
    try:
        provided = base64.b64decode(signature, validate=True)
    except binascii.Error, ValueError:
        return False
    expected = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(expected, provided)
