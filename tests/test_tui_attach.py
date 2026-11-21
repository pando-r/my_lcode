from __future__ import annotations

import base64
import pathlib
import struct
import subprocess
import sys
import zlib

import pytest

from lcode.audio import Segment, Transcript
from lcode.config import AudioConfig, VisionConfig
from lcode.tool import new_default_registry
from lcode.tool.transcribe_audio import TranscribeAudioTool
from lcode.tool.view_image import ViewImageTool
from lcode.tui import attach as attach_mod
from lcode.tui import attachments as attachments_mod
from lcode.tui.attach import (
    MAX_ATTACHMENTS,
    AttachError,
    Attachment,
    attach_summary,
    classify,
    dedupe,
    render_chips,
)
from lcode.tui.attachments import IMAGE_DEFAULT_QUESTION, prepare_attachments
from lcode.video import Frame


def png_bytes(width: int = 8, height: int = 8) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x80\x80\x80" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def wav_bytes(seconds: float = 0.2) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


# ── classify / dedupe / 展示 ─────────────────────────────────────────────


def test_classify_image(tmp_path):
    p = tmp_path / "shot.png"
    p.write_bytes(png_bytes(40, 20))
    item = classify(str(p))
    assert item.kind == "image"
    assert item.supported is True
    assert "40x20" in item.note
    assert item.label == "shot.png(图)"


def test_classify_audio(tmp_path):
    p = tmp_path / "note.wav"
    p.write_bytes(wav_bytes())
    item = classify(str(p))
    assert item.kind == "audio"
    assert item.supported is True
    assert item.label.endswith("(音)")


def test_classify_text_file(tmp_path):
    p = tmp_path / "app.log"
    p.write_text("line1\nline2\n", encoding="utf-8")
    item = classify(str(p))
    assert item.kind == "text"
    # 文本也允许附加：用户可能想让模型读日志
    assert item.supported is True


def test_classify_rejects_binary_and_missing(tmp_path):
    z = tmp_path / "a.zip"
    z.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    item = classify(str(z))
    assert item.kind == "archive"
    assert item.supported is False

    with pytest.raises(AttachError, match="文件不存在"):
        classify(str(tmp_path / "nope.png"))


def test_dedupe_uses_absolute_path(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(png_bytes())
    item = classify(str(p))
    assert len(dedupe([item, item])) == 1


def test_render_chips_empty_and_full(tmp_path):
    assert render_chips([]).plain == ""

    a = Attachment(path=str(tmp_path / "a.png"), kind="image", size=100, note="8x8 100B")
    b = Attachment(path=str(tmp_path / "b.wav"), kind="audio", size=200, note="audio/wav")
    text = render_chips([a, b])
    assert "a.png(图)" in text.plain
    assert "b.wav(音)" in text.plain
    assert "共 2 个" in text.plain
    assert "Esc 清空" in text.plain


def test_attach_summary(tmp_path):
    items = [
        Attachment(path=str(tmp_path / "a.png"), kind="image"),
        Attachment(path=str(tmp_path / "b.wav"), kind="audio"),
    ]
    assert attach_summary(items) == "a.png(图)、b.wav(音)"


def test_max_attachments_is_bounded():
    # 和 view_image 的 6 张上限对齐，避免附加了却发不出去
    assert MAX_ATTACHMENTS == 6


# ── prepare_attachments：附件 → 提示词 ───────────────────────────────────


async def test_prepare_image_goes_through_view_image(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(png_bytes())
    calls: list[dict] = []

    async def fake_asker(cfg, payloads, question, detail):
        calls.append({"question": question, "n": len(payloads)})
        return "第 3 行 KeyError"

    registry = new_default_registry(
        vision=VisionConfig(api_key="k", model="qwen3-vl-plus")
    )
    registry._tools["view_image"] = ViewImageTool(  # 注入假 asker
        VisionConfig(api_key="k", model="qwen3-vl-plus"), asker=fake_asker
    )

    prepared = await prepare_attachments(
        [classify(str(img))], registry, question="这个报错是什么"
    )
    assert not prepared.errors
    assert "第 3 行 KeyError" in prepared.blocks[0]
    assert "shot.png" in prepared.blocks[0]
    # 用户的问题要作为 view_image 的 question 传下去
    assert calls[0]["question"] == "这个报错是什么"

    prompt = prepared.prompt("这个报错是什么")
    assert "用户附加了以下文件" in prompt
    assert "第 3 行 KeyError" in prompt
    assert "用户的问题：这个报错是什么" in prompt


async def test_prepare_image_without_question_uses_default(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(png_bytes())
    seen: list[str] = []

    async def fake_asker(cfg, payloads, question, detail):
        seen.append(question)
        return "ok"

    registry = new_default_registry(vision=VisionConfig(api_key="k", model="m"))
    registry._tools["view_image"] = ViewImageTool(
        VisionConfig(api_key="k", model="m"), asker=fake_asker
    )
    prepared = await prepare_attachments([classify(str(img))], registry, question="")
    assert seen == [IMAGE_DEFAULT_QUESTION]
    assert "用户没有补充说明" in prepared.prompt("")


async def test_prepare_audio_goes_through_transcribe(tmp_path):
    wav = tmp_path / "note.wav"
    wav.write_bytes(wav_bytes())
    seen: list[dict] = []

    async def fake(cfg, path, language="", prompt=""):
        seen.append({"path": path, "prompt": prompt})
        return Transcript([Segment(0, 4, "讨论媒体层")], language="zh", model="qwen3-asr-flash")

    registry = new_default_registry(audio=AudioConfig(api_key="k", model="m"))
    registry._tools["transcribe_audio"] = TranscribeAudioTool(
        AudioConfig(api_key="k", model="m"), transcriber=fake
    )

    prepared = await prepare_attachments(
        [classify(str(wav))], registry, question="lcode 重构"
    )
    assert "讨论媒体层" in prepared.blocks[0]
    # prompt 也传给了转写（专有名词能提升识别率）
    assert seen[0]["prompt"] == "lcode 重构"


async def test_prepare_text_file_inlined(tmp_path):
    log = tmp_path / "err.log"
    log.write_text("Traceback:\n  KeyError\n", encoding="utf-8")
    registry = new_default_registry()
    prepared = await prepare_attachments([classify(str(log))], registry, question="什么问题")
    assert "Traceback" in prepared.blocks[0]
    assert "err.log" in prepared.blocks[0]


async def test_prepare_text_file_truncated(tmp_path):
    from lcode.tui.attachments import TEXT_MAX_CHARS

    big = tmp_path / "big.log"
    big.write_text("x" * (TEXT_MAX_CHARS + 500), encoding="utf-8")
    registry = new_default_registry()
    prepared = await prepare_attachments([classify(str(big))], registry)
    assert "[附件内容已截断]" in prepared.blocks[0]
    assert len(prepared.blocks[0]) < TEXT_MAX_CHARS + 200


async def test_prepare_unsupported_kind_reports_error(tmp_path):
    z = tmp_path / "a.zip"
    z.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    registry = new_default_registry()
    prepared = await prepare_attachments([classify(str(z))], registry)
    assert prepared.blocks == []
    assert prepared.errors
    assert "只能附加" in prepared.errors[0]


async def test_prepare_image_without_vision_tool_reports_error(tmp_path):
    # 没配 vision 时 view_image 根本不注册，要让用户看到明确原因
    img = tmp_path / "a.png"
    img.write_bytes(png_bytes())
    registry = new_default_registry()
    prepared = await prepare_attachments([classify(str(img))], registry)
    assert prepared.blocks == []
    assert "图片读取失败" in prepared.errors[0]


async def test_prepare_mixed_reports_partial(tmp_path):
    img = tmp_path / "ok.png"
    img.write_bytes(png_bytes())
    z = tmp_path / "bad.zip"
    z.write_bytes(b"PK\x03\x04" + b"\x00" * 32)

    async def fake_asker(cfg, payloads, question, detail):
        return "看到的东西"

    registry = new_default_registry(vision=VisionConfig(api_key="k", model="m"))
    registry._tools["view_image"] = ViewImageTool(
        VisionConfig(api_key="k", model="m"), asker=fake_asker
    )
    prepared = await prepare_attachments([classify(str(img)), classify(str(z))], registry)
    # 一部分成功：成功的内容仍要发出去，失败的列在「未能读取的部分」
    assert len(prepared.blocks) == 1
    assert len(prepared.errors) == 1
    prompt = prepared.prompt("看看")
    assert "未能读取的部分" in prompt


async def test_prepare_empty_is_identity():
    prepared = await prepare_attachments([], new_default_registry())
    assert prepared.prompt("hello") == "hello"


# ── 视频：抽帧 → 分批 view_image ─────────────────────────────────────────


def video_attachment(tmp_path, name: str = "run.mp4") -> Attachment:
    path = tmp_path / name
    path.write_bytes(b"\x00" * 64)
    # 目录里没有真的 mp4 也能测：抽帧被 monkeypatch 掉，kind 由调用方给定。
    return Attachment(path=str(path), kind="video")


def fake_frame_extraction(monkeypatch, count: int = 16):
    """把抽帧替换成「写出 count 张真 PNG」，并记录临时文件路径。"""
    written: list[str] = []

    def extract(path, out_dir, ffmpeg=None, frames=16):
        result = []
        for index in range(count):
            frame_path = pathlib.Path(out_dir) / f"frame{index:02d}.jpg"
            frame_path.write_bytes(png_bytes())
            written.append(str(frame_path))
            result.append(Frame(str(frame_path), float(index)))
        return result

    monkeypatch.setattr(attachments_mod.video_mod, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(attachments_mod.video_mod, "extract_frames", extract)
    return written


async def test_prepare_video_batches_frames_through_view_image(tmp_path, monkeypatch):
    written = fake_frame_extraction(monkeypatch)
    calls: list[dict] = []

    async def fake_asker(cfg, payloads, question, detail):
        calls.append({"n": len(payloads), "question": question})
        return f"这一批有 {len(payloads)} 帧"

    registry = new_default_registry(vision=VisionConfig(api_key="k", model="m"))
    registry._tools["view_image"] = ViewImageTool(
        VisionConfig(api_key="k", model="m"), asker=fake_asker
    )

    prepared = await prepare_attachments(
        [video_attachment(tmp_path)], registry, question="这视频讲了什么"
    )

    assert not prepared.errors
    block = prepared.blocks[0]
    assert "抽了 16 帧" in block
    # 16 帧受 view_image 的 6 张上限约束，切成 6+6+4
    assert [call["n"] for call in calls] == [6, 6, 4]
    # 每批都带时间范围，主模型才知道「哪一段」
    assert "第 0.0 秒 → 第 5.0 秒" in block
    assert "第 15.0 秒" in block
    # 用户的问题要传到视觉模型那里（不是只给「这视频讲了什么」的通用问法）
    assert "用户想知道：这视频讲了什么" in calls[0]["question"]
    # 临时帧用完就删，不在用户磁盘上留垃圾
    assert written and not any(pathlib.Path(p).exists() for p in written)


async def test_prepare_video_keeps_partial_answer_when_batch_fails(tmp_path, monkeypatch):
    fake_frame_extraction(monkeypatch)
    calls = {"n": 0}

    async def flaky(cfg, payloads, question, detail):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("502 Bad Gateway")
        return "第一批看清楚了"

    registry = new_default_registry(vision=VisionConfig(api_key="k", model="m"))
    registry._tools["view_image"] = ViewImageTool(
        VisionConfig(api_key="k", model="m"), asker=flaky
    )

    prepared = await prepare_attachments([video_attachment(tmp_path)], registry)

    # 一批失败不该吞掉整段视频：成功的那批仍要发出去
    assert not prepared.errors
    block = prepared.blocks[0]
    assert "第一批看清楚了" in block
    assert "这批帧没读出来" in block
    assert "502" in block


async def test_prepare_video_without_ffmpeg_reports_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments_mod.video_mod, "find_ffmpeg", lambda: None)
    registry = new_default_registry(vision=VisionConfig(api_key="k", model="m"))

    prepared = await prepare_attachments([video_attachment(tmp_path)], registry)

    assert prepared.blocks == []
    assert "ffmpeg" in prepared.errors[0]
    # 错误要指向「怎么装」，而不是只说失败了
    assert "winget" in prepared.errors[0] or "imageio-ffmpeg" in prepared.errors[0]


async def test_prepare_video_without_vision_reports_reason(tmp_path, monkeypatch):
    # 没配 vision 时不该白抽 16 帧
    monkeypatch.setattr(
        attachments_mod.video_mod,
        "extract_frames",
        lambda *a, **kw: pytest.fail("vision 未配置时不该抽帧"),
    )
    prepared = await prepare_attachments([video_attachment(tmp_path)], new_default_registry())
    assert prepared.blocks == []
    assert "vision" in prepared.errors[0]


async def test_prepare_video_and_audio_take_separate_paths(tmp_path, monkeypatch):
    # 视频走抽帧、音频走转写：两者不能互相吞掉
    fake_frame_extraction(monkeypatch)

    async def fake_asker(cfg, payloads, question, detail):
        return "画面内容"

    wav = tmp_path / "note.wav"
    wav.write_bytes(wav_bytes())

    async def fake_transcribe(cfg, path, language="", prompt=""):
        return Transcript([Segment(0, 2, "语音内容")], language="zh", model="m")

    registry = new_default_registry(
        vision=VisionConfig(api_key="k", model="m"),
        audio=AudioConfig(api_key="k", model="m"),
    )
    registry._tools["view_image"] = ViewImageTool(
        VisionConfig(api_key="k", model="m"), asker=fake_asker
    )
    registry._tools["transcribe_audio"] = TranscribeAudioTool(
        AudioConfig(api_key="k", model="m"), transcriber=fake_transcribe
    )

    prepared = await prepare_attachments(
        [classify(str(wav)), video_attachment(tmp_path)], registry
    )
    assert not prepared.errors
    joined = "\n".join(prepared.blocks)
    assert "语音内容" in joined
    assert "画面内容" in joined


# ── Windows 选择器：输出编码 ─────────────────────────────────────────────
# 真实故障：PowerShell 把路径按控制台代码页（中文系统 cp936）写到 stdout，
# 而 _windows_picker 用 utf-8 解，中文路径变成乱码 → classify 报「文件不存在」，
# 用户看到的是「文件明明存在却说读不到」。


def _script_of(cmd: list[str]) -> str:
    encoded = cmd[cmd.index("-EncodedCommand") + 1]
    return base64.b64decode(encoded).decode("utf-16-le")


def test_windows_picker_pins_utf8_output():
    # 脚本必须自己把输出编码钉成 UTF-8，否则中文路径必坏
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(attach_mod.subprocess, "run", fake_run)
        attach_mod._windows_picker("t")
    finally:
        monkeypatch.undo()

    script = _script_of(captured[0])
    assert "[Console]::OutputEncoding" in script
    assert "$OutputEncoding" in script


def test_windows_picker_keeps_non_ascii_path(tmp_path):
    img = tmp_path / "报错截图.png"
    img.write_bytes(png_bytes())
    payload = (str(img) + "\r\n").encode("utf-8")

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            attach_mod.subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr=b""),
        )
        paths = attach_mod.pick_files_blocking()
    finally:
        monkeypatch.undo()

    assert paths == [str(img)]
    assert classify(paths[0]).kind == "image"


def test_windows_picker_falls_back_to_console_codepage(tmp_path):
    # 某些宿主会忽略 OutputEncoding 设置：此时必须退回控制台代码页而不是出乱码
    img = tmp_path / "报错截图.png"
    img.write_bytes(png_bytes())
    oem = "oem" if sys.platform == "win32" else "gb18030"
    payload = (str(img) + "\r\n").encode(oem)

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            attach_mod.subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr=b""),
        )
        paths = attach_mod.pick_files_blocking()
    finally:
        monkeypatch.undo()

    assert paths == [str(img)], "解码失败时不该把路径交给 classify"
    assert classify(paths[0]).kind == "image"
