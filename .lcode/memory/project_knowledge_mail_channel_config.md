---
type: project_knowledge
title: lcode 邮件通道配置与收发流程
created: '2026-11-21T00:20:59.403772+08:00'
updated: '2026-11-21T22:45:00.000000+08:00'
---
lcode 邮件通道（.lcode/mail.yaml）：
- 账号：托管/收信邮箱 3952613547@qq.com（Lcode 自己的）；K 的日常发信地址 2987731879@qq.com。
- senders 是**收信**白名单，只有列出的地址会被处理，其余直接「退回」，日志形如「退回：<地址>（发件人不在白名单）」。两个地址都已加入；模板 src/lcode/templates/mail.yaml.example 里默认 senders: []。
- send_to 是**发信**白名单，控制 Agent 主动给别人发信（send_mail 工具）。默认为空 = 一封都不发，这是刻意的失败关闭：一旦允许任意地址，邮件正文里的提示注入就从「读本地文件」升级成「把文件发给 evil@x.com」。要给谁发就加进去。
- 排查提醒：退回记录只出现在 INBOX/日志里，容易被当成垃圾邮件略过；K 说「我发了邮件」时先确认他的发件地址，别默认是托管邮箱。
- 流程：python -m lcode --check-mail 入队（默认只读，需要写权限加 --mail-allow-write 或改 mail.yaml）→ python -m lcode --resident-once --mail-watch 执行并回信。主题必须以 [Lcode] 开头。
- 回信带 Auto-Submitted: auto-replied 和 In-Reply-To，用于防回信循环并让回信留在原会话线程；可在 IMAP「Sent Messages」里确认回信已发出。
- 正文：extract_body 取第一个**非空**的 text/plain 或 text/html 分支。QQ/网易客户端发的 multipart/alternative 里 plain 常是 0 字节空串，旧代码「找到 plain 就返回」导致正文永远读成空。另做 HTML 实体解码、script/style 剥离、引用块剥离（strip_quoted_history）。
- 话题续接：thread_key 把 Re:/答复:/Fw: 和前缀剥掉后归一化，同一话题的新任务会带上最近 3 轮的问答（THREAD_HISTORY_LIMIT/PROMPT_LIMIT/ANSWER_LIMIT）。
- 诊断入口：`lcode --mail-peek`（看每封信解析出的正文长度/话题键/指令）、`lcode --mail-peek --outbox`（看主动发信记录 .lcode/mail-outbox.jsonl）。
- 白名单管理入口：`lcode --mail-allow-to <地址>` 改 send_to（只动那一段、保留注释、自动给 `@` 加引号、写完自校验，失败回滚）；`lcode --mail-allow-from <地址>` 改 senders（对称，只给发信白名单会出现「对方回信被退回」的断裂）；`lcode --mail-allowed` 列出白名单 + SMTP 状态。手改 YAML 时最容易踩的坑就是 `- @qq.com` 不加引号。
- 已建立的好友通道（2026-11-21）：2072472552@qq.com（K 的朋友）同时进了 senders 和 send_to。已用它给该地址发过一封自我介绍信（主题「我是李俊琦的贾维斯（Lcode）」），确认已落进 IMAP 的 Sent Messages。
- 两个已修的隐蔽 bug：① reply() 曾只在 --mail-watch 时加载 SMTP 配置，导致 --check-mail + --resident-once 组合下每封邮件任务都静默不回信；② fetch_new 曾用 uids[:N] 取**最旧**的 N 封（IMAP 搜索结果从旧到新），收件箱超 40 封后新信进不了窗口。
- YAML 坑：域名通配 `- @qq.com` 不加引号会让 PyYAML 抛 ScannerError、整份配置作废（表现为「一封都不收」）。必须写 `- "@qq.com"`。现在解析失败会打到 stderr 并提示这一点。
