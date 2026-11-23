---
type: project_knowledge
title: lcode 语音看门狗（麦克风流卡死自救）+ 三个只有跑真循环才发现的 bug
created: '2026-11-23T17:45:00+08:00'
updated: '2026-11-23T17:45:00+08:00'
---
2026-11-23 实机故障：「小可」静默失聪 —— 进程活着、单实例锁端口 47311 还占着、`wake.log` 从 15:54 之后一句不写，喊什么都不应，CPU 也不再动。原因：主循环卡在 `stream.read()` 上永不返回。从外面看和「安静地在听」一模一样，只能靠杀进程重启。

现在加了看门狗（`src/lcode/voice/wake.py`）：

- 判据只有一个：`_beat`（主循环最后一次**真的读到音频块**的时刻），每次 `read()` 成功刷新。
- 巡检线程 `_watchdog_loop` 每 `WATCHDOG_TICK`（5 秒）看一次，超过 `voice.watchdog`（默认 `WATCHDOG_STALE` = 90 秒）无心跳就 `stream.abort()` 捅醒阻塞读（**必须绕开 `_audio_lock`**，主线程正抱着它卡在读里）+ 举 `_stalled` 牌。
- 主线程从 read 出来看到牌 → `close()` 旧流 → `open_input()` 重开 → 重建识别器，日志 `麦克风已重建，继续听`。
- `say()` 前后给 `_speaking` 加一减一：念一段 420 字回答能念 80 秒，这期间麦克风本来就停着，不算卡死（比按估计时长豁免精确）。
- 重开失败退避 `REOPEN_BACKOFF`（10 秒），避免「读失败→重开」忙循环。
- 配置 `voice.watchdog: 90`，`0` = 关；CLI `--voice-watchdog`。

三个只有跑真 `run()` 循环才发现的 bug（单测跑不到）：

1. **重开要先刷心跳**：`open_input()` 内部会重试数秒，这窗口里看门狗又举一次手 → 连着建两条流。修复：进 `_reopen_stream` 第一件事就刷 `_beat`。
2. **重开失败要放回 `_stalled`**：留在 True 会让看门狗以为「已在处理」，从此不再重试，麦克风永久丢失。
3. **不能用 `self._stream is None` 判断在听麦克风**：重开失败时流短暂为 None，主循环会掉进「回放 wav」分支 `next(None)` → TypeError 崩掉。修复：`run()` 里显式记 `mic_mode`。

验证手段：`scripts/voice_watchdog_probe.py` —— 用「read() 永远不返回」的假流跑真实 `run()`，`--watchdog 2`，期望 `recoveries=1`、`result=OK`。改完 `src/lcode/voice` 记得重启常驻实例（`~/.lcode/voice/wake.log` 里应出现 `看门狗：开（90 秒…）`）。

测试：`tests/test_voice.py` 110→111 个用例（含 `test_run_does_not_fall_into_replay_branch_when_stream_is_none`）。全量 `pytest tests -q` = 1133 passed / 3 skipped / 2 failed（失败是本机缺 Pillow 的 test_media，属既有）。
