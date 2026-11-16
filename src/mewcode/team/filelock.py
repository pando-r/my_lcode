"""Cross-process file lock based on O_CREAT|O_EXCL with stale detection."""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import time
from collections.abc import AsyncIterator

LOCK_MAX_RETRIES = 10
LOCK_STALE_AFTER = 10.0
LOCK_BACKOFF_MIN = 0.005
LOCK_BACKOFF_MAX = 0.1


@contextlib.asynccontextmanager
async def acquire(lock_path: str) -> AsyncIterator[None]:
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    acquired = False
    for _ in range(LOCK_MAX_RETRIES):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_path)
            except OSError:
                age = LOCK_STALE_AFTER + 1
            if age > LOCK_STALE_AFTER:
                with contextlib.suppress(OSError):
                    os.unlink(lock_path)
            await asyncio.sleep(random.uniform(LOCK_BACKOFF_MIN, LOCK_BACKOFF_MAX))
            continue
        os.close(fd)
        acquired = True
        break
    if not acquired:
        raise TimeoutError(f"无法获取文件锁: {lock_path}")
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(lock_path)


__all__ = [
    "LOCK_BACKOFF_MAX",
    "LOCK_BACKOFF_MIN",
    "LOCK_MAX_RETRIES",
    "LOCK_STALE_AFTER",
    "acquire",
]
