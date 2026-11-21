"""Speech-to-text: turn an audio file into time-stamped text.

音频不像图片那样能「塞给模型看」：唯一的通路是先转写。三种后端：

- ``dashscope``：阿里百炼原生 ``multimodal-generation`` 接口（``qwen3-asr-flash`` 等）。
  注意它**没有** OpenAI 的 ``/audio/transcriptions`` 端点，那个路径会 404。
- ``openai``：OpenAI 兼容的 ``/audio/transcriptions``（OpenAI 官方 / Groq / 部分厂商）。
- ``local``：faster-whisper，完全不联网（需 ``pip install lcode[media]``）。

返回的时间戳粒度取决于后端：faster-whisper 和 whisper-1 能给到句子级，
qwen3-asr-flash 只回整段文本，所以这里的「时间戳」是**分段级**（按切片的起止）。
"""

from __future__ import annotations

import asyncio
import base64
import math
import os
import shutil
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from lcode.config import AudioConfig
from lcode.tool.media import HEAD_BYTES, human_size, sniff_mime

# 单次上传的体积上限，按后端的实测/文档值分别设定。
# DashScope 原生接口实测：9.2MB 通过、11.6MB 报 "Multimodal file size is too large"，
# 取 9MB 留余量；OpenAI 兼容端点普遍卡 25MB，取 24MB。
DASHSCOPE_MAX_BYTES = 9 * 1024 * 1024
OPENAI_MAX_BYTES = 24 * 1024 * 1024
MAX_UPLOAD_BYTES = OPENAI_MAX_BYTES  # 兼容旧名字
# 单段时长：16kHz 单声道 wav 约 32KB/s，8 分钟 ≈ 15MB，稳在 25MB 限制之内。
SEGMENT_SECONDS = 480
# qwen3-asr-flash 单次上限 5 分钟；240s → 7.7MB，稳在 9MB 以内。
DASHSCOPE_SEGMENT_SECONDS = 240
# 切片统一转成 wav：mp3 编码器不是每个 ffmpeg 构建都带，wav 一定可用。
PART_SUFFIX = ".wav"
PART_MIME = "audio/wav"
MAX_ANSWER_CHARS = 16000

DASHSCOPE_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)
# 北京地域；新加坡地域要换成 dashscope-intl.aliyuncs.com。
DASHSCOPE_REGIONS = {
    "cn": "https://dashscope.aliyuncs.com",
    "intl": "https://dashscope-intl.aliyuncs.com",
}

# 各家对「音频格式」的要求不同，但 mp3/m4a/wav/flac/ogg 基本都认。
SUPPORTED_MIME = frozenset(
    {
        "audio/mpeg",
        "audio/wav",
        "audio/mp4",
        "audio/x-m4a",
        "audio/flac",
        "audio/ogg",
        "audio/aiff",
    }
)

# 有些容器格式 sniff 出来是通用的 audio/mp4，靠扩展名补齐判定。
_SUFFIX_MIME = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
}

# 视频也常被拿来转写（录屏、会议录像）：只取音轨，扩展名决定是否放行。
# 视频容器不逐个 sniff：扩展名足够判断「这是不是要抽音轨的东西」，
# 真正的格式校验交给 ffmpeg。
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".m4v"})

# 后端能力差异：写入结果元信息，让读结果的人知道时间戳有多准。
GRANULARITY = {
    "dashscope": "分段级（每次切片一个时间戳）",
    "openai": "句子级",
    "local": "句子级",
}


class AudioError(Exception):
    """Raised when audio cannot be prepared or transcribed."""


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    segments: list[Segment]
    language: str = ""
    model: str = ""
    parts: int = 1
    backend: str = ""
    duration: float = 0.0  # 音频总时长（秒），未知为 0
    note: str = ""  # 后端能力差异说明（例如「不支持 prompt」）

    def render(self, max_chars: int = MAX_ANSWER_CHARS) -> str:
        lines = [f"[{clock(s.start)} → {clock(s.end)}] {s.text}" for s in self.segments]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[transcript truncated]"
        return text

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()

    @property
    def granularity(self) -> str:
        return GRANULARITY.get(self.backend, "")


def clock(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def detect_mime(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            mime = sniff_mime(fh.read(HEAD_BYTES))
    except OSError as exc:
        raise AudioError(f"读取音频失败: {path}（{exc}）") from exc
    if mime in SUPPORTED_MIME:
        return mime
    by_suffix = _SUFFIX_MIME.get(path.suffix.lower(), "")
    return by_suffix or mime


def prepare(path: str | Path) -> tuple[Path, str]:
    """Validate an audio (or video) file and return (path, mime)."""
    p = Path(path)
    if not p.is_file():
        raise AudioError(f"文件不存在: {p}")
    suffix = p.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        # 视频一律先抽音轨再上传，所以这里不做容器嗅探，只给个标注用的 mime。
        return p, f"video/{suffix.lstrip('.')}"
    mime = detect_mime(p)
    if mime not in SUPPORTED_MIME:
        raise AudioError(
            f"不支持的音频格式: {p.name}（{mime or '未知类型'}）；"
            "支持 mp3 / wav / m4a / flac / ogg / aiff，视频支持 mp4 / mov / mkv / webm 等"
        )
    return p, mime


def segment_seconds(backend: str) -> int:
    return DASHSCOPE_SEGMENT_SECONDS if backend == "dashscope" else SEGMENT_SECONDS


def max_bytes(backend: str) -> int:
    return DASHSCOPE_MAX_BYTES if backend == "dashscope" else OPENAI_MAX_BYTES


def chunk_budget(backend: str) -> int:
    """单片的字节预算：留 20% 余量，给 JSON 包装和 base64 膨胀之外的开销。"""
    return int(max_bytes(backend) * 0.8)


async def transcribe(
    cfg: AudioConfig,
    path: str | Path,
    language: str = "",
    prompt: str = "",
) -> Transcript:
    """Transcribe `path` with the configured backend."""
    p, mime = prepare(path)
    if not cfg.ready():
        raise AudioError("audio 未配置：请在 .lcode/config.yaml 里写 audio 段")
    lang = language or cfg.language

    # 视频必须走 ffmpeg 抽音轨：直接上传视频，多数转写接口会拒。
    too_big = p.stat().st_size > max_bytes(cfg.backend)
    if too_big or p.suffix.lower() in VIDEO_SUFFIXES:
        return await _transcribe_in_parts(cfg, p, lang, prompt)
    if cfg.backend == "local":
        return await _local(cfg, p, lang, prompt, offset=0.0)
    if cfg.backend == "dashscope":
        return await _dashscope(cfg, p, mime, lang, prompt, offset=0.0)
    return await _remote(cfg, p, mime, lang, prompt, offset=0.0)


async def _transcribe_in_parts(
    cfg: AudioConfig, p: Path, language: str, prompt: str
) -> Transcript:
    # 大文件/视频要切片。优先 ffmpeg（能处理任意格式、能抽音轨）；
    # 没装 ffmpeg 时退回纯 Python 的 WAV 切片 —— 录屏/录音最常见的产物就是 wav，
    # 这样「装个 ffmpeg」不会成为使用长音频的前置门槛。
    budget = chunk_budget(cfg.backend)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None and detect_mime(p) != "audio/wav":
        raise AudioError(
            f"{p.name}（{human_size(p.stat().st_size)}）超过单次上传上限"
            f"（{human_size(max_bytes(cfg.backend))}），需要切片，但没找到 ffmpeg。\n"
            "请安装 ffmpeg（winget install Gyan.FFmpeg），"
            "或先把音频转成 wav（纯 Python 也能切），或手动截取需要的片段。"
        )

    with tempfile.TemporaryDirectory(prefix="lcode-audio-") as tmp:
        if ffmpeg is not None:
            seconds = await _run_ffmpeg_plain(ffmpeg, str(p), tmp, budget)
        else:
            seconds = _split_wav(p, Path(tmp), budget)
        parts = sorted(Path(tmp).glob(f"part*{PART_SUFFIX}"))
        if not parts:
            raise AudioError("切片失败：没有产出任何分段")
        segments: list[Segment] = []
        language_seen = ""
        notes: list[str] = []
        total = 0.0
        for index, part in enumerate(parts):
            offset = float(index * seconds)
            if cfg.backend == "local":
                piece = await _local(cfg, part, language, prompt, offset)
            elif cfg.backend == "dashscope":
                piece = await _dashscope(cfg, part, PART_MIME, language, prompt, offset)
            else:
                piece = await _remote(cfg, part, PART_MIME, language, prompt, offset)
            segments.extend(piece.segments)
            language_seen = language_seen or piece.language
            total = max(total, offset + piece.duration if piece.duration else 0.0)
            if piece.note and piece.note not in notes:
                notes.append(piece.note)
        return Transcript(
            segments,
            language_seen,
            cfg.model,
            parts=len(parts),
            backend=cfg.backend,
            duration=total,
            note="；".join(notes),
        )


def _split_wav(p: Path, out_dir: Path, budget: int) -> float:
    """Split a WAV by byte budget without ffmpeg. Returns seconds per chunk.

    按**字节**而不是时长切片：采样率不一定是 16k（SAPI 输出 22050，
    手机录音常见 44.1k），按时长算会算出超过上限的片。
    """
    with wave.open(str(p), "rb") as src:
        params = src.getparams()
        bytes_per_frame = max(1, params.nchannels * params.sampwidth)
        per_chunk = max(1, budget // bytes_per_frame)
        seconds = per_chunk / params.framerate
        index = 0
        while True:
            frames = src.readframes(per_chunk)
            if not frames:
                break
            target = out_dir / f"part{index:03d}{PART_SUFFIX}"
            with wave.open(str(target), "wb") as dst:
                dst.setnchannels(params.nchannels)
                dst.setsampwidth(params.sampwidth)
                dst.setframerate(params.framerate)
                dst.writeframes(frames)
            index += 1
        return seconds


async def _run_ffmpeg_plain(ffmpeg: str, src: str, out_dir: str, budget: int) -> float:
    """把源文件转成 16k 单声道 wav 并按字节预算切片。返回每片秒数。"""
    # 统一转 16k 单声道 16bit → 32000 B/s，字节预算可以直接换算成秒。
    bytes_per_second = 16000 * 2
    seconds = max(1, budget // bytes_per_second)
    pattern = os.path.join(out_dir, f"part%03d{PART_SUFFIX}")
    await _run_ffmpeg(
        ffmpeg,
        [
            "-i",
            src,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "segment",
            "-segment_time",
            str(seconds),
            pattern,
        ],
    )
    return float(seconds)


async def _run_ffmpeg(ffmpeg: str, args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:500]
        raise AudioError(f"ffmpeg 处理失败: {detail}")


# ── DashScope 原生接口 ────────────────────────────────────────────────────


def _dashscope_url(cfg: AudioConfig) -> str:
    # base_url 通常给的是 compatible-mode 地址；这里只需要地域，用它判断走哪个域。
    marker = "dashscope-intl" if cfg.base_url and "intl" in cfg.base_url else "cn"
    return DASHSCOPE_REGIONS[marker] + (
        "/api/v1/services/aigc/multimodal-generation/generation"
    )


async def _dashscope(
    cfg: AudioConfig,
    p: Path,
    mime: str,
    language: str,
    prompt: str,
    offset: float,
) -> Transcript:
    import httpx2

    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise AudioError(f"读取音频失败: {p}（{exc}）") from exc

    # DashScope 收 data URI；响应用 output.choices[].message.content[].text。
    body: dict = {
        "model": cfg.model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"audio": f"data:{mime};base64,{base64.b64encode(raw).decode()}"}
                    ],
                }
            ]
        },
    }
    asr_options: dict = {"enable_itn": cfg.itn}
    if language:
        asr_options["language"] = language
    body["parameters"] = {"asr_options": asr_options}

    try:
        async with httpx2.AsyncClient(timeout=cfg.timeout) as client:
            resp = await client.post(
                _dashscope_url(cfg),
                json=body,
                headers={
                    "Authorization": f"Bearer {cfg.api_key}",
                    "Content-Type": "application/json",
                },
            )
    except Exception as exc:  # noqa: BLE001 - 统一成可读的失败原因
        raise AudioError(f"转写请求失败: {type(exc).__name__}: {exc}") from exc

    if resp.status_code != 200:
        raise AudioError(f"转写请求失败（{resp.status_code}）: {resp.text[:400]}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise AudioError(f"响应不是合法 JSON: {resp.text[:200]}") from exc

    segments, language_seen = _parse_dashscope(data, offset)
    duration = float(_get(_get(data, "usage") or {}, "seconds") or 0.0)
    note = "" if not prompt else "该后端不支持 prompt 提示（已忽略）"
    return Transcript(
        segments,
        language_seen or language,
        cfg.model,
        backend="dashscope",
        duration=duration,
        note=note,
    )


def _parse_dashscope(data: object, offset: float) -> tuple[list[Segment], str]:
    choices = _get(_get(data, "output") or {}, "choices") or []
    if not isinstance(choices, list) or not choices:
        return [], ""
    message = _get(choices[0], "message") or {}
    language = ""
    annotations = _get(message, "annotations") or []
    if isinstance(annotations, list):
        for item in annotations:
            found = _get(item, "language")
            if isinstance(found, str) and found:
                language = found
                break

    content = _get(message, "content")
    texts: list[str] = []
    if isinstance(content, list):
        for block in content:
            piece = _get(block, "text")
            if isinstance(piece, str) and piece.strip():
                texts.append(piece.strip())
    elif isinstance(content, str) and content.strip():
        texts.append(content.strip())

    text = " ".join(texts).strip()
    if not text:
        return [], language

    usage = _get(data, "usage") or {}
    seconds = float(_get(usage, "seconds") or 0.0)
    # 没有句子级时间戳：整块文本落在「本切片起点 → 起点+时长」。
    return [Segment(offset, offset + seconds if seconds else offset + 60.0, text)], language


# ── OpenAI 兼容接口 ──────────────────────────────────────────────────────


async def _remote(
    cfg: AudioConfig,
    p: Path,
    mime: str,
    language: str,
    prompt: str,
    offset: float,
) -> Transcript:
    import openai

    client = openai.AsyncOpenAI(
        api_key=cfg.api_key,
        base_url=cfg.base_url or None,
        timeout=cfg.timeout,
    )
    kwargs: dict = {"model": cfg.model, "response_format": "verbose_json"}
    if language:
        kwargs["language"] = language
    if prompt:
        kwargs["prompt"] = prompt
    try:
        with p.open("rb") as fh:
            resp = await client.audio.transcriptions.create(file=(p.name, fh, mime), **kwargs)
    except Exception as exc:  # noqa: BLE001 - 统一成可读的失败原因
        raise AudioError(f"转写请求失败: {exc}") from exc
    segments, language_seen = _parse_remote(resp, offset)
    return Transcript(
        segments,
        language_seen or language,
        cfg.model,
        backend="openai",
        duration=segments[-1].end if segments else 0.0,
    )


def _parse_remote(resp, offset: float) -> tuple[list[Segment], str]:
    # verbose_json 不一定被每家兼容实现支持；退化成整体文本也要能用。
    raw_segments = _get(resp, "segments")
    language = _get(resp, "language") or ""
    if isinstance(raw_segments, list) and raw_segments:
        out = [
            Segment(
                offset + float(_get(item, "start") or 0.0),
                offset + float(_get(item, "end") or 0.0),
                str(_get(item, "text") or "").strip(),
            )
            for item in raw_segments
        ]
        return [s for s in out if s.text], language
    text = str(_get(resp, "text") or "").strip()
    if not text:
        return [], language
    return [Segment(offset, offset + 60.0, text)], language


def _get(obj, key):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


# ── 本地 faster-whisper ──────────────────────────────────────────────────


async def _local(
    cfg: AudioConfig, p: Path, language: str, prompt: str, offset: float
) -> Transcript:
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 取决于安装方式
        raise AudioError(
            "本地转写需要 faster-whisper。请安装：pip install lcode[media] "
            "（或改用 audio.backend: dashscope / openai）"
        ) from exc
    return await asyncio.to_thread(_local_sync, WhisperModel, cfg, p, language, prompt, offset)


def _local_sync(model_cls, cfg: AudioConfig, p: Path, language: str, prompt: str, offset):
    model = model_cls(cfg.local_model, device="cpu", compute_type="int8")
    raw_segments, info = model.transcribe(
        str(p), language=language or None, initial_prompt=prompt or None, vad_filter=True
    )
    segments = [
        Segment(offset + float(s.start), offset + float(s.end), s.text.strip())
        for s in raw_segments
        if s.text.strip()
    ]
    return Transcript(
        segments,
        getattr(info, "language", "") or language,
        cfg.local_model,
        backend="local",
        duration=segments[-1].end if segments else 0.0,
    )


def estimate_minutes(segments: list[Segment]) -> int:
    if not segments:
        return 0
    return math.ceil(segments[-1].end / 60)


__all__ = [
    "DASHSCOPE_ENDPOINT",
    "DASHSCOPE_MAX_BYTES",
    "DASHSCOPE_REGIONS",
    "DASHSCOPE_SEGMENT_SECONDS",
    "GRANULARITY",
    "MAX_UPLOAD_BYTES",
    "OPENAI_MAX_BYTES",
    "PART_MIME",
    "PART_SUFFIX",
    "SEGMENT_SECONDS",
    "SUPPORTED_MIME",
    "VIDEO_SUFFIXES",
    "AudioError",
    "Segment",
    "Transcript",
    "clock",
    "estimate_minutes",
    "max_bytes",
    "prepare",
    "segment_seconds",
    "transcribe",
]
