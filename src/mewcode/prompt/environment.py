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
    date: str  # 当前日期
    git_status: str  # Git 状态信息
    version: str  # 版本号
    model: str  # 模型名称

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
        if self.git_status:
            lines.append(f"Git Status: {self.git_status}")
        if self.version:
            lines.append(f"Version: {self.version}")
        if self.model:
            lines.append(f"Model: {self.model}")
        return "\n".join(lines)


def gather_environment(version: str, model: str) -> Environment:
    # Collect environment info; each field degrades to empty on failure.
    try:
        working_dir = os.getcwd()
    except OSError:
        working_dir = ""

    return Environment(
        working_dir=working_dir,
        platform=platform.system().lower(),
        date=datetime.date.today().isoformat(),
        git_status=_git_status(),
        version=version,
        model=model,
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
