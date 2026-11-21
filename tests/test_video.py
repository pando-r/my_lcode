from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from lcode import video as video_mod
from lcode.video import (
    FRAME_COUNT,
    Frame,
    VideoError,
    extract_frames,
    find_ffmpeg,
    frame_timestamps,
    probe_duration,
)


def completed(args, stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=stderr)


STDERR_WITH_DURATION = """
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'run.mp4':
  Duration: 00:00:28.05, start: 0.000000, bitrate: 64601 kb/s
  Stream #0:0[0x1](und): Video: h264 (High), 2560x1600, 60 fps
"""


# ── 抽帧时间点 ───────────────────────────────────────────────────────────


def test_frame_timestamps_are_evenly_spaced():
    stamps = frame_timestamps(28.0, 16)
    assert len(stamps) == 16
    step = 28.0 / 16
    # 取每段中点：跳过片头淡入和片尾黑场
    assert stamps[0] == pytest.approx(step / 2)
    assert stamps[-1] == pytest.approx(28.0 - step / 2)
    gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
    assert all(gap == pytest.approx(step) for gap in gaps)


def test_frame_timestamps_guards_bad_input():
    assert frame_timestamps(0.0, 16) == []
    assert frame_timestamps(10.0, 0) == []
    # 极短的视频也要给出 16 个递增时间点（不是报错）
    stamps = frame_timestamps(0.2, 16)
    assert len(stamps) == 16
    assert stamps == sorted(stamps)


def test_default_frame_count_is_16():
    assert FRAME_COUNT == 16


def test_frame_label_shows_seconds():
    assert Frame("a.jpg", 3.5).label == "第 3.5 秒"


# ── ffmpeg 定位 ──────────────────────────────────────────────────────────


def test_find_ffmpeg_prefers_path(monkeypatch):
    monkeypatch.setattr(video_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert find_ffmpeg() == "/usr/bin/ffmpeg"


def test_find_ffmpeg_falls_back_to_imageio(monkeypatch, tmp_path):
    # 没配 PATH 时不直接放弃：imageio-ffmpeg 自带一份二进制
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(video_mod.shutil, "which", lambda name: None)

    class _Fake:
        @staticmethod
        def get_ffmpeg_exe() -> str:
            return str(fake)

    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", _Fake)
    assert find_ffmpeg() == str(fake)


def test_find_ffmpeg_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(video_mod.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)  # import 时抛 ImportError
    assert find_ffmpeg() is None


def test_find_ffmpeg_ignores_broken_imageio(monkeypatch):
    monkeypatch.setattr(video_mod.shutil, "which", lambda name: None)

    class _Broken:
        @staticmethod
        def get_ffmpeg_exe() -> str:
            raise RuntimeError("download failed")

    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", _Broken)
    assert find_ffmpeg() is None


# ── 时长解析 ─────────────────────────────────────────────────────────────


def test_probe_duration_parses_stderr(monkeypatch):
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        # 没有输出文件时 ffmpeg 返回 1，这里必须照样解析 stderr
        return completed(cmd, stderr=STDERR_WITH_DURATION, code=1)

    monkeypatch.setattr(video_mod.subprocess, "run", fake_run)
    assert probe_duration("ffmpeg", "run.mp4") == pytest.approx(28.05)
    assert seen[0][-1] == "run.mp4"


def test_probe_duration_handles_hours(monkeypatch):
    def fake_run(cmd, **kwargs):
        return completed(cmd, stderr="  Duration: 01:02:03.50, start: 0.0\n")

    monkeypatch.setattr(video_mod.subprocess, "run", fake_run)
    assert probe_duration("ffmpeg", "long.mp4") == pytest.approx(3723.5)


def test_probe_duration_reports_unreadable(monkeypatch):
    def fake_run(cmd, **kwargs):
        return completed(cmd, stderr="run.mp4: Invalid data found when processing input\n")

    monkeypatch.setattr(video_mod.subprocess, "run", fake_run)
    with pytest.raises(VideoError, match="读不出视频时长"):
        probe_duration("ffmpeg", "broken.mp4")


def test_probe_duration_rejects_zero(monkeypatch):
    def fake_run(cmd, **kwargs):
        return completed(cmd, stderr="  Duration: 00:00:00.00, start: 0.0\n")

    monkeypatch.setattr(video_mod.subprocess, "run", fake_run)
    with pytest.raises(VideoError, match="时长异常"):
        probe_duration("ffmpeg", "empty.mp4")


# ── 抽帧 ─────────────────────────────────────────────────────────────────


def fake_extraction(monkeypatch, duration: float = 28.0, fail_after: float | None = None):
    """让 subprocess.run 假装成 ffmpeg：按 -ss 指定的时间写出 JPEG。"""
    monkeypatch.setattr(video_mod, "probe_duration", lambda exe, path: duration)
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        seconds = float(cmd[cmd.index("-ss") + 1])
        if fail_after is not None and seconds > fail_after:
            return completed(cmd, code=1)
        pathlib.Path(cmd[-1]).write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)
        return completed(cmd)

    monkeypatch.setattr(video_mod.subprocess, "run", run)
    return calls


def test_extract_frames_writes_16_files(tmp_path, monkeypatch):
    src = tmp_path / "run.mp4"
    src.write_bytes(b"\x00" * 64)
    calls = fake_extraction(monkeypatch)

    frames = extract_frames(src, tmp_path / "frames", ffmpeg="ffmpeg")

    assert len(frames) == 16
    assert all(pathlib.Path(frame.path).is_file() for frame in frames)
    assert [frame.seconds for frame in frames] == pytest.approx(frame_timestamps(28.0, 16))
    # -ss 在 -i 之前：走关键帧定位，避免把整片解码一遍
    assert calls[0].index("-ss") < calls[0].index("-i")
    assert "-an" in calls[0]  # 抽画面不需要音轨
    # 缩放表达式里的逗号必须转义，否则会被当成下一个滤镜
    scale = calls[0][calls[0].index("-vf") + 1]
    assert scale.startswith("scale=min(")
    assert "\\," in scale


def test_extract_frames_tolerates_failed_seeks(tmp_path, monkeypatch):
    src = tmp_path / "run.mp4"
    src.write_bytes(b"\x00" * 64)
    fake_extraction(monkeypatch, duration=28.0, fail_after=20.0)

    frames = extract_frames(src, tmp_path / "frames", ffmpeg="ffmpeg")
    # 片尾 seek 落空少几帧照样能用
    assert 0 < len(frames) < 16


def test_extract_frames_raises_when_nothing_extracted(tmp_path, monkeypatch):
    src = tmp_path / "run.mp4"
    src.write_bytes(b"\x00" * 64)
    fake_extraction(monkeypatch, fail_after=0.0)

    with pytest.raises(VideoError, match="没有抽出任何画面"):
        extract_frames(src, tmp_path / "frames", ffmpeg="ffmpeg")


def test_extract_frames_without_ffmpeg_gives_install_hint(tmp_path, monkeypatch):
    src = tmp_path / "run.mp4"
    src.write_bytes(b"\x00" * 64)
    monkeypatch.setattr(video_mod, "find_ffmpeg", lambda: None)

    with pytest.raises(VideoError) as err:
        extract_frames(src, tmp_path / "frames")
    assert "ffmpeg" in str(err.value)
    assert "winget" in str(err.value) or "imageio-ffmpeg" in str(err.value)


def test_extract_frames_rejects_missing_file(tmp_path):
    with pytest.raises(VideoError, match="文件不存在"):
        extract_frames(tmp_path / "nope.mp4", tmp_path / "frames", ffmpeg="ffmpeg")
