from __future__ import annotations


def safe_terminal_field(value: object) -> str:
    text = str(value)
    escaped: list[str] = []
    for char in text:
        codepoint = ord(char)
        if char == "\n":
            escaped.append("\\n")
        elif char == "\r":
            escaped.append("\\r")
        elif char == "\t":
            escaped.append("\\t")
        elif (0 <= codepoint < 32) or codepoint == 127:
            escaped.append(f"\\x{codepoint:02x}")
        elif 128 <= codepoint <= 159:
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(char)
    return "".join(escaped)
