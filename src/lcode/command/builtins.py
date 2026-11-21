"""Register the 15 built-in commands on a fresh registry."""

from __future__ import annotations

from lcode.command.builtin_local import (
    handle_memory,
    handle_permission,
    handle_session,
    handle_status,
    make_help_handler,
)
from lcode.command.builtin_media import handle_audio, handle_image
from lcode.command.builtin_prompt import handle_do, handle_review
from lcode.command.builtin_team import handle_team
from lcode.command.builtin_ui import (
    handle_clear,
    handle_compact,
    handle_copy,
    handle_exit,
    handle_plan,
    handle_resume,
)
from lcode.command.builtin_worktree import handle_worktree
from lcode.command.command import Command, Kind
from lcode.command.hooks import handle_hooks
from lcode.command.registry import Registry

BUILTIN_COMMAND_NAMES = (
    "audio",
    "clear",
    "compact",
    "copy",
    "do",
    "exit",
    "help",
    "hooks",
    "image",
    "memory",
    "permission",
    "plan",
    "resume",
    "review",
    "session",
    "status",
    "team",
    "worktree",
)


def register_builtins(reg: Registry) -> None:
    # 幂等注册：/skill reload 时先摘掉旧版本（含被 skill 覆盖的同名命令）再挂回。
    for name in BUILTIN_COMMAND_NAMES:
        reg.remove(name)
    # 名字/别名的冲突检测在 Registry.register 内完成，启动期即终止。
    reg.register(
        Command(
            "audio",
            "转写音频：/audio <路径> [提示]",
            Kind.LOCAL,
            handle_audio,
            accepts_args=True,
        )
    )
    reg.register(Command("clear", "清空当前会话并新建会话", Kind.UI, handle_clear))
    reg.register(Command("compact", "手动压缩上下文", Kind.UI, handle_compact))
    reg.register(
        Command(
            "copy",
            "把最近回复/整个屏幕复制到剪贴板",
            Kind.LOCAL,  # 只读对话 + 写剪贴板：流式回答过程中也允许用
            handle_copy,
            accepts_args=True,
        )
    )
    reg.register(Command("do", "执行当前计划", Kind.PROMPT, handle_do))
    reg.register(Command("exit", "退出 Lcode", Kind.UI, handle_exit))
    reg.register(Command("help", "列出所有可用命令", Kind.LOCAL, make_help_handler(reg)))
    reg.register(Command("hooks", "列出已加载的生命周期 Hook", Kind.LOCAL, handle_hooks))
    reg.register(
        Command(
            "image",
            "看图：/image <路径> [问题]",
            Kind.LOCAL,
            handle_image,
            accepts_args=True,
        )
    )
    reg.register(Command("memory", "列出已加载的记忆文件", Kind.LOCAL, handle_memory))
    reg.register(Command("permission", "显示当前权限模式", Kind.LOCAL, handle_permission))
    reg.register(Command("plan", "切换到计划模式", Kind.UI, handle_plan))
    reg.register(Command("resume", "恢复历史会话", Kind.UI, handle_resume))
    reg.register(Command("review", "请求代码审查", Kind.PROMPT, handle_review))
    reg.register(Command("session", "显示当前会话信息", Kind.LOCAL, handle_session))
    reg.register(Command("status", "显示当前运行状态", Kind.LOCAL, handle_status))
    reg.register(Command("team", "管理 Agent Team", Kind.LOCAL, handle_team, accepts_args=True))
    reg.register(
        Command(
            "worktree",
            "管理 Worktree 隔离副本",
            Kind.UI,
            handle_worktree,
            accepts_args=True,
        )
    )
