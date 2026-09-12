from line_webhook_id_collector.text_utils import pack_blocks, utf16_len


def test_utf16_len_counts_surrogate_pairs() -> None:
    assert utf16_len("abc") == 3
    assert utf16_len("中文") == 2
    assert utf16_len("😀") == 2


def test_pack_single_message() -> None:
    assert pack_blocks(["a", "b"], max_len=100) == ["a\nb"]


def test_pack_splits_on_block_boundary() -> None:
    blocks = ["x" * 40, "y" * 40, "z" * 40]
    out = pack_blocks(blocks, max_len=85)
    assert out == ["x" * 40 + "\n" + "y" * 40, "z" * 40]


def test_pack_respects_utf16() -> None:
    out = pack_blocks(["😀" * 3, "😀" * 3], max_len=7)
    assert out == ["😀" * 3, "😀" * 3]


def test_pack_trailer_when_overflow() -> None:
    blocks = [f"item{i}" for i in range(20)]  # 每個 5~6 字
    out = pack_blocks(blocks, max_len=20, max_messages=2, trailer=lambda n: f"+{n} more")
    assert len(out) == 2
    assert out[-1].endswith(" more")
    shown = sum(msg.count("item") for msg in out)
    remaining = int(out[-1].rsplit("+", 1)[1].split()[0])
    assert shown + remaining == 20
    for msg in out:
        assert utf16_len(msg) <= 20


def test_pack_empty() -> None:
    assert pack_blocks([]) == []


def test_pack_hard_splits_oversized_block() -> None:
    out = pack_blocks(["a" * 12], max_len=5)
    assert out == ["aaaaa", "aaaaa", "aa"]
