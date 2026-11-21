"""Parse a submitted input line into a slash-command name (and optional args)."""

from __future__ import annotations


def parse_with_args(input_text: str) -> tuple[str, str, bool]:
    # 返回 (name, args, is_slash)。空 name + is_slash 表示退化输入。
    text = input_text.strip()
    if not text.startswith("/"):
        return "", "", False

    body = text[1:]
    # 纯 "/" 或斜杠后紧跟空白：视为退化输入，走未命中分支。
    if not body or body[0].isspace():
        return "", "", True

    parts = body.split(maxsplit=1)
    name = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    return name, args, True


def parse(input_text: str) -> tuple[str, bool]:
    # ch10 兼容入口：带尾随参数时 name 为空，交由 dispatcher 走未命中分支。
    name, args, is_slash = parse_with_args(input_text)
    if args:
        return "", is_slash
    return name, is_slash
