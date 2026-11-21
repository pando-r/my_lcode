---
type: project_knowledge
title: lcode 邮件通道配置与收发流程
created: '2026-11-21T00:20:59.403772+08:00'
updated: '2026-11-21T00:20:59.403772+08:00'
---
lcode 邮件通道（.lcode/mail.yaml）：
- 账号：托管/收信邮箱 3952613547@qq.com（Lcode 自己的）；K 的日常发信地址 2987731879@qq.com。
- senders 是发件人白名单，只有列出的地址会被处理，其余直接「退回」，日志形如「退回：<地址>（发件人不在白名单）」。两个地址都已加入；模板 src/lcode/templates/mail.yaml.example 里默认 senders: []。
- 排查提醒：退回记录只出现在 INBOX/日志里，容易被当成垃圾邮件略过；K 说「我发了邮件」时先确认他的发件地址，别默认是托管邮箱。
- 流程：python -m lcode --check-mail 入队（默认只读，需要写权限加 --mail-allow-write 或改 mail.yaml）→ python -m lcode --resident-once --mail-watch 执行并回信。主题必须以 [Lcode] 开头。
- 回信带 Auto-Submitted: auto-replied 和 In-Reply-To，用于防回信循环并让回信留在原会话线程；可在 IMAP「Sent Messages」里确认回信已发出。
