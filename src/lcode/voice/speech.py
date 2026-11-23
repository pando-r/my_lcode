"""把文字念出来：在线神经语音（默认）或 Windows 自带 SAPI（离线）。

两种引擎，取舍不同：

- ``edge``（默认）：和 Edge 浏览器「大声朗读」同一个在线服务，白嫖、不要 key。
  ``zh-CN-XiaoxiaoNeural`` 这类神经语音几乎听不出是合成的——它就是「换掉人机音」
  最省事的一条路。代价：要联网，**要念的文字会发到微软**
  （回答本身早就发给模型了，这里只是多一个接收方），并且要多装 ``edge-tts``。
- ``sapi``：Windows 自带的 ``System.Speech``，离线、零第三方依赖，
  中文通常只有 ``Microsoft Huihui Desktop`` 这种老式拼接音——
  能用、永远能出声，但「人机感」就是它给的。

为什么两条路都绕一道 PowerShell：
- SAPI 的扬声器输出只有 COM / .NET 层才拿得到，Python 侧最近的路就是 PowerShell；
- 中文文本如果塞进命令行参数，会经过一次 GBK 往返，容易变乱码；
  所以统一写进 UTF-8 临时文件，让 PowerShell 用 UTF-8 读——稳。

edge 那边多出来的问题是「mp3 谁来解码」。答案还是系统：
``System.Windows.Media.MediaPlayer`` 直接吃 mp3，而且播放期间进程活着、
放完就退出——和 SAPI 那条「进程退出 = 念完了」的判据完全一致，
``wake.py`` 的 ``_tts_running()`` 一行都不用改。

这一层**不依赖 numpy / vosk / sounddevice**，所以即使没装 ``lcode[voice]``，
``speech.speak("完成了")`` 也能单独用来念一句话（装没装 edge-tts 决定走哪条路）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

# ── 引擎 ──────────────────────────────────────────────────────────────

SAPI = "sapi"
EDGE = "edge"
ENGINES = (EDGE, SAPI)

# 默认 edge 音色：中文女声里最自然的一档，和「小可」这个名字也搭
DEFAULT_EDGE_VOICE = "zh-CN-XiaoxiaoNeural"

# 候选清单。写死而不是联网去问：试听挑音色时列表要立刻出来，
# 而且微软那个接口的返回结构改过好几次，问一次就多一个坏点。
EDGE_VOICES: tuple[tuple[str, str], ...] = (
    ("zh-CN-XiaoxiaoNeural", "女声，温柔自然（默认）"),
    ("zh-CN-XiaoyiNeural", "女声，偏活泼"),
    ("zh-CN-YunxiNeural", "男声，年轻"),
    ("zh-CN-YunjianNeural", "男声，浑厚"),
    ("zh-CN-YunyangNeural", "男声，新闻播报"),
    ("zh-CN-YunxiaNeural", "男声，少年"),
    ("zh-CN-liaoning-XiaobeiNeural", "女声，东北口音"),
    ("zh-CN-shaanxi-XiaoniNeural", "女声，陕西口音"),
    ("zh-TW-HsiaoChenNeural", "女声，台湾腔"),
    ("zh-HK-HiuMaanNeural", "女声，粤语"),
)

# rate 参数沿用 SAPI 的 -10..10（正数更快），edge 那边乘 10 变成百分比。
EDGE_RATE_STEP = 10

# SAPI 优先中文女声；找不到就退回系统默认，实在不行就静默跳过
PREFERRED_VOICES = (
    "Microsoft Huihui Desktop",
    "Microsoft Yaoyao",
    "Microsoft Kangkang",
    "Microsoft Xiaoxiao",
)

# 念太长的东西没人听，也会让人等得不耐烦
MAX_SPEAK_CHARS = 420

# pythonw（计划任务）下绝不能闪控制台窗口
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_LOG = logging.getLogger("lcode.voice")


def _powershell() -> str:
    return "powershell"


def available() -> bool:
    """当前平台是否支持本模块的朗读方式（只有 Windows）。"""
    return sys.platform == "win32"


def edge_available() -> bool:
    """在线神经语音能不能用：要 Windows（播放靠 MediaPlayer）+ 装了 edge-tts。"""
    if not available():
        return False
    try:
        return importlib.util.find_spec("edge_tts") is not None
    except (ImportError, ValueError):
        return False


def list_voices() -> list[str]:
    """系统里装了的 SAPI 音色名。"""
    if not available():
        return []
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
        ".GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
    )
    try:
        proc = subprocess.run(
            [_powershell(), "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_NO_WINDOW,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def pick_voice(installed: list[str] | None = None) -> str:
    voices = installed if installed is not None else list_voices()
    for wanted in PREFERRED_VOICES:
        for v in voices:
            if v.strip().lower() == wanted.lower():
                return v
    # 退一步：任何名字带 Huihui/Yaoyao/Chinese/zh 的
    for v in voices:
        if re.search(r"huihui|yaoyao|kangkang|chinese|zh-cn", v, re.I):
            return v
    return ""


def default_voice(engine: str) -> str:
    """没配音色时该用哪个。"""
    if engine == EDGE:
        return DEFAULT_EDGE_VOICE
    return pick_voice()


_EDGE_NAME = re.compile(r"^[a-z]{2,3}-[A-Za-z]{2,4}-\w+Neural$")


def _edge_voice(voice: str) -> str:
    """把配置里的音色名收进 edge 的命名空间。

    配错（比如照抄了 SAPI 的 ``Microsoft Huihui Desktop``）时退回默认音色，
    否则 edge 那边会 400，人只看到「念不出来」而不知道为什么。
    """
    name = (voice or "").strip()
    return name if _EDGE_NAME.match(name) else DEFAULT_EDGE_VOICE


# ── 把 Agent 的输出改成「能听懂的话」 ──────────────────────────────────


_CODE_BLOCK = re.compile(r"```[\s\S]*?(?:```|$)")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_URL = re.compile(r"https?://\S+")
_HEADING = re.compile(r"^\s*#{1,6}\s*", re.M)
_BULLET = re.compile(r"^\s*[-*+]\s+", re.M)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]{3,}\|?\s*$", re.M)
_RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", re.M)
_MULTI_BLANK = re.compile(r"\n{2,}")
# SAPI 念「4:19」会变成「四，冒号，一九」；转成中文时间说法
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_ARROWS = re.compile(r"→|=>|->|←|⟶")
_TICKS = re.compile(r"[·•—–]+")


def sanitize(text: str) -> str:
    """markdown → 朗读稿：去掉一切「眼睛需要、耳朵不需要」的东西。

    Agent 的回答默认是 markdown：表格、代码块、加粗、链接。直接念出来是
    「星号星号 结论 星号星号」「竖线 项目 竖线 状态 竖线」，一整段噪音。
    这一步是语音体验里性价比最高的地方，比换更贵的 TTS 模型有用得多。
    """
    out = text or ""
    out = _CODE_BLOCK.sub("（代码省略）", out)
    out = _TABLE_SEP.sub("", out)
    out = _TABLE_ROW.sub("", out)
    out = _LINK.sub(r"\1", out)  # 只留可见文字，丢地址
    out = _URL.sub("", out)
    out = _RULE.sub("", out)
    out = _HEADING.sub("", out)
    out = _BULLET.sub("，", out)
    out = _INLINE_CODE.sub(r"\1", out)
    out = _EMPHASIS.sub("", out)
    out = out.replace("|", "，")
    out = _CLOCK.sub(r"\1 点 \2 分", out)
    out = _ARROWS.sub("，变成，", out)
    out = _TICKS.sub("，", out)
    out = _MULTI_BLANK.sub("\n", out)
    lines = [line.strip() for line in out.splitlines()]
    out = "，".join(line for line in lines if line)
    out = re.sub(r"[，,]{2,}", "，", out)
    out = re.sub(r"([。！？；])，", r"\1", out)
    out = re.sub(r"\s+", " ", out).strip("，, 。")
    return out


def clip(text: str, limit: int = MAX_SPEAK_CHARS) -> str:
    """超长就截到句子边界，别在半句中间断掉。"""
    if len(text) <= limit:
        return text
    head = text[:limit]
    for mark in ("。", "！", "？", "；", "，"):
        cut = head.rfind(mark)
        if cut > limit * 0.6:
            return head[: cut + 1] + "……更详细的结果在终端里。"
    return head + "……更详细的结果在终端里。"


# ── 协程 → 同步 ────────────────────────────────────────────────────────


def _run_coroutine(coro: Any) -> Any:
    """在同步代码里跑一个协程，哪怕当前线程已经有事件循环。

    语音主循环本身跑在 asyncio 里（``cli.main`` = ``asyncio.run(_amain(...))``），
    第一版直接在精转写里调 ``asyncio.run`` 就撞过
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``。
    这里沿用同一个教训：已经有 loop 就另起线程跑，绝不把调用方炸掉。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - 原样抛回调用方
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ── edge：合成 ─────────────────────────────────────────────────────────


def _synth_edge(text: str, voice: str, rate: int, path: Path) -> bool:
    """把文字合成成 mp3 落到 ``path``；失败返回 False（调用方回退 SAPI）。"""
    try:
        import edge_tts
    except ImportError:
        return False

    rate_pct = f"{int(rate) * EDGE_RATE_STEP:+d}%"

    async def run() -> None:
        comm = edge_tts.Communicate(text, _edge_voice(voice), rate=rate_pct)
        await comm.save(str(path))

    try:
        _run_coroutine(run())
    except Exception as exc:  # noqa: BLE001 - 连不上微软就回退 SAPI，不炸主流程
        # 这里只记一条 warning：语音要的是「有声音」，不是「必须有声音 + 堆栈」
        _LOG.warning("edge TTS 合成失败：%s: %s", type(exc).__name__, exc)
        return False
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


# ── 播放（mp3，交给系统的 MediaPlayer） ────────────────────────────────
#
# 为什么不用 winsound / SoundPlayer：它们只吃 wav，而 edge 给的是 mp3，
# 转码要么装 ffmpeg 要么装 pydub，为一个「念一句话」不值得。
# MediaPlayer 是 WPF 自带的，能直接放 mp3，而且 NaturalDuration 一到手就能
# 按真实时长等待——比 SAPI 那句「进程退出就是念完了」还要准。

_PLAY_SCRIPT = """Add-Type -AssemblyName PresentationCore
$p = New-Object System.Windows.Media.MediaPlayer
$p.Open([uri]'__PATH__')
$p.Play()
$deadline = (Get-Date).AddSeconds(10)
while (-not $p.NaturalDuration.HasTimeSpan) {
  if ((Get-Date) -gt $deadline) { break }
  Start-Sleep -Milliseconds 50
}
$ms = 3000
if ($p.NaturalDuration.HasTimeSpan) {
  $ms = $p.NaturalDuration.TimeSpan.TotalMilliseconds
}
Start-Sleep -Milliseconds ([int]$ms + 250)
$p.Close()
Start-Sleep -Milliseconds 150
Remove-Item -LiteralPath '__PATH__' -ErrorAction SilentlyContinue"""


def _play_script(path: Path) -> str:
    return _PLAY_SCRIPT.replace("__PATH__", str(path).replace("'", "''"))


def _play_sync(path: Path, timeout: float) -> bool:
    try:
        proc = subprocess.run(
            [_powershell(), "-NoProfile", "-Command", _play_script(path)],
            capture_output=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001 - 念不出来不该影响主流程
        return False
    return proc.returncode == 0


def _play_async(path: Path) -> subprocess.Popen | None:
    """不阻塞地播；返回的进程活着 = 还在念（唤醒逻辑靠这个判断）。"""
    try:
        return subprocess.Popen(
            [_powershell(), "-NoProfile", "-Command", _play_script(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001
        _unlink(path)
        return None


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _temp(suffix: str) -> Path:
    handle, name = tempfile.mkstemp(prefix="lcode-tts-", suffix=suffix)
    os.close(handle)
    return Path(name)


# ── SAPI（离线） ───────────────────────────────────────────────────────


def _script(voice: str, path: Path, rate: int) -> str:
    lines = [
        "Add-Type -AssemblyName System.Speech",
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
    ]
    if voice:
        safe = voice.replace("'", "''")
        lines.append("try { $s.SelectVoice('" + safe + "') } catch { }")
    lines.append(f"$s.Rate = {int(rate)}")
    lines.append(f"$t = [IO.File]::ReadAllText('{path}', [Text.Encoding]::UTF8)")
    lines.append("$s.Speak($t)")
    lines.append("$s.Dispose()")
    return "; ".join(lines)


def _sapi_speak(text: str, voice: str, rate: int, timeout: float) -> bool:
    path = _temp(".txt")
    try:
        with path.open("w", encoding="utf-8") as fh:
            fh.write(text)
        proc = subprocess.run(
            [_powershell(), "-NoProfile", "-Command", _script(voice, path, rate)],
            capture_output=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 - 念不出来不该影响主流程
        return False
    finally:
        _unlink(path)


def _sapi_async(text: str, voice: str, rate: int) -> subprocess.Popen | None:
    """临时文件交给 PowerShell 自己删——父进程先删会抢在它读取之前。"""
    path = _temp(".txt")
    try:
        with path.open("w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        _unlink(path)
        return None
    script = (
        _script(voice, path, rate)
        + f"; Remove-Item -LiteralPath '{path}' -ErrorAction SilentlyContinue"
    )
    try:
        return subprocess.Popen(
            [_powershell(), "-NoProfile", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001
        _unlink(path)
        return None


# ── 真正发声 ────────────────────────────────────────────────────────────


def _edge_speak(text: str, voice: str, rate: int, timeout: float) -> bool:
    if not edge_available():
        return False
    path = _temp(".mp3")
    try:
        if not _synth_edge(text, voice, rate, path):
            _unlink(path)
            return False
        return _play_sync(path, timeout)
    finally:
        _unlink(path)


def _edge_async(text: str, voice: str, rate: int) -> subprocess.Popen | None:
    if not edge_available():
        return None
    path = _temp(".mp3")
    if not _synth_edge(text, voice, rate, path):
        _unlink(path)
        return None
    return _play_async(path)


def speak(
    text: str,
    voice: str = "",
    rate: int = 0,
    engine: str = EDGE,
    timeout: float = 180.0,
) -> bool:
    """同步念完再返回（True=真念了）。空文本直接返回 False。

    ``engine=edge`` 失败时自动回退 SAPI：断网/微软抽风时人还能听到声音，
    只是那一刻的音色会变回「人机」——比彻底没声音好。
    """
    body = (text or "").strip()
    if not body or not available():
        return False
    if engine == EDGE:
        if _edge_speak(body, voice, rate, timeout):
            return True
        _LOG.warning("edge TTS 不可用，这次改用 Windows 自带语音")
        voice = ""  # edge 的音色名 SAPI 不认，交给它自己挑
    return _sapi_speak(body, voice, rate, timeout)


def speak_async(
    text: str,
    voice: str = "",
    rate: int = 0,
    engine: str = EDGE,
) -> subprocess.Popen | None:
    """不阻塞地念（用于「在」这种短反馈，别让主循环卡住）。

    返回的是**播放进程**：它活着 = 还在出声。``wake.py`` 靠 ``poll()``
    判断「自己正在念」，两种引擎在这件事上表现一致。
    """
    body = (text or "").strip()
    if not body or not available():
        return None
    if engine == EDGE:
        proc = _edge_async(body, voice, rate)
        if proc is not None:
            return proc
        _LOG.warning("edge TTS 不可用，这次改用 Windows 自带语音")
        voice = ""
    return _sapi_async(body, voice, rate)


__all__ = [
    "DEFAULT_EDGE_VOICE",
    "EDGE",
    "EDGE_VOICES",
    "ENGINES",
    "MAX_SPEAK_CHARS",
    "PREFERRED_VOICES",
    "SAPI",
    "available",
    "clip",
    "default_voice",
    "edge_available",
    "list_voices",
    "pick_voice",
    "sanitize",
    "speak",
    "speak_async",
]
