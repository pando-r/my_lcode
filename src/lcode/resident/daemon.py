"""常驻守护循环：轮询队列、逐条执行、把结果写回磁盘。

为什么是「轮询 + 落盘」而不是「长驻内存」：
Windows 上长驻进程会随关机消失，而任务队列在磁盘上，重启后 ``reset_running()``
能把中断的任务放回待办。所以「每天跑一轮」可以由任务计划程序调用 ``--resident-once``
实现，不依赖进程活着。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lcode.config import Config
from lcode.notify import APP_NAME, LEVEL_ERROR, LEVEL_OK, Notifier
from lcode.permission import Mode
from lcode.resident.queue import ORIGIN_MAIL, Task, TaskQueue, summarize
from lcode.resident.runner import RunnerOptions, TaskRunner
from lcode.resident.safeio import warn

LOCK_NAME = "daemon.lock"
LOG_NAME = "daemon.log"

DEFAULT_INTERVAL = 300.0
DEFAULT_MAX_TASKS_PER_ROUND = 5

# 邮件回信正文上限：结果可能很长，但一封信塞几万字没人会读。
REPLY_BODY_LIMIT = 8000


@dataclass
class DaemonOptions:
    root: str
    interval: float = DEFAULT_INTERVAL
    max_tasks_per_round: int = DEFAULT_MAX_TASKS_PER_ROUND
    mode: Mode = Mode.DEFAULT
    max_turns: int = 20
    timeout: float = 900.0
    provider_index: int = 0
    quiet: bool = False
    log_path: str = ""
    extra_allowed: list[str] = field(default_factory=list)
    # 每跑完一条任务就把结果推给用户（桌面横幅 / webhook）；测试里关掉。
    notify: bool = True
    # 每轮先把收件箱里的新邮件变成任务。
    mail: bool = False
    # 邮件来源的任务默认只读（见 mailbox 模块文档第 3 条），这里允许显式放开。
    mail_allow_write: bool = False


class Daemon:
    def __init__(self, cfg: Config, opts: DaemonOptions) -> None:
        self._cfg = cfg
        self._opts = opts
        self._dir = Path(opts.root) / ".lcode" / "resident"
        self._queue = TaskQueue(self._dir)
        self._log_path = Path(opts.log_path) if opts.log_path else self._dir / LOG_NAME
        self._runner: TaskRunner | None = None
        self._stopping = False
        # 通知通道：配置来自 .lcode/notify.yaml。构造失败也不能拦住常驻启动。
        self._notifier: Notifier | None = None
        if opts.notify:
            try:
                self._notifier = Notifier(opts.root)
            except Exception as exc:  # noqa: BLE001 - 通知是增强，不是前置条件
                warn(f"[resident] warn: 通知初始化失败: {exc}")
        # 收件箱：同样只在需要时构造，配置不全时一起放在下面按需跳过。
        self._mail_cfg = None
        if opts.mail:
            try:
                from lcode.mailbox import load_config as load_mail_config

                self._mail_cfg = load_mail_config(opts.root)
            except Exception as exc:  # noqa: BLE001 - 收件箱坏了也不该阻断队列
                warn(f"[resident] warn: 收件箱初始化失败: {exc}")
        self._reply_cfg = None
        if opts.mail:
            try:
                from lcode.notify import load_config as load_notify_config

                self._reply_cfg = load_notify_config(opts.root).email
            except Exception as exc:  # noqa: BLE001
                warn(f"[resident] warn: 回信账号读取失败: {exc}")

    # ── logging ──────────────────────────────────────────────────────────

    def log(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        if not self._opts.quiet:
            # pythonw + 无管道时 stderr 是 None；编码不兼容时也不能抛。
            warn(line)
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            # 日志写不进去不该影响干活。
            pass

    def notify(
        self,
        title: str,
        message: str,
        level: str,
        task_id: str = "",
        exclude: tuple[str, ...] = (),
    ) -> None:
        """把一条通知推给用户；任何失败都降级成日志，绝不影响任务本身。"""
        if self._notifier is None:
            return
        try:
            channels = self._notifier.send(
                title, message, level=level, source=task_id or "resident", exclude=exclude
            )
        except Exception as exc:  # noqa: BLE001 - 通知永远不能杀死常驻进程
            self.log(f"通知失败：{type(exc).__name__}: {exc}")
            return
        self.log(f"通知已发送（{'、'.join(channels) or '无通道'}）：{title}")

    # ── mailbox ──────────────────────────────────────────────────────────

    def ingest_mail(self) -> int:
        """拉取收件箱，把通过闸门的邮件变成任务，返回新增条数。

        顺序是「先入队、后标已读」：反过来一旦入队失败，这封信就永久变成已读，
        人不会发现自己的请求被吃掉了。
        """
        if self._mail_cfg is None or not self._mail_cfg.ready():
            return 0
        from lcode.mailbox import RejectionLog, fetch_new

        try:
            # 用队列里已有的 Message-ID 做去重：比已读标记可靠，也不依赖 uid 稳定。
            known = {task.message_id for task in self._queue.load() if task.message_id}
            result = fetch_new(self._mail_cfg, skip_ids=known)
        except Exception as exc:  # noqa: BLE001 - 收件箱不可用不该杀死轮询
            self.log(f"收件箱拉取失败：{type(exc).__name__}: {exc}")
            return 0

        # 退信每轮都会重新看到（它们留在收件箱里，我们不改用户的已读状态）；
        # RejectionLog 保证每封只报一次。
        for item in RejectionLog(self._rejection_path()).take_new(result.rejected):
            self.log(f"退回邮件（{item.reason}）：{item.sender} / {item.subject[:60]}")

        ingested = 0
        for mail in result.accepted:
            if mail.message_id and self._queue.has_message(mail.message_id):
                continue  # 同一封信不重复入队（并发的两次轮询之间也能挡住）
            try:
                task = self._queue.enqueue(
                    mail.prompt(),
                    origin=ORIGIN_MAIL,
                    reply_to=mail.sender,
                    subject=mail.subject,
                    message_id=mail.message_id,
                )
            except ValueError as exc:
                self.log(f"邮件转任务失败（{exc}）：{mail.sender}")
                continue
            ingested += 1
            self.log(f"邮件入队 {task.id}：{_oneline(mail.prompt(), 60)}")
        return ingested

    def _rejection_path(self) -> Path:
        from lcode.mailbox import REPORTED_NAME

        return self._dir / REPORTED_NAME

    def reply(self, task: Task, text: str, ok: bool = True) -> bool:
        """把任务结果回信给来信人。"""
        if not task.reply_to or self._reply_cfg is None:
            return False
        from lcode.notify import send_mail

        subject = task.subject or "Lcode 任务"
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        body = (text or "").strip() or "（任务没有产生文本输出。）"
        if not ok:
            body = f"任务执行失败。\n\n{body}"
        body += f"\n\n---\n任务：{task.id}\n结果文件：{task.result_file or '（无）'}\n"
        try:
            sent = send_mail(
                self._reply_cfg,
                task.reply_to,
                subject,
                body[:REPLY_BODY_LIMIT],
                in_reply_to=task.message_id,
            )
        except Exception as exc:  # noqa: BLE001 - 回信失败不能影响任务状态
            self.log(f"回信异常：{type(exc).__name__}: {exc}")
            return False
        self.log(f"回信{'成功' if sent else '失败'} {task.id} → {task.reply_to}")
        return sent

    # ── single instance ──────────────────────────────────────────────────

    def _lock_path(self) -> Path:
        return self._dir / LOCK_NAME

    def acquire_lock(self) -> bool:
        path = self._lock_path()
        self._dir.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            try:
                pid = int(path.read_text(encoding="utf-8").strip() or "0")
            except (OSError, ValueError):
                pid = 0
            if pid and pid != os.getpid() and _pid_alive(pid):
                self.log(f"已有常驻进程在运行（pid={pid}），本次退出")
                return False
            self.log(f"发现残留锁（pid={pid} 已不存在），接管")
        path.write_text(str(os.getpid()), encoding="utf-8")
        return True

    def release_lock(self) -> None:
        path = self._lock_path()
        try:
            if path.is_file() and path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                path.unlink()
        except OSError:
            pass

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._runner = TaskRunner(
            self._cfg,
            RunnerOptions(
                root=self._opts.root,
                mode=self._opts.mode,
                max_turns=self._opts.max_turns,
                timeout=self._opts.timeout,
                extra_allowed=self._opts.extra_allowed,
            ),
            provider_index=self._opts.provider_index,
        )
        await self._runner.start()
        revived = self._queue.reset_running()
        if revived:
            self.log(f"把 {revived} 条被中断的任务放回待办")
        self.log(f"常驻启动：{self._runner.describe()}，队列 {self._queue.path}")

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.close()
            self._runner = None

    def stop(self) -> None:
        self._stopping = True

    # ── rounds ───────────────────────────────────────────────────────────

    async def run_once(self) -> int:
        """处理本轮所有待办任务，返回处理条数。"""
        if self._runner is None:
            raise RuntimeError("Daemon 尚未 start()")
        # 先收信再干活：这样「发了邮件 → 本轮就跑」而不必等下一轮。
        self.ingest_mail()
        handled = 0
        while handled < self._opts.max_tasks_per_round and not self._stopping:
            task = self._queue.next_pending()
            if task is None:
                break
            await self._run_task(task)
            handled += 1
        return handled

    async def run_forever(self) -> None:
        self.log(f"轮询间隔 {self._opts.interval:g}s，Ctrl+C 退出")
        while not self._stopping:
            try:
                handled = await self.run_once()
                if handled:
                    self.log(f"本轮处理 {handled} 条任务")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单轮失败不能杀死守护进程
                self.log(f"本轮异常：{type(exc).__name__}: {exc}")
                # 整轮都挂了是最该让人知道的情况：不通知的话轮询会一直空转。
                self.notify(
                    f"{APP_NAME} 常驻本轮异常",
                    f"{type(exc).__name__}: {exc}"[:400],
                    LEVEL_ERROR,
                )
            try:
                await asyncio.wait_for(self._wait_interval(), timeout=self._opts.interval)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
        self.log("收到停止信号，正在退出")

    async def _wait_interval(self) -> None:
        # 单独拉出来，方便被 cancel 打断（测试里用短间隔）。
        while not self._stopping:
            await asyncio.sleep(0.2)

    async def _run_task(self, task: Task) -> None:
        assert self._runner is not None
        self._queue.mark_running(task.id)
        self.log(f"开始 {task.id}：{_oneline(task.prompt)}")
        started = datetime.now()
        mode = self._task_mode(task)
        try:
            text = await self._runner.run(task.prompt, mode=mode)
        except TimeoutError:
            self._queue.mark_failed(task.id, f"超时（{self._opts.timeout:g}s）")
            self.log(f"超时 {task.id}")
            self.notify(
                f"{APP_NAME} 任务超时：{task.id}",
                f"{_oneline(task.prompt, 60)}\n超过 {self._opts.timeout:g}s 未完成，已标记失败。",
                LEVEL_ERROR,
                task.id,
                exclude=_notify_exclude(task),
            )
            self.reply(task, f"超过 {self._opts.timeout:g}s 未完成，已停止。", ok=False)
            return
        except asyncio.CancelledError:
            # 被中断的任务下轮会被 reset_running 放回待办。
            self.log(f"中断 {task.id}")
            raise
        except Exception as exc:  # noqa: BLE001 - 单条任务失败不影响后续
            detail = f"{type(exc).__name__}: {exc}"
            self._queue.mark_failed(task.id, detail)
            self.log(f"失败 {task.id}：{detail}")
            self.notify(
                f"{APP_NAME} 任务失败：{task.id}",
                f"{_oneline(task.prompt, 60)}\n{detail[:400]}",
                LEVEL_ERROR,
                task.id,
                exclude=_notify_exclude(task),
            )
            self.reply(task, detail[:400], ok=False)
            return

        path = self._queue.write_result(task.id, text, task.prompt)
        self._queue.mark_done(task.id, path, text)
        elapsed = (datetime.now() - started).total_seconds()
        self.log(f"完成 {task.id}（{elapsed:.1f}s）→ {path}")
        # 邮件来源的任务已经要回信了，再发一封「任务完成」通知等于同一件事说两遍。
        self.notify(
            f"{APP_NAME} 任务完成：{task.id}",
            f"{_oneline(task.prompt, 60)}\n{_summarize_for_notify(text, path)}",
            LEVEL_OK,
            task.id,
            exclude=_notify_exclude(task),
        )
        self.reply(task, text)

    def _task_mode(self, task: Task) -> Mode | None:
        """邮件来源的任务强制只读，除非显式打开了 allow_write。

        邮件正文是不可信输入，提示注入的代价在这里是真实的：
        「把私钥发到某地址」这种请求，只读模式下最多只能读到文件内容，
        而读得到也发不出去——发信本身要走 notify/回信通道。
        """
        if task.origin != ORIGIN_MAIL:
            return None
        cfg = self._mail_cfg
        if cfg is not None and cfg.allow_write:
            return None
        return Mode.DEFAULT

    # ── introspection ────────────────────────────────────────────────────

    @property
    def queue(self) -> TaskQueue:
        return self._queue

    def has_pending(self) -> bool:
        """队列里是否还有待办任务。供轻量预检用，不启动 runner。"""
        return self._queue.next_pending() is not None


def _pid_alive(pid: int) -> bool:
    # Windows 上没有可靠的 os.kill(pid, 0) 语义，用 OpenProcess 探活。
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但不属于当前用户
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            # 进程存在也可能已经退出（zombie handle），再查一次退出码。
            code = ctypes.c_ulong(0)
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))  # type: ignore[attr-defined]
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 探测失败时保守认为存活
        return True


def _oneline(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _notify_exclude(task: Task) -> tuple[str, ...]:
    """邮件来源的任务跳过 email 通知通道。

    它会单独收到一封回信，再发一条「任务完成」通知就是把同一件事说两遍。
    更糟的是那条通知的主题没有前缀，会被我们自己的收件箱过滤器退回，
    在 daemon.log 里留下一行噪声。
    """
    return ("email",) if task.origin == ORIGIN_MAIL else ()


def _summarize_for_notify(text: str, result_path: str) -> str:
    """通知正文只放一行有信息量的摘要 + 结果文件位置。

    桌面横幅最多显示两三行，把整篇报告塞进去等于什么都没说；
    真正想看内容的人会打开 results/<id>.md。
    """
    summary = summarize(text or "")
    if not summary:
        return f"结果已保存：{result_path}"
    return f"{_oneline(summary, 120)}\n结果已保存：{result_path}"


__all__ = [
    "DEFAULT_INTERVAL",
    "DEFAULT_MAX_TASKS_PER_ROUND",
    "REPLY_BODY_LIMIT",
    "Daemon",
    "DaemonOptions",
]
