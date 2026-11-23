"""把唤醒后那一段音频交给云端 ASR 精转写（vosk 只当触发器）。

## 为什么需要

42MB 的 vosk 中文小模型只够判断「有没有人在叫它」，转写质量不稳定：实测把
「小可小可，现在几点」听成「小口小口」，把「现在几点」听成「法」。项目里已经有
``qwen3-asr-flash`` 通道（``lcode.audio``），把**同一段音频**再转一遍，指令准确率
立刻上一个台阶——小模型再怎么调参也到不了。

分工：
- vosk：唤醒词检测 + 句子边界（这两件事它又快又准，且不联网）；
- 云端：把这句话转成文字（只有这一步出网）。

## 边界（这三点是刻意的）

- **默认关。** ``voice.cloud_asr`` 默认 ``false``——打开等于把麦克风里的声音发到
  ``audio`` 段指向的服务，这是明确的隐私决定，不能是默认行为。
- **多 0.5~1 秒延迟。** 只在真的听到一句待处理的指令时才调用，不对环境噪声浪费。
- **失败就回落到 vosk。** 一次网络抖动不该让整句话丢掉（``voice.cloud_fallback``）。

## 为什么单独一个模块

``wake.py`` 已经有 700 多行状态机，而这里只做「PCM → 文本」一件事，
且是唯一会碰网络的地方。分开之后：

- ``lcode.voice`` 其余部分仍然可以在没配 ``audio`` 段的机器上跑；
- 测试注入一个假函数即可，不需要 mock HTTP。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path

# 与 devices.RATE 一致。这里重复写一次是为了不引入 devices（它依赖 sounddevice 的用法，
# 但不在导入期 import），数值对不上会让云端听到变速的声音。
PCM_RATE = 16000


def write_wav(pcm: bytes, path: Path, rate: int = PCM_RATE) -> None:
    """把 16bit 单声道 PCM 写成 wav。

    云端接口只吃文件/URL，所以要落一次盘。故意用临时目录里的文件而不是内存流：
    ``lcode.audio`` 的 ``prepare()`` 要靠扩展名补 mime，纯内存对象它不认。
    """
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(pcm)


def transcribe_pcm(
    audio_cfg,
    pcm: bytes,
    path: Path,
    language: str = "zh",
) -> str:
    """同步地把一段 PCM 转成文本；失败抛 :class:`lcode.audio.AudioError`。

    ## 为什么绕一层线程

    ``lcode.audio.transcribe`` 是 async 的，直觉写法是 ``asyncio.run(...)``。
    但语音主循环**本身就跑在事件循环里**——``cli.main()`` 是
    ``asyncio.run(_amain(args))``，而 ``_amain`` 里同步调用 ``assistant.run()``。
    在同一个线程里再 ``asyncio.run`` 会直接报
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``。

    把协程交给一个**新线程**跑，新线程没有事件循环，``asyncio.run`` 才合法。
    协程对象本身不绑定 loop，跨线程 await 是安全的。
    """
    from lcode import audio as audio_mod

    write_wav(pcm, path)
    coro = audio_mod.transcribe(audio_cfg, path, language=language)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        transcript = pool.submit(asyncio.run, coro).result()
    return transcript.text.strip()


def make_transcriber(
    audio_cfg,
    log: Callable[[str], None] = lambda _m: None,
    language: str = "zh",
) -> Callable[[bytes], str]:
    """给 ``VoiceAssistant`` 注入用的**精转写函数**。

    返回的 callable 约定：**永不抛异常**，失败返回空字符串。理由是这个函数跑在
    语音主循环的路径上——那里任何未捕获的异常都会让「它突然没反应了」，
    而真正的失败原因已经在日志里了。
    """
    from lcode.audio import AudioError

    def transcribe(pcm: bytes) -> str:
        if not pcm:
            return ""
        try:
            with tempfile.TemporaryDirectory(prefix="lcode-voice-asr-") as tmp:
                return transcribe_pcm(audio_cfg, pcm, Path(tmp) / "utter.wav", language)
        except AudioError as exc:
            log(f"! 云端精转写失败：{exc}")
        except Exception as exc:  # noqa: BLE001 - 语音不能因为转写挂掉
            log(f"! 云端精转写异常：{type(exc).__name__}: {exc}")
        return ""

    def ready() -> bool:
        try:
            return bool(audio_cfg is not None and audio_cfg.ready())
        except Exception:  # noqa: BLE001
            return False

    transcribe.ready = ready  # type: ignore[attr-defined]
    return transcribe


__all__ = ["PCM_RATE", "make_transcriber", "transcribe_pcm", "write_wav"]
