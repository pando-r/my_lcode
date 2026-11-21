---
type: project_knowledge
title: lcode 常驻任务（resident）架构与用法
created: '2026-11-20T16:24:55.485279+08:00'
updated: '2026-11-20T16:24:55.485279+08:00'
---
lcode 常驻/离线任务功能：不维护长驻进程，状态全部落盘，由外部定时触发（Windows 关机即丢失进程）。

目录 .lcode/resident/（已 gitignore）：queue.json（任务状态）、results/<id>.md（完整报告）、daemon.log、daemon.lock（单实例锁）。

四个模块：
1. queue.py —— JSON 数组队列，字段 id/prompt/status/created/started/finished/result_file/summary/error。写 queue.json.tmp 后 os.replace 保证原子；JSON 损坏改名 queue.json.corrupt-<ts> 并返回空队列；启动时 reset_running() 把残留 running 改回 pending（僵尸任务复活）。
2. runner.py —— TaskRunner 走无头 Agent：provider/MCP/权限引擎在 start() 建一次，但每条任务新建 Conversation + SessionRuntime（上下文隔离）。用 agent.run_to_completion()（is_subagent=True，不传 approval_upgrader，ASK 降级为 DENY_ONCE 防止挂起）；asyncio.timeout 控制单条超时。
3. daemon.py —— --resident-once 跑一轮即退（给计划任务用）；--resident 轮询（调试用）。单实例锁存 pid，拉起时先 _pid_alive 探活，Windows 用 OpenProcess+GetExitCodeProcess 判 STILL_ACTIVE(259)，探测失败保守返回 True。单条失败只标 failed，整轮异常只记日志。
4. safeio.py —— pythonw.exe 下 sys.stdout/stderr 为 None，print(file=sys.stderr) 会 AttributeError 崩溃；warn() 做 None 兜底并吞 UnicodeEncodeError/ValueError/OSError（GBK 终端写 emoji）。

另给 Agent 增加 denied_text 参数（默认「用户拒绝执行」保兼容），常驻模式传入「常驻模式没有人在场审批，该工具被自动拒绝。这不是用户否决你——请不要重试同一个调用……」，避免模型误以为用户在场否决而反复重试。

用法：lcode --enqueue "任务" 入队；lcode --tasks 查看；lcode --resident-once 手动跑。权限档 --resident-mode default（只读）/acceptEdits/bypassPermissions，黑名单与路径沙箱在所有模式生效。参数：--resident-batch 5、--resident-turns 20、--resident-timeout 900、--interval 300。定时：scripts/resident_task.ps1 -Time 03:00 注册计划任务（自动选 pythonw.exe，带 StartWhenAvailable，-Remove 删除）。
