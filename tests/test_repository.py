import boto3
import pytest
from moto import mock_aws

from line_webhook_id_collector.events import Target
from line_webhook_id_collector.repository import TargetRepository

from .dynamodb_helpers import create_table

BOT = "alert-bot"
U = "U" + "a" * 32
G = "C" + "b" * 32
T1 = 1789194900000
T2 = 1789195000000


@pytest.fixture
def repo():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="ap-northeast-1")
        create_table(resource, "targets")
        yield TargetRepository("targets", resource=resource)


def test_insert_new_target(repo) -> None:
    assert repo.upsert(BOT, Target(U, "user"), "active", T1, "follow") == "stored"
    item = repo.get(BOT, U)
    assert item["bot_id"] == BOT
    assert item["target_id"] == U
    assert item["target_type"] == "user"
    assert item["status"] == "active"
    assert item["first_seen_at"] == "2026-09-12T06:35:00Z"
    assert item["last_seen_at"] == "2026-09-12T06:35:00Z"
    assert int(item["last_event_ts"]) == T1
    assert item["last_event_type"] == "follow"
    assert "role" not in item


def test_update_preserves_first_seen(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "active", T1, "follow")
    repo.upsert(BOT, Target(U, "user"), "active", T2, "message")
    item = repo.get(BOT, U)
    assert item["first_seen_at"] == "2026-09-12T06:35:00Z"
    assert item["last_seen_at"] == "2026-09-12T06:36:40Z"
    assert item["last_event_type"] == "message"


def test_duplicate_event_is_harmless(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "active", T1, "follow")
    assert repo.upsert(BOT, Target(U, "user"), "active", T1, "follow") == "stored"
    assert len(repo.list_targets(BOT)) == 1


def test_stale_event_rejected(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "inactive", T2, "unfollow")
    assert repo.upsert(BOT, Target(U, "user"), "active", T1, "message") == "stale"
    assert repo.get(BOT, U)["status"] == "inactive"


def test_mark_inactive_then_reactivate(repo) -> None:
    repo.upsert(BOT, Target(G, "group"), "active", T1, "join")
    assert repo.upsert(BOT, Target(G, "group"), "inactive", T2, "leave") == "marked_inactive"
    assert repo.get(BOT, G)["status"] == "inactive"
    repo.upsert(BOT, Target(G, "group"), "active", T2 + 1, "join")
    assert repo.get(BOT, G)["status"] == "active"


def test_upsert_does_not_touch_role(repo) -> None:
    repo.add_admin(BOT, U, T1)
    repo.upsert(BOT, Target(U, "user"), "active", T2, "message")
    assert repo.get(BOT, U)["role"] == "admin"


def test_add_admin_creates_full_record(repo) -> None:
    repo.add_admin(BOT, U, T1)
    item = repo.get(BOT, U)
    assert item["role"] == "admin"
    assert item["target_type"] == "user"
    assert item["status"] == "active"
    assert item["first_seen_at"] == "2026-09-12T06:35:00Z"
    assert item["last_event_type"] == "admin_add"


def test_add_admin_keeps_existing_observation(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "active", T1, "follow")
    repo.add_admin(BOT, U, T2)
    item = repo.get(BOT, U)
    assert item["last_event_type"] == "follow"
    assert item["role"] == "admin"


def test_remove_admin(repo) -> None:
    repo.add_admin(BOT, U, T1)
    assert repo.remove_admin(BOT, U) is True
    assert "role" not in repo.get(BOT, U)


def test_remove_admin_missing_does_not_create(repo) -> None:
    assert repo.remove_admin(BOT, U) is False
    assert repo.get(BOT, U) is None


def test_remove_admin_non_admin_returns_false(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "active", T1, "follow")
    assert repo.remove_admin(BOT, U) is False


def test_get_missing(repo) -> None:
    assert repo.get(BOT, "U" + "f" * 32) is None


def test_list_is_scoped_by_bot(repo) -> None:
    repo.upsert(BOT, Target(U, "user"), "active", T1, "follow")
    repo.upsert("other", Target(G, "group"), "active", T1, "join")
    ids = {i["target_id"] for i in repo.list_targets(BOT)}
    assert ids == {U}


def test_list_follows_pagination(repo, monkeypatch) -> None:
    # 用假的分頁回應驗證 LastEvaluatedKey 有被追蹤（真的寫到 1MB 太慢）
    pages = [
        {"Items": [{"target_id": "a"}], "LastEvaluatedKey": {"bot_id": BOT, "target_id": "a"}},
        {"Items": [{"target_id": "b"}]},
    ]
    seen_kwargs: list[dict] = []

    def fake_query(**kwargs):
        seen_kwargs.append(kwargs)
        return pages[len(seen_kwargs) - 1]

    monkeypatch.setattr(repo._table, "query", fake_query)
    items = repo.list_targets(BOT)
    assert [i["target_id"] for i in items] == ["a", "b"]
    assert "ExclusiveStartKey" not in seen_kwargs[0]
    assert seen_kwargs[1]["ExclusiveStartKey"] == {"bot_id": BOT, "target_id": "a"}
