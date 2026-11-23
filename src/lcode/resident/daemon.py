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
from lcode.resident.queue import ORIGIN_MAIL, ORIGIN_VOICE, Task, TaskQueue
from lcode.resident.runner import RunnerOptions, TaskRunner
from lcode.resident.safeio import warn

LOCK_NAME = "daemon.lock"
LOG_NAME = "daemon.log"

DEFAULT_INTERVAL = 300.0
DEFAULT_MAX_TASKS_PER_ROUND = 5

# 邮件回信正文上限：结果可能很长，但一封信塞几万字没人会读。
REPLY_BODY_LIMIT = 8000

# 同一话题的上下文预算：最多带几轮、每轮问题/回答各截多少字。
THREAD_HISTORY_LIMIT = 3
THREAD_PROMPT_LIMIT = 400
THREAD_ANSWER_LIMIT = 800

# 通知里用 prompt 当「任务标签」的长度上限。
# 超过这个长度就认定它是一段指令而不是一句话标题，整个丢掉：
# 开机简报的 prompt 有六百多字，截前 60 字糊在横幅第一行纯属噪声，
# 真正有信息量的答复反而被挤下去。
PROMPT_LABEL_LIMIT = 60

# 通知正文的字符预算。开机简报本身就是「200 字以内、一屏看完」，
# 所以要整段带上；几千字的长报告才需要截断。
NOTIFY_EXCERPT_LIMIT = 320


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
            self._mail_cfg = self._ensure_mail_cfg()
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
                    thread=mail.thread_key,
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
        if not task.reply_to:
            return False
        cfg = self._ensure_reply_cfg()
        if cfg is None:
            return False
        from lcode.notify import send_mail

        subject = task.subject or "Lcode 任务"
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        body = (text or "").strip() or "（任务没有产生文本输出。）"
        if not ok:
            body = f"任务执行失败。\n\n{body}"
        body += (
            f"\n\n---\n任务：{task.id}\n结果文件：{task.result_file or '（无）'}\n"
            "直接回复这封信可以接着追问，我会带上这个话题之前的问答。\n"
        )
        try:
            sent = send_mail(
                cfg,
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

    def _ensure_reply_cfg(self):
        """取回信账号；没配置时说清原因，而不是静默跳过。

        早期这里只看 ``opts.mail``：用 ``--check-mail`` + ``--resident-once``
        这套推荐组合时（收信和执行分开调度），任务确实是邮件来的，回信账号却
        根本没加载 → 每封信都石沉大海，日志里连一行记录都没有。
        现在改成「谁跑到了邮件任务就按需加载」，并显式说出跳过原因。
        """
        if self._reply_cfg is not None:
            return self._reply_cfg
        try:
            from lcode.notify import load_config as load_notify_config

            self._reply_cfg = load_notify_config(self._opts.root).email
        except Exception as exc:  # noqa: BLE001 - 回信是增强，不该打断任务
            self.log(f"回信账号读取失败：{type(exc).__name__}: {exc}")
            return None
        if not self._reply_cfg.ready():
            self.log(
                "无法回信：.lcode/notify.yaml 的 email 段没配全"
                "（需要 enabled/smtp_host/user/auth_code/to）"
            )
        return self._reply_cfg

    def _ensure_mail_cfg(self):
        """按需加载收件箱配置，并把 ``--mail-allow-write`` 叠加进去。

        为什么要单独一层：``--check-mail``（收信）和 ``--resident-once``（执行）
        是分开调度的，后者往往不带 ``--mail-watch``。早期只在 ``opts.mail`` 为真
        时加载配置，于是：
          · ``--mail-allow-write`` 完全无效（配置里那份 allow_write 才是唯一来源）；
          · 队列里的邮件任务读不到 allow_write，只能一律只读。
        现在两条路径都走这里，参数与配置合并后以「更宽松者」为准。
        """
        if self._mail_cfg is not None:
            return self._mail_cfg
        try:
            from lcode.mailbox import load_config as load_mail_config

            cfg = load_mail_config(self._opts.root)
        except Exception as exc:  # noqa: BLE001 - 收件箱坏了也不该阻断队列
            warn(f"[resident] warn: 收件箱初始化失败: {exc}")
            return None
        if self._opts.mail_allow_write and not cfg.allow_write:
            cfg.allow_write = True
        self._mail_cfg = cfg
        return cfg

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
        prompt = self._prompt_with_thread(task)
        try:
            text = await self._runner.run(prompt, mode=mode)
        except TimeoutError:
            self._queue.mark_failed(task.id, f"超时（{self._opts.timeout:g}s）")
            self.log(f"超时 {task.id}")
            self.notify(
                f"{APP_NAME} 任务超时：{task.id}",
                _notify_body(
                    f"超过 {self._opts.timeout:g}s 未完成，已标记失败。",
                    _prompt_label(task.prompt),
                ),
                LEVEL_ERROR,
                task.id,
                exclude=_notify_exclude(task),
            )
            self._reply_and_warn(task, f"超过 {self._opts.timeout:g}s 未完成，已停止。", ok=False)
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
                _notify_body(detail[:400], _prompt_label(task.prompt)),
                LEVEL_ERROR,
                task.id,
                exclude=_notify_exclude(task),
            )
            self._reply_and_warn(task, detail[:400], ok=False)
            return

        path = self._queue.write_result(task.id, text, task.prompt)
        self._queue.mark_done(task.id, path, text)
        elapsed = (datetime.now() - started).total_seconds()
        self.log(f"完成 {task.id}（{elapsed:.1f}s）→ {path}")
        # 邮件来源的任务已经要回信了，再发一封「任务完成」通知等于同一件事说两遍。
        self.notify(
            f"{APP_NAME} 任务完成：{task.id}",
            _summarize_for_notify(text, path, prompt=task.prompt),
            LEVEL_OK,
            task.id,
            exclude=_notify_exclude(task),
        )
        self._reply_and_warn(task, text)

    def _reply_and_warn(self, task: Task, text: str, ok: bool = True) -> None:
        """回信；没送出去时用**非邮件**通道补一条通知。

        回信是把结果交给来信人的唯一途径。SMTP 挂了、授权码过期、账号没配全
        时它返回 False——如果这里只是安静地返回，用户会以为 Agent 没理他，
        而这恰恰是最需要被告知的情况。所以补一条桌面/webhook 通知，
        内容指向 results 文件，至少不会彻底黑洞。
        """
        if self.reply(task, text, ok=ok):
            return
        if task.origin != ORIGIN_MAIL:
            return
        self.notify(
            f"{APP_NAME} 回信失败：{task.id}",
            _notify_body(
                f"结果没能发回 {task.reply_to or '（无地址）'}，"
                "请检查 .lcode/notify.yaml 的 email 段（smtp_host / auth_code / to）。",
                _prompt_label(task.prompt),
                f"结果文件：{task.result_file or '（无）'}",
            ),
            LEVEL_ERROR,
            task.id,
            exclude=_notify_exclude(task),
        )

    def _prompt_with_thread(self, task: Task) -> str:
        """同一话题的追问：把之前的问答拼在指令前面一起交给 Agent。

        没有这个话题的注记就是原样返回。上下文只取「用户上一轮问的 + 我上一轮
        答的」，条数与字数都有上限：这本来是给邮件追问设计的（邮件是单轮协议），
        语音连续对话共用同一条路径——同样不能凭它做无限对话，那会把上下文
        一路撑到模型窗口爆掉。
        """
        if not task.thread:
            return task.prompt
        history = self._queue.thread_history(
            task.thread, exclude_id=task.id, limit=THREAD_HISTORY_LIMIT
        )
        if not history:
            return task.prompt
        blocks = []
        for item in history:
            answer = self._queue.result_text(item, THREAD_ANSWER_LIMIT)
            # 优先用用户原话：prompt 可能带着几十字的语音指令前缀，
            # 拼进上下文会让每一轮都重复一遍提示词。
            said = item.say or item.prompt
            blocks.append(
                f"提问：{_clip(said, THREAD_PROMPT_LIMIT)}\n"
                f"回答：{_clip(answer, THREAD_ANSWER_LIMIT)}"
            )
        return (
            "下面是同一个话题里之前的往来（供你理解上下文，不要重复作答）：\n\n"
            + "\n\n".join(blocks)
            + f"\n\n---\n本次新消息：\n{task.prompt}"
        )

    def _task_mode(self, task: Task) -> Mode | None:
        """邮件来源的任务强制只读，除非显式打开了 allow_write。

        邮件正文是不可信输入，提示注入的代价在这里是真实的：
        「把私钥发到某地址」这种请求，只读模式下最多只能读到文件内容，
        而读得到也发不出去——发信本身要走 notify/回信通道。
        """
        if task.origin != ORIGIN_MAIL:
            return None
        cfg = self._ensure_mail_cfg()
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


def _clip(text: str, limit: int) -> str:
    """截断但不改换行：上下文里的分行读起来比压成一行清楚。"""
    body = (text or "").strip()
    return body if len(body) <= limit else body[: limit - 3] + "..."


def _notify_exclude(task: Task) -> tuple[str, ...]:
    """邮件来源的任务跳过 email 通知通道。

    它会单独收到一封回信，再发一条「任务完成」通知就是把同一件事说两遍。
    更糟的是那条通知的主题没有前缀，会被我们自己的收件箱过滤器退回，
    在 daemon.log 里留下一行噪声。
    """
    # 语音提问同理：回答已经当场念出来了，再发一封邮件是重复且打扰。
    if task.origin in (ORIGIN_MAIL, ORIGIN_VOICE):
        return ("email",)
    return ()


def _summarize_for_notify(text: str, result_path: str, prompt: str = "") -> str:
    """通知正文：**完整答复在前**，任务标签在后，最后是结果文件位置。

    顺序和取多少都是踩过的坑：

    1. 早期把 prompt 放第一行（``_oneline(task.prompt, 60)``），桌面横幅看到的
       是一段提示词开头——开机简报的 prompt 六百多字，横幅第一行就是「你是 K
       的贾维斯。他刚开机，你主动开口。 写一份简短的晨间简报…」，把指令原样
       糊给用户看。现在长 prompt 直接不显示（见 ``_prompt_label``）。
    2. 早期用 ``summarize()`` 只取一行，简报只剩「下午好，K。」这句问候，
       天气和建议全丢。简报本来就是按「一屏看完」写的，所以整段带上，
       只有超过 ``NOTIFY_EXCERPT_LIMIT`` 的长报告才截断。
    """
    return _notify_body(
        _notify_excerpt(text),
        _prompt_label(prompt),
        f"结果已保存：{result_path}",
    )


def _notify_excerpt(text: str, limit: int = NOTIFY_EXCERPT_LIMIT) -> str:
    """把答复整理成推送正文：保留分行、去掉 Markdown 记号、超长才截断。

    保留换行是有意的：横幅首行用来判断「这是好消息还是坏消息」，
    压成一行反而更难读。
    """
    lines = []
    for raw in (text or "").splitlines():
        line = _plain_line(raw)
        if line:
            lines.append(line)
    body = "\n".join(lines)
    if len(body) <= limit:
        return body
    return body[: limit - 1].rstrip() + "…"


def _plain_line(line: str) -> str:
    """去掉横幅里看得见却读不懂的记号：``**加粗**``、反引号、行首 ``#`` 与列表符。"""
    text = line.strip()
    if not text:
        return ""
    text = text.lstrip("#> ").strip()
    text = text.replace("**", "").replace("`", "")
    if text.startswith(("- ", "* ")):
        text = text[2:].strip()
    return text


def _notify_body(*lines: str) -> str:
    """按顺序拼接通知正文，丢掉空行（不留下孤立的换行）。"""
    return "\n".join(line for line in lines if line)


def _prompt_label(prompt: str) -> str:
    """把短指令当成任务标签；长指令返回空串，整个不显示。

    判据用**完整长度**而不是截断后的长度：一句话的请求（``--enqueue 整理 docs``）
    能帮人认出这是哪条任务；而整段提示词（开机简报那种）截出来的前几十字
    毫无信息量，只会占掉横幅第一行的位置。
    """
    flat = " ".join((prompt or "").split())
    if not flat or len(flat) > PROMPT_LABEL_LIMIT:
        return ""
    return f"任务：{flat}"


__all__ = [
    "DEFAULT_INTERVAL",
    "DEFAULT_MAX_TASKS_PER_ROUND",
    "REPLY_BODY_LIMIT",
    "THREAD_ANSWER_LIMIT",
    "THREAD_HISTORY_LIMIT",
    "THREAD_PROMPT_LIMIT",
    "Daemon",
    "DaemonOptions",
]
