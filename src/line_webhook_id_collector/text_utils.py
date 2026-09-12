"""LINE 文字訊息的長度計算與切段。LINE 以 UTF-16 code unit 計算長度。"""

from __future__ import annotations

from collections.abc import Callable

LINE_TEXT_MAX = 5000
LINE_REPLY_MAX_MESSAGES = 5


def utf16_len(s: str) -> int:
    """計算字串以 UTF-16 code unit 表示時的長度（LINE 訊息長度即以此計算）。"""
    return len(s.encode("utf-16-le")) // 2


def _hard_split(block: str, max_len: int) -> list[str]:
    """單一 block 超過上限時依 code point 硬切（不會切在 surrogate pair 中間）。

    以累加目前長度的方式計算（每個字元只呼叫一次 utf16_len），
    避免對逐漸變長的 current 重新編碼、造成 O(max_len^2) 的效能問題。
    """
    pieces: list[str] = []
    current: list[str] = []
    current_len = 0
    for ch in block:
        ch_len = utf16_len(ch)
        if current_len + ch_len > max_len:
            pieces.append("".join(current))
            current = [ch]
            current_len = ch_len
        else:
            current.append(ch)
            current_len += ch_len
    if current:
        pieces.append("".join(current))
    return pieces


def pack_blocks(
    blocks: list[str],
    max_len: int = LINE_TEXT_MAX,
    max_messages: int = LINE_REPLY_MAX_MESSAGES,
    trailer: Callable[[int], str] | None = None,
) -> list[str]:
    """把 block 用換行串接成最多 max_messages 則訊息，不在 block 中間切。

    放不下時呼叫 trailer(remaining_count) 取得尾註並附在最後一則末尾，
    尾註長度納入計算；單一 block 超過 max_len 時會先硬切。
    """
    expanded: list[str] = []
    for block in blocks:
        expanded.extend(_hard_split(block, max_len) if utf16_len(block) > max_len else [block])

    messages: list[str] = []
    current = ""
    index = 0
    while index < len(expanded):
        block = expanded[index]
        candidate = block if not current else f"{current}\n{block}"
        if utf16_len(candidate) <= max_len:
            current = candidate
            index += 1
            continue
        messages.append(current)
        current = ""
        if len(messages) == max_messages:
            break
    else:
        if current:
            messages.append(current)
        return messages

    # 走到這裡代表已達 max_messages 但還有 block 沒放
    remaining = len(expanded) - index
    if trailer is None:
        return messages
    # 從最後一則的尾端移除 block，直到放得下尾註
    last_blocks = messages[-1].split("\n")
    while last_blocks:
        note = trailer(remaining)
        candidate = "\n".join([*last_blocks, note])
        if utf16_len(candidate) <= max_len:
            messages[-1] = candidate
            return messages
        last_blocks.pop()
        remaining += 1
    # 就連空的最後一則也放不下尾註：以 UTF-16 長度為準硬切尾註文字（不可用 code point 切片，
    # 否則含 emoji 等 surrogate pair 字元時可能超過 max_len）。
    note_pieces = _hard_split(trailer(remaining), max_len)
    messages[-1] = note_pieces[0] if note_pieces else ""
    return messages
