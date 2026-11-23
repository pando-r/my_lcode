# 语音唤醒：喊一句，它开口回答

## 解决的问题

Lcode 是终端程序，`/audio` 只能转写「你手上已有的音频文件」。想要 Siri 那种
「张嘴就能问」的体验，缺的是三样东西：常驻麦克风、唤醒词、把回答念出来。

这一段把三样都补上了，**但刻意做成可选插件**：

```
pip install lcode[voice]          # vosk + sounddevice + numpy（约 100MB）
python -m lcode.voice.download    # 中文小模型 42MB
lcode --voice                     # 开始监听
```

不装的话，`lcode` 的启动、提示词、工具列表**一个字节都不变**——语音是
`lcode[voice]` 里的依赖，核心包里没有。

## 全链路

```
麦克风 16k ──> 高通滤波 ──> vosk 小模型 ──> 唤醒词命中
                                ↓
                        入队 resident/queue.json（origin=voice）
                                ↓
                       sys.executable -m lcode --resident-once
                                ↓
                  results/job_xxx.md ──> 清洗 markdown ──> 神经语音念出来（edge）
```

识别在本机（vosk + SenseVoice 都不联网）；**朗读默认走微软在线神经语音**，
要联网、要念的文字会发给微软；只有中间那句「回答」走模型。
不想联网就把 `voice.tts_engine` 改成 `sapi`（Windows 自带，音色偏机械）。

## 为什么唤醒词是「小可」而不是「Lcode」

vosk 中文小模型没有英文音素，念 "Lcode" 会被识别成「埃欧可德」这类四不像。
「小可」（xiǎo kě）与 Lcode 的尾音接近，模型识别稳定，所以拿它当**音近唤醒词**。

内置一组常见误写，全部认（漏唤醒比误唤醒烦人——误唤醒最多多问一遍）：

```
小可小可 / 你好小可 / 你好可可 / 嘿小可 / 黑小可 / 为小可 / 小可 / 可可
```

改词只需配置里一行 `voice.wake_words`，不用改代码。换词后建议用
`--voice-simulate` 回放验证，别直接靠嘴试。

### 误听词：认得出，但不许当指令

唤醒词还有一层「音近误听」：同一句「小可小可」，小模型时不时听成
「小口小口」「削个」「肖克」。这些词单独列在 `voice.wake_near_misses`
（默认 15 个，含 `小口 / 小柯 / 削个 / 肖克 …`），规则只有一条：

**整句除了误听词什么都不剩时，才算「只是喊了一声」。**

不这样区分就会两头出错：

- 不认误听词 → 同一句话被听错就等于没喊醒，得重复喊；
- 无脑认 → 「削个苹果」被拆成唤醒 + 指令「苹果」发出去。

实机日志里的真实故障（`~/.lcode/voice/commands.jsonl`）：喊完唤醒词，
vosk 把这句话的 final 结果识别成「小口小口」，旧逻辑在 LISTENING 状态
直接把整句当指令入队，Agent 白跑几十秒后回一句「我没听清」。

修法分两层：

1. `strip_wake` 增加误听词回退：整句都是误听 → 判为唤醒、指令为空，
   调用方延长等待（`→ 还在等`）而不是入队；
2. `strip_leading_wake` 反复剥开头的唤醒词/误听词——一句话说完时
   「小口小口，现在几点」要剥掉两层才剩得下「现在几点」。
   单个误听词不剥（`min_repeat=2`），否则「削个木头」会被吃掉开头。

改过 `wake_words` 就该顺手改 `wake_near_misses`；写成 `[]` 可以整条关掉。

## 关键决策

**1. 走 resident 队列，不直接调 Agent。**

语音是一句几秒、Agent 干活几十秒的两段式节奏。走队列的好处：

- 中间不必一直占着麦克风（回答期间可以让它继续听下一句唤醒词）；
- 直接复用常驻模式的权限、记忆、MCP、子 Agent——一行不用重写；
- `origin=voice` 这个来源标记早就留好了（`queue.py:31`），daemon 见到它会
  跳过 email 通知通道：答案都念出来了，再发一封邮件是重复且打扰。

**2. 入队改成同进程调用，只有「催一轮」起子进程。**

原版（`~/.lcode/voice/bridge.py`）全靠 `shutil.which("lcode")` 找可执行文件再起
子进程入队。那个假设很脆：一旦安装方式变了（uv tool → pip -e，或者那个 shim 被
删掉），语音会在**唤醒成功之后**才报「找不到 lcode 命令」——体验最差的时机。

现在 `TaskQueue.enqueue()` 是直接调用；催一轮用 `sys.executable -m lcode`，
正在运行的这份解释器里一定有 lcode，不用去 PATH 里猜。

**3. 朗读有两个引擎，默认是在线神经语音。**

K 的原话是「音色有点人机」。原因很具体：Windows 自带的中文 TTS 只有
`Microsoft Huihui Desktop` 这一档，是十几年前的**拼接音**——调参数、改语速都救不了。
所以加了 `edge-tts`（Edge 浏览器「大声朗读」用的同一个服务，免 key）：

| 引擎 | 音色 | 联网 | 依赖 |
|---|---|---|---|
| `edge`（默认） | `zh-CN-XiaoxiaoNeural` 等 10 档，几乎听不出是合成 | 是 | `pip install edge-tts` |
| `sapi` | `Microsoft Huihui Desktop`，明显人机 | 否 | 无 |

两条路的共同点（这些没变）：

- 都绕一道 PowerShell；中文文本走 UTF-8 临时文件而不是命令行参数——参数会
  经历一次 GBK 往返，中文很容易变乱码；
- **markdown 必须洗掉再念**。这是语音体验里性价比最高的一步：
  `**结论**` 会被念成「星号星号结论星号星号」，表格念成「竖线项目竖线状态竖线」。
- 都**不依赖 `numpy/sounddevice/vosk`**，所以不装 `lcode[voice]` 也能
  `from lcode.voice import speech; speech.speak("完成了")`。

`edge` 多出来的那个问题是「mp3 谁来解码」：`winsound` 只吃 wav，转码要么装
ffmpeg 要么装 pydub。最后用的是 `System.Windows.Media.MediaPlayer`（WPF 自带），
直接放 mp3，而且 `NaturalDuration` 一到手就能按真实时长等待——比 SAPI 那句
「进程退出就是念完了」还准。**进程活着 = 还在念**这个判据两种引擎一致，
`_tts_running()` 一行都不用改。

`edge` 合成失败（断网、微软抽风）时自动回退 SAPI：那一刻音色会变回人机，
但至少还有声音，且日志里会写一行「edge TTS 不可用，这次改用 Windows 自带语音」。

换音色（音色只能靠耳朵定，所以留了试听入口）：

```bash
lcode --voice-list-voices                              # 列出两套音色，标出当前那个
lcode --voice-say "我是小可，现在可以说话了"            # 用当前音色念一句
lcode --voice-say "我是小可" --voice-tts-voice zh-CN-YunxiNeural   # 试别的
```

定下来写进配置：

```yaml
voice:
  tts_engine: edge                   # edge / sapi
  tts_voice: zh-CN-XiaoxiaoNeural    # 温柔自然；YunxiNeural 年轻男声……
```

两种引擎各记一个坑：

- **音色名不能混用。** `Microsoft Huihui Desktop` 塞给 edge 会 400，表现只是
  「念不出来」；所以 `_edge_voice()` 把不认识的名字静默换回默认音色。
  反过来 edge 的名字塞给 SAPI 会让 `SelectVoice` 失败，`try/catch` 兜住，退回系统默认。
- **配了 edge 但没装 edge-tts，必须显式降级。** `VoiceAssistant.tts_engine()`
  报的是**实际**引擎，启动日志会写 `! 配置要的是 edge，但没装 edge-tts，已降级`。
  否则日志说在用 edge、耳朵听到的却是 Huihui，这种「配了但没生效」最难查。

**4. 念回答前必须关麦克风。**

否则自己的声音被麦克风收回去 → 触发唤醒词 → 自己把自己叫醒，死循环。
所以 `say()` 的顺序是：暂停流 → 同步念完 → 重开流并丢掉尾部残余 → 重建识别器。
全程持 `audio_lock`，主循环此刻读不到麦克风。

`greet()`（念那个「在」）是例外：它用 `speak_async`，因为等 PowerShell 起进程要
几百毫秒，同步念会把主循环卡住、麦克风缓冲直接溢出。代价是这段音频流还开着，
于是主循环在朗读进程存活期间**只读不吃**（读是为了排空缓冲）：`_tts_running()`
判据是「播放进程还活着」——播放是同步的，进程退出就等于念完了。
没有这一层，「在」会被转写成「小口」「逍客」这类伪指令，日志里全是它。

换上在线语音之后这一层还多了一个状态：**合成要 1~2 秒**，这段时间连播放进程
都还没有，`poll()` 无从判断。所以 `greet()` 把整段（含挑音色）丢进后台线程，
并用 `_tts_pending` 标记「正在准备说话」——主循环同样丢弃音频块，否则准备阶段的
环境声会被当成指令。合成线程卡死时由 `_speak_until`（6 秒）兜底放行。

副作用是**「在」和「好」会抢话**：以前 SAPI 几乎立刻出声，用户说完指令时「在」
早念完了；在线语音有 1~2 秒合成延迟，人经常在「在」还没响的时候就说完了整句，
`say("好")` 一出声就和它叠在一起。所以 `say()` 里加了一步 `_drain_tts()`：
先等「在」念完（最多 `DRAIN_TTS_MAX`＝3 秒），再念下一句。
上限刻意压短——宁可偶尔叠音，也不能把主流程卡住。

**5. 连续对话：一个话题键 + 一个追问窗口。**

早期版本每唤醒一次只能问一句：`handle_command` 消费掉指令后立刻回 IDLE，
8 秒的 `AWAIT_TIMEOUT` 在 Agent 答完之前早就过期了。更糟的是每一轮都新建
`Conversation`（`resident/runner.py`），语音入队又没带 `thread`，所以第二句
「那它呢」模型完全不知道在说什么。

现在两件事一起补：

- **话题键**（`_current_thread`）：同一段对话共用一个 `voice-<时间>-<序号>`，
  透传进 `TaskQueue.enqueue(thread=...)`；daemon 的 `_prompt_with_thread`
  本来就有（邮件追问用的），直接把最近 3 轮问答拼进上下文。
  空闲超过 `THREAD_IDLE`（180 秒）就换新键——「半小时后再问一句」不该被
  塞进一堆无关旧对话里。
- **追问窗口**（`_open_followup`）：回答念完之后再开 `FOLLOWUP_TIMEOUT`（12 秒）
  的监听，追问不用重新喊唤醒词，一声轻响（`beep(False)`，与唤醒的上行双音区分）
  表示「轮到你说」。

三个前提缺一不可：`conversation` 没被关、真的在听麦克风（回放模式没有后续输入）、
`turn` 还是当前轮（回答期间用户又问了新问题，旧回答回来时不能把状态抢回 LISTENING）。
关掉用 `--voice-no-conversation` 或配置 `voice.conversation: false`。

**拼上下文必须用 `say`（用户原话），不能用 `prompt`。** 语音入队的 prompt 是
`VOICE_HINT + "\n\n" + 原话`，自带 45 字提示词；早期直接拿 prompt 拼历史，于是
每轮都重复一遍「不要用列表、表格、代码块」，3 轮 135 字纯噪音，还会让模型
反复看到格式指令而更容易跑偏。现在 `Task.say` 单独存原话（`queue.py`），
`bridge.enqueue(..., say=text)` 一路透传，`_prompt_with_thread` 优先用 `say`、
没有才回退 `prompt`（旧队列兼容）。

**6. 高通滤波默认开，不是可调参数。**

实测这台机器两路 MME 麦克风在**完全安静**时 RMS ≈ 1000（约 -30dBFS），
频谱分析：97% 能量在 300Hz 以下，主峰约 34Hz——是低频嗡声（风扇/机箱共振），
不是宽带白噪。人声在 300~3400Hz，一个 180Hz 高通几乎不伤语音，却能把嗡声
压掉 30dB 以上。不滤波时 vosk **什么都认不出**；加上之后立刻可用。

**7. 选麦克风看电平，不看名字。**

很多机器有多个「立体声混音」「虚拟设备」，名字看不出谁能用。所以逐个录 0.6 秒
测 RMS，静音（< 100）的直接跳过。MME 主机 API 优先：16k 采样率在 MME 上几乎
所有设备都支持，走 WASAPI 共享模式经常要求 44.1k/48k，重采样由驱动做，掉帧不稳。

**8. 麦克风打不开要重试，不要退。**

开机自启时 Windows 音频服务可能还没就绪，第一次必然打不开。原版直接抛异常退出，
表现就是「语音助手开机后从来没反应」——而它只是比音频服务早起了几秒。
现在重试 8 次、每次间隔 5 秒。

**9. 单实例锁用绑本地端口，不用 pid 文件。**

两个实例同时开麦克风，会各答一遍同一句话。绑 `127.0.0.1:47311`，进程被强杀时
操作系统立刻释放；pid 文件做不到（留下一个骗人的锁）。

**10. 库代码不留全局开关。**

原版靠 `globals()["QUIET_IO"] = ...` 控制回放时静音。搬进核心代码时改成实例字段
`_mute`——全局变量在库代码里是毒药，两次调用会互相污染。

**11. 看门狗：麦克风流卡死时自己换一条。**

最阴的故障不是崩溃，是**静默失聪**。2026-11-23 实机就这样：

```
[15:54:00] → 等指令超时，回到待命      ← 最后一行日志
（之后 1.5 小时零日志，喊什么都不应）
```

进程活着、单实例锁端口 47311 还占着（连「再起一份」都会被拒）、CPU 也不再动——
主循环卡在 `stream.read()` 上永远不返回。从外面看，它和「安静地在听」完全一样。

看门狗（`VoiceAssistant._watchdog_loop`）的判据就一个：**主循环最后一次真的读到
音频块的时间**（`_beat` 心跳，每次 `read()` 成功就刷新）。巡检线程每 5 秒看一次，
超过 `voice.watchdog`（默认 90 秒）没有心跳就：

1. `stream.abort()` 把卡住的 `read()` 捅醒——**必须绕开 `_audio_lock`**，
   主线程此刻正抱着那把锁卡在读里，再去要锁就是自己跟自己死锁；
2. 举 `_stalled` 牌；
3. 主线程从 `read()` 出来，看到牌就 `close()` 旧流、`open_input()` 重开一条、
   重建识别器，日志写 `麦克风已重建，继续听`。

三个细节都是踩出来的：

- **念回答时不能算卡死。** `say()` 会先停麦克风，一段 420 字的回答能念 80 秒，
  这期间本来就读不到音频。所以 `say()` 前后给 `_speaking` 加一减一，看门狗见它非零
  就跳过（`_speak_until` 那种按估计时长豁免的做法不可靠，这里是精确的）。
- **只由主线程换流。** 看门狗只 `abort()`，不 close、不重开——流的生命周期归
  主线程一个人管，否则两个线程同时 close 一条流会撞出更难查的错。
- **重开失败要退避。** 蓝牙耳机没连回来、设备被别的程序独占时，重开必然失败；
  不退避就是「读失败 → 重开 → 立刻再读」的忙循环，烧 CPU 还刷屏日志
  （`REOPEN_BACKOFF`＝10 秒）。

实测验证：`scripts/voice_watchdog_probe.py` 用「`read()` 永远不返回」的假流跑
**真实的 `run()` 主循环**（不是单测里那个精简版），`--watchdog 2`，输出：

```
streams_handed=['stalled', 'good']
old_stream_aborted=True
recoveries=1
result=OK
! 看门狗：2 秒没读到音频块（麦克风流卡死），第 1 次重建
麦克风已重建，继续听
```

第一版跑出来是 `recoveries=2`「连着建两条流」，由此翻出三个只有跑真循环才看得见的
bug（单测里都测不到）：

1. **重开时要先刷心跳。** `_reopen_stream` 原本开完流才刷 `_beat`，
   而 `open_input()` 内部会重试好几秒——这个窗口里看门狗又举了一次手。
   现在进函数第一件事就是刷心跳。
2. **重开失败要把牌放回去。** `_stalled` 若留在 True，看门狗以为「已经在处理」，
   从此不再试第二次，麦克风就永远没了。
3. **不能用 `_stream is None` 判断是不是在听麦克风。** 重开失败时流会短暂变 None，
   主循环按「没流 = 回放」分支走，就会 `next(None)` 直接 TypeError 崩掉。
   现在 `run()` 里显式记 `mic_mode`。

关掉用 `voice.watchdog: 0` 或 `--voice-watchdog 0`——调麦克风参数时不想被自动
重建打断，才需要关；日常别关。

## 开机自启：启动文件夹的快捷方式，不是计划任务

`scripts/voice_task.ps1`（注册计划任务）在这台机器上跑不通：
`Register-ScheduledTask` 对非提升权限的 shell 返回 `0x80070005`（拒绝访问）。
试过最小化的 `New-ScheduledTaskAction` + `AtLogOn` 也一样——不是参数写错，
是这台机器的策略限制。

所以改走启动文件夹：

```bash
powershell -ExecutionPolicy Bypass -File scripts\voice_autostart.ps1
powershell -ExecutionPolicy Bypass -File scripts\voice_autostart.ps1 -Remove
```

它做的事：删掉任何「跑 `wake.py`」或「指向 `~/.lcode/voice`」的旧快捷方式，
再在 `shell:startup` 建一个 `Lcode-voice.lnk`，指向
`.venv\Scripts\pythonw.exe -m lcode --voice --voice-project <项目根>`。

没有延迟启动也没关系：`open_input()` 本来就有 8 次 × 5 秒的重试，
专门用于「登录时音频服务还没就绪」。

**为什么必须删旧快捷方式**：这台机器之前自启的是 `~/.lcode/voice/wake.py`
——一份 686 行的旧脚本副本，和 `src/lcode/voice/` 早已分叉。表现就是
「改了代码但语音行为没变」。现在那份副本整体收进了
`~/.lcode/voice/_legacy/`（只归档不删除，实机调参记录还在里面）。

## 用法

```bash
lcode --voice                  # 常驻监听（要的就是 Siri 那样）
lcode --voice-selftest         # 录 4 秒，验证麦克风+识别，不入队
lcode --voice-list-devices     # 列音频设备（找不到麦克风时先跑这个）
lcode --voice --voice-debug    # 打印识别片段，调唤醒词时用
lcode --voice --voice-no-speak # 只入队，不念回答（结果去终端看）
lcode --voice --voice-no-conversation  # 每问一句都要重新喊唤醒词（关掉追问窗口）
lcode --voice --voice-refine sensevoice   # 单次指定精转写后端（覆盖配置）
lcode --voice --voice-watchdog 0          # 关掉看门狗（调麦克风时用）
lcode --voice --voice-tts-voice zh-CN-YunxiNeural   # 临时换个音色试试
lcode --voice --voice-tts-engine sapi     # 临时退回离线语音
lcode --voice-list-voices                 # 列出所有可选音色（不需要麦克风/模型）
lcode --voice-say "试一下音色"             # 直接念一句，不用喊唤醒词
```

调参用回放，别靠嘴试（回放默认静音，避免调试时被自己的电脑念出来）：

```bash
lcode --voice --voice-simulate 录音.wav
lcode --voice --voice-simulate 录音.wav --voice-simulate-audio  # 真出声
```

对话形态（实机）：

```
你：小可小可
它：在                      ← 同时两声上行提示音
你：现在几点
它：好                      ← 立刻应一声，LLM 要几十秒
   （几十秒后）
它：现在是下午五点四十八分。
    ← 一声轻响：轮到你说
你：那明天呢                ← 不用喊唤醒词，且它知道「那」指的是什么
它：……
```

也支持一句话说完：「小可，现在几点」。

念完回答会开一段追问窗口（12 秒）。窗口过了、或者空闲超过 3 分钟，
下一句就回到「先喊唤醒词」，上下文也重新开一段（见决策 5）。

**精转写：vosk 只当触发器，整句交给 SenseVoice（本地、免费）**

vosk 42MB 小模型只够判断「有没有人在叫它」。它把「小可小可，现在几点」听成
「小口小口」、把「现在几点」听成「法」——这是模型能力的上限，调参到不了。
所以加了一层精转写：**vosk 听到一句话说完，把这段音频交给更强的模型重转一次**。

三个后端，`voice.refine` 选：

```yaml
voice:
  refine: sensevoice    # off / sensevoice / cloud
  refine_min_seconds: 0.6   # 太短的片段不值得转
  refine_fallback: true     # 转失败就用 vosk 的结果
  refine_threads: 4         # onnxruntime 线程数
```

| 后端 | 模型 | 大小 | 延迟（3.2 秒音频） | 联网 | 费用 |
|---|---|---|---|---|---|
| `off` | vosk-small-cn | 42MB | 942ms | 否 | 0 |
| **`sensevoice`** | **SenseVoice-Small int8** | **228MB** | **80ms** | **否** | **0** |
| `cloud` | qwen3-asr-flash | — | 642ms + 网络 | 是 | ¥0.00022/秒 |

实测同一段 SAPI 合成音频：

```
vosk:       小可 小可 现在 几点      ← 词间带空格，唤醒词也在里面
SenseVoice: 小可小可，现在几点？      ← 带标点、词不散
```

**为什么换掉云端**：SenseVoice 不但免费、不联网，还比云端快 8 倍、比 vosk 快 12 倍，
准确率与云端持平。代价只有一次性 228MB 磁盘 + 启动时 3 秒加载（助手启动时会预热，
不占你第一句话的时间）。

```bash
python -m lcode.voice.download --sensevoice   # 下载（走 hf-mirror，GitHub 直连会超时）
lcode --voice --voice-refine sensevoice       # 单次指定（覆盖配置）
lcode --voice --voice-refine off              # 临时退回纯 vosk
```

**为什么用 sherpa-onnx 而不是 FunASR / faster-whisper**：

- FunASR 要装 torch（2GB+），而我们只需要「短句转写」一件事；
- faster-whisper 依赖 ctranslate2，中文准确率低于 SenseVoice
  （公开评测 92.3% vs 91.8%）；
- sherpa-onnx 只依赖 onnxruntime，pip 包 19MB，不拖 torch。

**为什么 vosk 不能一起去掉**：vosk 是**流式**的，能边听边吐 partial——你喊出
「小可」半句它就响应了。SenseVoice 是**非流式**的，必须等整句说完才能转。
所以分工是固定的：vosk 管唤醒与断句，SenseVoice 管转写。

只在这两种情况下才转：已经在听指令、或这句话里认出唤醒词。电视声、闲聊不转。

### 实机接通时踩的两个坑

**坑 1：`asyncio.run()` 不能在运行中的事件循环里调用。**

`lcode.audio.transcribe` 是 async 的，第一版图省事写成
`asyncio.run(audio_mod.transcribe(...))`。但语音主循环**本身就在事件循环里**
——`cli.main()` 是 `asyncio.run(_amain(args))`，而 `_amain` 里同步调用
`assistant.run()`。于是每次精转写都炸：

```
! 云端精转写异常：RuntimeError: asyncio.run() cannot be called from a running event loop
RuntimeWarning: coroutine 'transcribe' was never awaited
```

表现是「看起来能用」——因为 fallback 兜住了，静默回落到 vosk，只有开 `--voice-debug`
才看得出来云端一次都没成功过。

修法（`cloud.py:transcribe_pcm`）：把协程交给**新线程**跑。新线程没有事件循环，
`asyncio.run` 才合法；协程对象不绑定 loop，跨线程 await 是安全的。

```python
coro = audio_mod.transcribe(audio_cfg, path, language=language)
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    transcript = pool.submit(asyncio.run, coro).result()
```

对应测试：`test_transcribe_pcm_survives_running_event_loop`，它在外层协程里调用
同步函数，正是那个故障的最小复现。

**坑 2：回放 wav 没有尾部静音时，vosk 永远不吐 `done`。**

vosk 靠内部 VAD 判断「这句说完了」。`sim_wake.wav` 是 SAPI 直接合成的，末尾没有
静音，于是识别器一直不返回 final 结果，程序走的是「文件读完 → `FinalResult()`」
那条 flush 路径。而我只在 `done` 分支接了 `refine()`，漏了 flush 分支——
回放测试就完全看不到云端精转写。

两处都修了：flush 分支也走 `_should_refine` + `refine`。同时补了
`captures/sim_wake_tail.wav`（末尾加 1.5 秒静音），它更贴近真实麦克风
——人说完话自然会停顿。

**教训：** 语音这种「有 fallback 兜底」的链路，失败是静默的。测的时候必须看
`--voice-debug` 日志里有没有那行 `→ 精转写（…）`；只看到「能回答」不代表
精转写真的跑了——回落到 vosk 同样能回答，只是会把字听错。

## 能多轮对话吗？能，但有三条边界

**能。** 一次唤醒可以连着问，不用每句都重新喊。实现是「一个话题键 + 一个追问窗口」
（见决策 5）。实际跑出来是这样：

```
你：小可小可
它：在                     ← 上行双音
你：现在几点
它：好                     ← 立刻应一声，LLM 要几十秒
    （几十秒后）
它：现在是下午一点三十分。
    ← 一声轻响：轮到你说（追问窗口开 12 秒）
你：那明天呢                ← 不用喊唤醒词，且它知道「那」指什么
```

### 边界 1：追问窗口只有 12 秒

念完回答后开 12 秒监听窗口，`FOLLOWUP_TIMEOUT`。窗口过了、或者空闲超过
`THREAD_IDLE`（180 秒），下一句就回到「先喊唤醒词」，上下文也重新开一段。

12 秒是刻意压短的：窗口太长会让它在你不说话时一直占着麦克风。
想连着聊很久的话，每轮都得在这 12 秒里接上。

### 边界 2：上下文只带最近 3 轮

`THREAD_HISTORY_LIMIT=3`，每轮问题截 400 字、回答截 800 字
（`THREAD_PROMPT_LIMIT` / `THREAD_ANSWER_LIMIT`，都在 `daemon.py`）。
聊上十轮，最早几轮会被挤掉——这是有意的，语音对话不该把模型窗口撑爆。

拼接由 `Daemon._prompt_with_thread` 做，第二条消息实际收到的 prompt 长这样：

```
下面是同一个话题里之前的往来（供你理解上下文，不要重复作答）：

提问：现在几点
回答：现在是下午一点三十分。

---
本次新消息：
【语音提问】…不要用列表、表格、代码块和标题。

那明天呢
```

注意历史里是**干净的原话**「现在几点」，没有那段 45 字的语音提示词。
这一条踩过坑：早期直接拿 `task.prompt` 拼上下文，而语音入队的 prompt 是
`VOICE_HINT + "\n\n" + 用户原话`，于是每轮都重复一遍提示词——3 轮就是
135 字纯噪音，模型还会反复看到「不要用列表」，反而更容易跑偏。

修法是给任务加一个 `say` 字段单独存用户原话（`queue.py:Task.say`），
`_prompt_with_thread` 优先用它，没有才回退到 `prompt`（旧队列兼容）。
语音侧 `bridge.enqueue(..., say=text)`，`say` 一路透传到队列。

### 边界 3：只取 `done` 的轮次

还在跑（`running`）或失败（`failed`）的上一轮不进上下文：失败的任务没有可用
回答，把错误信息当上下文喂进去只会让模型顺着错误往下编。

### 想开新话题

只要停超过 3 分钟，下一句话自动算新话题（新话题键）。
也可以 `--voice-no-conversation` 彻底关掉追问窗口，每次都要重新喊唤醒词。

## 数据落在哪

跟「代码在 D、数据在 C」的约定一致，都在 `~/.lcode/voice/`：

| 路径 | 内容 |
|---|---|
| `models/vosk-model-small-cn-0.22/` | 42MB 中文模型 |
| `wake.log` | 运行日志（超过 2MB 自动清空） |
| `commands.jsonl` | 每句被识别出的指令，一行一条 |
| `_legacy/` | 整合进核心代码之前的旧脚本副本（只归档，不要再运行） |

升级/重装代码不用重下模型。

## 已知限制

- **只有 Windows。** 朗读走 PowerShell（SAPI / WPF 的 `MediaPlayer`）；麦克风侧
  （sounddevice/vosk）跨平台，但 `_NO_WINDOW`、`winsound` 提示音都是 Windows 专用。
  macOS/Linux 要补 `say` / `espeak` 分支。
- **朗读默认要联网，文字会发给微软**（`tts_engine: edge`）。回答本来就已经发给模型了，
  这里只是多一个接收方；不能接受就把 `tts_engine` 改成 `sapi`，代价是音色变回人机。
- **没有打断（barge-in）。** 它说话时你插话它听不见——麦克风这时是关的（见决策 4）。
  真做需要回声消除（AEC），否则会自我打断死循环；而且 `qwen3-asr-flash` 不是
  流式接口，要做流式 ASR 得换 WebSocket 通道。
- **精转写会给延迟再加一点**（SenseVoice 实测 80ms，可忽略；`cloud` 后端约 0.5~1 秒）。
  这段时间主循环读不到麦克风（串行等待），只在你说完一句话时发生。
- **追问窗口是定时的，不是「听到你说完为止」。** 12 秒内没开口就回到待命；
  想连着聊很久的话，每轮都得在这 12 秒里接上，否则要重新喊唤醒词。
  这个值是故意的：窗口太长会让它在你不说话时一直占着麦克风。
- **上下文是「最近 3 轮」，不是完整对话。** `THREAD_HISTORY_LIMIT=3`，
  每轮问题截 400 字、回答截 800 字（daemon 里的常量）。聊上十轮，最早的
  几轮会被挤掉——这是有意的，语音对话不该把模型窗口撑爆。
- **只有已完成（`done`）的轮次进上下文。** 上一轮还在跑或失败了，它看不到——
  拿错误信息当上下文只会让模型顺着错往下编
  （`TaskQueue.thread_history` 只取 `done`）。
- **语音任务默认只读。** 走 resident，而 resident 没有人坐在终端前审批，
  所以写文件/执行命令会被自动拒绝。要放开得给 `--resident-mode acceptEdits`，
  但那等于给「房间里随便一句话」发了写权限，想清楚再开。
- **延迟 = 模型延迟。** 从说完到开口答，实测取决于 Agent 跑多久（简单问题十几秒）。
  这不是语音层的开销，是 LLM 的。
- **唤醒词误报率跟环境强相关。** 开着吸尘器/风扇时误唤醒会明显变多；
  调高 `threshold` 能缓解，但也会让轻声说话唤不醒。
- **误唤醒之后紧跟的那句话仍会被当指令。** 2026-11-23 11:19 实测：环境声触发了
  唤醒，随后听到「小块小块去怎么样」，剥离误听词后剩「去怎么样」四个字，
  超过 `MIN_COMMAND_CHARS` 就入队了，Agent 猜着答了一轮。
  这是架构层面的固有代价——判断「这句像不像指令」需要语义理解，本地做不了。
  单条代价是一次模型调用（几十秒 + 一点 token），不会误改文件。

## 测试

`tests/test_voice.py`（122 个）覆盖**约束**而不是「麦克风真的能听」——后者依赖硬件，
CI 里跑不了。真机验证的部分（增益、噪声门限、误报率）在本文档的实测记录里。

- 唤醒词剥离：「小可小可」必须优先于「小可」，否则指令会变成「小可…」
- 误听词：「小口小口」整句只剩误听词 → 判为唤醒且指令为空；「削个苹果」
  不能被拆成「苹果」；单遍「削个木头」不剥开头
- 精转写：默认关（不调用）、替换文本、剥唤醒词前缀、空结果回落、
  fallback 关闭、异常兜底、短片段跳过、缓冲只消费一次、`_should_refine` 判定、
  **在运行中的事件循环里也能用**（那个 asyncio 故障的复现）
- SenseVoice：模型/词表缺失时的报错文案带头下载命令、空 PCM 不加载模型、
  富文本标签清洗（`<|zh|>` 这类不能进上下文）、`make_transcriber` 永不抛异常
- 指令流水写盘：测试必须落在 tmp_path。`_command_log` 默认指向
  `~/.lcode/voice/commands.jsonl`（用户级数据），漏改会往真实记录里掺假数据
- 朗读稿清洗：代码块/表格/URL/加粗必须消失，`4:19` 要转成「4 点 19 分」
- 状态机：IDLE/LISTENING 两态的转移，短指令拒绝，只听见唤醒词时延长等待
- 连续对话：话题键在空闲前保持不变、追问窗口的三个前提（开关/麦克风/轮次）、
  「回答期间又问了新问题」时旧回答不能把状态抢回 LISTENING
- greet 自录音：朗读进程活着时丢弃音频，进程退出或超时后立刻恢复
- 队列桥：`origin=voice` 与 `thread` 必须写进队列（daemon 靠它们区分通知通道和取上下文）
- 设备选择：假 sounddevice 里静音设备被跳过、全静音时回退 None
- 音频前处理：高通压掉直流、裁静音后长度变短
- 看门狗：卡死时 abort + 举手、健康流不动、念回答期间豁免、`watchdog: 0` 关掉、
  读失败/被 abort/流缺失三条路都会重开、重开失败才退避、重开失败要放回 `_stalled`
  （否则看门狗不再重试）、重开前先刷心跳（否则连建两条流）、
  看门狗已报过的故障不再重复报「读失败」
- 配置：`watchdog` 默认 90 秒、`0` 是明确关掉、负数当写错回落到默认
- 朗读引擎：默认必须是 `edge`（这条锁的是「换掉人机音」这个决定）、`sapi` 可显式
  选回、写错的值静默回默认、配置 edge 但没装 edge-tts 时 `tts_engine()` 必须报 sapi
  （否则日志说 edge、耳朵听到 Huihui）、音色名跨引擎时静默换回安全值、
  `say()` 要把引擎透传下去、`greet()` 不许阻塞主循环（在线合成 1 秒多）、
  合成期间 `_tts_running()` 仍为真、合成卡死时 6 秒后放行、
  `say()` 先等「在」念完再开口（否则两个声音叠在一起）、等「在」有 3 秒上限

跑真 `run()` 主循环的那条路，用 `scripts/voice_watchdog_probe.py` 手工验证
（单测跑不到 `_read_block` → `_reopen_stream` → 继续听的完整链路）。

依赖 vosk/numpy 的用例用 `importorskip` 跳过——**没装 `lcode[voice]` 时整份文件
仍应全绿**，而不是一堆 ImportError。
