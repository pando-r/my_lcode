---
type: project_knowledge
title: lcode 语音精转写换成免费本地 SenseVoice（替代百炼 qwen3-asr-flash）
created: '2026-11-23T15:20:00+08:00'
updated: '2026-11-23T15:20:00+08:00'
---
K 说「百炼太贵了，看看有没有免费开源的」。结论：换成**本地 SenseVoice-Small**，更快、免费、不联网，准确率与云端持平。

## 三方实测（同一段 3.23 秒 SAPI 合成音频「小可小可，现在几点」）

| 通道 | 识别结果 | 热态延迟 | 成本 | 联网 |
|---|---|---|---|---|
| vosk-model-small-cn-0.22（42MB） | `小可 小可 现在 几点` | 942ms | 0 | 否 |
| **SenseVoice-Small int8（228MB）** | `小可小可，现在几点？` | **80ms**（p95 306ms） | **0** | **否** |
| qwen3-asr-flash（云端） | 当时欠费 400 Arrearage，未取到 | 642ms | ¥0.00022/秒 | 是 |

SenseVoice 比云端快 8 倍、比 vosk 快 12 倍。加载模型约 3 秒（启动时预热一次即 1 秒）。

## 花了多少钱（顺带算清）

qwen3-asr-flash 0.00022 元/秒，一次语音提问只上传指令那 ~3 秒 ≈ ¥0.0007。
每天 50 次 ≈ ¥0.03/天、¥1/月。**真正贵的不是 ASR**——是 Agent 回答那几十秒的 LLM 调用，
那笔和语音无关，纯文本问答同样要花。而且百炼对新用户有 36,000 秒（10 小时）免费额度，
90 天有效，按每次 3 秒够 12000 次提问（每天 50 次能用 240 天）。

所以「贵」的感受很可能来自别处（模型欠费后的失败重试、或 vision/audio 一起用）。
但既然本地方案更快更省心，换掉是对的。

## 技术选型

用 **sherpa-onnx**（pip `sherpa-onnx>=1.10`，只依赖 onnxruntime，包 19MB）：

- 否掉 FunASR：要装 torch（2GB+），我们只需要「短句转写」一件事；
- 否掉 faster-whisper：依赖 ctranslate2，中文准确率低于 SenseVoice（92.3% vs 91.8%）；
- sherpa-onnx 提供 `OfflineRecognizer.from_sense_voice(model=, tokens=, num_threads=, use_itn=, language=)`。

模型文件（**只要两个**，不要 894MB 的 model.onnx）：
- `model.int8.onnx` 239,233,841 字节
- `tokens.txt` 315,894 字节

下载源：**hf-mirror.com**（GitHub release 在这台机器上直连超时）。
repo：`csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`。
裸 urllib 会被 hf-mirror 403，要带 `User-Agent`。

## 代码结构

新增 `src/lcode/voice/local_asr.py`：
- `SenseVoice` 类：懒加载 recognizer（3 秒），`transcribe(pcm: bytes) -> str`，`ready` / `problems()`。
- `_clean()`：剥掉 SenseVoice 可能带出的富文本标签 `<|zh|>` `<|NEUTRAL|>` `<|Speech|>` 和 `▁`。
- `make_transcriber()`：和 `cloud.make_transcriber` 同一套约定——**永不抛异常**，失败返回 ""，
  挂 `ready()` / `warmup()` 属性。

`config.py`：`voice.refine` 三值（`off` / `sensevoice` / `cloud`），取代原来的布尔 `cloud_asr`。
**关键坑**：`refine` 的 dataclass 默认值必须写成**空串**而不是 `"off"`——写成 `"off"` 会让
`effective_refine()` 首行判断立刻返回，老配置里的 `cloud_asr: true` 永远读不到（实测复现过）。

`wake.py`：`VoiceOptions.refine` 取代 `cloud_asr`；`_transcriber()` 一处判断；启动时对带
`warmup` 的函数调一次（避免第一句话等 3 秒）。

`cli.py`：`_voice_asr()` 按 `refine` 分三支装配；参数是 `--voice-refine {off,sensevoice,cloud}`。

`download.py`：`python -m lcode.voice.download --sensevoice`，逐文件校验字节数
（半截文件会让 onnxruntime 报看不懂的错）。

`pyproject.toml`：voice extra 加 `sherpa-onnx>=1.10`。

## 为什么 vosk 不能一起去掉

vosk 是**流式**的（边听边吐 partial，喊出「小可」半句就能响应）；SenseVoice 是**非流式**的
（要整句）。所以分工固定：vosk 管唤醒词检测 + 断句，SenseVoice 管整句转写。

## 配置

项目 `.lcode/config.yaml` 已改成 `refine: sensevoice`（`refine_threads: 4`、
`refine_min_seconds: 0.6`、`refine_fallback: true`）。实例已重启生效。

测试：tests/test_voice.py 93 个（新增 SenseVoice 8 个 + refine 配置 5 个）。
