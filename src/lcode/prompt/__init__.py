"""System prompt assembly, environment, reminder, and banner."""

# 把内部零件统一导出成对外 API，以及放一个所有子模块都不适合放的“总装”函数。
from __future__ import annotations

from lcode.prompt.environment import Environment, gather_environment
from lcode.prompt.modules import Module, fixed_modules, optional_modules
from lcode.prompt.reminder import EXECUTE_DIRECTIVE, plan_reminder, system_reminder
from lcode.prompt.skills_block import (
    ActiveSkillEntry,
    SkillCatalogItem,
    render_active_skills_block,
    render_skills_catalog,
)

CAT_BANNER = "  /\\_/\\\n ( o.o )\n  > ^ <"
READY_HINT = "已就绪，输入 /help 查看可用命令。"


# 组装系统提示词
# 按权重排序，跳过空内容，用空行分隔
# 最终返回一个字符串，包含所有模块的内容
def assemble_system(mods: list[Module]) -> str:
    # Assemble modules by ascending priority, skipping empty content, joined by blank lines.
    ordered = sorted(mods, key=lambda m: m.priority)
    parts = [m.content for m in ordered if m.content]
    return "\n\n".join(parts)


# 这是用的挺多的函数
# instructions 非空时填入 custom-instructions（priority 80）；
# skills_catalog 非空时填入 skills-catalog（priority 90）；
# memory 非空时填入 long-term-memory（priority 100）。
def build_system_prompt(instructions: str = "", memory: str = "", skills_catalog: str = "") -> str:
    # Build the stable system prompt from fixed + optional modules.
    return assemble_system(fixed_modules() + optional_modules(instructions, memory, skills_catalog))


# 生成启动时打印在终端里的欢迎横幅
def render_banner(version: str, cwd: str) -> str:
    # Assemble the startup banner: cat + app name/version + cwd + ready line.
    lines = [
        CAT_BANNER,
        "",
        f"Lcode v{version}",
        f"cwd: {cwd}",
        "",
        READY_HINT,
    ]
    return "\n".join(lines)


# 导出所有模块
# 包含系统提示词、环境信息、提醒指令、欢迎横幅
__all__ = [
    "Module",
    "fixed_modules",
    "optional_modules",
    "assemble_system",
    "build_system_prompt",
    "Environment",
    "gather_environment",
    "system_reminder",
    "plan_reminder",
    "EXECUTE_DIRECTIVE",
    "CAT_BANNER",
    "READY_HINT",
    "render_banner",
    "ActiveSkillEntry",
    "SkillCatalogItem",
    "render_active_skills_block",
    "render_skills_catalog",
]
