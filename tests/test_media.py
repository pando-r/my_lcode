from __future__ import annotations

import json
import struct
import wave
import zlib

import pytest

from lcode.audio import AudioError, Segment, Transcript, clock, prepare
from lcode.command.builtin_media import (
    AUDIO_USAGE,
    handle_audio,
    handle_image,
    split_path_and_question,
)
from lcode.command.ui import NopUI
from lcode.config import AudioConfig, VisionConfig
from lcode.tool import Registry, Result, new_default_registry
from lcode.tool.media import human_size, image_size, looks_binary, probe, sniff_mime
from lcode.tool.read_file import ReadFileTool
from lcode.tool.transcribe_audio import TranscribeAudioTool
from lcode.tool.view_image import ViewImageTool
from lcode.vision import VisionError, load_image


def png_bytes(width: int = 1, height: int = 1, value: int = 0) -> bytes:
    # 手搓一个合法 PNG：不依赖 Pillow，也能被 Pillow 正常打开。
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes([value, value, value]) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


# ── 类型嗅探 ─────────────────────────────────────────────────────────────


def test_sniff_common_types():
    assert sniff_mime(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "image/png"
    assert sniff_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 8) == "image/jpeg"
    assert sniff_mime(b"GIF89a" + b"\x00" * 8) == "image/gif"
    assert sniff_mime(b"%PDF-1.7\n") == "application/pdf"
    assert sniff_mime(b"PK\x03\x04" + b"\x00" * 8) == "application/zip"
    assert sniff_mime(b"ID3\x04\x00") == "audio/mpeg"
    assert sniff_mime(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "audio/wav"
    # 纯文本没有魔数
    assert sniff_mime(b"def main():\n    pass\n") == ""


def test_sniff_mp3_without_id3():
    assert sniff_mime(b"\xff\xfb\x90\x64") == "audio/mpeg"


def test_sniff_mp4_family_splits_audio_and_video():
    # mp4 家族靠 ftyp 的 major brand 区分：M4A/M4B 是纯音频（手机录音常见），
    # 判成 video 会让附件链路去抽帧——那条路对纯音频必然失败。
    assert sniff_mime(b"\x00\x00\x00\x14ftypM4A \x00\x00\x00\x00") == "audio/mp4"
    assert sniff_mime(b"\x00\x00\x00\x14ftypM4B \x00\x00\x00\x00") == "audio/mp4"
    assert sniff_mime(b"\x00\x00\x00\x14ftypmp42\x00\x00\x00\x00") == "video/mp4"
    assert sniff_mime(b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00") == "video/mp4"


def test_probe_m4a_voice_memo_is_audio(tmp_path):
    p = tmp_path / "memo.m4a"
    p.write_bytes(b"\x00\x00\x00\x14ftypM4A " + b"\x00" * 64)
    assert probe(p).kind == "audio"


def test_probe_mp4_is_video(tmp_path):
    p = tmp_path / "run.mp4"
    p.write_bytes(b"\x00\x00\x00\x14ftypisom" + b"\x00" * 64)
    assert probe(p).kind == "video"


def test_looks_binary_and_size():
    assert looks_binary(b"hello\nworld\n") is False
    assert looks_binary(b"\x00\x01\x02") is True
    assert image_size(png_bytes(37, 21)) == (37, 21)
    assert human_size(2048) == "2.0KB"


def test_probe_text_and_image(tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("hello", encoding="utf-8")
    info = probe(txt)
    assert info.is_text and info.kind == "text"

    img = tmp_path / "a.png"
    img.write_bytes(png_bytes(4, 4))
    info = probe(img)
    assert info.is_image and info.mime == "image/png"
    assert (info.width, info.height) == (4, 4)


def test_probe_docx_is_office(tmp_path):
    doc = tmp_path / "报告.docx"
    doc.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    info = probe(doc)
    assert info.kind == "office"
    assert info.mime.endswith("wordprocessingml.document")


# ── read_file 不再把二进制当文本 ─────────────────────────────────────────


async def test_read_file_rejects_image(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(png_bytes(20, 10))
    r = await ReadFileTool().execute(json.dumps({"path": str(img)}))
    assert r.is_error
    assert "image/png" in r.content
    assert "20x10" in r.content
    assert "view_image" in r.content
    # 关键回归点：不再吐出替换字符组成的乱码
    assert "\ufffd" not in r.content


async def test_read_file_rejects_audio(tmp_path):
    mp3 = tmp_path / "voice.mp3"
    mp3.write_bytes(b"ID3\x04\x00\x00" + b"\x00" * 64)
    r = await ReadFileTool().execute(json.dumps({"path": str(mp3)}))
    assert r.is_error
    assert "audio/mpeg" in r.content


async def test_read_file_decodes_gbk(tmp_path):
    p = tmp_path / "gbk.txt"
    p.write_bytes("中文内容\n".encode("gbk"))
    r = await ReadFileTool().execute(json.dumps({"path": str(p)}))
    assert not r.is_error
    assert "中文内容" in r.content
    assert "\ufffd" not in r.content


# ── 配置 ─────────────────────────────────────────────────────────────────


def test_new_default_registry_without_vision_has_base_tools():
    reg = new_default_registry()
    # 7 个文件/命令/通知工具 + send_mail（主动发信）= 8
    assert reg.count() == 8
    assert reg.get("view_image") is None
    assert reg.get("send_mail") is not None


def test_new_default_registry_with_vision_adds_view_image():
    reg = new_default_registry(VisionConfig(api_key="k", model="qwen-vl-max"))
    assert reg.count() == 9
    assert reg.get("view_image") is not None
    # 只读：Plan 模式下也能用，且会并发执行
    assert reg.is_read_only("view_image") is True


async def test_registry_honours_tool_timeout():
    import asyncio

    class SlowTool:
        read_only: bool = True
        timeout = 0.05

        def name(self) -> str:
            return "slow"

        def description(self) -> str:
            return "slow"

        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        async def execute(self, args: str) -> Result:
            await asyncio.sleep(1)
            return Result(content="done")

    reg = Registry()
    reg.register(SlowTool())
    # 调用方给了 30s，但工具自带的 0.05s 优先。
    result = await reg.execute("slow", "{}", timeout=30)
    assert result.is_error
    assert "0.05" in result.content


# ── view_image ───────────────────────────────────────────────────────────


class FakeVision:
    def __init__(self) -> None:
        self.calls: list[tuple[list, str, str]] = []

    async def __call__(self, cfg, payloads, question, detail):
        self.calls.append((payloads, question, detail))
        return "报错在第 3 行：KeyError: 'name'"


async def test_view_image_answers_question(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(png_bytes(100, 50))
    asker = FakeVision()
    tool = ViewImageTool(VisionConfig(api_key="k", model="qwen-vl-max"), asker=asker)

    r = await tool.execute(
        json.dumps({"paths": [str(img)], "question": "报错在哪一行？"})
    )
    assert not r.is_error
    assert "KeyError" in r.content
    assert "shot.png" in r.content
    assert "100x50" in r.content
    assert "vision: qwen-vl-max" in r.content
    assert asker.calls[0][1] == "报错在哪一行？"


async def test_view_image_requires_question(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(png_bytes())
    tool = ViewImageTool(VisionConfig(api_key="k", model="m"))
    r = await tool.execute(json.dumps({"paths": [str(img)]}))
    assert r.is_error
    assert "question" in r.content


async def test_view_image_rejects_unknown_format(tmp_path):
    bad = tmp_path / "a.txt"
    bad.write_text("not an image", encoding="utf-8")
    tool = ViewImageTool(VisionConfig(api_key="k", model="m"))
    r = await tool.execute(json.dumps({"paths": [str(bad)], "question": "看图"}))
    assert r.is_error
    assert "不支持的图片格式" in r.content


async def test_view_image_without_config_returns_hint(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(png_bytes())
    r = await ViewImageTool(None).execute(
        json.dumps({"paths": [str(img)], "question": "看图"})
    )
    assert r.is_error
    assert "vision" in r.content


async def test_view_image_uses_config_detail_and_max_side(tmp_path):
    # 参数里不写 detail 时，应该用配置里的值（不是硬编码的 high）。
    img = tmp_path / "shot.png"
    img.write_bytes(png_bytes(64, 64))
    asker = FakeVision()
    cfg = VisionConfig(api_key="k", model="m", detail="low", max_image_side=32)
    tool = ViewImageTool(cfg, asker=asker)
    r = await tool.execute(json.dumps({"paths": [str(img)], "question": "看结构"}))
    assert not r.is_error
    assert asker.calls[0][2] == "low"
    # max_side 也来自配置：64x64 的图被缩到 32
    payload = asker.calls[0][0][0]
    assert max(payload.width, payload.height) <= 32


async def test_view_image_crop_out_of_range(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(png_bytes(10, 10))
    tool = ViewImageTool(VisionConfig(api_key="k", model="m"), asker=FakeVision())
    r = await tool.execute(
        json.dumps({"paths": [str(img)], "question": "看", "crop": [0, 0, 100, 100]})
    )
    assert r.is_error
    assert "crop 超出图片范围" in r.content


def test_load_image_downscales_large_image(tmp_path):
    pytest.importorskip("PIL")
    img = tmp_path / "big.png"
    img.write_bytes(png_bytes(64, 32))
    payload = load_image(str(img), max_side=16)
    assert payload.mime in ("image/png", "image/jpeg")
    assert max(payload.width, payload.height) <= 16
    assert "已缩放" in payload.note


def test_load_image_missing_file(tmp_path):
    with pytest.raises(VisionError):
        load_image(str(tmp_path / "nope.png"))


def test_check_total_size_rejects_oversized_batch():
    from lcode.vision import MAX_TOTAL_BYTES, ImagePayload, check_total_size, total_bytes

    # 每张 4MB，三张就 12MB，超过 10MB 总上限（base64 后 16MB，撞 DashScope 的 16MiB）
    payloads = [
        ImagePayload(f"/x{i}.png", "image/png", b"\x00" * (4 * 1024 * 1024))
        for i in range(3)
    ]
    assert total_bytes(payloads) > MAX_TOTAL_BYTES
    with pytest.raises(VisionError, match="超过单次请求上限"):
        check_total_size(payloads)


def test_check_total_size_allows_normal_batch():
    from lcode.vision import ImagePayload, check_total_size

    payloads = [ImagePayload(f"/x{i}.png", "image/png", b"\x00" * 1000) for i in range(6)]
    check_total_size(payloads)  # 不抛异常


async def test_view_image_rejects_oversized_batch_before_request(tmp_path, monkeypatch):
    # 总大小超限时不该发请求：错误要指向「怎么改」，而不是服务端原始 400。
    from lcode.tool import view_image as view_image_mod
    from lcode.vision import ImagePayload

    asker = FakeVision()
    tool = ViewImageTool(VisionConfig(api_key="k", model="m"), asker=asker)

    def fake_load(path, max_side=1568, crop=None):
        # 每张 4MB，三张 12MB > 10MB 上限
        return ImagePayload(str(path), "image/png", b"\x00" * (4 * 1024 * 1024))

    monkeypatch.setattr(view_image_mod, "load_image", fake_load)

    paths = [str(tmp_path / f"big{i}.png") for i in range(3)]
    r = await tool.execute(json.dumps({"paths": paths, "question": "看"}))

    assert r.is_error
    assert "超过单次请求上限" in r.content
    assert "max_side" in r.content  # 明确告诉模型怎么补救
    assert asker.calls == []  # 关键：没有发请求


async def test_view_image_allows_normal_batch(tmp_path):
    asker = FakeVision()
    tool = ViewImageTool(VisionConfig(api_key="k", model="m"), asker=asker)
    paths = []
    for i in range(2):
        p = tmp_path / f"ok{i}.png"
        p.write_bytes(png_bytes(64, 64))
        paths.append(str(p))
    r = await tool.execute(json.dumps({"paths": paths, "question": "看"}))
    assert not r.is_error
    assert len(asker.calls[0][0]) == 2


# ── /image 命令 ──────────────────────────────────────────────────────────


def test_split_path_and_question_quoted():
    assert split_path_and_question('"C:\\a b\\c.png" 为什么报错') == (
        "C:\\a b\\c.png",
        "为什么报错",
    )


def test_split_path_and_question_with_space_in_path(tmp_path):
    folder = tmp_path / "my shots"
    folder.mkdir()
    img = folder / "bug.png"
    img.write_bytes(png_bytes())
    args = f"{img} 这个报错是什么"
    path, question = split_path_and_question(args)
    assert path == str(img)
    assert question == "这个报错是什么"


def test_split_path_and_question_missing_file():
    path, question = split_path_and_question("nowhere.png 是哪张图")
    assert path == "nowhere.png"
    assert question == "是哪张图"


async def test_handle_image_records_request(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(png_bytes())
    ui = NopUI()
    ui.set_command_args(f"{img} 图里有什么")
    await handle_image(ui)
    assert ui.viewed == [(str(img), "图里有什么")]


async def test_handle_image_without_args_prints_usage():
    ui = NopUI()
    await handle_image(ui)
    assert any("用法" in line for line in ui.printed)


# ── 音频 ─────────────────────────────────────────────────────────────────


def wav_bytes(seconds: float = 0.1) -> bytes:
    # 最小合法 WAV：44 字节头 + 一段静音 PCM。
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


def test_clock_formats():
    assert clock(0) == "00:00"
    assert clock(75) == "01:15"
    assert clock(3725) == "1:02:05"


def test_transcript_render_includes_timestamps():
    t = Transcript(
        [Segment(0, 3.5, "第一句"), Segment(3.5, 9.2, "第二句")], language="zh", model="whisper-1"
    )
    text = t.render()
    assert "[00:00 → 00:03] 第一句" in text
    assert "第二句" in text
    assert t.text == "第一句 第二句"


def test_prepare_accepts_wav(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(wav_bytes())
    path, mime = prepare(p)
    assert path == p
    assert mime == "audio/wav"


def test_prepare_rejects_text_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    with pytest.raises(AudioError, match="不支持的音频格式"):
        prepare(p)


def test_prepare_rejects_missing_file(tmp_path):
    with pytest.raises(AudioError, match="文件不存在"):
        prepare(tmp_path / "nope.wav")


def test_prepare_accepts_video_container(tmp_path):
    # 录屏这类视频也常被拿来转写：扩展名放行，音轨交给 ffmpeg 抽。
    p = tmp_path / "screen.mov"
    p.write_bytes(b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 64)
    path, mime = prepare(p)
    assert path == p
    assert mime.startswith("video/")


def test_prepare_rejects_unknown_video_suffix(tmp_path):
    p = tmp_path / "a.xyz"
    p.write_bytes(b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 64)
    with pytest.raises(AudioError, match="不支持的音频格式"):
        prepare(p)


def test_audio_config_ready():
    # local 后端不需要 key；openai 后端缺 key 就不可用。
    assert AudioConfig(backend="local").ready() is True
    assert AudioConfig(backend="openai", api_key="", model="whisper-1").ready() is False
    assert AudioConfig(backend="openai", api_key="k", model="whisper-1").ready() is True


def test_registry_registers_transcribe_only_when_configured():
    bare = new_default_registry()
    assert bare.get("transcribe_audio") is None

    reg = new_default_registry(audio=AudioConfig(api_key="k", model="whisper-1"))
    assert reg.count() == 9
    tool = reg.get("transcribe_audio")
    assert tool is not None
    assert reg.is_read_only("transcribe_audio") is True
    # 转写是慢工具，必须自带更长的超时
    assert tool.timeout >= 300


async def test_transcribe_tool_returns_timestamped_text(tmp_path):
    p = tmp_path / "note.wav"
    p.write_bytes(wav_bytes())
    calls: list[dict] = []

    async def fake(cfg, path, language="", prompt=""):
        calls.append({"path": path, "language": language, "prompt": prompt})
        return Transcript(
            [Segment(0, 4, "讨论重构"), Segment(4, 9, "决定先做媒体层")],
            language="zh",
            model="whisper-1",
        )

    tool = TranscribeAudioTool(
        AudioConfig(backend="openai", api_key="k", model="whisper-1"), transcriber=fake
    )
    r = await tool.execute(json.dumps({"path": str(p), "prompt": "lcode 重构"}))
    assert not r.is_error
    assert "讨论重构" in r.content
    assert "[00:00 → 00:04]" in r.content
    assert "note.wav" in r.content
    assert "asr: openai/whisper-1" in r.content
    assert calls[0]["prompt"] == "lcode 重构"


async def test_transcribe_tool_without_config_returns_hint(tmp_path):
    r = await TranscribeAudioTool(None).execute(json.dumps({"path": "a.wav"}))
    assert r.is_error
    assert "audio" in r.content


async def test_transcribe_tool_reports_silence(tmp_path):
    p = tmp_path / "silent.wav"
    p.write_bytes(wav_bytes())

    async def empty(cfg, path, language="", prompt=""):
        return Transcript([], language="zh", model="whisper-1")

    tool = TranscribeAudioTool(AudioConfig(api_key="k", model="m"), transcriber=empty)
    r = await tool.execute(json.dumps({"path": str(p)}))
    assert not r.is_error
    assert "没有识别出任何语音" in r.content


async def test_transcribe_tool_surfaces_audio_error(tmp_path):
    p = tmp_path / "note.wav"
    p.write_bytes(wav_bytes())

    async def boom(cfg, path, language="", prompt=""):
        raise AudioError("转写请求失败: 401")

    tool = TranscribeAudioTool(AudioConfig(api_key="k", model="m"), transcriber=boom)
    r = await tool.execute(json.dumps({"path": str(p)}))
    assert r.is_error
    assert "401" in r.content


async def test_handle_audio_records_request(tmp_path):
    p = tmp_path / "note.wav"
    p.write_bytes(wav_bytes())
    ui = NopUI()
    ui.set_command_args(f"{p} 关于重构的讨论")
    await handle_audio(ui)
    assert ui.transcribed == [(str(p), "关于重构的讨论")]


async def test_handle_audio_without_args_prints_usage():
    ui = NopUI()
    await handle_audio(ui)
    assert any("用法" in line for line in ui.printed)
    assert "/audio" in AUDIO_USAGE


# ── 真实请求体（不打网络，只检查发出去长什么样）──────────────────────────


async def test_ask_vision_openai_payload(monkeypatch, tmp_path):
    import openai

    from lcode import vision as vision_mod

    captured: list[dict] = []

    class _Msg:
        content = " 图里有三行报错 "

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        async def create(self, **kwargs):
            captured.append(kwargs)
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _FakeClient:
        def __init__(self, **kwargs):
            self.init = kwargs
            self.chat = _Chat()

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeClient)

    img = png_file(tmp_path)
    payload = load_image(str(img))
    cfg = VisionConfig(api_key="sk-v", model="qwen-vl-max", detail="low")
    answer = await vision_mod.ask_vision(cfg, [payload], "图里有什么", "low")

    assert answer == "图里有三行报错"
    body = captured[0]
    assert body["model"] == "qwen-vl-max"
    user_blocks = body["messages"][1]["content"]
    image_block = user_blocks[0]
    assert image_block["type"] == "image_url"
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")
    assert image_block["image_url"]["detail"] == "low"
    # 问题作为最后一段文字块
    assert user_blocks[-1] == {"type": "text", "text": "图里有什么"}


async def test_ask_vision_anthropic_payload(monkeypatch, tmp_path):
    import anthropic

    from lcode import vision as vision_mod

    captured: list[dict] = []

    class _Block:
        type = "text"
        text = " 第 2 行有错 "

    class _Msg:
        content = [_Block()]

    class _Messages:
        async def create(self, **kwargs):
            captured.append(kwargs)
            return _Msg()

    class _FakeClient:
        def __init__(self, **kwargs):
            self.messages = _Messages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeClient)

    img = png_file(tmp_path)
    payload = load_image(str(img))
    cfg = VisionConfig(protocol="anthropic", api_key="sk-a", model="claude-x")
    answer = await vision_mod.ask_vision(cfg, [payload], "哪里错了")

    assert answer == "第 2 行有错"
    blocks = captured[0]["messages"][0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["type"] == "base64"
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[-1]["text"] == "哪里错了"


async def test_transcribe_remote_payload(monkeypatch, tmp_path):
    import openai

    from lcode import audio as audio_mod

    captured: list[dict] = []
    audio_file = tmp_path / "note.wav"
    audio_file.write_bytes(wav_bytes())

    class _Transcriptions:
        async def create(self, **kwargs):
            captured.append(kwargs)
            return {
                "language": "zh",
                "segments": [
                    {"start": 0.0, "end": 2.5, "text": "第一段"},
                    {"start": 2.5, "end": 5.0, "text": "  "},
                    {"start": 5.0, "end": 7.0, "text": "第二段"},
                ],
            }

    class _Audio:
        transcriptions = _Transcriptions()

    class _FakeClient:
        def __init__(self, **kwargs):
            self.audio = _Audio()

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeClient)

    cfg = AudioConfig(backend="openai", api_key="sk-a", model="whisper-1", language="zh")
    transcript = await audio_mod.transcribe(cfg, audio_file, prompt="lcode 重构")

    assert transcript.language == "zh"
    assert [s.text for s in transcript.segments] == ["第一段", "第二段"]
    assert transcript.segments[0].start == 0.0
    kwargs = captured[0]
    assert kwargs["model"] == "whisper-1"
    assert kwargs["response_format"] == "verbose_json"
    assert kwargs["language"] == "zh"
    assert kwargs["prompt"] == "lcode 重构"
    # 上传三元组：(文件名, 文件对象, mime)
    assert kwargs["file"][0] == "note.wav"
    assert kwargs["file"][2] == "audio/wav"


async def test_transcribe_remote_falls_back_to_plain_text(monkeypatch, tmp_path):
    import openai

    from lcode import audio as audio_mod

    audio_file = tmp_path / "a.wav"
    audio_file.write_bytes(wav_bytes())

    class _Transcriptions:
        async def create(self, **kwargs):
            # 有些兼容实现不支持 verbose_json，只回 text
            return {"text": "整段话"}

    class _Audio:
        transcriptions = _Transcriptions()

    class _FakeClient:
        def __init__(self, **kwargs):
            self.audio = _Audio()

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeClient)

    cfg = AudioConfig(backend="openai", api_key="k", model="whisper-1")
    transcript = await audio_mod.transcribe(cfg, audio_file)
    assert transcript.text == "整段话"
    assert transcript.segments[0].end > transcript.segments[0].start


def png_file(tmp_path, width: int = 24, height: int = 12):
    path = tmp_path / "shot.png"
    path.write_bytes(png_bytes(width, height))
    return path


# ── DashScope 原生 ASR（实测过的几个坑）──────────────────────────────────


DASHSCOPE_OK = {
    "output": {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "annotations": [{"emotion": "neutral", "language": "zh", "type": "audio_info"}],
                    "content": [{"text": "这是一段语音测试，端口号是8714。"}],
                    "role": "assistant",
                },
            }
        ]
    },
    "usage": {"audio_tokens": 183, "seconds": 7, "total_tokens": 213},
    "request_id": "abc",
}


async def test_dashscope_parses_native_response(monkeypatch, tmp_path):
    from lcode import audio as audio_mod

    audio_file = tmp_path / "a.wav"
    audio_file.write_bytes(wav_bytes())
    captured: list[dict] = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return DASHSCOPE_OK

    async def fake_post(self, url, json=None, headers=None):
        captured.append({"url": url, "json": json, "headers": headers})
        return _Resp()

    monkeypatch.setattr("httpx2.AsyncClient.post", fake_post)

    cfg = AudioConfig(backend="dashscope", api_key="sk-a", model="qwen3-asr-flash", language="zh")
    t = await audio_mod.transcribe(cfg, audio_file)

    assert t.text == "这是一段语音测试，端口号是8714。"
    assert t.language == "zh"
    assert t.backend == "dashscope"
    assert t.duration == 7.0
    assert t.segments[0].start == 0.0
    assert t.segments[0].end == 7.0
    # 关键：走的是原生 multimodal 端点，不是 OpenAI 的 /audio/transcriptions（那个会 404）
    assert "multimodal-generation/generation" in captured[0]["url"]
    assert "/audio/transcriptions" not in captured[0]["url"]
    # 音频以 data URI 放在 input.messages[].content[].audio
    body = captured[0]["json"]
    audio_uri = body["input"]["messages"][0]["content"][0]["audio"]
    assert audio_uri.startswith("data:audio/wav;base64,")
    assert body["parameters"]["asr_options"]["language"] == "zh"


async def test_dashscope_itn_toggle(monkeypatch, tmp_path):
    from lcode import audio as audio_mod

    audio_file = tmp_path / "a.wav"
    audio_file.write_bytes(wav_bytes())
    captured: list[dict] = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return DASHSCOPE_OK

    async def fake_post(self, url, json=None, headers=None):
        captured.append(json)
        return _Resp()

    monkeypatch.setattr("httpx2.AsyncClient.post", fake_post)

    for itn in (True, False):
        cfg = AudioConfig(backend="dashscope", api_key="k", model="m", itn=itn)
        await audio_mod.transcribe(cfg, audio_file)
        assert captured[-1]["parameters"]["asr_options"]["enable_itn"] is itn


async def test_dashscope_reports_http_error(monkeypatch, tmp_path):
    from lcode import audio as audio_mod

    audio_file = tmp_path / "a.wav"
    audio_file.write_bytes(wav_bytes())

    class _Resp:
        status_code = 400
        text = '{"code":"InvalidParameter","message":"Multimodal file size is too large"}'

    async def fake_post(self, url, json=None, headers=None):
        return _Resp()

    monkeypatch.setattr("httpx2.AsyncClient.post", fake_post)

    cfg = AudioConfig(backend="dashscope", api_key="k", model="m")
    with pytest.raises(AudioError, match="file size is too large"):
        await audio_mod.transcribe(cfg, audio_file)


def test_dashscope_max_bytes_is_stricter_than_openai():
    from lcode.audio import DASHSCOPE_MAX_BYTES, max_bytes

    # 实测：DashScope 9.2MB 通过、11.6MB 拒绝；OpenAI 兼容端点普遍到 25MB。
    assert max_bytes("dashscope") < max_bytes("openai")
    assert DASHSCOPE_MAX_BYTES < 11 * 1024 * 1024


def test_chunk_budget_leaves_headroom():
    from lcode.audio import chunk_budget, max_bytes

    for backend in ("dashscope", "openai"):
        assert chunk_budget(backend) < max_bytes(backend)


def test_split_wav_uses_bytes_not_seconds(tmp_path):
    """切片必须按字节算：采样率不一定是 16k，按时长算会切出超限的片。"""
    from lcode.audio import _split_wav

    # 22.05kHz（SAPI 默认输出），10 秒 ≈ 441KB
    src = tmp_path / "hi.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * (22050 * 10))

    out = tmp_path / "parts"
    out.mkdir()
    budget = 100 * 1024  # 每个切片不允许超过 ~100KB
    seconds = _split_wav(src, out, budget)

    parts = sorted(out.glob("part*.wav"))
    assert len(parts) > 1
    for part in parts:
        assert part.stat().st_size <= budget + 64  # 允许头部几十字节
    # 返回的秒数要和实际字节预算对应（22050Hz × 2B = 44100 B/s）
    assert abs(seconds - budget / 44100) < 0.1


async def test_transcribe_splits_before_uploading_when_over_limit(monkeypatch, tmp_path):
    """超过上限的 wav 必须先切片再上传，不能原样发上去被 400。"""
    from lcode import audio as audio_mod

    src = tmp_path / "big.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        # 12MB：超过 dashscope 的 9MB 上限
        w.writeframes(b"\x00\x00" * (6 * 1024 * 1024))

    uploaded: list[int] = []

    async def fake_dashscope(cfg, p, mime, language, prompt, offset):
        size = p.stat().st_size
        uploaded.append(size)
        assert size <= audio_mod.DASHSCOPE_MAX_BYTES, f"单片 {size} 仍超限"
        return audio_mod.Transcript(
            [audio_mod.Segment(offset, offset + 10, f"片段{offset}")],
            language="zh",
            model=cfg.model,
            backend="dashscope",
            duration=10.0,
        )

    monkeypatch.setattr(audio_mod, "_dashscope", fake_dashscope)

    cfg = AudioConfig(backend="dashscope", api_key="k", model="m")
    t = await audio_mod.transcribe(cfg, src)

    assert t.parts == len(uploaded) > 1
    assert all(size <= audio_mod.DASHSCOPE_MAX_BYTES for size in uploaded)
    # 偏移量必须累加，否则所有片段都显示 [00:00 → ...]
    starts = [s.start for s in t.segments]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
