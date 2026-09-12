import pytest

from line_webhook_id_collector import line_client as mod
from line_webhook_id_collector.line_client import LineApiError, LineClient


class FakeApiClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeApi:
    def __init__(self) -> None:
        self.replies: list = []
        self.fail_reply = False
        self.fail_lookup = False
        self.api_client = FakeApiClient()

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
    with pytest.raises(LineApiError) as exc_info:
        client.reply("rt", ["a"])
    # LINE SDK 例外的 str(exc) 可能夾帶回應 body；LineApiError 只保留類別名稱，
    # 不可洩漏原始例外訊息內容。
    assert "boom" not in str(exc_info.value)
    assert "RuntimeError" in str(exc_info.value)


def test_reply_failure_does_not_leak_response_body(client) -> None:
    """模擬 LINE SDK 的 ApiException：str(exc) 含回應 body，只允許保留 status。"""

    class FakeApiException(Exception):
        def __init__(self) -> None:
            super().__init__('400 Bad Request: {"message":"invalid reply token: secret-leak"}')
            self.status = 400

    def raise_it(*args, **kwargs):
        raise FakeApiException

    client.fake.reply_message = raise_it
    with pytest.raises(LineApiError) as exc_info:
        client.reply("rt", ["a"])
    message = str(exc_info.value)
    assert "secret-leak" not in message
    assert "FakeApiException" in message
    assert "400" in message


def test_reply_failure_with_falsy_status_is_still_included(client) -> None:
    """`status` 為 0（falsy 但確實存在）時，仍應出現在 LineApiError 訊息中；
    原本 `if status` 會把 `status=0` 誤判為「沒有 status」而漏掉。"""

    class FakeApiExceptionZeroStatus(Exception):
        def __init__(self) -> None:
            super().__init__("boom")
            self.status = 0

    def raise_it(*args, **kwargs):
        raise FakeApiExceptionZeroStatus

    client.fake.reply_message = raise_it
    with pytest.raises(LineApiError) as exc_info:
        client.reply("rt", ["a"])
    assert "status=0" in str(exc_info.value)


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


def test_close_closes_underlying_api_client_when_created(client) -> None:
    client.reply("rt", ["a"])  # 觸發 api property，建立底層 api_client
    client.close()
    assert client.fake.api_client.closed is True


def test_close_is_a_noop_when_api_never_built(client) -> None:
    """從未呼叫過任何 API（`api` property 未被存取過）時，close() 不應報錯。"""
    client.close()
    assert client.fake.api_client.closed is False


def test_context_manager_closes_on_exit(client) -> None:
    with client as c:
        c.reply("rt", ["a"])
    assert client.fake.api_client.closed is True
