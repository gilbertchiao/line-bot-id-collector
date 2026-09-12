import base64
import hashlib
import hmac
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from line_webhook_id_collector import handler as handler_mod
from line_webhook_id_collector.repository import TargetRepository, create_table

FIXTURES = Path(__file__).parent / "fixtures"
BOT = "alert-bot"
SECRET = "test-channel-secret"
BOOT = "U" + "a" * 32
G = "C" + "b" * 32
PREFIX = "line-webhook-id-collector/"


def sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def make_request(
    events: list | str, bot_id: str = BOT, signature: str | None = "auto", base64_body: bool = False
) -> dict:
    raw = (
        events.encode()
        if isinstance(events, str)
        else json.dumps({"destination": BOOT, "events": events}).encode()
    )
    body = base64.b64encode(raw).decode() if base64_body else raw.decode()
    headers = {}
    if signature == "auto":
        headers["x-line-signature"] = sign(raw)
    elif signature is not None:
        headers["x-line-signature"] = signature
    return {
        "pathParameters": {"bot_id": bot_id},
        "headers": headers,
        "body": body,
        "isBase64Encoded": base64_body,
        "requestContext": {"requestId": "req-1"},
    }


class FakeLineClient:
    def __init__(self) -> None:
        self.replies = []
        self.fail = False

    def reply(self, reply_token, texts):
        if self.fail:
            raise handler_mod.LineApiError("boom")
        self.replies.append((reply_token, texts))

    def get_group_name(self, group_id):
        return None

    def get_user_name(self, user_id):
        return None


@pytest.fixture
def env(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", "targets")
        monkeypatch.setenv("SECRET_NAME_PREFIX", PREFIX)
        resource = boto3.resource("dynamodb", region_name="ap-northeast-1")
        create_table(resource, "targets")
        sm = boto3.client("secretsmanager", region_name="ap-northeast-1")
        sm.create_secret(
            Name=f"{PREFIX}{BOT}",
            SecretString=json.dumps(
                {
                    "channel_secret": SECRET,
                    "channel_access_token": "tok",
                    "bootstrap_admin_user_id": BOOT,
                }
            ),
        )
        sm.create_secret(
            Name=f"{PREFIX}no-token", SecretString=json.dumps({"channel_secret": SECRET})
        )
        sm.create_secret(Name=f"{PREFIX}broken", SecretString="{}")
        line = FakeLineClient()
        handler_mod._deps = None
        monkeypatch.setattr(handler_mod, "_make_line_client", lambda token: line)
        yield {
            "repo": TargetRepository("targets", resource=resource),
            "line": line,
            "sm": sm,
        }
        handler_mod._deps = None


def call(request: dict) -> dict:
    return handler_mod.lambda_handler(request, None)


# ---- HTTP 層 ----


def test_empty_events_returns_200_without_writes(env) -> None:
    assert call(make_request([]))["statusCode"] == 200
    assert env["repo"].list_targets(BOT) == []


def test_bad_bot_id_format_404(env) -> None:
    assert call(make_request([], bot_id="Bad_ID"))["statusCode"] == 404


def test_unknown_bot_404(env) -> None:
    assert call(make_request([], bot_id="nope"))["statusCode"] == 404


def test_broken_secret_500(env) -> None:
    assert call(make_request([], bot_id="broken"))["statusCode"] == 500


def test_missing_signature_401_and_secret_not_read(env, monkeypatch) -> None:
    called = []
    original = env["sm"].get_secret_value

    def fake_get_secret_value(self, **kw):
        called.append(1)
        return original(**kw)

    monkeypatch.setattr(
        handler_mod,
        "_make_secrets_client",
        lambda: type("C", (), {"get_secret_value": fake_get_secret_value})(),
    )
    assert call(make_request([load("follow.json")], signature=None))["statusCode"] == 401
    assert called == []


def test_invalid_signature_401_no_writes(env) -> None:
    resp = call(make_request([load("follow.json")], signature=sign(b"other")))
    assert resp["statusCode"] == 401
    assert env["repo"].list_targets(BOT) == []


def test_invalid_json_400(env) -> None:
    assert call(make_request("{not json"))["statusCode"] == 400


def test_events_not_list_400(env) -> None:
    assert call(make_request('{"events": "x"}'))["statusCode"] == 400


def test_base64_body(env) -> None:
    resp = call(make_request([load("follow.json")], base64_body=True))
    assert resp["statusCode"] == 200
    assert env["repo"].get(BOT, BOOT)["status"] == "active"


# ---- 收集 ----


def test_multiple_events_all_stored(env) -> None:
    resp = call(make_request([load("follow.json"), load("join_group.json")]))
    assert resp["statusCode"] == 200
    assert {i["target_id"] for i in env["repo"].list_targets(BOT)} == {BOOT, G}


def test_invalid_event_entries_skipped(env) -> None:
    resp = call(make_request(["junk", {"no": "type"}, load("follow.json")]))
    assert resp["statusCode"] == 200
    assert env["repo"].get(BOT, BOOT) is not None


def test_unsupported_event_200(env) -> None:
    assert call(make_request([load("postback.json")]))["statusCode"] == 200


def test_unfollow_marks_inactive(env) -> None:
    call(make_request([load("follow.json")]))
    call(make_request([load("unfollow.json")]))
    assert env["repo"].get(BOT, BOOT)["status"] == "inactive"


def test_write_failure_returns_500_but_continues(env, monkeypatch) -> None:
    calls = {"n": 0}
    original = TargetRepository.upsert

    def flaky(self, bot_id, target, status, event_ts, event_type):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dynamo down")
        return original(self, bot_id, target, status, event_ts, event_type)

    monkeypatch.setattr(TargetRepository, "upsert", flaky)
    resp = call(make_request([load("follow.json"), load("join_group.json")]))
    assert resp["statusCode"] == 500
    assert env["repo"].get(BOT, G) is not None


def test_malformed_member_joined_does_not_502(env) -> None:
    """`joined` 欄位非預期結構（例如字串而非 dict）不應讓整個 webhook 失敗。"""
    ev = load("member_joined.json")
    ev["joined"] = "x"
    resp = call(make_request([ev, load("follow.json")]))
    assert resp["statusCode"] == 200
    assert env["repo"].get(BOT, BOOT) is not None


def test_event_analysis_failure_is_isolated(env, monkeypatch) -> None:
    """`analyze_event` 對單一事件拋出未預期例外時，只記 log 並跳過，其餘事件正常處理。"""
    original_analyze = handler_mod.analyze_event
    calls = {"n": 0}

    def flaky_analyze(raw_event, now_ms):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original_analyze(raw_event, now_ms)

    monkeypatch.setattr(handler_mod, "analyze_event", flaky_analyze)
    resp = call(make_request([load("join_group.json"), load("follow.json")]))
    assert resp["statusCode"] == 200
    assert env["repo"].get(BOT, BOOT) is not None


# ---- 指令 ----


def test_admin_id_command_replies(env) -> None:
    resp = call(make_request([load("message_user.json")]))
    assert resp["statusCode"] == 200
    assert env["line"].replies == [("rt-5", [f"LINE Target\n\nType: user\nUser ID:\n{BOOT}"])]


def _replied_log_record(captured_err: str) -> dict:
    records = [json.loads(line) for line in captured_err.strip().splitlines() if line.strip()]
    replied = [r for r in records if r.get("result") == "replied"]
    assert len(replied) == 1
    return replied[0]


def test_id_command_logs_bare_keyword(env, capsys) -> None:
    call(make_request([load("message_user.json")]))
    captured_err = capsys.readouterr().err
    assert _replied_log_record(captured_err)["command"] == "/id"


def test_admin_add_command_logs_subcommand_without_id(env, capsys) -> None:
    ev = load("message_user.json")
    target_user = "U" + "9" * 32
    ev["message"]["text"] = f"/admin add {target_user}"
    call(make_request([ev]))
    captured_err = capsys.readouterr().err
    assert _replied_log_record(captured_err)["command"] == "/admin add"
    assert target_user not in captured_err


def test_non_admin_silent(env) -> None:
    ev = load("message_user.json")
    ev["source"]["userId"] = "U" + "9" * 32
    call(make_request([ev]))
    assert env["line"].replies == []
    assert env["repo"].get(BOT, "U" + "9" * 32) is not None


def test_group_command_silent(env) -> None:
    call(make_request([load("message_group_with_user.json")]))
    assert env["line"].replies == []


def test_redelivery_collects_but_does_not_reply(env) -> None:
    call(make_request([load("message_user_redelivery.json")]))
    assert env["line"].replies == []
    assert env["repo"].get(BOT, BOOT) is not None


def test_no_token_silent(env) -> None:
    resp = call(make_request([load("message_user.json")], bot_id="no-token"))
    assert resp["statusCode"] == 200
    assert env["line"].replies == []


def test_reply_failure_still_200(env) -> None:
    env["line"].fail = True
    assert call(make_request([load("message_user.json")]))["statusCode"] == 200


def test_command_runs_after_all_events_collected(env, monkeypatch) -> None:
    order = []
    original_upsert = TargetRepository.upsert
    original_reply = env["line"].reply

    def tracking(self, *a, **kw):
        order.append("upsert")
        return original_upsert(self, *a, **kw)

    def reply(reply_token, texts):
        order.append("reply")
        return original_reply(reply_token, texts)

    monkeypatch.setattr(TargetRepository, "upsert", tracking)
    monkeypatch.setattr(env["line"], "reply", reply)
    call(make_request([load("message_user.json"), load("join_group.json")]))
    assert order == ["upsert", "upsert", "reply"]
