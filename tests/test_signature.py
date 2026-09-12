import base64
import hashlib
import hmac

import pytest

from line_webhook_id_collector.signature import (
    BodyDecodeError,
    decode_body,
    is_plausible_signature,
    verify_signature,
)

SECRET = "test-channel-secret"
BODY = b'{"destination":"U0","events":[]}'


def sign(body: bytes, secret: str = SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_valid_signature() -> None:
    assert verify_signature(SECRET, BODY, sign(BODY)) is True


def test_invalid_signature() -> None:
    assert verify_signature(SECRET, BODY, sign(BODY, "other")) is False


def test_modified_body() -> None:
    assert verify_signature(SECRET, BODY + b" ", sign(BODY)) is False


def test_garbage_signature_does_not_raise() -> None:
    assert verify_signature(SECRET, BODY, "not base64!!") is False


def test_plausible_signature() -> None:
    assert is_plausible_signature(sign(BODY)) is True
    assert is_plausible_signature(None) is False
    assert is_plausible_signature("") is False
    assert is_plausible_signature("abc") is False
    assert is_plausible_signature(base64.b64encode(b"x" * 10).decode()) is False


def test_decode_plain_body() -> None:
    assert decode_body({"body": BODY.decode(), "isBase64Encoded": False}) == BODY


def test_decode_base64_body() -> None:
    encoded = base64.b64encode(BODY).decode()
    assert decode_body({"body": encoded, "isBase64Encoded": True}) == BODY


def test_decode_missing_body() -> None:
    assert decode_body({}) == b""


def test_decode_bad_base64() -> None:
    with pytest.raises(BodyDecodeError):
        decode_body({"body": "%%%", "isBase64Encoded": True})


def test_base64_body_signature_roundtrip() -> None:
    event = {"body": base64.b64encode(BODY).decode(), "isBase64Encoded": True}
    assert verify_signature(SECRET, decode_body(event), sign(BODY)) is True
