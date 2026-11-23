---
type: project_knowledge
title: lcode 语音唤醒误听词 + 自启真实来源（P0/P1 落地）
created: '2026-11-23T11:20:00+08:00'
updated: '2026-11-23T11:20:00+08:00'
---
两件事都是 2026-11-23 实机排查出来的，分别对应「跑的不是这份代码」和「唤醒词被当成指令」。

## 一、自启跑的是旧副本（P0）

现象：改了 src/lcode/voice/ 但语音行为完全不变。根因不是代码没生效，是**根本没跑这份代码**。

1. 开机自启的入口是启动文件夹里的一个 .lnk（`Lcode语音助手.lnk`），指向 `C:\Users\29877\.lcode\voice\.venv\Scripts\pythonw.exe wake.py`——`~/.lcode/voice/wake.py` 是整合进核心代码前的 686 行原版，和 src 早已分叉。
2. `~/.lcode/voice/` 里还散着 bridge.py / dsp.py / speech.py 等 20 多个副本，都是那次搬家的遗留。
3. 计划任务里没有 voice（只有 lcode-boot-brief / Lcode-CD守护 / lcode-mail-watch）。
4. `.lcode/config.yaml` 里当时没有 voice 段，唤醒词/门限全是代码默认。

修法：
- `scripts/voice_autostart.ps1`（新增）：在 shell:startup 建 `Lcode-voice.lnk` → `.venv\Scripts\pythonw.exe -m lcode --voice --voice-project <项目根>`，并**先删掉任何「跑 wake.py」或「指向 \.lcode\voice\」的旧快捷方式**（否则两个抢麦克风）。
- 为什么不用 `scripts/voice_task.ps1`：这台机器上 `Register-ScheduledTask` 对非提升 shell 返回 0x80070005（拒绝访问），最小化的 AtLogOn 任务也一样。启动文件夹快捷方式不需要任何权限，效果相同；开机时音频服务没就绪由 `open_input()` 的 8 次×5 秒重试兜住，不需要人为延迟。
- 旧副本整体移进 `~/.lcode/voice/_legacy/`（29 个文件，只归档不删除——实机调参记录还在里面）。
- 补了 `.lcode/config.yaml` 的 voice 段。

## 二、唤醒词被当成指令（P1）

现象（commands.jsonl 实证 6 条）：喊「小可小可」，vosk 把同一句识别成「小口小口」；`process_text` 在 LISTENING 状态把整句当指令入队，Agent 白跑几十秒后回「我没听清」。

根因两条：
1. `strip_wake` 只认正式唤醒词，误听词命中不了 → 回落到「整句当指令」；
2. 更隐蔽：partial 结果先命中唤醒词触发 `wake_up()` 进 LISTENING，随后同一句的 final 结果（内容是唤醒词本身）落到 LISTENING 分支，长度 ≥ MIN_COMMAND_CHARS 就被发出去。

修法（都在 src/lcode/voice/）：
- config.py 新增 `DEFAULT_WAKE_NEAR_MISSES`（15 个：小口/小柯/小科/小客/小刻/小珂/小壳/小颗/小阔/小渴/晓可/肖可/筱可/削个/肖克）与 `VoiceConfig.wake_near_misses`（None=默认，[]=显式关闭），解析走新的 `_word_list`（字符串或列表都认）。`VoiceOptions` 的默认值同样来自这里。
- `strip_wake(text, wakes, near_misses=())`：正式词命中优先；否则把误听词全删掉，**只有删完什么都不剩**才判为「只是喊了一声」（`(True, "")`）。所以「削个苹果」不会被拆成唤醒+「苹果」。
- 新增 `strip_leading_wake(text, words, min_repeat=1)`：反复剥**开头**的唤醒词/误听词，解决「小口小口，现在几点」一次说完要剥两层的问题。只认开头（句中的「小口」可能是「瓶口小」），误听词用 `min_repeat=2`（单遍「削个木头」是正常指令）。
- `process_text` 两个分支都接上了：LISTENING 里剥完为空 → 延长 deadline（`→ 还在等`），不再入队；IDLE 里认不出正式词时再试「连喊两遍的误听词」。
- 主循环的 partial 提前唤醒、超长强制收尾的 tail 处理也一并传了 near_misses。

验证：`--voice-simulate` 回放 SAPI 合成的「小可小可，现在几点」（captures/sim_wake.wav），日志出现 `…小口 小口` 但**没有**入队，最终只入队 `现在几点`；测试 67 个全绿（新增 9 个：误听整句命中、误听+指令不拆、min_repeat、partial/final 双消费）。

教训：`~/.lcode/voice/captures/cap*.wav` 那几段真实录音里，vosk 只认得出「现在」——老录音对验证唤醒词没用，得用 SAPI 合成一段确定含唤醒词的音频。
