"""UI-affecting commands: /exit /plan /compact /resume /clear /copy."""

from __future__ import annotations

import re

from lcode.command.ui import UI
from lcode.permission import Mode

CODE_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
COPY_USAGE = (
    "用法：/copy [last|all|code]（last=最近一条回复，all=整个屏幕，code=最近回复里的代码块）"
)


async def handle_exit(ui: UI) -> None:
    ui.quit()


async def handle_plan(ui: UI) -> None:
    ui.set_mode(Mode.PLAN)
    ui.println("已切换到 PLAN 模式")


async def handle_compact(ui: UI) -> None:
    if not ui.idle():
        ui.error("请等待当前任务完成")
        return
    ui.force_compact()


async def handle_resume(ui: UI) -> None:
    if not ui.idle():
        ui.error("请等待当前任务完成")
        return
    ui.open_resume_menu()


async def handle_clear(ui: UI) -> None:
    await ui.clear_and_new_session()
    # F18：清空对话历史的同时清空激活态，旧 SOP 不再注入 environment。
    ui.clear_active_skills()
    ui.println("已清空当前会话，开启新会话")


async def handle_copy(ui: UI) -> None:
    # 键盘用户没有鼠标：把屏幕上看得到的东西直接送进系统剪贴板。
    target = (ui.command_args() or "last").strip().lower()
    if target in ("last", "reply"):
        text = ui.last_assistant_message()
        label = "最近一条回复"
    elif target == "all":
        text = ui.log_text()
        label = "整个屏幕"
    elif target == "code":
        blocks = CODE_BLOCK.findall(ui.last_assistant_message())
        text = "\n\n".join(block.rstrip("\n") for block in blocks)
        label = "代码块"
    else:
        ui.error(COPY_USAGE)
        return
    if not text.strip():
        ui.error(f"没有可复制的{label}内容")
        return
    ui.copy_text(text)
    ui.println(f"已复制{label}（{len(text)} 字符）")
