"""Literal human display of repository-controlled labels and diagnostics."""

from __future__ import annotations


def display_text(value: object) -> str:
    """Show nonprinting characters as visible escapes, without changing stored identities."""

    text = str(value)
    if not text or text.isprintable():
        return text

    parts: list[str] = []
    short = {"\n": r"\n", "\r": r"\r", "\t": r"\t"}
    start = 0
    for index, char in enumerate(text):
        if char.isprintable():
            continue
        if start < index:
            parts.append(text[start:index])
        start = index + 1
        if char in short:
            parts.append(short[char])
        else:
            code = ord(char)
            if code <= 0xFF:
                parts.append(f"\\x{code:02x}")
            elif code <= 0xFFFF:
                parts.append(f"\\u{code:04x}")
            else:
                parts.append(f"\\U{code:08x}")
    parts.append(text[start:])
    return "".join(parts)
