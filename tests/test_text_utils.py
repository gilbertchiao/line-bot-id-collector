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


def test_pack_hard_splits_oversized_block_with_emoji() -> None:
    block = "😀" * 30
    out = pack_blocks([block], max_len=7, max_messages=100)
    # 每片都在 max_len 之內，且合併回去要等於原字串（沒有字元遺失或多出）
    assert "".join(out) == block
    for piece in out:
        assert utf16_len(piece) <= 7
        # 沒有切在 surrogate pair 中間：能正確以 UTF-16 編碼/解碼還原
        assert piece.encode("utf-16-le").decode("utf-16-le") == piece


def test_pack_trailer_alone_overflow_truncated_by_utf16() -> None:
    # trailer 本身（含 emoji）就超過 max_len，且已無 block 可從最後一則移除騰出空間
    out = pack_blocks(
        ["ab", "cd", "ef"],
        max_len=5,
        max_messages=1,
        trailer=lambda n: "😀" * 10,
    )
    assert len(out) == 1
    assert utf16_len(out[-1]) <= 5
    assert out[-1].encode("utf-16-le").decode("utf-16-le") == out[-1]
