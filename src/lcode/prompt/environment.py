"""Runtime environment information gathering."""

# 运行时环境快照模块
from __future__ import annotations

import datetime
import os
import platform
import subprocess
from dataclasses import dataclass


@dataclass
class Environment:
    working_dir: str  # 当前工作目录
    platform: str  # 操作系统平台
    date: str  # 当前日期（含星期，见 _today_label）
    git_status: str  # Git 状态信息
    version: str  # 版本号
    model: str  # 模型名称
    time: str = ""  # 当前时间 HH:MM
    time_of_day: str = ""  # 时段（早上/上午/中午/下午/晚上/深夜），见 _time_of_day

    # 采集运行时环境信息
    def render(self) -> str:
        # Render the environment info block (empty fields omitted).
        lines: list[str] = []
        if self.working_dir:
            lines.append(f"Working Directory: {self.working_dir}")
        if self.platform:
            lines.append(f"Platform: {self.platform}")
        if self.date:
            lines.append(f"Date: {self.date}")
        if self.time:
            # 时段直接跟在时间后面，模型不必自己从 14:55 推算「这是下午」。
            suffix = f" ({self.time_of_day})" if self.time_of_day else ""
            lines.append(f"Time: {self.time}{suffix}")
        if self.git_status:
            lines.append(f"Git Status: {self.git_status}")
        if self.version:
            lines.append(f"Version: {self.version}")
        if self.model:
            lines.append(f"Model: {self.model}")
        return "\n".join(lines)


# 星期必须显式给出来：「今天适合做什么」高度依赖星期几（周六 vs 周一给的
# 建议完全不同），而只有 ISO 日期时模型得自己算，经常算错或干脆不问。
_WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WEEKDAYS_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 时段边界（左闭右开，小时为单位）。为什么要在这里算而不是交给模型：
# 「早上好/下午好」看着简单，但 11:58、12:01 这种边界上不同模型给的答案不同，
# 同一个模型换个温度也可能抖；而问候语是简报第一句，说错最刺眼。
# 同一时段在不同人嘴里也可能叫法不同，所以边界集中在这张表里，一处可改。
_TIME_OF_DAY: tuple[tuple[int, str], ...] = (
    (5, "早上"),
    (9, "上午"),
    (12, "中午"),
    (14, "下午"),
    (18, "晚上"),
    (23, "深夜"),
)


def _time_of_day(now: datetime.datetime) -> str:
    """把时刻映射成中文时段；23:00–04:59 是「深夜」（跨零点，单独处理）。"""
    hour = now.hour
    label = _TIME_OF_DAY[-1][1]  # 0–4 点落在最后一档
    for start, name in _TIME_OF_DAY:
        if hour >= start:
            label = name
    return label


def _today_label(now: datetime.datetime) -> str:
    index = now.weekday()
    return f"{now.date().isoformat()} ({_WEEKDAYS_EN[index]}, {_WEEKDAYS_ZH[index]})"


def gather_environment(version: str, model: str) -> Environment:
    # Collect environment info; each field degrades to empty on failure.
    try:
        working_dir = os.getcwd()
    except OSError:
        working_dir = ""

    now = datetime.datetime.now()
    return Environment(
        working_dir=working_dir,
        platform=platform.system().lower(),
        date=_today_label(now),
        git_status=_git_status(),
        version=version,
        model=model,
        time=now.strftime("%H:%M"),
        time_of_day=_time_of_day(now),
    )


def _git_status() -> str:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    out = proc.stdout.strip()
    if not out:
        return "clean"
    lines = out.splitlines()
    if len(lines) > 5:
        return f"{len(lines)} files changed"
    return "; ".join(lines[:5])
