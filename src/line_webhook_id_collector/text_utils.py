"""LINE 文字訊息的長度計算與切段。LINE 以 UTF-16 code unit 計算長度。"""

from __future__ import annotations

from collections.abc import Callable

LINE_TEXT_MAX = 5000
LINE_REPLY_MAX_MESSAGES = 5


def utf16_len(s: str) -> int:
    """計算字串以 UTF-16 code unit 表示時的長度（LINE 訊息長度即以此計算）。"""
    return len(s.encode("utf-16-le")) // 2


def _hard_split(block: str, max_len: int) -> list[str]:
    """單一 block 超過上限時依 code point 硬切（不會切在 surrogate pair 中間）。"""
    pieces: list[str] = []
    current = ""
    for ch in block:
        if utf16_len(current + ch) > max_len:
            pieces.append(current)
            current = ch
        else:
            current += ch
    if current:
        pieces.append(current)
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
    messages[-1] = trailer(remaining)[:max_len]
    return messages
