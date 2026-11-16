"""Path sandbox: resolve symlinks, then prefix-check against project root (N2)."""

# 路径沙箱——一个用于限制命令执行路径的机制。
from __future__ import annotations

import os
from pathlib import Path


# 展开目录路径
def resolve_root(root: str) -> str:
    return str(Path(root).expanduser().resolve(strict=True))


def eval_symlinks_or_ancestor(abs_path: str) -> str:
    # Resolve symlinks; for non-existent targets, fall back to nearest existing ancestor.
    p = Path(abs_path)
    if p.is_symlink() or p.exists():
        return str(p.resolve(strict=True))
    # 收集还不存在的部分路径
    suffix: list[str] = []
    cur = p
    # 遍历路径，直到找到存在的目录
    while not cur.exists():
        suffix.append(cur.name)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    base = str(cur.resolve(strict=True))
    for name in reversed(suffix):
        base = os.path.join(base, name)
    return base


def sandbox_ok(root: str, path: str) -> bool:
    if not path:
        return True
    p = Path(path)
    if not p.is_absolute():
        p = Path(root) / p
    resolved = eval_symlinks_or_ancestor(str(p))
    if resolved == root or resolved.startswith(root + os.sep):
        return True
    # N9：允许系统临时目录作为中转；/private/tmp 是 macOS 真实路径。
    for tmp_root in (os.path.realpath("/tmp"), "/private/tmp"):
        if resolved == tmp_root or resolved.startswith(tmp_root + os.sep):
            return True
    return False
