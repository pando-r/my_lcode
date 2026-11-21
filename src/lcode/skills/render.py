"""Render a skill body with arguments."""

from __future__ import annotations

from lcode.skills.parser import SkillDef, substitute_arguments


def render_body(skill: SkillDef, args: str = "") -> str:
    # 有 $ARGUMENTS 时替换；没有占位符但用户给了参数时追加 User Request 段。
    body = substitute_arguments(skill.prompt_body, args)
    if args and "$ARGUMENTS" not in skill.prompt_body:
        body = body.rstrip() + "\n\n## User Request\n\n" + args
    return body


__all__ = ["render_body"]
