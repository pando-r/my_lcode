"""Register skills as slash commands and provide /skill management."""

from __future__ import annotations

from lcode.command.builtins import register_builtins
from lcode.command.command import Command, Kind
from lcode.command.registry import Registry
from lcode.command.ui import UI
from lcode.skills.executor import SkillExecutor
from lcode.skills.loader import SkillLoader

# 本次会话已由 skill 注册的命令名；reload/重新注册时先清掉。
_REGISTERED_SKILL_NAMES: list[str] = []

SKILL_USAGE = "用法: /skill list | info <name> | reload"


def register_skill_commands(
    reg: Registry, loader: SkillLoader, executor: SkillExecutor | None
) -> None:
    # 每个 skill → KindPrompt 命令；同名时 skill 版本覆盖已有 handler（F14）。
    remove_skill_commands(reg)
    for skill in loader.list():
        name = skill.name
        reg.remove(name)

        async def handler(ui: UI, _name: str = name) -> None:
            if executor is None:
                ui.error("Skill 执行器未初始化")
                return
            await executor.execute(ui, _name, ui.command_args())

        reg.register(
            Command(
                name=name,
                description=f"{skill.description} [skill]",
                kind=Kind.PROMPT,
                handler=handler,
                accepts_args=True,
            )
        )
        _REGISTERED_SKILL_NAMES.append(name)


def remove_skill_commands(reg: Registry) -> None:
    for name in list(_REGISTERED_SKILL_NAMES):
        reg.remove(name)
    _REGISTERED_SKILL_NAMES.clear()


def register_skill_management(
    reg: Registry, loader: SkillLoader, executor: SkillExecutor | None
) -> None:
    # /skill list | info <name> | reload（F13）。
    reg.remove("skill")

    async def handler(ui: UI) -> None:
        await handle_skill(ui, reg, loader, executor)

    reg.register(
        Command(
            name="skill",
            description="管理 Skill：list | info <name> | reload",
            kind=Kind.LOCAL,
            handler=handler,
            accepts_args=True,
        )
    )


async def handle_skill(
    ui: UI,
    reg: Registry,
    loader: SkillLoader,
    executor: SkillExecutor | None,
) -> None:
    args = ui.command_args().strip()
    sub, _, rest = args.partition(" ")
    rest = rest.strip()

    if not args or sub == "list":
        _list_skills(ui)
    elif sub == "info":
        _info_skill(ui, loader, rest)
    elif sub == "reload":
        added, removed = loader.reload()
        # 顺序：摘掉旧 skill（含覆盖内置者）→ 恢复内置 → 挂上当前 skill。
        remove_skill_commands(reg)
        register_builtins(reg)
        register_skill_commands(reg, loader, executor)
        ui.println(
            f"已重新加载 Skill：新增 {len(added)}，移除 {len(removed)}，"
            f"当前 {len(loader.list())} 个"
        )
    else:
        ui.error(SKILL_USAGE)


def _list_skills(ui: UI) -> None:
    skills = ui.list_catalog_skills()
    if not skills:
        ui.println("无可用 Skill")
        return
    width = max(len(skill.name) for skill in skills)
    lines = [f"  {skill.name:<{width}}  {skill.description}  [{skill.source}]" for skill in skills]
    active = ui.list_active_skills()
    if active:
        lines.append("已激活: " + ", ".join(active))
    ui.println("\n".join(lines))


def _info_skill(ui: UI, loader: SkillLoader, name: str) -> None:
    if not name:
        ui.error("用法: /skill info <name>")
        return
    skill = loader.get(name)
    if skill is None:
        ui.error(f"未知 Skill: {name}")
        return

    lines = [
        f"name: {skill.name}",
        f"description: {skill.description}",
        f"mode: {skill.mode}",
        f"context: {skill.context}",
        f"model: {skill.model or '(default)'}",
        f"source: {loader.get_source_label(skill.name) or skill.source.value}",
        f"path: {skill.source_path}",
        f"directory: {'yes' if skill.is_directory else 'no'}",
    ]
    ui.println("\n".join(lines))


__all__ = [
    "SKILL_USAGE",
    "handle_skill",
    "register_skill_commands",
    "register_skill_management",
    "remove_skill_commands",
]
