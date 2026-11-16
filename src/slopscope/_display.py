"""Literal human display of repository-controlled labels and diagnostics."""

from __future__ import annotations


def display_text(value: object) -> str:
    """Show nonprinting characters as visible escapes, without changing stored identities."""

    parts: list[str] = []
    short = {"\n": r"\n", "\r": r"\r", "\t": r"\t"}
    for char in str(value):
        if char.isprintable():
            parts.append(char)
        elif char in short:
            parts.append(short[char])
        else:
            code = ord(char)
            if code <= 0xFF:
                parts.append(f"\\x{code:02x}")
            elif code <= 0xFFFF:
                parts.append(f"\\u{code:04x}")
            else:
                parts.append(f"\\U{code:08x}")
    return "".join(parts)
