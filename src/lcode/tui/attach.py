"""Pending attachments: pick media files, classify them, show what's queued.

终端拿不到 Ctrl+V 的图片（Textual 只收文本，Windows Terminal 的图片粘贴
根本不进应用），所以需要一个显式的入口：一个按钮 → 系统文件对话框。

对话框用子进程调各平台原生选择器，而不是 tkinter：
tkinter 在 Textual 的事件循环里会阻塞主线程，放线程里又不稳。
子进程 + asyncio.to_thread 干净得多，而且和 notify.py 用 PowerShell 的路子一致。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text

from lcode.tool.media import MediaInfo, human_size, probe

# 附件类型 → 展示用的短标记
KIND_LABELS = {
    "image": "图",
    "audio": "音",
    "video": "视频",
    "pdf": "PDF",
    "office": "文档",
    "archive": "压缩",
    "text": "文本",
    "binary": "二进制",
}

# 不能被 view_image / transcribe_audio 处理、但用户可能想附加的类型
UNSUPPORTED_HINT = "只能附加图片、音频、视频和文本文件"

# 对话框里的类型过滤（Windows 语法）
_WIN_FILTER = (
    "媒体文件|*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp"
    ";*.mp3;*.wav;*.m4a;*.flac;*.ogg;*.aac"
    ";*.mp4;*.mov;*.mkv;*.webm;*.avi"
    "|图片|*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp"
    "|音频|*.mp3;*.wav;*.m4a;*.flac;*.ogg;*.aac"
    "|视频|*.mp4;*.mov;*.mkv;*.webm;*.avi"
    "|所有文件|*.*"
)

# 一次最多附加几个：图片受 view_image 的 6 张上限约束，音频越多越慢。
MAX_ATTACHMENTS = 6


class AttachError(Exception):
    """Raised when the picker cannot be shown or a file cannot be read."""


@dataclass(frozen=True)
class Attachment:
    path: str
    kind: str
    size: int = 0
    note: str = ""

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def label(self) -> str:
        return f"{self.name}({KIND_LABELS.get(self.kind, self.kind)})"

    @property
    def supported(self) -> bool:
        # text 也允许：用户可能想附一个日志/配置让模型读。
        return self.kind in ("image", "audio", "video", "text")


def classify(path: str) -> Attachment:
    """Turn a path into an Attachment, raising AttachError if it can't be read."""
    p = Path(path)
    if not p.is_file():
        raise AttachError(f"文件不存在: {path}")
    try:
        info: MediaInfo = probe(p)
    except OSError as exc:
        raise AttachError(f"读取失败: {path}（{exc}）") from exc
    kind = info.kind
    note = ""
    if kind == "text":
        note = f"{human_size(info.size)}"
    elif info.width and info.height:
        note = f"{info.width}x{info.height} {human_size(info.size)}"
    else:
        note = f"{info.mime} {human_size(info.size)}"
    return Attachment(path=str(p), kind=kind, size=info.size, note=note)


def dedupe(items: list[Attachment]) -> list[Attachment]:
    # 同一个文件拖两次不该算两个附件。
    seen: set[str] = set()
    out: list[Attachment] = []
    for item in items:
        key = os.path.normcase(os.path.abspath(item.path))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def render_chips(items: list[Attachment]) -> Text:
    """One-line summary of the pending queue (empty queue → empty Text)."""
    if not items:
        return Text()
    text = Text()
    text.append("📎 ", style="bold")
    for index, item in enumerate(items):
        if index:
            text.append("  ")
        style = "cyan" if item.supported else "yellow"
        text.append(item.label, style=style)
        if index == 0:
            text.append(f"  ({items[0].note})", style="dim")
    if len(items) > 1:
        text.append(f"  共 {len(items)} 个", style="dim")
    text.append("   [Esc 清空]", style="dim")
    return text


def attach_summary(items: list[Attachment]) -> str:
    return "、".join(item.label for item in items)


# ── 各平台的文件选择器 ────────────────────────────────────────────────────


def _decode_console(data: bytes) -> str:
    """解码子进程 stdout。

    -EncodedCommand 只保证「传进去的脚本」不会被 ANSI 代码页折损；
    「出来的路径」仍按控制台代码页编码（中文 Windows = cp936），直接
    utf-8 解会把中文路径变成乱码，随后 classify() 只能报「文件不存在」。
    脚本里已经把 OutputEncoding 钉成 UTF-8，这里再兜一层：
    万一某些宿主忽略了那行设置，就退回 Windows 控制台代码页。
    """
    for encoding in ("utf-8", "oem"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _windows_picker(title: str) -> str:
    # OpenFileDialog 用 Multiselect；路径逐行输出到 stdout。
    # -EncodedCommand（UTF-16LE base64）避免中文路径被 ANSI 代码页折损，
    # 这和 notify.py 里 Toast 的处理是同一个原因。
    # 头两行把「输出」也钉成 UTF-8：重定向到管道时 PowerShell 用
    # [Console]::OutputEncoding 编码，默认是 OEM 代码页。
    script = f"""
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = [Console]::OutputEncoding
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = '{title}'
$d.Multiselect = $true
$d.Filter = '{_WIN_FILTER}'
$d.RestoreDirectory = $true
if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
  foreach ($f in $d.FileNames) {{ Write-Output $f }}
}}
"""
    import base64

    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        timeout=300,
        check=False,
    )
    return _decode_console(result.stdout)


def _macos_picker(title: str) -> str:
    script = (
        'set theFiles to choose file with prompt "' + title + '" '
        "with multiple selections allowed\n"
        "set out to \"\"\n"
        "repeat with f in theFiles\n"
        "  set out to out & (POSIX path of f) & linefeed\n"
        "end repeat\n"
        "return out"
    )
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, timeout=300, check=False
    )
    return result.stdout.decode("utf-8", errors="replace")


def _linux_picker(title: str) -> str:
    commands = [
        ["zenity", "--file-selection", "--multiple", f"--title={title}",
         "--separator=\n"],
        ["kdialog", "--getopenfilename", os.path.expanduser("~"), "*", "--multiple",
         "--separate-output"],
    ]
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        result = subprocess.run(command, capture_output=True, timeout=300, check=False)
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")
    raise AttachError(
        "找不到文件选择器：请安装 zenity 或 kdialog，或直接用 /image <路径> 指定文件。"
    )


def pick_files_blocking(title: str = "选择要附加的文件") -> list[str]:
    """Show the native file picker and return selected paths (blocking)."""
    if sys.platform == "win32":
        raw = _windows_picker(title)
    elif sys.platform == "darwin":
        raw = _macos_picker(title)
    else:
        raw = _linux_picker(title)
    return [line.strip() for line in raw.splitlines() if line.strip()]


async def pick_files(title: str = "选择要附加的文件") -> list[str]:
    # 选择器是阻塞的子进程：扔到线程里，别冻住 TUI。
    return await asyncio.to_thread(pick_files_blocking, title)


__all__ = [
    "KIND_LABELS",
    "MAX_ATTACHMENTS",
    "UNSUPPORTED_HINT",
    "AttachError",
    "Attachment",
    "attach_summary",
    "classify",
    "dedupe",
    "pick_files",
    "pick_files_blocking",
    "render_chips",
]
