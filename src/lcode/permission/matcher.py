"""Structured matchers shared by permission rules and hook conditions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


class Matcher(Protocol):
    # 四种匹配类型统一接口；实例不可变，运行期只调用 match / __str__。
    def match(self, value: str) -> bool: ...

    def __str__(self) -> str: ...


@dataclass(frozen=True)
class ExactMatcher:
    # 整串相等。"=git status" 只命中完全一致的命令。
    value: str

    def match(self, value: str) -> bool:
        return value == self.value

    def __str__(self) -> str:
        return f"={self.value}"


@dataclass(frozen=True)
class GlobMatcher:
    # 缺省类型：Bash 命令按整串通配，其余按路径 glob 语义。
    pattern: str
    is_command: bool

    def match(self, value: str) -> bool:
        if self.is_command:
            return match_command(self.pattern, value)
        return match_path(self.pattern, value)

    def __str__(self) -> str:
        return self.pattern


@dataclass(frozen=True)
class RegexMatcher:
    # 正则使用 search（不是 fullmatch），便于匹配命令/路径中的片段。
    src: str
    compiled: re.Pattern[str]

    def match(self, value: str) -> bool:
        return self.compiled.search(value) is not None

    def __str__(self) -> str:
        return f"~{self.src}"


@dataclass(frozen=True)
class NotMatcher:
    # 一元取反；inner 可以是任意 Matcher，因此支持 !=x、!~re、!glob。
    inner: Matcher

    def match(self, value: str) -> bool:
        return not self.inner.match(value)

    def __str__(self) -> str:
        return f"!{self.inner}"


def compile_matcher(pattern: str, *, is_command: bool) -> Matcher:
    """Compile "=exact" / "~regex" / "!inner" / "glob" into a Matcher.

    Raises ValueError for an empty pattern or an invalid regex.
    """
    if not pattern:
        raise ValueError("empty matcher pattern")
    head, rest = pattern[0], pattern[1:]
    if head == "=":
        return ExactMatcher(rest)
    if head == "~":
        try:
            return RegexMatcher(rest, re.compile(rest))
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
    if head == "!":
        return NotMatcher(compile_matcher(rest, is_command=is_command))
    return GlobMatcher(pattern, is_command)


def match_command(pattern: str, target: str) -> bool:
    # 命令通配：* 匹配任意字符（含空格），其余字符按字面量。
    return re.fullmatch(_command_glob_to_regex(pattern), target) is not None


def match_path(pattern: str, target: str) -> bool:
    # 路径通配：** 跨目录，* 不跨 "/"。
    return re.fullmatch(_path_glob_to_regex(pattern), target) is not None


def _command_glob_to_regex(pattern: str) -> str:
    # 反斜杠转义下一个字符，优先于 "*" 通配（供持久化规则转义字面量）。
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
        elif char == "*":
            out.append(".*")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    return "".join(out)


def _path_glob_to_regex(pattern: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i] == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
        elif pattern[i] == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


__all__ = [
    "ExactMatcher",
    "GlobMatcher",
    "Matcher",
    "NotMatcher",
    "RegexMatcher",
    "compile_matcher",
    "match_command",
    "match_path",
]
