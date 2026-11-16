"""Delete expired session directories (F25/F26, N3)."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from mewcode.compact.state import parse_session_time

logger = logging.getLogger(__name__)


def clean_expired(
    sessions_dir: str,
    max_age: timedelta,
    now: datetime | None = None,
) -> list[str]:
    # 只处理 ID 能解析出新格式时间戳的目录；旧格式目录一律跳过（避免误删）。
    now = now or datetime.now()
    root = Path(sessions_dir)
    removed: list[str] = []
    if not root.is_dir():
        return removed

    try:
        children = list(root.iterdir())
    except OSError:
        logger.warning("扫描会话目录失败: %s", root)
        return removed

    for child in children:
        if not child.is_dir():
            continue
        try:
            created = parse_session_time(child.name)
        except ValueError:
            continue
        if now - created <= max_age:
            continue
        try:
            shutil.rmtree(child)
            removed.append(child.name)
        except OSError:
            # 单个目录失败不影响其他目录。
            logger.warning("清理过期会话失败: %s", child, exc_info=True)
    return removed
