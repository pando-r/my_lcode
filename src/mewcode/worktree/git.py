"""Git subprocess helpers plus filesystem-only worktree inspection."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    return env


async def _run_git(work_dir: str, *args: str) -> str:
    # 统一非交互环境；stdin 关闭，避免 hook/认证提示挂起事件循环。
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=work_dir,
        env=_git_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        message = stderr_b.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"git {' '.join(args)} 失败（{proc.returncode}）")
    return stdout_b.decode("utf-8", errors="replace").rstrip("\r\n")


async def _has_worktree_changes(wt_path: str, base_commit: str) -> bool:
    # fail-closed：任何 git 错误都按"有变更"处理，宁可保留目录。
    try:
        status = await _run_git(wt_path, "status", "--porcelain")
        if status.strip():
            return True
        count = await _run_git(wt_path, "rev-list", "--count", f"{base_commit}..HEAD")
        return int(count.strip() or "0") > 0
    except Exception:  # noqa: BLE001 - fail closed
        return True


async def _has_unpushed_commits(wt_path: str) -> bool:
    try:
        output = await _run_git(wt_path, "rev-list", "--max-count=1", "HEAD", "--not", "--remotes")
        return bool(output.strip())
    except Exception:  # noqa: BLE001
        return True


def _resolve_head_sha_from_fs(wt_path: str) -> str | None:
    # 快速恢复：只读 .git 指针/HEAD/refs，不启动任何 git 子进程。
    try:
        dot_git = Path(wt_path) / ".git"
        if dot_git.is_dir():
            gitdir = dot_git
        elif dot_git.is_file():
            text = dot_git.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = Path(text.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = Path(wt_path) / gitdir
        else:
            return None

        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head or None
        ref = head.split(":", 1)[1].strip()

        common = gitdir
        common_file = gitdir / "commondir"
        if common_file.is_file():
            common = (gitdir / common_file.read_text(encoding="utf-8").strip()).resolve()

        ref_file = common / ref
        if ref_file.is_file():
            sha = ref_file.read_text(encoding="utf-8").strip()
            return sha or None

        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
        return None
    except OSError:
        return None


def warn(message: str) -> None:
    print(f"worktree: {message}", file=sys.stderr)


__all__ = [
    "_git_env",
    "_has_unpushed_commits",
    "_has_worktree_changes",
    "_resolve_head_sha_from_fs",
    "_run_git",
    "warn",
]
