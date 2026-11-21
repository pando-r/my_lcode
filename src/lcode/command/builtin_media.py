"""Local media commands: /image and /audio — ask a media model, inject the answer."""

from __future__ import annotations

from pathlib import Path

from lcode.command.ui import UI

IMAGE_USAGE = (
    "用法：/image <图片路径> [问题]\n"
    "例：/image C:\\Users\\K\\shot.png 这个报错是什么原因"
)

# 没给问题时给一个「先摸清情况」的默认问题：有文字就逐字给出，
# 这样主模型拿到的是可引用的原文，而不是一句「一张截图」。
DEFAULT_QUESTION = "这张图里有什么？如果包含文字（报错、代码、界面文案），请逐字给出。"

AUDIO_USAGE = (
    "用法：/audio <音频路径> [提示]\n"
    "例：/audio C:\\Users\\K\\备忘.m4a 这是关于 lcode 重构的讨论"
)


def split_path_and_question(text: str) -> tuple[str, str]:
    """Split "/image" args into (path, rest), tolerating spaces in the path."""
    text = text.strip()
    if not text:
        return "", ""

    # 显式引号最可靠：/image "C:\\My Pictures\\a.png" 为什么报错
    if text[0] in "\"'":
        quote = text[0]
        end = text.find(quote, 1)
        if end > 0:
            return text[1:end], text[end + 1 :].strip()

    # 没有引号时，从最短前缀开始试，取第一个真实存在的文件
    # （Windows 截图常放在 "C:\\Users\\K\\My Pictures" 这类带空格目录下）。
    for offset, char in enumerate(text):
        if not char.isspace():
            continue
        candidate = text[:offset]
        if Path(candidate).expanduser().is_file():
            return candidate, text[offset:].strip()

    if Path(text).expanduser().is_file():
        return text, ""

    head, _, tail = text.partition(" ")
    return head, tail.strip()


async def handle_image(ui: UI) -> None:
    args = ui.command_args().strip()
    if not args:
        ui.println(IMAGE_USAGE)
        return
    if not ui.idle():
        ui.error("请等待当前任务完成")
        return
    path, question = split_path_and_question(args)
    if not path:
        ui.println(IMAGE_USAGE)
        return
    ui.view_image(path, question or DEFAULT_QUESTION)


async def handle_audio(ui: UI) -> None:
    args = ui.command_args().strip()
    if not args:
        ui.println(AUDIO_USAGE)
        return
    if not ui.idle():
        ui.error("请等待当前任务完成")
        return
    path, prompt = split_path_and_question(args)
    if not path:
        ui.println(AUDIO_USAGE)
        return
    ui.transcribe_audio(path, prompt)


__all__ = [
    "AUDIO_USAGE",
    "DEFAULT_QUESTION",
    "IMAGE_USAGE",
    "handle_audio",
    "handle_image",
    "split_path_and_question",
]
