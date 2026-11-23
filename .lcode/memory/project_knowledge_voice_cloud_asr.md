---
type: project_knowledge
title: lcode 语音 P2：云端精转写（默认关）+ 百炼欠费现状
created: '2026-11-23T11:45:00+08:00'
updated: '2026-11-23T14:00:00+08:00'
---
vosk 只当触发器（唤醒词检测 + 句子边界），指令交给 qwen3-asr-flash 精转写；默认关（音频出本机是隐私决定），实机接通时踩了 asyncio 嵌套和回放静音两个坑，都已修复并补了回归测试。

## 设计

vosk 只当**触发器**（唤醒词检测 + 句子边界），真正那句指令交给 `audio` 段的后端
（qwen3-asr-flash）精转写。分工理由：这两件事 vosk 又快又准且不联网，转写质量才是短板。

新增 `src/lcode/voice/cloud.py`（唯一碰网络的地方）：
- `write_wav(pcm, path, rate=16000)`：16bit 单声道 PCM 落成 wav（`lcode.audio.prepare()` 靠扩展名补 mime，内存流不认）。
- `transcribe_pcm(audio_cfg, pcm, path, language)`：**必须放到新线程里跑 `asyncio.run`**（见下方坑 1）。
- `make_transcriber(audio_cfg, log)`：返回**永不抛异常**的 callable（失败返回 ""），并挂一个 `ready()` 属性（检查 `audio_cfg.ready()`）。

wake.py：
- `VoiceOptions` 新增 `cloud_asr=False / cloud_min_seconds=0.6 / cloud_fallback=True`；`VoiceAssistant.__init__` 新增可注入 `asr` 参数（None = 音频不出本机）。
- `self._utter`（bytearray）从 `_new_recognizer()` 起攒整句 PCM（高通之后、喂识别器之前 append），上限 30 秒；`done` 时 `refine(text)` 取走并清空。
- `refine(text)`：不可用/太短/失败 → 原样返回 vosk 文本；成功则剥掉唤醒词前缀（云端转的是整句「小可小可，现在几点」）再当指令。
- `_should_refine(text)`：只在「已在 LISTENING」或「文本里含唤醒词/误听词」时才发请求——环境噪声不该上传。
- 主循环 `done` 分支先 refine 再 `process_text`，所以**「vosk 只认出两个字」的句子也有救**（这是放在 process_text 之前而不是 answer 线程里的原因）。
- 超长强制收尾那段不走精转写（可能念了篇文章，上传又贵又不准）。
- 启动时如果开了会打一行 `云端精转写：开（…）`——用户有权知道音频会出网。

cli.py：`--voice-cloud-asr` / `--voice-no-cloud-asr`；`_voice_asr(cfg, opts, log)` 两道闸（`voice.cloud_asr` 显式打开 + `audio` 段配全），任一不满足返回 None 并 log 提示。

## 实机接通踩的两个坑（2026-11-23 下午）

### 坑 1：`asyncio.run()` 不能在运行中的事件循环里调用

第一版 `transcribe_pcm` 直接 `asyncio.run(audio.transcribe(...))`，实机每次都炸：

```
! 云端精转写异常：RuntimeError: asyncio.run() cannot be called from a running event loop
RuntimeWarning: coroutine 'transcribe' was never awaited
```

根因：语音主循环**本身就在事件循环里**——`cli.main()` 是 `asyncio.run(_amain(args))`，
`_amain` 里同步调 `assistant.run()`。所以「调用点在应答线程、没有事件循环」这个假设是错的。

修法：把协程交给新线程跑（新线程没有 loop，`asyncio.run` 才合法）：
```python
coro = audio_mod.transcribe(audio_cfg, path, language=language)
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    transcript = pool.submit(asyncio.run, coro).result()
```
测试 `test_transcribe_pcm_survives_running_event_loop` 在外层协程里调用同步函数，是该故障的最小复现。

**这个 bug 特别阴**：因为 fallback 兜住了，表面上「语音能正常回答」，只有开 `--voice-debug`
才看得出云端一次都没成功过。凡是带 fallback 的链路，验证时必须确认主路径真的走了。

### 坑 2：回放 wav 无尾部静音 → vosk 永不吐 done

vosk 靠内部 VAD 判断句子结束。SAPI 直接合成的 `sim_wake.wav` 末尾没静音，识别器一直不返回
final，程序走「文件读完 → `FinalResult()`」flush 路径，而 `refine()` 只接在 `done` 分支——
回放测试完全看不到云端精转写。两处都补上了。

同时新增 `captures/sim_wake_tail.wav`（末尾 +1.5 秒静音），更贴近真实麦克风（人说完话自然会停顿）。

## 实测结论（重要）

用 SAPI 合成的「小可小可，现在几点」（22050Hz → 重采样 16k）：
- vosk 小模型：`小可 小可 现在 几点`（可用）
- qwen3-asr-flash：**11:45 首次测报 `400 Arrearage`（账户欠费），13:20 复测返回 200**，
  转出 `小可小可，现在几点？` —— 云端确实更准（带标点、词不散）。

结论：**Arrearage 是账户侧的临时状态，不是代码问题**。遇到它先看余额/是否刚充值，
过一会儿复测。这也说明 fallback 必须默认开：账号状态是外部变量。

同日顺带确认整个 DashScope 通道可用：
- `qwen3-vl-plus`（视觉）：1x1 图报「尺寸不合规」（参数错误，说明鉴权和计费都通），
  64x64 图返回「红色」→ 200
- `qwen-flash`（文本对照）：200
- `/compatible-mode/v1/models` 能列出账号可见的模型清单

注意 `audio.py` 里 import 的是 **`httpx2`**（venv 里装的是这个名字，不是 `httpx`）——
写探活脚本时别 import 错。

## 已知取舍

- 精转写那 0.5~1 秒是**串行**的：主循环此刻读不到麦克风（没做成后台线程）。发生在你说完一句之后，体感像「多想了一下」。
- `cloud_fallback: false` 时云端失败等于丢掉这句（宁可没答也不要答错），会记一条 `refine` 返回空。

## 开关

生效的只有 `D:\Project\AgentProject\my_mecode\.lcode\config.yaml` 的 `voice.cloud_asr`
（`~/.lcode/config.yaml` 和 `.example` 都不生效，前者是改名前的残留）。
`VoiceConfig.cloud_asr` 的类默认值**必须保持 False**——改它会等于让所有人默认把麦克风音频发出去。
2026-11-23 排查时这个默认值被误改成 True，被 `test_voice_cloud_asr_off_by_default` 抓住。

测试：tests/test_voice.py 81 个（P2 新增 14 个：默认关不调用、替换文本、剥唤醒词前缀、
云端空结果、fallback 关闭、异常兜底、短片段跳过、缓冲只消费一次、_should_refine 判定、
ready 钩子、配置解析、**运行中事件循环可用**、wav 写入格式）。
