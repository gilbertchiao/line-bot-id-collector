import pytest

from line_webhook_id_collector import line_client as mod
from line_webhook_id_collector.line_client import LineApiError, LineClient


class FakeApi:
    def __init__(self) -> None:
        self.replies: list = []
        self.fail_reply = False
        self.fail_lookup = False

    def reply_message(self, request, _request_timeout=None):
        if self.fail_reply:
            raise RuntimeError("boom")
        self.replies.append(request)

    def get_group_summary(self, group_id, _request_timeout=None):
        if self.fail_lookup:
            raise RuntimeError("boom")
        return type("R", (), {"group_name": f"group:{group_id}"})()

    def get_profile(self, user_id, _request_timeout=None):
        if self.fail_lookup:
            raise RuntimeError("boom")
        return type("R", (), {"display_name": f"user:{user_id}"})()


@pytest.fixture
def client(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(LineClient, "_build_api", lambda self: fake)
    c = LineClient("token")
    c.fake = fake
    return c


def test_reply_sends_all_texts_in_one_call(client) -> None:
    client.reply("rt", ["a", "b"])
    assert len(client.fake.replies) == 1
    request = client.fake.replies[0]
    assert request.reply_token == "rt"
    assert [m.text for m in request.messages] == ["a", "b"]


def test_reply_failure_raises(client) -> None:
    client.fake.fail_reply = True
    with pytest.raises(LineApiError):
        client.reply("rt", ["a"])


def test_lookups(client) -> None:
    assert client.get_group_name("C1") == "group:C1"
    assert client.get_user_name("U1") == "user:U1"


def test_lookup_failure_returns_none(client) -> None:
    client.fake.fail_lookup = True
    assert client.get_group_name("C1") is None
    assert client.get_user_name("U1") is None


def test_real_api_is_constructed() -> None:
    api = LineClient("token")._build_api()
    assert hasattr(api, "reply_message")
    assert mod.MessagingApi is not None
