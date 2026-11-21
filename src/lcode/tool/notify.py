"""notify 工具：让 Agent 主动把结果推出终端。

为什么需要它：
终端里的输出只有你盯着屏幕时才看得见。长任务跑完、后台任务失败、或者
「这件事你得尽快知道」的时候，Agent 需要一个能主动找上门的动作。

通道由 ``lcode.notify`` 提供（桌面横幅 + JSONL 日志 + webhook），
这里只是一层薄封装：真正的发送逻辑不该被工具层和常驻层各写一遍。
"""

from __future__ import annotations

import asyncio
import json

from lcode.notify import LEVEL_ERROR, LEVELS, Notifier
from lcode.tool import Result
from lcode.tool.ctx import resolve_path

TITLE_LIMIT = 80
MESSAGE_LIMIT = 800


class NotifyTool:
    # 通知有副作用（弹横幅、写日志），严格说不是只读；
    # 但权限层会把它归到 READ，见 permission/settings.py 的 categorize 注释。
    read_only: bool = False

    def name(self) -> str:
        return "notify"

    def description(self) -> str:
        return (
            "给用户发一条通知（桌面横幅 + 日志，配置了 webhook 还会推到手机）。"
            "适合：任务耗时较长、需要在用户可能不看屏幕时提醒结果、或发现必须尽快知道的问题。"
            "不要用它替代正常回答。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "通知标题，一句话"},
                "message": {"type": "string", "description": "通知正文，尽量简短可读"},
                "level": {
                    "type": "string",
                    "enum": list(LEVELS),
                    "description": "级别：info / ok / warn / error（默认 info）",
                },
            },
            "required": ["title", "message"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)
        if not isinstance(data, dict):
            return Result(content="参数必须是 JSON 对象", is_error=True)

        title = str(data.get("title") or "").strip()
        message = str(data.get("message") or "").strip()
        if not title:
            return Result(content="缺少参数: title", is_error=True)
        if not message:
            return Result(content="缺少参数: message", is_error=True)

        level = str(data.get("level") or "info").strip().lower()
        if level not in LEVELS:
            level = LEVEL_ERROR if "error" in level else "info"

        root = resolve_path("")  # 工具上下文 cwd，就是项目根
        notifier = Notifier(root)
        # webhook 是同步阻塞调用，放到线程里，别卡住 TUI 事件循环。
        delivered = await asyncio.to_thread(
            notifier.send,
            title[:TITLE_LIMIT],
            message[:MESSAGE_LIMIT],
            level,
            "tool",
        )
        if not delivered:
            return Result(
                content=f"通知未送达（日志也写不进去）：{notifier.log_path}", is_error=True
            )
        return Result(content=f"通知已发送，通道：{'、'.join(delivered)}")


__all__ = ["MESSAGE_LIMIT", "TITLE_LIMIT", "NotifyTool"]
