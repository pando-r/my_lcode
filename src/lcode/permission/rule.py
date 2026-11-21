"""Rules and structured matching for permission checks."""

# 规则引擎·用于匹配命令和路径的规则。
# 匹配语法：=exact / ~regex / !inner / glob（缺省）。
from __future__ import annotations

from dataclasses import dataclass, field

from lcode.permission import Decision
from lcode.permission.matcher import (
    GlobMatcher,
    Matcher,
    compile_matcher,
    match_command,
    match_path,
)


@dataclass
class Rule:
    tool: str
    matcher: Matcher | None
    allow: bool
    raw: str = ""

    def __post_init__(self) -> None:
        # 兼容旧的 Rule(tool, "glob", allow) 构造方式。
        if isinstance(self.matcher, str):
            self.matcher = GlobMatcher(self.matcher, self.tool == "Bash") if self.matcher else None

    @property
    def pattern(self) -> str:
        # 兼容旧调用方：返回匹配器的字符串形式（无 matcher 表示全匹配）。
        return "" if self.matcher is None else str(self.matcher)


@dataclass
class RuleSet:
    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def match(self, friendly: str, target: str) -> tuple[Decision, bool]:
        for r in self.deny:
            if r.tool == friendly and match_rule(r, target):
                return Decision.DENY, True
        for r in self.allow:
            if r.tool == friendly and match_rule(r, target):
                return Decision.ALLOW, True
        return Decision.ALLOW, False


def parse_rule(s: str) -> tuple[Rule | None, str | None]:
    """Parse a rule string; return (rule, error). Exactly one side is non-None."""
    raw = s.strip()
    if not raw:
        return None, "empty rule"
    if "(" in raw:
        if not raw.endswith(")") or raw[0] == "(":
            return None, "invalid rule syntax"
        tool = raw[: raw.index("(")].strip()
        pattern = raw[raw.index("(") + 1 : -1]
    else:
        tool = raw
        pattern = ""
    if not tool:
        return None, "missing tool name"

    matcher: Matcher | None = None
    if pattern:
        try:
            matcher = compile_matcher(pattern, is_command=(tool == "Bash"))
        except ValueError as exc:
            return None, str(exc)
    return Rule(tool=tool, matcher=matcher, allow=False, raw=raw), None


def match_rule(r: Rule, target: str) -> bool:
    # matcher=None 表示该工具全匹配。
    return r.matcher is None or r.matcher.match(target)


def match_pattern(pattern: str, target: str, is_path: bool) -> bool:
    # 兼容 ch08 的底层函数签名；新代码应优先使用 Matcher。
    if pattern == "":
        return True
    return GlobMatcher(pattern, not is_path).match(target)


__all__ = [
    "Rule",
    "RuleSet",
    "match_command",
    "match_path",
    "match_pattern",
    "match_rule",
    "parse_rule",
]
