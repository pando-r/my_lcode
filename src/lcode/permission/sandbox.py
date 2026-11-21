"""Path sandbox: resolve symlinks, then prefix-check against project root (N2)."""

# 路径沙箱——一个用于限制命令执行路径的机制。
from __future__ import annotations

import os
import tempfile
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


def _same_or_under(path: str, parent: str) -> bool:
    # Windows 路径大小写不敏感（C:\Temp 与 c:\temp 是同一处），
    # 而 posix 下必须保持敏感，所以只在这里做 normcase。
    path_n = os.path.normcase(os.path.normpath(path))
    parent_n = os.path.normcase(os.path.normpath(parent))
    return path_n == parent_n or path_n.startswith(parent_n + os.sep)


def _direct_child(path: str, parent: str) -> bool:
    path_n = os.path.normcase(os.path.normpath(path))
    parent_n = os.path.normcase(os.path.normpath(parent))
    return os.path.dirname(path_n) == parent_n


def temp_roots() -> list[str]:
    """N9：允许系统临时目录作为中转。

    POSIX 上是 /tmp（macOS 真实路径是 /private/tmp）；Windows 上没有 /tmp，
    真正的中转目录是 %TEMP%。之前写死 /tmp 会让这条白名单在 Windows 上形同虚设。
    """
    roots: list[str] = []
    try:
        system_tmp = os.path.realpath(tempfile.gettempdir())
    except OSError:  # pragma: no cover - 环境异常时保持空列表
        system_tmp = ""
    if system_tmp:
        roots.append(system_tmp)
    if os.name != "nt":
        roots.append(os.path.realpath("/tmp"))
        roots.append("/private/tmp")
    # 去重并保持顺序，避免同一目录被比较两次。
    seen: set[str] = set()
    unique: list[str] = []
    for root in roots:
        key = os.path.normcase(os.path.normpath(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def sandbox_ok(root: str, path: str) -> bool:
    if not path:
        return True
    p = Path(path)
    if not p.is_absolute():
        p = Path(root) / p
    resolved = eval_symlinks_or_ancestor(str(p))
    if _same_or_under(resolved, root):
        return True
    # N9 临时目录只放行「直接子项」：中转文件是单层的，
    # 若放行整个子树，项目外的 ../兄弟目录会顺着白名单逃出去。
    for tmp_root in temp_roots():
        if _direct_child(resolved, tmp_root):
            return True
    return False
