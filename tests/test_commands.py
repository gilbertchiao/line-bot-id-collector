from line_webhook_id_collector.commands import CommandContext, execute_command, is_admin
from line_webhook_id_collector.config import Settings
from line_webhook_id_collector.secrets import BotSecret
from line_webhook_id_collector.text_utils import utf16_len

BOT = "alert-bot"
BOOT = "U" + "0" * 32
ADMIN2 = "U" + "1" * 32
USER = "U" + "2" * 32
G1 = "C" + "a" * 32
G2 = "C" + "b" * 32
R1 = "R" + "c" * 32
NOW = 1789195399000


class FakeRepo:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def _put(self, target_id, target_type, status="active", ts=1, role=None):
        item = {
            "bot_id": BOT,
            "target_id": target_id,
            "target_type": target_type,
            "status": status,
            "last_event_ts": ts,
            "first_seen_at": "x",
            "last_seen_at": "y",
            "last_event_type": "message",
        }
        if role:
            item["role"] = role
        self.items[target_id] = item

    def get(self, bot_id, target_id):
        return self.items.get(target_id)

    def list_targets(self, bot_id):
        return list(self.items.values())

    def add_admin(self, bot_id, user_id, now_ms):
        if user_id in self.items:
            self.items[user_id]["role"] = "admin"
        else:
            self._put(user_id, "user", role="admin")

    def remove_admin(self, bot_id, user_id):
        item = self.items.get(user_id)
        if not item or item.get("role") != "admin":
            return False
        del item["role"]
        return True


class FakeClient:
    def __init__(self) -> None:
        self.names = {}
        self.lookups = 0
        self.delay = 0.0
        self.time = [0.0]

    def get_group_name(self, group_id):
        self.lookups += 1
        self.time[0] += self.delay
        return self.names.get(group_id)

    def get_user_name(self, user_id):
        self.lookups += 1
        self.time[0] += self.delay
        return self.names.get(user_id)


def make_ctx(repo=None, client=None, sender=BOOT, token="tok", limit=50, budget=8.0):
    repo = repo or FakeRepo()
    client = client if client is not None else FakeClient()
    return CommandContext(
        bot_id=BOT,
        settings=Settings(
            table_name="t", name_lookup_limit=limit, name_lookup_budget_seconds=budget
        ),
        secret=BotSecret(
            channel_secret="s", channel_access_token=token, bootstrap_admin_user_id=BOOT
        ),
        repo=repo,
        client=client,
        sender_id=sender,
        now_ms=NOW,
        clock=lambda: client.time[0],
    )


# ---- is_admin ----


def test_is_admin_bootstrap() -> None:
    repo = FakeRepo()
    secret = BotSecret("s", "t", BOOT)
    assert is_admin(repo, BOT, secret, BOOT) is True
    assert is_admin(repo, BOT, secret, USER) is False


def test_is_admin_from_db_regardless_of_status() -> None:
    repo = FakeRepo()
    repo._put(ADMIN2, "user", status="inactive", role="admin")
    assert is_admin(repo, BOT, BotSecret("s", "t", None), ADMIN2) is True


# ---- /id, /help, unknown ----


def test_id() -> None:
    out = execute_command("/id", make_ctx())
    assert out == [f"LINE Target\n\nType: user\nUser ID:\n{BOOT}"]


def test_keyword_case_and_whitespace() -> None:
    assert execute_command("  /ID  ", make_ctx()) is not None


def test_unknown_is_silent() -> None:
    assert execute_command("hello", make_ctx()) is None
    assert execute_command("/nope", make_ctx()) is None
    assert execute_command("", make_ctx()) is None


def test_help() -> None:
    out = execute_command("/help", make_ctx())
    assert out and "/list" in out[0] and "/admin add" in out[0]


# ---- /list ----


def test_list_empty() -> None:
    assert execute_command("/list", make_ctx()) == ["No targets found."]


def test_list_groups_sorted_and_named() -> None:
    repo = FakeRepo()
    repo._put(G1, "group", ts=1)
    repo._put(G2, "group", ts=2)
    client = FakeClient()
    client.names[G2] = "Newer"
    out = execute_command("/list", make_ctx(repo, client))
    text = out[0]
    assert text.startswith("Groups (2)\n- Newer\n  " + G2 + "\n- (unknown)\n  " + G1)


def test_list_sections_and_admin_marker() -> None:
    repo = FakeRepo()
    repo._put(G1, "group")
    repo._put(R1, "room")
    repo._put(USER, "user")
    repo._put(ADMIN2, "user", role="admin")
    client = FakeClient()
    client.names[USER] = "王小明"
    client.names[ADMIN2] = "李小華"
    out = execute_command("/list", make_ctx(repo, client))
    text = out[0]
    assert "Groups (1)" in text
    assert "Rooms (1)\n- " + R1 in text
    assert "Users (2)" in text
    assert "- 王小明\n  " + USER in text
    assert "- 李小華 [admin]\n  " + ADMIN2 in text
    assert client.lookups == 3  # room 不查名稱


def test_list_filters_inactive_by_default_and_all_shows_it() -> None:
    repo = FakeRepo()
    repo._put(G1, "group", status="inactive")
    assert execute_command("/list", make_ctx(repo)) == ["No targets found."]
    out = execute_command("/list all", make_ctx(repo))
    assert "[inactive]" in out[0]


def test_list_type_filters() -> None:
    repo = FakeRepo()
    repo._put(G1, "group")
    repo._put(USER, "user")
    assert "Users" not in execute_command("/list groups", make_ctx(repo))[0]
    assert "Groups" not in execute_command("/list users", make_ctx(repo))[0]
    assert execute_command("/list rooms", make_ctx(repo)) == ["No targets found."]


def test_list_bad_arg() -> None:
    assert execute_command("/list foo", make_ctx()) == ["Usage: /list [groups|users|rooms|all]"]


def test_list_lookup_limit() -> None:
    repo = FakeRepo()
    for i in range(10):
        repo._put(f"C{i:032x}", "group", ts=i)
    client = FakeClient()
    execute_command("/list", make_ctx(repo, client, limit=3))
    assert client.lookups == 3


def test_list_lookup_budget() -> None:
    repo = FakeRepo()
    for i in range(10):
        repo._put(f"C{i:032x}", "group", ts=i)
    client = FakeClient()
    client.delay = 3.0
    execute_command("/list", make_ctx(repo, client, budget=8.0))
    assert client.lookups == 3  # 0→3→6→9 秒，第 4 次前已超過預算


def test_list_chunks_and_trailer() -> None:
    repo = FakeRepo()
    for i in range(700):
        repo._put(f"C{i:032x}", "group", ts=i)
    out = execute_command("/list", make_ctx(repo, limit=0))
    assert 1 < len(out) <= 5
    for msg in out:
        assert utf16_len(msg) <= 5000
    assert "aws dynamodb query" in out[-1]
    assert "--table-name t" in out[-1]
    assert f'"{BOT}"' in out[-1]


# ---- /admin ----


def test_admin_list_union_dedup() -> None:
    repo = FakeRepo()
    repo._put(BOOT, "user", role="admin")
    repo._put(ADMIN2, "user", role="admin")
    client = FakeClient()
    client.names[ADMIN2] = "Dev2"
    out = execute_command("/admin list", make_ctx(repo, client))
    text = out[0]
    assert text.count(BOOT) == 1
    assert "(bootstrap)" in text
    assert "- Dev2\n  " + ADMIN2 in text


def test_admin_add_new_and_existing() -> None:
    repo = FakeRepo()
    assert execute_command(f"/admin add {ADMIN2}", make_ctx(repo)) == [f"Added admin: {ADMIN2}"]
    assert repo.items[ADMIN2]["role"] == "admin"
    assert execute_command(f"/admin add {ADMIN2}", make_ctx(repo)) == [f"Already admin: {ADMIN2}"]


def test_admin_add_bootstrap() -> None:
    assert execute_command(f"/admin add {BOOT}", make_ctx()) == ["Already bootstrap admin."]


def test_admin_add_invalid_id_preserves_case_check() -> None:
    # 註：不可直接用 ADMIN2.upper()，因為 ADMIN2 只含數字（無 a-f 字母），
    # upper() 對其為 no-op，永遠等於原字串，無法測出「大小寫敏感」的行為。
    # 改用含 hex 字母的 ID，使其大寫化後確實變成不合法格式。
    valid_lowercase_id = "U" + "a" * 32
    assert execute_command("/admin add UABC", make_ctx()) == ["Invalid user ID format."]
    assert execute_command(f"/admin add {valid_lowercase_id.upper()}", make_ctx()) == [
        "Invalid user ID format."
    ]


def test_admin_remove() -> None:
    repo = FakeRepo()
    repo._put(ADMIN2, "user", role="admin")
    assert execute_command(f"/admin remove {ADMIN2}", make_ctx(repo)) == [
        f"Removed admin: {ADMIN2}"
    ]
    assert execute_command(f"/admin remove {ADMIN2}", make_ctx(repo)) == [f"Not an admin: {ADMIN2}"]


def test_admin_remove_bootstrap_and_self() -> None:
    repo = FakeRepo()
    repo._put(ADMIN2, "user", role="admin")
    assert execute_command(f"/admin remove {BOOT}", make_ctx(repo, sender=ADMIN2)) == [
        "Cannot remove bootstrap admin."
    ]
    assert execute_command(f"/admin remove {ADMIN2}", make_ctx(repo, sender=ADMIN2)) == [
        "Cannot remove yourself."
    ]


def test_admin_usage() -> None:
    out = execute_command("/admin", make_ctx())
    assert out == ["Usage: /admin list | /admin add <userId> | /admin remove <userId>"]
    assert execute_command("/admin add", make_ctx()) == out
