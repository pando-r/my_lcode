---
type: project_knowledge
title: lcode 语音连续对话（话题键 + 追问窗口）
created: '2026-11-22T18:40:00+08:00'
updated: '2026-11-22T18:40:00+08:00'
---
lcode 语音通道原先「一次唤醒只能问一句、且每句都是全新会话」，两处根因与两处修法：

根因 1（没有追问窗口）：wake.py 的 handle_command 消费掉指令后立刻 `_state = IDLE`，8 秒 AWAIT_TIMEOUT 在 Agent 答完之前早已过期。
根因 2（没有上下文）：voice/bridge.enqueue 没有传 `thread`，而 daemon._prompt_with_thread 在 `not task.thread` 时直接返回原 prompt；runner 每条任务新建 Conversation。

修法 A（话题键）：VoiceAssistant._current_thread() 生成 `voice-<YYYYMMDD-HHMMSS>-<会话序号>`，透传 bridge.enqueue(thread=...)。同一段对话共用键 → daemon 把最近 3 轮问答（THREAD_HISTORY_LIMIT=3，问题截 400 字、回答截 800 字）拼进 prompt。空闲超过 THREAD_IDLE=180 秒换新键。序号来自 _sessions，避免同一秒内重开撞车。
修法 B（追问窗口）：answer_voice 结束后调 _open_followup(turn)，把状态设为 LISTENING、deadline = now + FOLLOWUP_TIMEOUT(12 秒)、beep(False) 一声轻响。三个前提缺一不可：opts.conversation 为真（配置 voice.conversation / --voice-no-conversation 关闭）、`_stream is not None` 且非静音（回放模式没有后续输入）、turn 仍是 self._turn（回答期间用户又问新问题时不抢状态）。

顺带修掉的第三处：greet() 用 speak_async 念「在」时麦克风还开着，自己的声音被录回去转成「小口」「逍客」这类伪指令。现在主循环在 `_tts_running()` 为真期间只 read 不吃（排空缓冲不喂识别器）；判据是「PowerShell 进程还活着」（Speak 同步调用，进程退出即念完），上限 GREET_MUTE_MAX=6 秒防卡死。

测试：tests/test_voice.py 54 个、tests/test_resident.py 新增 4 个（话题上下文注入、未完成任务不进上下文、无 thread 即单轮、不同 thread 不串味）。
