"""本地精转写：SenseVoice-Small（sherpa-onnx + onnxruntime，不联网、不要 key）。

## 为什么换掉云端

实测同一段 3.23 秒音频（SAPI 合成「小可小可，现在几点」），三个通道：

| 通道 | 识别结果 | 热态延迟 | 成本 |
|---|---|---|---|
| vosk 小模型（42MB） | `小可 小可 现在 几点` | 942ms | 免费 |
| **SenseVoice-Small（228MB）** | **`小可小可，现在几点？`** | **80ms** | **免费** |
| qwen3-asr-flash（云端） | `小可小可，现在几点？` | 642ms | ¥0.00022/秒 |

本地不但免费，还比云端快 8 倍、比 vosk 快 12 倍，准确率和云端持平
（带标点、词不散）。代价是一次性 228MB 磁盘 + 启动时 3 秒加载。

## 为什么用 sherpa-onnx 而不是 FunASR / faster-whisper

- **FunASR** 要装 torch（2GB+），我们只需要「短句转写」一件小事；
- **faster-whisper** 依赖 ctranslate2，中文准确率低于 SenseVoice
  （公开评测：SenseVoice-Small 中文 92.3% vs Whisper-Large 91.8%）；
- **sherpa-onnx** 只依赖 onnxruntime，pip 包 19MB，不拖 torch。

## 和 vosk 的分工

vosk 仍然负责**唤醒词检测**：它是流式的，能边听边吐 partial，喊出「小可」
半句就能响应。SenseVoice 是非流式的（要整句），做不到这一点。
所以架构是：

    麦克风 → vosk（流式，只听唤醒词 + 断句）
              ↓ 一句话说完
           SenseVoice（整句转写，80ms）
              ↓
           入队 → Agent → SAPI 念出来

``voice.refine: sensevoice`` 打开这条精转写链路；``off`` 就是纯 vosk。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

# 16k 单声道 int16。和 devices.RATE 一致（这里重复写常量是为了不在导入期
# 拖进 sounddevice 的用法，数值对不上会让模型听到变速的声音）。
PCM_RATE = 16000

DEFAULT_THREADS = 4
# SenseVoice 输出的富文本标签（<|zh|><|NEUTRAL|><|Speech|>…）。
# sherpa 的 result.text 一般已经清干净，这里再兜一层，防止不同模型版本带出来。
_TAG_PREFIXES = ("<|", "▁")


class SenseVoiceError(Exception):
    """模型缺失、onnxruntime 装不上——都是「可以拿给人看」的失败。"""


def default_model_dir() -> Path:
    """跟着「代码在 D、数据在 C」的约定落在 ~/.lcode/voice/models。"""
    return Path.home() / ".lcode" / "voice" / "models" / "sense-voice"


class SenseVoice:
    """SenseVoice-Small 的薄封装：``model_dir`` 里要有 model.int8.onnx + tokens.txt。

    懒加载：构造时不碰磁盘，``warmup()`` 或第一次调用才建 recognizer
    （实测约 3 秒）。语音助手在开麦克风前调一次 ``warmup()``，
    避免第一句话白等 3 秒。
    """

    def __init__(
        self,
        model_dir: Path | None = None,
        log: Callable[[str], None] = lambda _m: None,
        num_threads: int = DEFAULT_THREADS,
        itn: bool = True,
        language: str = "zh",
    ) -> None:
        self._dir = Path(model_dir) if model_dir else default_model_dir()
        self._log = log
        self._threads = max(1, num_threads)
        self._itn = itn
        self._language = language
        self._recognizer = None
        # sherpa 的 recognizer 不是线程安全的，解码要串行
        self._lock = threading.Lock()

    # ── 就绪判断 ──────────────────────────────────────────────────────

    @property
    def model_path(self) -> Path:
        return self._dir / "model.int8.onnx"

    @property
    def tokens_path(self) -> Path:
        return self._dir / "tokens.txt"

    def problems(self) -> list[str]:
        """缺什么，用人话说清楚（不抛异常，给 CLI 报错用）。"""
        out: list[str] = []
        if not self.model_path.is_file():
            out.append(
                f"缺少 SenseVoice 模型：{self.model_path}\n"
                "  下载：python -m lcode.voice.download --sensevoice"
            )
        elif not self.tokens_path.is_file():
            out.append(
                f"缺少 tokens.txt：{self.tokens_path}\n"
                "  下载：python -m lcode.voice.download --sensevoice"
            )
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            out.append("缺少 sherpa-onnx。安装：pip install lcode[voice]")
        return out

    @property
    def ready(self) -> bool:
        return not self.problems()

    # ── 加载 ──────────────────────────────────────────────────────────

    def warmup(self) -> None:
        """提前把 recognizer 建好（约 3 秒），别让第一句话等它。"""
        self._ensure()

    def _ensure(self):
        if self._recognizer is not None:
            return self._recognizer
        if not self.ready:
            raise SenseVoiceError("；".join(self.problems()))
        import sherpa_onnx

        self._log("加载 SenseVoice…")
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(self.model_path),
            tokens=str(self.tokens_path),
            num_threads=self._threads,
            use_itn=self._itn,
            language=self._language,
        )
        self._log("SenseVoice 就绪")
        return self._recognizer

    # ── 转写 ──────────────────────────────────────────────────────────

    def transcribe(self, pcm: bytes) -> str:
        """16k 单声道 int16 PCM → 文本。失败抛 :class:`SenseVoiceError`。"""
        if not pcm:
            return ""
        import numpy as np

        arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        rec = self._ensure()
        with self._lock:
            stream = rec.create_stream()
            stream.accept_waveform(PCM_RATE, arr)
            rec.decode_stream(stream)
            text = stream.result.text
        return _clean(text)

    def __call__(self, pcm: bytes) -> str:
        return self.transcribe(pcm)


def _clean(text: str) -> str:
    """去掉 SenseVoice 可能带出的富文本标签和词边界记号。

    SenseVoice 的输出里可能混着 ``<|zh|>`` ``<|NEUTRAL|>`` ``<|Speech|>``
    这类标签（情感/事件/语种），以及 ``▁`` 这种句子开头的占位符。
    念出来会变成噪音，转写结果也不该带着它们进上下文。
    """
    out = text or ""
    if "<|" in out:
        # 逐段剥掉尖括号标签
        pieces = []
        depth = 0
        for ch in out:
            if ch == "<":
                depth += 1
            elif ch == ">":
                if depth:
                    depth -= 1
                else:
                    pieces.append(ch)
            elif not depth:
                pieces.append(ch)
        out = "".join(pieces)
    out = out.replace("▁", " ").strip()
    return " ".join(out.split())


def make_transcriber(
    model_dir: Path | None = None,
    log: Callable[[str], None] = lambda _m: None,
    num_threads: int = DEFAULT_THREADS,
    itn: bool = True,
    language: str = "zh",
) -> Callable[[bytes], str]:
    """给 ``VoiceAssistant`` 注入用的精转写函数。

    和 ``cloud.make_transcriber`` 同一套约定：**永不抛异常**，失败返回空串，
    并挂一个 ``ready()``。区别是这里不出网——失败原因只有「模型没下全」
    或「onnxruntime 崩了」，都在日志里。
    """
    engine = SenseVoice(
        model_dir=model_dir, log=log, num_threads=num_threads, itn=itn, language=language
    )

    def transcribe(pcm: bytes) -> str:
        try:
            return engine.transcribe(pcm)
        except SenseVoiceError as exc:
            log(f"! 本地精转写失败：{exc}")
        except Exception as exc:  # noqa: BLE001 - 语音不能因为转写挂掉
            log(f"! 本地精转写异常：{type(exc).__name__}: {exc}")
        return ""

    transcribe.ready = lambda: engine.ready  # type: ignore[attr-defined]
    transcribe.warmup = engine.warmup  # type: ignore[attr-defined]
    return transcribe


__all__ = [
    "DEFAULT_THREADS",
    "PCM_RATE",
    "SenseVoice",
    "SenseVoiceError",
    "default_model_dir",
    "make_transcriber",
]
