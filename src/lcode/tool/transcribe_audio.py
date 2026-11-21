"""transcribe_audio: turn an audio file into timestamped text."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from lcode import audio as audio_mod
from lcode.config import AudioConfig
from lcode.tool import Result, _truncate
from lcode.tool.ctx import resolve_path
from lcode.tool.media import human_size
from lcode.audio import GRANULARITY, MAX_ANSWER_CHARS, AudioError, Transcript, max_bytes

# 转写是长任务：10 分钟录音在 CPU 上可能跑几分钟。
DEFAULT_TIMEOUT = 900.0

AUDIO_SETUP_HINT = (
    "audio 未配置，无法转写音频。请在 .lcode/config.yaml 加一段：\n"
    "audio:\n"
    "  backend: dashscope       # 百炼原生接口（qwen3-asr-flash）\n"
    "  api_key: \"${DASHSCOPE_API_KEY}\"\n"
    "  model: qwen3-asr-flash\n"
    "  language: zh\n"
    "# 也支持 backend: openai（/audio/transcriptions）或 backend: local（faster-whisper）"
)

Transcriber = Callable[..., Awaitable[Transcript]]


class TranscribeAudioTool:
    read_only: bool = True
    timeout: float = DEFAULT_TIMEOUT

    def __init__(
        self,
        audio: AudioConfig | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        self._audio = audio
        self._transcribe = transcriber or audio_mod.transcribe

    def name(self) -> str:
        return "transcribe_audio"

    def description(self) -> str:
        over = human_size(max_bytes("dashscope"))
        return (
            "把音频（或视频的音轨）转写成带时间戳的文字。"
            "语音备忘、会议录音、录屏里的讲解都在这里处理——音频本身模型读不了。"
            f"超过单次上传上限（约 {over}）会自动切段，因此长录音也能跑。"
            "录音里常有专有名词时，用 prompt 告诉它主题能明显提升识别率。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "音频/视频文件路径"},
                "language": {
                    "type": "string",
                    "description": "语言代码（zh / en / ja…），默认取配置里的值",
                },
                "prompt": {
                    "type": "string",
                    "description": "可选提示：告诉模型这段话的主题/专有名词，能显著提升识别率",
                },
            },
            "required": ["path"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)
        if not isinstance(data, dict):
            return Result(content="参数必须是对象", is_error=True)

        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            return Result(content="缺少参数: path（音频文件路径）", is_error=True)

        cfg = self._audio
        if cfg is None or not cfg.ready():
            return Result(content=AUDIO_SETUP_HINT, is_error=True)

        resolved = resolve_path(path)
        language = str(data.get("language") or "").strip()
        prompt = str(data.get("prompt") or "").strip()

        try:
            transcript = await self._transcribe(cfg, resolved, language=language, prompt=prompt)
        except AudioError as exc:
            return Result(content=str(exc), is_error=True)
        except Exception as exc:  # noqa: BLE001 - 任何失败都要变成结构化结果
            return Result(content=f"转写失败: {exc}", is_error=True)

        if not transcript.segments:
            return Result(content="（没有识别出任何语音；可能是纯音乐或静音）")

        return Result(content=_format(Path(resolved), transcript, cfg))


def _format(path: Path, transcript: Transcript, cfg: AudioConfig) -> str:
    body = _truncate(transcript.render(MAX_ANSWER_CHARS), 2000, MAX_ANSWER_CHARS)
    try:
        size = human_size(path.stat().st_size)
    except OSError:
        size = "?"
    chunks = f"，分 {transcript.parts} 批" if transcript.parts > 1 else ""
    duration = f"，{transcript.duration:.0f}s" if transcript.duration else ""
    language = f"，语言 {transcript.language}" if transcript.language else ""
    backend = transcript.backend or cfg.backend
    granularity = transcript.granularity or GRANULARITY.get(backend, "")
    meta = [
        "",
        "---",
        f"[{path.name} {size}{duration}，{len(transcript.segments)} 段"
        f"{chunks}{language}]",
        f"[asr: {backend}/{transcript.model or cfg.model}，"
        f"时间戳{granularity or '未知'}]",
    ]
    if transcript.note:
        meta.append(f"[注意: {transcript.note}]")
    return body + "\n".join(meta)


__all__ = ["AUDIO_SETUP_HINT", "DEFAULT_TIMEOUT", "TranscribeAudioTool"]
