"""Turn pending attachments into text the main model can read.

主模型是纯文本的，所以图片/音频/视频必须先过副模型：图片走 view_image，
音频走 transcribe_audio，视频先抽帧再走 view_image，文本直接读。
结果拼成一条普通 user 消息 —— 对话历史里因此只有文本，JSONL 落盘和
上下文压缩都不用改。

视频为什么不能只转写音轨：录屏和游戏录像大多没有说话声，只转写的话
用户得到的是「没有识别出任何语音」，画面信息全丢。抽固定帧数（16 帧）
是成本和覆盖面的折中——问「这视频讲了什么」够用，具体细节再让模型
用 crop 放大看。

这一步独立成模块（而不是塞进 app.py）是为了能单测：TUI 里的交互难测，
但「附件 → 提示词」的映射是纯函数。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from dataclasses import dataclass, field

from lcode import video as video_mod
from lcode.tool import Registry, Result
from lcode.tui.attach import UNSUPPORTED_HINT, Attachment
from lcode.video import Frame, VideoError
from lcode.vision import MAX_IMAGES

# 用户没写问题时给一个通用问题：有文字就逐字给出，避免只得到「一张图」。
IMAGE_DEFAULT_QUESTION = (
    "这张图里有什么？如果包含文字（报错、代码、界面文案），请逐字给出。"
)
# 读文本附件时的行数/字符上限：附加日志时不该把上下文撑爆。
TEXT_MAX_CHARS = 12000

# 能变成文字的 kind：其余（压缩包/文档/二进制）会落进 errors。
READABLE_KINDS = frozenset({"image", "audio", "video", "text"})

_VISION_TIMEOUT_NOTE = "（vision 未配置，图片无法读取）"


@dataclass
class Prepared:
    """Attachments expanded into text, plus a per-file report."""

    blocks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def prompt(self, user_text: str) -> str:
        if not self.blocks and not self.errors:
            return user_text
        parts = ["（用户附加了以下文件，内容是自动转成的文字）", ""]
        parts.extend(self.blocks)
        if self.errors:
            parts.append("")
            parts.append("未能读取的部分：")
            parts.extend(f"- {line}" for line in self.errors)
        parts.append("")
        parts.append(f"用户的问题：{user_text}" if user_text else "用户没有补充说明，请先概述。")
        return "\n".join(parts)


async def prepare_attachments(
    items: list[Attachment],
    registry: Registry,
    question: str = "",
) -> Prepared:
    """Expand attachments into text blocks. Never raises; failures land in .errors."""
    prepared = Prepared()
    images = [item for item in items if item.kind == "image"]
    audios = [item for item in items if item.kind == "audio"]
    videos = [item for item in items if item.kind == "video"]
    texts = [item for item in items if item.kind == "text"]
    others = [item for item in items if item.kind not in READABLE_KINDS]

    for item in others:
        prepared.errors.append(f"{item.name}: {UNSUPPORTED_HINT}")

    if texts:
        for item in texts:
            block = _read_text(item)
            if block is None:
                prepared.errors.append(f"{item.name}: 读取失败")
            else:
                prepared.blocks.append(block)

    if images:
        block = await _run_view_image(images, registry, question or IMAGE_DEFAULT_QUESTION)
        if block is None:
            prepared.errors.append("、".join(item.name for item in images) + ": 图片读取失败")
        else:
            prepared.blocks.append(block)

    for item in audios:
        block = await _run_transcribe(item, registry, question)
        if block is None:
            prepared.errors.append(f"{item.name}: 转写失败")
        else:
            prepared.blocks.append(block)

    for item in videos:
        try:
            prepared.blocks.append(await _run_video(item, registry, question))
        except VideoError as exc:
            prepared.errors.append(f"{item.name}: {exc}")

    return prepared


def _read_text(item: Attachment) -> str | None:
    try:
        raw = open(item.path, "rb").read()
    except OSError:
        return None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    if len(text) > TEXT_MAX_CHARS:
        text = text[:TEXT_MAX_CHARS] + "\n[附件内容已截断]"
    return f"--- {item.name} ---\n{text}"


async def _run_view_image(
    items: list[Attachment], registry: Registry, question: str
) -> str | None:
    tool = registry.get("view_image")
    if tool is None:
        return None
    args = json.dumps({"paths": [item.path for item in items], "question": question})
    result: Result = await registry.execute("view_image", args)
    if result.is_error:
        return None
    names = "、".join(item.name for item in items)
    return f"--- {names} ---\n{result.content}"


async def _run_transcribe(item: Attachment, registry: Registry, prompt: str) -> str | None:
    tool = registry.get("transcribe_audio")
    if tool is None:
        return None
    args: dict = {"path": item.path}
    if prompt:
        args["prompt"] = prompt
    result: Result = await registry.execute("transcribe_audio", json.dumps(args))
    if result.is_error:
        return None
    return f"--- {item.name} ---\n{result.content}"


async def _run_video(item: Attachment, registry: Registry, question: str) -> str:
    """抽固定帧数后分批交给 view_image。失败抛 VideoError（原因已是人话）。"""
    if registry.get("view_image") is None:
        raise VideoError("vision 未配置，无法读取视频画面")
    ffmpeg = video_mod.find_ffmpeg()
    if ffmpeg is None:
        raise VideoError(video_mod.FFMPEG_HINT)

    # 帧只在这一层活着：视觉请求发完就删，别在用户磁盘上留 16 张临时图。
    with tempfile.TemporaryDirectory(prefix="lcode-video-") as tmp:
        try:
            frames = await asyncio.to_thread(
                video_mod.extract_frames, item.path, tmp, ffmpeg
            )
        except VideoError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise VideoError(f"抽帧失败：{exc}") from exc

        parts = [f"--- {item.name}（抽了 {len(frames)} 帧，只有画面，音轨未转写）---"]
        for batch in _batches(frames, MAX_IMAGES):
            span = _span(batch)
            args = json.dumps(
                {
                    "paths": [frame.path for frame in batch],
                    "question": _frame_question(question, span),
                }
            )
            result: Result = await registry.execute("view_image", args)
            if result.is_error:
                # 一批失败不该吞掉其它批：能看多少给多少。
                parts.append(f"[{span}] 这批帧没读出来：{_one_line(result.content)}")
            else:
                parts.append(f"[{span}]\n{result.content}")
    return "\n\n".join(parts)


def _batches(frames: list[Frame], size: int) -> list[list[Frame]]:
    # view_image 一次最多 6 张，16 帧因此是 6+6+4 三次请求。
    return [frames[index : index + size] for index in range(0, len(frames), size)]


def _span(frames: list[Frame]) -> str:
    if len(frames) == 1:
        return frames[0].label
    return f"{frames[0].label} → {frames[-1].label}"


def _frame_question(question: str, span: str) -> str:
    head = (
        f"这是一段视频按时间顺序抽出的连续帧（{span}）。"
        "请按顺序描述：主体是什么、在做什么、帧与帧之间发生了什么变化，"
        "画面里有没有可读的文字或界面元素。"
        "只描述看得见的内容；看不清就说看不清，不要推测剧情。"
    )
    return f"{head}\n用户想知道：{question}" if question else head


def _one_line(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


__all__ = [
    "IMAGE_DEFAULT_QUESTION",
    "TEXT_MAX_CHARS",
    "Prepared",
    "prepare_attachments",
]
