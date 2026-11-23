---
type: project_knowledge
title: lcode 开机简报（boot brief）机制与踩过的坑
created: '2026-11-22T00:15:00.000000+08:00'
updated: '2026-11-22T14:40:16.630401+08:00'
---
开机简报：每次 Windows 登录时主动打招呼 + 报衡阳天气 + 给建议，走桌面横幅。

- 安装：`powershell -ExecutionPolicy Bypass -File scripts\boot_brief.ps1`（已在本机注册，任务名 lcode-boot-brief）。参数：-DelaySeconds / -NetworkWaitSeconds / -DryRun / -Remove（-EveryLogon、-MinHour 已移除）。
- 两个脚本分工：boot_brief.ps1 只负责注册计划任务；boot_brief_run.ps1 走真正流程（等 60s → 等网络 180s → 入队 → resident-once）。两者**刻意纯 ASCII**：PS 5.1 用 OEM 代码页读无 BOM 文件，非 ASCII 会让整个脚本解析失败（实测报 TerminatorExpectedAtEndOfString）。
- 提示词在 `.lcode/brief.md`（UTF-8），不在代码里。改口吻/内容直接编辑它。
- `--enqueue-file`：从 UTF-8 文件读指令入队。存在理由：计划任务动作是一整条命令字符串，中文过 OEM 代码页变乱码，走文件绕开。
- gather_environment 额外给 Time (HH:MM) 和星期（Date 形如 `2026-11-22 (Sunday, 周日)`）：问候要分早晚，「今天适合做什么」依赖星期几，只给 ISO 日期模型会算错。
- **时段在代码里算死，不交给模型推**（K 要求「分辨时间来判断早上好/下午好/晚上好」）：`prompt/environment.py: _time_of_day` 把小时映射成 早上/上午/中午/下午/晚上/深夜，render 成 `Time: 14:58 (下午)`。分档：5-8 早上 / 9-11 上午 / 12-13 中午 / 14-17 下午 / 18-22 晚上 / 23-4 深夜（跨零点用「回落默认值 + 逐个 >= 比较」实现）。理由：11:58 与 12:01 这种边界不同模型答案不同、同模型换温度也抖，而问候语是简报第一句。**措辞不写死**：`brief.md` 里是一张「时段→问候语」表，深夜那档明确要求别说「晚上好」，改用「这个点还没睡」这类。13 个参数化边界测试（含每小时 59 分不跳档）。
- **不做任何时间维度去重，每次登录都发**（K 明确要求；两次错误方案之后定稿）：
  - 方案 A 日期戳：凌晨 00:21 重启占掉全天名额，同日 11:20 真正开机时 `already briefed today -- skip`，K 一条没收到。
  - 方案 B `LastBootUpTime.Ticks`：逻辑像对，但本机快速启动开启，「关机」+ 电源键 = 休眠恢复内核会话，LastBootUpTime 不变 → 简报静默消失。
  - 结论：漏发比多发一条伤害大，宁可注销重登多收一条。
- 唯一保留的守卫：入队前查队列里有没有同 prompt 挂在 pending（防失败重试堆积）。**比较前必须去掉 \r**：Python read_text() 把 CRLF 归一成 LF，PowerShell Get-Content -Raw 保留原样，直接比永远不等（实测 624 vs 597 字符）。
- 坑 1：`$ErrorActionPreference="Stop"` 下原生命令写 stderr 会被 PS 5.1 变成 NativeCommandError 终止脚本（表现为 enqueue 后没下文、exit 1）。解法：`cmd /c "... > out 2>&1"` 重定向到文件再读。
- 坑 2：`--resident-once` 撞 `daemon.lock` 会静默失败（「已有常驻进程在运行（pid=xxx），本次退出」）。现在重试一次（等 90s），且**只在输出含 pid= 时**重试。
- 天气靠 mcp__web__search_web（Tavily→Bing→DDG）；TAVILY_API_KEY 设在机器级环境变量，计划任务子进程也能读到。
- 验证：去掉去重后连跑多次（14:36:40 / 14:38:02 / 14:38:15 / 14:39:51）全部入队并 done，无 skip；tests/test_boot_brief.py 14 个全过。
- **横幅正文拼装在 `resident/daemon.py: _summarize_for_notify`，顺序 = 答复 → 任务标签 → 结果文件**。两个坑都是 K 亲自撞出来并指出的：
  1. 早期正文用 `_oneline(task.prompt, 60)` 打头 → 横幅第一行是 brief.md 开头（「你是 K 的贾维斯。他刚开机，你主动开口。写一份简短的晨间简报…」），用户看到的是自己写的提示词，答复被挤到第二行。现在超过 `PROMPT_LABEL_LIMIT`(60 字) 的 prompt **整个不显示**；一句话的手动任务仍保留 `任务：xxx` 标签（判据用完整长度，不是截断后长度）。
  2. 早期用 `summarize()`（queue.py，挑第一个够长的行）→ 简报只剩「下午好，K。」，天气和建议全丢。现在 `_notify_excerpt` 保留全部分行，预算 `NOTIFY_EXCERPT_LIMIT`(320 字)，超了才截断 + `…` + 结果路径。**换掉这条就不能再 import summarize**（已从 daemon 的 import 移除）。
  - 顺带：横幅不渲染 Markdown，`_plain_line` 去掉 `**`、反引号、行首 `#`/`>`/`- `。
  - 超时/失败/回信失败三条通知原来是同一个毛病（prompt 打头），已统一成 `_notify_body(原因, _prompt_label(prompt))`。
  - 新增 7 条测试在 `tests/test_resident.py` 的「通知正文：答复在前」一节。
- 日志里的 `开始 {id}：{prompt前80字}` **保留 prompt 是对的**（事后追查要它），只有用户可见的通知不能带。
- 遗留：横幅是否真的弹出 Python 侧看不到（_spawn 只能保证子进程起来）；日志有 done 但屏幕没东西 = Toast 被静音。
- Task Scheduler LastTaskResult=267009 (0x41301) = 「正在运行」，不是错误。
