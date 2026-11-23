"""语音通道：唤醒词 → 转写 → Agent 回答 → 念出来。

这是「耳朵和嘴」，不是「脑子」：识别用本地 vosk 小模型（不联网），
朗读默认走微软在线神经语音（要联网、音色像真人；配 tts_engine: sapi 可换回
Windows 自带的离线语音），中间那句回答才走 Lcode 的模型。

## 为什么单独成包

``numpy`` / ``sounddevice`` / ``vosk`` 三个依赖加起来上百兆，绝大多数用户
用不上。所以：

- 装 ``pip install lcode[voice]`` 才有这些依赖；
- **本包的 import 不碰它们**（都在函数内部导入），所以 ``import lcode.voice``
  在没装依赖的机器上也不会炸；
- CLI 的 ``--voice*`` 系列参数在缺依赖时给一句「装 lcode[voice]」而不是
  扔一个 ImportError 栈。

``speech`` 是唯一的例外：它只用 Windows 自带的 PowerShell（SAPI 或 WPF 的
MediaPlayer），没有 numpy/sounddevice/vosk 之类的重依赖，所以
``lcode.voice.speech.speak("完成了")`` 在任何 Windows 环境都能用——
装了 ``edge-tts`` 就走在线神经语音，没装就落回 SAPI。

## 和主体怎么连

走 ``resident`` 队列（``bridge.py``），不直接调 Agent：

    语音 ──入队──> .lcode/resident/queue.json
                              ↓
                     --resident-once 跑一轮
                              ↓
                 results/job_xxx.md ──读文件──> 语音念出来

好处是解耦：说一句话几秒，Agent 干活几十秒，中间不必占着麦克风；
而且复用常驻模式的权限、记忆、MCP，一行都不用重写。
"""

from __future__ import annotations

from dataclasses import dataclass

# 这些是「能用」的必要条件。speech 不在列表里——它零依赖。
_REQUIRED = ("numpy", "sounddevice", "vosk")


@dataclass(frozen=True)
class Availability:
    """依赖与模型的就绪情况。给 CLI 报错用，不抛异常。"""

    missing_packages: tuple[str, ...] = ()
    model_dir: str = ""
    model_ok: bool = False
    tts_ok: bool = False
    # 配置里选的朗读引擎（edge / sapi）；报错文案按它给建议
    tts_engine: str = "sapi"

    @property
    def ready(self) -> bool:
        return not self.missing_packages and self.model_ok

    def problems(self) -> list[str]:
        out: list[str] = []
        if self.missing_packages:
            out.append(
                "缺少 Python 包："
                + "、".join(self.missing_packages)
                + "\n  安装：pip install lcode[voice]"
            )
        if not self.model_ok:
            out.append(f"缺少 vosk 模型：{self.model_dir}\n  下载：python -m lcode.voice.download")
        if not self.tts_ok:
            out.append(
                "没有可用的 TTS 朗读语音（不影响使用，但回答念不出来）\n"
                "  edge（默认，像真人）：pip install edge-tts\n"
                "  sapi（离线）：设置 → 时间和语言 → 语音 → 添加「中文（简体）」语音包"
            )
        return out


def missing_packages() -> tuple[str, ...]:
    import importlib.util

    return tuple(name for name in _REQUIRED if importlib.util.find_spec(name) is None)


def check(cfg=None) -> Availability:
    """检查依赖 + 模型 + TTS 是否就绪。任何一项失败都不抛异常。"""
    from lcode.voice import speech
    from lcode.voice.config import VoiceConfig

    if cfg is None:
        cfg = VoiceConfig()
    elif not isinstance(cfg, VoiceConfig):
        cfg = VoiceConfig()

    model_dir = cfg.effective_model_dir()
    # TTS 判定按**配置选的引擎**来：edge 只要装了 edge-tts 就绪，
    # 没装 edge-tts 也别急着报错——speech 层会自动落回 SAPI，
    # 所以两个引擎有一个能用就算 ok。
    tts_ok = bool(speech.available() and (speech.pick_voice() or speech.edge_available()))
    return Availability(
        missing_packages=missing_packages(),
        model_dir=str(model_dir),
        model_ok=model_dir.is_dir(),
        tts_engine=cfg.tts_engine,
        tts_ok=tts_ok,
    )


__all__ = ["Availability", "check", "missing_packages"]
