"""常驻任务队列：磁盘上的 JSON，原子写入，可跨进程重启存活。

设计取舍：
- 状态全落盘（``.lcode/resident/queue.json``），所以「每天跑一轮」不再依赖进程一直活着。
- 写入走「临时文件 + os.replace」，断电/崩溃不会留下半个 JSON。
- 结果正文不进队列，单独存 ``results/<id>.md``；队列里只留摘要，避免文件无限膨胀。
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from lcode.resident.safeio import warn

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

QUEUE_NAME = "queue.json"
RESULTS_DIR = "results"

ORIGIN_CLI = "cli"
ORIGIN_MAIL = "mail"
# 语音提问：结果会被念出来，用户已经在跟前听着了，再发一封邮件纯属打扰。
ORIGIN_VOICE = "voice"

SUMMARY_LIMIT = 280


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_task_id() -> str:
    return f"job_{secrets.token_hex(4)}"


@dataclass
class Task:
    # 一条常驻任务。``prompt`` 是给 Agent 的完整指令，其余都是状态簿记。
    id: str
    prompt: str
    status: str = PENDING
    created: str = field(default_factory=_now)
    started: str = ""
    finished: str = ""
    result_file: str = ""
    summary: str = ""
    error: str = ""
    # 来源标记："cli"（--enqueue）或 "mail"（收件箱）。邮件来源要额外受只读限制。
    origin: str = ORIGIN_CLI
    # 回信地址：非空表示跑完要把结果发回这个地址。
    reply_to: str = ""
    # 原邮件主题与 Message-ID，用于回信时保持同一会话线程。
    subject: str = ""
    message_id: str = ""
    # 话题键（同主题的追问共用）。回信追问时要靠它把上一轮问答带进上下文。
    thread: str = ""
    # 用户**原话**。``prompt`` 是给 Agent 的完整指令，可能带着一长串前缀
    # （语音那 45 字的「【语音提问】…不要用列表」）。拼上下文时必须用原话，
    # 否则每一轮都会把提示词再塞一遍：3 轮就是 135 字纯噪音，
    # 而且模型会反复看到「不要用列表」这类指令，反而更容易跑偏。
    # 空表示没单独记录（旧队列 / 命令行入队），此时回退到 prompt。
    say: str = ""

    def __post_init__(self) -> None:
        # 手写 JSON 时容易漏 id，补一个，省得队列里出现匿名任务。
        if not self.id:
            self.id = new_task_id()


class TaskQueue:
    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._path = self._dir / QUEUE_NAME

    # ── paths ────────────────────────────────────────────────────────────

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def path(self) -> Path:
        return self._path

    def results_dir(self) -> Path:
        return self._dir / RESULTS_DIR

    def result_path(self, task_id: str) -> Path:
        return self.results_dir() / f"{task_id}.md"

    # ── read / write ─────────────────────────────────────────────────────

    def load(self) -> list[Task]:
        # 任何损坏都降级为空队列，并把坏文件改名留证，绝不静默丢用户数据。
        if not self._path.is_file():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self._quarantine(exc)
            return []
        if not isinstance(raw, list):
            self._quarantine(ValueError("queue.json 顶层不是数组"))
            return []
        tasks: list[Task] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            prompt = item.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                continue
            status = item.get("status")
            tasks.append(
                Task(
                    id=str(item.get("id") or ""),
                    prompt=prompt,
                    status=status if isinstance(status, str) and status else PENDING,
                    created=str(item.get("created") or _now()),
                    started=str(item.get("started") or ""),
                    finished=str(item.get("finished") or ""),
                    result_file=str(item.get("result_file") or ""),
                    summary=str(item.get("summary") or ""),
                    error=str(item.get("error") or ""),
                    origin=str(item.get("origin") or ORIGIN_CLI),
                    reply_to=str(item.get("reply_to") or ""),
                    subject=str(item.get("subject") or ""),
                    message_id=str(item.get("message_id") or ""),
                    thread=str(item.get("thread") or ""),
                    say=str(item.get("say") or ""),
                )
            )
        return tasks

    def save(self, tasks: list[Task]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [asdict(task) for task in tasks], ensure_ascii=False, indent=2, sort_keys=False
        )
        # 先写同目录临时文件再 replace：同分区内 replace 是原子的。
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, self._path)

    def _quarantine(self, exc: Exception) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self._path.with_suffix(f".json.corrupt-{stamp}")
        try:
            os.replace(self._path, target)
            where = f"，已备份为 {target.name}"
        except OSError:
            where = ""
        warn(f"[resident] warn: 队列文件损坏（{exc}）{where}")

    # ── operations ───────────────────────────────────────────────────────

    def enqueue(
        self,
        prompt: str,
        origin: str = ORIGIN_CLI,
        reply_to: str = "",
        subject: str = "",
        message_id: str = "",
        thread: str = "",
        say: str = "",
    ) -> Task:
        text = prompt.strip()
        if not text:
            raise ValueError("任务内容不能为空")
        tasks = self.load()
        task = Task(
            id=new_task_id(),
            prompt=text,
            origin=origin,
            reply_to=reply_to,
            subject=subject,
            message_id=message_id,
            thread=thread,
            say=say.strip(),
        )
        tasks.append(task)
        self.save(tasks)
        return task

    def thread_history(self, thread: str, exclude_id: str = "", limit: int = 3) -> list[Task]:
        """同一话题下已跑完的历史任务，按时间从早到晚返回。

        只取 ``done``：失败的任务没有可用的回答，把错误信息当上下文喂进去
        只会让模型顺着错误往下编。``limit`` 限制条数，避免一个聊了一周的话题
        把每封新邮件的上下文都撑爆。
        """
        if not thread:
            return []
        done = [
            task
            for task in self.load()
            if task.thread == thread and task.id != exclude_id and task.status == DONE
        ]
        return done[-limit:]

    def has_message(self, message_id: str) -> bool:
        """同一个 Message-ID 是否已经入过队。

        IMAP 的 \\Seen 标记在入队之后才写，中间崩溃会让同一封信被重复拉取；
        这条检查把重复挡在执行之前，成本只有一次队列读取。
        """
        if not message_id:
            return False
        return any(task.message_id == message_id for task in self.load())

    def next_pending(self) -> Task | None:
        # 严格 FIFO：先入队先跑，避免长任务永远被后来的短任务插队。
        for task in self.load():
            if task.status == PENDING:
                return task
        return None

    def mark_running(self, task_id: str) -> Task | None:
        return self._update(task_id, status=RUNNING, started=_now(), error="")

    def mark_done(self, task_id: str, result_file: str, text: str) -> Task | None:
        return self._update(
            task_id,
            status=DONE,
            finished=_now(),
            result_file=result_file,
            summary=_summarize(text),
        )

    def mark_failed(self, task_id: str, error: str) -> Task | None:
        return self._update(task_id, status=FAILED, finished=_now(), error=error[:1000])

    def reset_running(self) -> int:
        # 进程被强杀会留下 RUNNING 僵尸任务；下次启动把它们放回待办。
        tasks = self.load()
        revived = 0
        for task in tasks:
            if task.status == RUNNING:
                task.status = PENDING
                task.started = ""
                task.error = "上次运行被中断，已重新排队"
                revived += 1
        if revived:
            self.save(tasks)
        return revived

    def _update(self, task_id: str, **changes) -> Task | None:
        tasks = self.load()
        for task in tasks:
            if task.id != task_id:
                continue
            for key, value in changes.items():
                setattr(task, key, value)
            self.save(tasks)
            return task
        return None

    def result_text(self, task: Task, limit: int = 800) -> str:
        """读回一条已完成任务的结果正文，截断到 ``limit`` 字。

        优先读 ``results/<id>.md`` 的「## 结果」段：一句话摘要（``summary``）
        往往只说结论，追问时模型需要的是上一轮到底答了什么。
        读不到文件就退回摘要，再不行退回空串。
        """
        text = ""
        if task.result_file:
            try:
                text = Path(task.result_file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        if text.strip():
            marker = text.find("## 结果")
            if marker >= 0:
                text = text[marker + len("## 结果") :]
            text = text.strip()
        if not text:
            text = (task.summary or "").strip()
        return text[: max(0, limit)]

    # ── results ──────────────────────────────────────────────────────────

    def write_result(self, task_id: str, text: str, prompt: str) -> str:
        self.results_dir().mkdir(parents=True, exist_ok=True)
        path = self.result_path(task_id)
        body = [
            "---",
            f"task: {task_id}",
            f"finished: {_now()}",
            "---",
            "",
            "## 任务",
            "",
            prompt.strip(),
            "",
            "## 结果",
            "",
            text.rstrip(),
            "",
        ]
        path.write_text("\n".join(body), encoding="utf-8")
        return str(path)


def summarize(text: str) -> str:
    # 队列里只留一行摘要；正文在 results/<id>.md。
    # 首行常是「K，结果如下：」这类过渡句，所以优先挑真正有信息量的那行。
    lines = []
    for raw in text.splitlines():
        line = raw.lstrip("#> ").strip()
        if not line:
            continue
        lines.append(" ".join(line.split()))
        if len(lines) >= 8:
            break
    if not lines:
        return ""
    for line in lines:
        if len(line) >= 20:
            return line[:SUMMARY_LIMIT]
    longest = max(lines, key=len)
    return longest[:SUMMARY_LIMIT]


# 旧名字保留：已有调用方（测试、队列内部）还按 _summarize 引用它。
_summarize = summarize


__all__ = [
    "DONE",
    "FAILED",
    "ORIGIN_CLI",
    "ORIGIN_MAIL",
    "ORIGIN_VOICE",
    "PENDING",
    "QUEUE_NAME",
    "RESULTS_DIR",
    "RUNNING",
    "Task",
    "TaskQueue",
    "new_task_id",
    "summarize",
]
