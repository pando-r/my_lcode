"""常驻模式：无头轮询任务队列，让 lcode 在没有 TUI 的情况下也能干活。"""

from __future__ import annotations

from lcode.resident.daemon import Daemon, DaemonOptions
from lcode.resident.queue import DONE, FAILED, PENDING, RUNNING, Task, TaskQueue
from lcode.resident.runner import RunnerOptions, TaskRunner

__all__ = [
    "DONE",
    "FAILED",
    "PENDING",
    "RUNNING",
    "Daemon",
    "DaemonOptions",
    "RunnerOptions",
    "Task",
    "TaskQueue",
    "TaskRunner",
]
