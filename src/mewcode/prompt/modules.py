"""Fixed and optional system prompt modules."""

# 系统提示词的模块化设计。
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    name: str
    priority: int  # 排序权重，数字越小越靠前（优先级更高）
    content: str  # 模块正文


def fixed_modules() -> list[Module]:
    # The seven fixed modules, ordered by priority.
    return [
        Module(
            name="身份",
            priority=10,
            content="You are MewCode, a helpful AI agent running in the user's terminal.",
        ),
        Module(
            name="系统约束",
            priority=20,
            content=(
                "Operate within the user's working directory. Never expose API keys or "
                "secrets. Be careful with destructive actions."
            ),
        ),
        Module(
            name="任务模式",
            priority=30,
            content=(
                "Work in a multi-step loop: investigate, act, then answer. Read before you "
                "edit. Keep using tools across multiple steps to make progress, and only give "
                "your final concise answer once the task is complete."
            ),
        ),
        Module(
            name="动作执行",
            priority=40,
            content=(
                "Call tools when you need information or must perform an action. Consecutive "
                "read-only calls may run concurrently; be careful with side-effecting actions."
            ),
        ),
        Module(
            name="工具使用",
            priority=50,
            content=(
                "Prefer dedicated tools (read_file / glob / grep) over composing shell commands. "
                "Always read_file before edit_file to confirm the target string is unique."
            ),
        ),
        Module(
            name="语气风格",
            priority=60,
            content="Be concise and direct. Do not flatter or over-explain.",
        ),
        Module(
            name="文本输出",
            priority=70,
            content="Use Markdown (code blocks, lists, emphasis) when it improves clarity.",
        ),
    ]


# 三个可选槽位：自定义指令（MEWCODE.md）、Skill Catalog、长期记忆索引。
# 参数为空字符串时对应模块内容为空，组装阶段会被跳过。
PRIO_SKILLS_CATALOG = 90


def optional_modules(
    instructions: str = "", memory: str = "", skills_catalog: str = ""
) -> list[Module]:
    return [
        Module(name="自定义指令", priority=80, content=instructions),
        Module(name="skills-catalog", priority=PRIO_SKILLS_CATALOG, content=skills_catalog),
        Module(name="长期记忆", priority=100, content=memory),
    ]
