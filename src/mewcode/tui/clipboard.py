"""Best-effort writes to the OS clipboard.

Textual already emits OSC 52 when the app copies text, which modern terminals
(and SSH sessions) honour, but some terminals silently ignore it. These helpers
write the clipboard directly as a fallback. Every call is best-effort: a
failure only means "the terminal has to handle OSC 52", never a crash.
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def set_clipboard(text: str) -> bool:
    """Copy `text` to the OS clipboard. Returns True when a writer succeeded."""
    try:
        if sys.platform == "win32":
            return _set_windows(text)
        if sys.platform == "darwin":
            return _pipe(["pbcopy"], text)
        for command in (
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ):
            if shutil.which(command[0]):
                return _pipe(command, text)
    except Exception:  # noqa: BLE001 - clipboard is a nicety, never fatal
        return False
    return False


def _pipe(command: list[str], text: str) -> bool:
    completed = subprocess.run(
        command,
        input=text.encode("utf-8"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=2,
        check=False,
    )
    return completed.returncode == 0


def _set_windows(text: str) -> bool:
    # ctypes 直写 CF_UNICODETEXT：不经过控制台代码页，中文/emoji 都不会被折损。
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE

    data = text.encode("utf-16-le") + b"\x00\x00"
    if not user32.OpenClipboard(None):
        return False
    handle = None
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        handle = None  # 所有权已交给剪贴板，不能再 GlobalFree
        return True
    finally:
        user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


__all__ = ["set_clipboard"]
