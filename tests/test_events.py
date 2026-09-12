import json
from pathlib import Path

from line_webhook_id_collector.events import Target, analyze_event, iso_from_ms

FIXTURES = Path(__file__).parent / "fixtures"
U = "U" + "a" * 32
U2 = "U" + "d" * 32
G = "C" + "b" * 32
R = "R" + "c" * 32
NOW = 1789195399000


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_iso_from_ms() -> None:
    assert iso_from_ms(1789194900000) == "2026-09-12T06:35:00Z"


def test_follow() -> None:
    out = analyze_event(load("follow.json"), NOW)
    assert out.event_type == "follow"
    assert out.source_type == "user"
    assert out.active_targets == (Target(U, "user"),)
    assert out.inactive_targets == ()
    assert out.event_ts == 1789194900000
    assert out.command is None


def test_unfollow_marks_inactive_only() -> None:
    out = analyze_event(load("unfollow.json"), NOW)
    assert out.active_targets == ()
    assert out.inactive_targets == (Target(U, "user"),)


def test_join_group_and_room() -> None:
    assert analyze_event(load("join_group.json"), NOW).active_targets == (Target(G, "group"),)
    assert analyze_event(load("join_room.json"), NOW).active_targets == (Target(R, "room"),)


def test_leave_marks_group_inactive() -> None:
    out = analyze_event(load("leave_group.json"), NOW)
    assert out.active_targets == ()
    assert out.inactive_targets == (Target(G, "group"),)


def test_member_joined_collects_members_and_group() -> None:
    out = analyze_event(load("member_joined.json"), NOW)
    assert set(out.active_targets) == {Target(G, "group"), Target(U, "user"), Target(U2, "user")}


def test_message_user_yields_command_candidate() -> None:
    out = analyze_event(load("message_user.json"), NOW)
    assert out.active_targets == (Target(U, "user"),)
    assert out.command is not None
    assert out.command.user_id == U
    assert out.command.text == "/id"
    assert out.command.reply_token == "rt-5"


def test_message_group_with_user() -> None:
    out = analyze_event(load("message_group_with_user.json"), NOW)
    assert set(out.active_targets) == {Target(G, "group"), Target(U, "user")}
    assert out.command is None


def test_message_group_without_user() -> None:
    out = analyze_event(load("message_group_without_user.json"), NOW)
    assert out.active_targets == (Target(G, "group"),)


def test_message_room() -> None:
    out = analyze_event(load("message_room.json"), NOW)
    assert set(out.active_targets) == {Target(R, "room"), Target(U, "user")}
    assert out.command is None


def test_non_text_message_has_no_command() -> None:
    assert analyze_event(load("message_user_sticker.json"), NOW).command is None


def test_redelivery_flag() -> None:
    out = analyze_event(load("message_user_redelivery.json"), NOW)
    assert out.is_redelivery is True
    assert out.command is not None  # 由 handler 決定不執行


def test_unsupported_event_still_extracts_source() -> None:
    out = analyze_event(load("postback.json"), NOW)
    assert out.event_type == "postback"
    assert out.active_targets == (Target(U, "user"),)


def test_event_without_source() -> None:
    out = analyze_event(load("account_link_no_source.json"), NOW)
    assert out.active_targets == ()
    assert out.source_type is None


def test_missing_timestamp_uses_now() -> None:
    event = load("follow.json")
    del event["timestamp"]
    assert analyze_event(event, NOW).event_ts == NOW


def test_invalid_events_return_none() -> None:
    assert analyze_event("nope", NOW) is None
    assert analyze_event({"source": {}}, NOW) is None
    assert analyze_event({"type": 5}, NOW) is None
