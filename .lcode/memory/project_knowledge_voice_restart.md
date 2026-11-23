---
type: project_knowledge
title: lcode 语音实例重启与「代码是否生效」验证
created: '2026-11-23T13:29:18.851432+08:00'
updated: '2026-11-23T13:29:18.851432+08:00'
---
改完 src/lcode/voice 后要重启常驻实例，否则跑的仍是旧代码（这次实机排查反复踩到）。

重启步骤（Windows）：
1. `Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'"` 列出命令行含 `--voice` 的进程，`taskkill /F` 全部杀掉。注意**会有两个 pythonw**（父+子），都杀。
2. `Start-Process -FilePath .venv\Scripts\pythonw.exe -ArgumentList '-m','lcode','--voice','--voice-project','<项目根>' -WorkingDirectory '<项目根>'`。
3. 等 15 秒，看 `~/.lcode/voice/wake.log` 新增段。

验证三件事：
- `netstat -ano | findstr 47311`：只有绑 47311 的才是真正干活的那个 PID（单实例锁就是绑端口）。
- 日志里有 `云端精转写：开（…）` 才说明 `voice.cloud_asr: true` 生效。
- 源代码自检：`cloud.py` 含 `ThreadPoolExecutor`，不含裸 `asyncio.run(audio_mod`。

开机自启的快捷方式指向同一份 `pythonw -m lcode --voice`，所以重启只需杀进程再起一次，不用动快捷方式。
