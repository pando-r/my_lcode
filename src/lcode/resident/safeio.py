"""常驻模式的输出兜底。

pythonw.exe（计划任务用）可以完全没有 stdout/stderr；某些终端还是 GBK 编码。
两种情况下 ``print(..., file=sys.stderr)`` 都会抛异常，而常驻进程不该因为
「写不出一行提示」就崩掉，所以统一走这里。
"""

from __future__ import annotations

import sys


def warn(message: str) -> None:
    """尽力写到 stderr；写不出去就静默放弃（日志文件另有记录）。"""
    stream = sys.stderr
    if stream is None:
        return
    try:
        print(message, file=stream)
    except (UnicodeEncodeError, ValueError, OSError):
        # GBK 终端里出现 emoji、或流已被关闭：提示不重要，别让它杀死进程。
        pass


__all__ = ["warn"]
