"""Engine: four-layer check pipeline (blacklist → sandbox → rules → mode fallback)."""

# 前四层流水线（黑名单→沙箱→规则→模式兜底），逐层短路。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lcode.llm import ToolCall
from lcode.permission import Category, Decision, Mode
from lcode.permission.blacklist import _BLACKLIST, hits_blacklist
from lcode.permission.rule import RuleSet
from lcode.permission.sandbox import resolve_root, sandbox_ok
from lcode.permission.settings import (
    Settings,
    SettingsError,
    categorize,
    extract_target,
    friendly_name,
    load_settings,
    to_rule_set,
)


@dataclass
class Engine:
    root: str
    blacklist: list
    user: RuleSet
    project: RuleSet
    local: RuleSet
    local_path: str
    start_mode: Mode

    def check(self, mode: Mode, call: ToolCall, read_only: bool) -> tuple[Decision, str]:
        cat = categorize(call.name, read_only)
        friendly = friendly_name(call.name)
        target, is_file, ok = extract_target(call)

        # ① blacklist (EXEC only)
        if cat is Category.EXEC and target and hits_blacklist(target):
            return Decision.DENY, f"命中危险命令黑名单：{target}"

        # ② sandbox (file tools only)
        if is_file:
            if not ok:
                return Decision.DENY, "无法解析文件路径参数，安全拒绝"
            if not sandbox_ok(self.root, target):
                return Decision.DENY, f"路径在项目目录之外：{target}"

        # ③ rules: local → project → user
        for ruleset in (self.local, self.project, self.user):
            decision, hit = ruleset.match(friendly, target)
            if hit:
                if decision is Decision.DENY:
                    return Decision.DENY, f"匹配 deny 规则：{friendly}({_rule_pat(target)})"
                return Decision.ALLOW, ""

        # ④ mode fallback (Allow/Ask only)
        decision = mode_fallback(mode, cat)
        if decision is Decision.ALLOW:
            return Decision.ALLOW, ""
        cat_name = ["只读", "文件写", "命令执行"][cat.value]
        return Decision.ASK, f"{mode} 模式下 {cat_name} 类操作需确认"


def mode_fallback(mode: Mode, cat: Category) -> Decision:
    if cat is Category.READ or mode is Mode.BYPASS:
        return Decision.ALLOW
    if mode is Mode.ACCEPT_EDITS and cat is Category.WRITE:
        return Decision.ALLOW
    return Decision.ASK


def new_engine(root: str) -> tuple[Engine, Exception | None]:
    err = None
    try:
        resolved = resolve_root(root)
    except Exception as exc:  # noqa: BLE001
        resolved = root
        err = exc

    home = str(Path.home())
    user_settings = _safe_load(str(Path(home) / ".lcode" / "settings.yaml"))
    project_settings = _safe_load(str(Path(resolved) / ".lcode" / "settings.yaml"))
    local_settings = _safe_load(str(Path(resolved) / ".lcode" / "settings.local.yaml"))
    local_path = str(Path(resolved) / ".lcode" / "settings.local.yaml")

    engine = Engine(
        root=resolved,
        blacklist=_BLACKLIST,
        user=to_rule_set(user_settings),
        project=to_rule_set(project_settings),
        local=to_rule_set(local_settings),
        local_path=local_path,
        start_mode=_start_mode(local_settings, project_settings, user_settings),
    )
    return engine, err


def _safe_load(path: str) -> Settings:
    try:
        return load_settings(path)
    except SettingsError:
        return Settings()


def _start_mode(local: Settings, project: Settings, user: Settings) -> Mode:
    for s in (local, project, user):
        mode, ok = parse_mode_local(s.default_mode)
        if ok:
            return mode
    return Mode.DEFAULT


def parse_mode_local(s: str) -> tuple[Mode, bool]:
    from lcode.permission import parse_mode

    return parse_mode(s)


def _rule_pat(target: str) -> str:
    return target if len(target) <= 60 else target[:57] + "..."
