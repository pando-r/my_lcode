"""send_mail 工具：让 Agent 主动给别人写一封信（不只是回自己的信）。

为什么需要它：
没有这个工具时，Agent 只能「回信给来信人」——收件人永远是 ``task.reply_to``。
用户想让它「给老王发一封说明」，只能自己去复制粘贴。

为什么它必须带白名单：
这是整个工具层唯一能把数据**送出去**的通道。一旦放开成「任意地址」，邮件正文里
的提示注入就从「读你的文件」升级成「把你的文件发到 evil@x.com」——
前者有路径沙箱兜底，后者一旦发出去就撤不回来。所以：
``.lcode/mail.yaml`` 的 ``send_to`` 为空时，这个工具**一封都不发**。

三道约束叠在一起：
1. ``send_to`` 白名单（能不能发给这个人）；
2. 权限层把它归 EXEC（现在是能不能发，TUI 里会 ASK，无人值守时 DENY）；
3. 每次发送记一行 ``.lcode/mail-outbox.jsonl``（发过什么，可查证）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from lcode.mailbox import OUTBOX_NAME, can_send_to, record_outbox
from lcode.mailbox import load_config as load_mail_config
from lcode.notify import load_config as load_notify_config
from lcode.notify import send_mail
from lcode.tool import Result
from lcode.tool.ctx import resolve_path

SUBJECT_LIMIT = 200
BODY_LIMIT = 50_000


class SendMailTool:
    # 有外部副作用且不可撤回：权限层必须把它当 EXEC 处理，不能像 notify 那样归 READ。
    read_only: bool = False

    def name(self) -> str:
        return "send_mail"

    def description(self) -> str:
        return (
            "给别人发一封邮件（收件人必须在 .lcode/mail.yaml 的 send_to 白名单里）。"
            "适合：用户明确要求「给某某发一封说明/通知/答复」。"
            "发出去的邮件撤不回来——收件人和正文都写清楚再调用，不要用它群发或转发敏感内容。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "收件人邮箱地址"},
                "subject": {"type": "string", "description": "邮件主题，一句话"},
                "body": {"type": "string", "description": "邮件正文（纯文本）"},
            },
            "required": ["to", "subject", "body"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)
        if not isinstance(data, dict):
            return Result(content="参数必须是 JSON 对象", is_error=True)

        to = str(data.get("to") or "").strip()
        subject = str(data.get("subject") or "").strip()
        body = str(data.get("body") or "").strip()
        if not to:
            return Result(content="缺少参数: to", is_error=True)
        if not subject:
            return Result(content="缺少参数: subject", is_error=True)
        if not body:
            return Result(content="缺少参数: body", is_error=True)
        if len(subject) > SUBJECT_LIMIT:
            return Result(content=f"主题太长（上限 {SUBJECT_LIMIT} 字）", is_error=True)
        if len(body) > BODY_LIMIT:
            return Result(content=f"正文太长（上限 {BODY_LIMIT} 字）", is_error=True)

        root = Path(resolve_path(""))
        mail_cfg = load_mail_config(root)
        outbox = root / ".lcode" / OUTBOX_NAME

        # 白名单是硬闸门：为空或不在表里，一律不发，并且把「怎么办」说清楚，
        # 否则用户只会看到一句「被拒绝」而不知道要去改哪个文件。
        if not mail_cfg.send_to:
            record_outbox(outbox, to, subject, body, ok=False, reason="send_to 为空")
            return Result(
                content=(
                    "未发送：发信白名单是空的。\n"
                    "要允许主动发信，在 .lcode/mail.yaml 里加：\n"
                    "  send_to:\n"
                    f"    - {to}\n"
                    "（留空 = 一封都不发，这是刻意的默认值）"
                ),
                is_error=True,
            )
        if not can_send_to(to, mail_cfg.send_to):
            record_outbox(outbox, to, subject, body, ok=False, reason="收件人不在白名单")
            allowed = "、".join(mail_cfg.send_to)
            return Result(
                content=(
                    f"未发送：{to} 不在发信白名单里。\n"
                    f"当前允许的收件人：{allowed}\n"
                    "确需发给它可以把它加进 .lcode/mail.yaml 的 send_to。"
                ),
                is_error=True,
            )

        email_cfg = load_notify_config(root).email
        if not email_cfg.smtp_ready():
            record_outbox(outbox, to, subject, body, ok=False, reason="SMTP 未配置")
            return Result(
                content=(
                    "未发送：.lcode/notify.yaml 的 email 段没配全，"
                    "需要 enabled / smtp_host / user / auth_code。"
                ),
                is_error=True,
            )

        # SMTP 是同步阻塞调用，放线程里别卡住事件循环。
        sent = await asyncio.to_thread(send_mail, email_cfg, to, subject, body)
        record_outbox(
            outbox, to, subject, body, ok=sent, reason="" if sent else "SMTP 发送失败"
        )
        if not sent:
            return Result(
                content=(
                    f"发送失败：{to}。常见原因是授权码过期、SMTP 被网络拦截、"
                    "或收件人地址不存在。邮件没有发出去，可以稍后重试。"
                ),
                is_error=True,
            )
        return Result(content=f"邮件已发送：{to}，主题「{subject}」（记录在 {outbox.name}）")


__all__ = ["BODY_LIMIT", "SUBJECT_LIMIT", "SendMailTool"]
