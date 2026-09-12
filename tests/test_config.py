import pytest

from line_webhook_id_collector.config import ConfigError, load_settings


def test_defaults() -> None:
    s = load_settings({"DYNAMODB_TABLE_NAME": "t"})
    assert s.table_name == "t"
    assert s.secret_name_prefix == "line-webhook-id-collector/"
    assert s.secret_cache_ttl_seconds == 300
    assert s.name_lookup_limit == 50
    assert s.name_lookup_budget_seconds == 8.0
    assert s.log_level == "INFO"
    assert s.log_target_ids is False


def test_overrides() -> None:
    s = load_settings(
        {
            "DYNAMODB_TABLE_NAME": "t",
            "SECRET_NAME_PREFIX": "custom/",
            "SECRET_CACHE_TTL_SECONDS": "10",
            "NAME_LOOKUP_LIMIT": "5",
            "NAME_LOOKUP_BUDGET_SECONDS": "2.5",
            "LOG_LEVEL": "debug",
            "LOG_TARGET_IDS": "TRUE",
        }
    )
    assert s.secret_name_prefix == "custom/"
    assert s.secret_cache_ttl_seconds == 10
    assert s.name_lookup_limit == 5
    assert s.name_lookup_budget_seconds == 2.5
    assert s.log_level == "DEBUG"
    assert s.log_target_ids is True


def test_missing_table_name() -> None:
    with pytest.raises(ConfigError):
        load_settings({})
