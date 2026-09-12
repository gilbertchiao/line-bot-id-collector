import json

import boto3
import pytest
from moto import mock_aws

from line_webhook_id_collector.secrets import (
    SecretCache,
    SecretConfigError,
    SecretNotFound,
)

PREFIX = "line-webhook-id-collector/"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def sm():
    with mock_aws():
        yield boto3.client("secretsmanager", region_name="ap-northeast-1")


def put(sm, bot_id: str, payload: dict | str) -> None:
    value = payload if isinstance(payload, str) else json.dumps(payload)
    sm.create_secret(Name=f"{PREFIX}{bot_id}", SecretString=value)


def test_get_full_secret(sm) -> None:
    put(
        sm,
        "alert-bot",
        {
            "channel_secret": "s",
            "channel_access_token": "t",
            "bootstrap_admin_user_id": "U" + "a" * 32,
        },
    )
    cache = SecretCache(sm, PREFIX, ttl_seconds=300)
    secret = cache.get("alert-bot")
    assert secret.channel_secret == "s"
    assert secret.channel_access_token == "t"
    assert secret.bootstrap_admin_user_id == "U" + "a" * 32


def test_optional_fields_default_none(sm) -> None:
    put(sm, "b", {"channel_secret": "s"})
    secret = SecretCache(sm, PREFIX, 300).get("b")
    assert secret.channel_access_token is None
    assert secret.bootstrap_admin_user_id is None


def test_missing_channel_secret_is_config_error(sm) -> None:
    put(sm, "b", {"channel_access_token": "t"})
    with pytest.raises(SecretConfigError):
        SecretCache(sm, PREFIX, 300).get("b")


def test_invalid_json_is_config_error(sm) -> None:
    put(sm, "b", "not json")
    with pytest.raises(SecretConfigError):
        SecretCache(sm, PREFIX, 300).get("b")


def test_not_found(sm) -> None:
    with pytest.raises(SecretNotFound):
        SecretCache(sm, PREFIX, 300).get("nope")


def test_cache_hit_avoids_second_call(sm) -> None:
    put(sm, "b", {"channel_secret": "s"})
    clock = FakeClock()
    cache = SecretCache(sm, PREFIX, 300, clock=clock)
    cache.get("b")
    sm.delete_secret(SecretId=f"{PREFIX}b", ForceDeleteWithoutRecovery=True)
    assert cache.get("b").channel_secret == "s"


def test_cache_expires_after_ttl(sm) -> None:
    put(sm, "b", {"channel_secret": "s"})
    clock = FakeClock()
    cache = SecretCache(sm, PREFIX, 300, clock=clock)
    cache.get("b")
    sm.update_secret(SecretId=f"{PREFIX}b", SecretString=json.dumps({"channel_secret": "s2"}))
    clock.now += 301
    assert cache.get("b").channel_secret == "s2"


def test_negative_cache(sm) -> None:
    clock = FakeClock()
    cache = SecretCache(sm, PREFIX, 300, clock=clock)
    with pytest.raises(SecretNotFound):
        cache.get("later")
    put(sm, "later", {"channel_secret": "s"})
    with pytest.raises(SecretNotFound):
        cache.get("later")
    clock.now += 301
    assert cache.get("later").channel_secret == "s"


def test_cache_bounded(sm) -> None:
    cache = SecretCache(sm, PREFIX, 300, max_entries=3)
    for i in range(10):
        with pytest.raises(SecretNotFound):
            cache.get(f"bot-{i}")
    assert len(cache._entries) <= 3
