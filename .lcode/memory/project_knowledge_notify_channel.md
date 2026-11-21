---
type: project_knowledge
title: lcode 通知通道（缺口一）架构与用法
created: '2026-11-20T18:32:00+08:00'
updated: '2026-11-20T18:32:00+08:00'
---
lcode 通知通道解决「任务静默失败」问题：常驻任务没人知道出事了。

三通道（src/lcode/notify.py 的 Notifier）：
1. log —— 永远开，写 .lcode/notifications.jsonl 一行一条 JSON（title/message/level/source/at）。是「你到底有没有告诉过我」的证据链。
2. desktop —— Windows Toast（PowerShell -EncodedCommand）+ macOS osascript + Linux notify-send，用 Popen 不 wait（args 里带 CREATE_NO_WINDOW=0x08000000 防闪窗）。
3. webhook —— 标准库 urllib POST JSON，同步带超时（--resident-once 跑完就退，不能交给线程）。

两个入口：
- resident/daemon.py 的 _run_task：完成 → level=ok，失败/超时 → level=error，正文带失败原因。run_forever 里整轮异常也通知。空队列不通知（否则每天定时跑会天天弹「没有任务」）。
- tool/notify.py 的 NotifyTool：注册进 new_default_registry（现共 7 个工具），模型自己决定何时发。

关键坑（都实测过）：
- Windows PowerShell 5.1 里 New-Object Windows.Data.Xml.Dom.XmlDocument 会报「找不到类型」，必须带程序集限定名 [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]，见 notify.py 的 _PS_TOAST_TYPES。
- powershell -Command 从 Python 传字符串会经 ANSI 代码页，中文必乱码；必须 base64(UTF-16LE) + -EncodedCommand。
- 开 VPN 时 urllib 会把 127.0.0.1 也丢给代理，本地 webhook 连不上；_opener_for 对本机地址强制 ProxyHandler({})，外网沿用系统代理。

权限分类：permission/settings.py 的 categorize 里显式把 notify 归 Category.READ。若归 EXEC，default 模式下 ASK → 无人值守 DENY，恰恰让最需要通知时失效。NotifyTool.read_only=False，所以不会被当只读工具并发执行。

契约：通知失败绝不能让任务失败。Notifier.send 每个通道包 _safe_channel，Daemon.notify 再包一层 try/except。

配置：.lcode/notify.yaml（desktop/webhook/min_level/webhook_timeout，模板在 src/lcode/templates/notify.yaml.example），环境变量 LCODE_NOTIFY_DESKTOP/WEBHOOK/MIN_LEVEL 优先。命令行总开关 --no-notify。

测试：tests/test_notify.py 30 个 + test_resident.py 通知段；test_tool.py 的工具数与 test_tui.py 的 status 断言都随 6→7 更新过。文档：docs/notify.md。
