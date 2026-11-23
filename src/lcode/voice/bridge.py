"""语音与 Lcode 主体之间的桥：入队 → 催它跑一轮 → 等结果 → 取回文本。

为什么需要这一层：
Lcode 是终端 Agent，没有一个「一次问答、直接回 stdout」的模式。
但它有一条现成的异步通道——``resident`` 队列：
任务落盘成 ``queue.json`` 的一条，``--resident-once`` 跑一轮，
结果写成 ``.lcode/resident/results/<job>.md``。

语音助手正好需要这种解耦：嘴上说一句话（几秒），Agent 干活（几十秒），
结果落盘后再说回来。中间不需要一直占着麦克风。

## 接入核心代码后改了什么

原先这一层靠 ``shutil.which("lcode")`` 找到可执行文件、再起子进程去入队。
那个设计有个脆的假设：**PATH 上必须有 ``lcode``**。一旦用户改了安装方式
（uv tool → pip -e，或者干脆把那个 shim 删了），语音就在「找不到 lcode 命令」
这里断掉，而且是在唤醒成功之后才报错——体验最差的时机。

现在：
- **入队直接调用** ``TaskQueue.enqueue()``——同进程，零子进程，零 PATH 依赖。
- **催一轮**才起子进程，用的是 ``sys.executable -m lcode``：
  正在运行的这份解释器里一定有 lcode，不需要去 PATH 里猜。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

QUEUE_REL = Path(".lcode") / "resident" / "queue.json"
_JOB_LINE = re.compile(r"(job_[0-9a-fA-F]+)")
_FRONT_MATTER = re.compile(r"\A---\n[\s\S]*?\n---\n")
_RESULT_HEAD = re.compile(r"^##\s*结果\s*$", re.M)

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class BridgeError(Exception):
    """队列读不了、任务失败、子进程起不来。"""


def queue_path(project: str | Path) -> Path:
    return Path(project) / QUEUE_REL


def resident_dir(project: str | Path) -> Path:
    return Path(project) / ".lcode" / "resident"


def enqueue(
    text: str,
    project: str | Path,
    origin: str = "voice",
    thread: str = "",
    say: str = "",
) -> str:
    """把一句话丢进队列，返回 job id。

    ``origin`` 默认 ``voice``：daemon 看到这个来源会跳过 email 通知通道——
    答案已经当场念出来了，再往邮箱发一遍纯属打扰。

    ``thread`` 是话题键：同一段连续对话共用同一个值，daemon 靠它把上一轮
    问答拼进上下文（``Daemon._prompt_with_thread``）。留空就是单轮问答，
    模型看不到之前说过什么。

    ``say`` 是用户原话。``text`` 往往带着 ``VOICE_HINT`` 那段提示词，
    拼上下文时用 ``say`` 才不会每轮都重复一遍提示词。
    """
    from lcode.resident.queue import ORIGIN_VOICE, TaskQueue

    body = (text or "").strip()
    if not body:
        raise BridgeError("要入队的内容是空的")
    try:
        task = TaskQueue(resident_dir(project)).enqueue(
            body, origin=origin or ORIGIN_VOICE, thread=thread, say=say
        )
    except (OSError, ValueError) as exc:
        raise BridgeError(f"入队失败：{exc}") from exc
    return task.id


def trigger_run_once(project: str | Path) -> subprocess.Popen | None:
    """在后台催一轮。

    已经有 daemon 在跑时会拿不到锁、立刻退出——所以这里不需要先探测，
    多叫一次是无害的；能跑起来就快，跑不起来就等原来那个 daemon。

    用 ``sys.executable -m lcode``：这份解释器里一定装着 lcode，
    不必依赖 PATH 上那个 shim（那正是旧版最容易断的地方）。
    """
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "lcode", "--resident-once", "--interval", "0"],
            cwd=str(project),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001 - 催不动就靠原有 daemon
        return None


def read_task(project: str | Path, job_id: str) -> dict:
    """从 queue.json 里找这条任务；找不到返回空 dict。"""
    path = queue_path(project)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id") == job_id:
                return item
    return {}


def result_text(task: dict) -> str:
    """从结果文件里取出「## 结果」这一段；取不到就用 summary。

    和 ``TaskQueue.result_text`` 的区别：这里刻意不截断。
    念的时候才按 ``speak_limit`` 截——截在写盘那一步会让「终端里看全文」
    也只剩八百字。
    """
    file = task.get("result_file") or ""
    if file:
        try:
            body = Path(file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            body = ""
        if body:
            body = _FRONT_MATTER.sub("", body)
            head = _RESULT_HEAD.search(body)
            if head:
                body = body[head.end() :]
            return body.strip()
    return (task.get("summary") or "").strip()


def wait_for(
    project: str | Path,
    job_id: str,
    timeout: float = 180.0,
    on_tick=None,
) -> tuple[str, str]:
    """轮询到任务离开 pending/running。返回 (状态, 文本)。

    状态是 ``done`` / ``failed`` / ``timeout`` 之一。
    用轮询而不是等子进程：daemon 可能是别的进程在跑，也可能中途重启。
    """
    deadline = time.monotonic() + timeout
    last_status = ""
    while time.monotonic() < deadline:
        task = read_task(project, job_id)
        status = task.get("status", "")
        if status and status != last_status:
            last_status = status
            if on_tick:
                on_tick(status)
        if status == "done":
            return "done", result_text(task)
        if status == "failed":
            return "failed", (task.get("error") or "任务失败，没有留下原因").strip()
        time.sleep(0.6)
    return "timeout", ""


__all__ = [
    "BridgeError",
    "enqueue",
    "queue_path",
    "read_task",
    "resident_dir",
    "result_text",
    "trigger_run_once",
    "wait_for",
]
