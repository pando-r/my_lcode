"""视频抽帧：把「附加一个视频」也变成主模型能读的文字。

主模型是纯文本的，视频没法直接塞进对话。附件链路里视频原本只走
transcribe_audio 抽音轨——录屏/游戏录像往往没有语音，用户看到的就是
「没有识别出任何语音」，画面信息全部丢失。这里补上另一半：按固定帧数
均匀抽帧，交给 view_image 逐批看。

两个刻意的取舍：

- **固定帧数而不是固定间隔**：16 帧对 10 秒 clips 和 10 分钟录像都给出同样的
  成本上限，问「这视频讲了什么」够用；细节复盘再让模型自己用 crop 放大。
- **不用 ffprobe**：时长从 `ffmpeg -i` 的 stderr 里解析，少一个依赖。
  ffmpeg 本身找不到时先退回 imageio-ffmpeg 自带的二进制（pip 装过就在），
  免得「装个 ffmpeg」变成使用前置门槛。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# 抽帧数量：和 view_image 的 6 张上限配合（6+6+4 = 3 次视觉请求）。
FRAME_COUNT = 16
# 抽出来的帧先缩到这个宽度再送视觉模型：1600p 原图里全是游戏 UI 细节，
# 每张 3 万 token 级别的成本换不来更好的「发生了什么」。
FRAME_WIDTH = 960
# ffmpeg 的 mjpeg 质量参数（2 最好，31 最差）。3 ≈ 每帧 40KB。
JPEG_QUALITY = "3"

# 单个 ffmpeg 调用的上限：抽帧是本地解码，正常几秒；卡住就是文件坏了。
RUN_TIMEOUT = 120.0

FFMPEG_HINT = (
    "需要 ffmpeg 才能抽视频画面，但没找到。\n"
    "请安装：winget install Gyan.FFmpeg（或 apt/brew 对应命令），"
    "或 pip install imageio-ffmpeg 让它自带一份二进制。"
)

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


class VideoError(Exception):
    """Raised when a video cannot be probed or its frames cannot be extracted."""


@dataclass(frozen=True)
class Frame:
    path: str
    seconds: float

    @property
    def label(self) -> str:
        return f"第 {self.seconds:.1f} 秒"


def find_ffmpeg() -> str | None:
    """Locate an ffmpeg binary: PATH first, then imageio-ffmpeg's bundled copy."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - 二进制缺失/下载失败都算「没有」
        return None
    return exe if exe and Path(exe).is_file() else None


def probe_duration(ffmpeg: str, path: str | Path) -> float:
    """Read the media duration (seconds) out of `ffmpeg -i` stderr."""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=RUN_TIMEOUT,
        check=False,  # 没有输出文件时 ffmpeg 必然返回 1，这里只看 stderr
    )
    match = _DURATION_RE.search(proc.stderr or "")
    if match is None:
        raise VideoError(f"读不出视频时长：{Path(path).name}（可能不是可解码的视频文件）")
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    duration = hours * 3600 + minutes * 60 + seconds
    if duration <= 0:
        raise VideoError(f"视频时长异常（{duration}s）：{Path(path).name}")
    return duration


def frame_timestamps(duration: float, count: int = FRAME_COUNT) -> list[float]:
    """Evenly spaced sample points, taken mid-segment to skip fade-in/out frames."""
    if duration <= 0 or count <= 0:
        return []
    step = duration / count
    return [step * (index + 0.5) for index in range(count)]


def extract_frames(
    path: str | Path,
    out_dir: str | Path,
    ffmpeg: str | None = None,
    count: int = FRAME_COUNT,
) -> list[Frame]:
    """Extract `count` evenly spaced JPEG frames into `out_dir`.

    每帧单独 seek：`-ss` 放在 `-i` 前面走的是「关键帧定位 + 解到目标时间」，
    比整片解码一遍快得多，也不会因为一次 `fps` 滤镜就把 216MB 原片全解完。
    """
    exe = ffmpeg or find_ffmpeg()
    if exe is None:
        raise VideoError(FFMPEG_HINT)
    source = Path(path)
    if not source.is_file():
        raise VideoError(f"文件不存在: {source}")

    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(exe, source)

    frames: list[Frame] = []
    # 逗号在 filtergraph 里是参数分隔符，必须转义才不会被当成「下一个滤镜」。
    scale = f"scale=min({FRAME_WIDTH}\\,iw):-2"
    for index, seconds in enumerate(frame_timestamps(duration, count)):
        target = target_dir / f"frame{index:02d}.jpg"
        proc = subprocess.run(
            [
                exe,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{seconds:.3f}",
                "-i",
                str(source),
                "-an",
                "-frames:v",
                "1",
                "-vf",
                scale,
                "-q:v",
                JPEG_QUALITY,
                str(target),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RUN_TIMEOUT,
            check=False,
        )
        # 个别 seek 落空（片尾元信息比实际画面长）不算失败，少一帧照样能用。
        if proc.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            frames.append(Frame(str(target), seconds))
    if not frames:
        raise VideoError(f"没有抽出任何画面：{source.name}")
    return frames


__all__ = [
    "FFMPEG_HINT",
    "FRAME_COUNT",
    "FRAME_WIDTH",
    "JPEG_QUALITY",
    "VideoError",
    "Frame",
    "extract_frames",
    "find_ffmpeg",
    "frame_timestamps",
    "probe_duration",
]
