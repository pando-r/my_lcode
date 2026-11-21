from __future__ import annotations

import json
import os

import pytest

from lcode.resident.daemon import Daemon, DaemonOptions, _oneline, _pid_alive
from lcode.resident.queue import (
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    Task,
    TaskQueue,
    _summarize,
)


def _queue(tmp_path) -> TaskQueue:
    return TaskQueue(tmp_path / "resident")


# ── queue: basic ─────────────────────────────────────────────────────────


def test_empty_queue(tmp_path):
    queue = _queue(tmp_path)
    assert queue.load() == []
    assert queue.next_pending() is None


def test_enqueue_persists_across_instances(tmp_path):
    first = _queue(tmp_path)
    task = first.enqueue("  写一个冒泡排序  ")
    assert task.status == PENDING
    assert task.prompt == "写一个冒泡排序"  # 首尾空白被裁掉

    # 换一个实例重新读盘，模拟进程重启。
    second = TaskQueue(tmp_path / "resident")
    loaded = second.load()
    assert [t.id for t in loaded] == [task.id]
    assert loaded[0].prompt == "写一个冒泡排序"


def test_enqueue_rejects_blank(tmp_path):
    queue = _queue(tmp_path)
    with pytest.raises(ValueError):
        queue.enqueue("   ")


def test_next_pending_is_fifo(tmp_path):
    queue = _queue(tmp_path)
    a = queue.enqueue("第一条")
    b = queue.enqueue("第二条")
    assert queue.next_pending().id == a.id
    queue.mark_done(a.id, str(tmp_path / "a.md"), "done")
    assert queue.next_pending().id == b.id


def test_mark_running_done_failed(tmp_path):
    queue = _queue(tmp_path)
    task = queue.enqueue("任务")

    queue.mark_running(task.id)
    running = queue.load()[0]
    assert running.status == RUNNING and running.started

    queue.mark_done(task.id, "/tmp/x.md", "结果正文")
    done = queue.load()[0]
    assert done.status == DONE
    assert done.result_file == "/tmp/x.md"
    assert done.finished

    failed = queue.enqueue("会失败")
    queue.mark_failed(failed.id, "KeyError: boom")
    assert queue.load()[-1].status == FAILED
    assert queue.load()[-1].error == "KeyError: boom"


def test_mark_unknown_id_is_noop(tmp_path):
    queue = _queue(tmp_path)
    queue.enqueue("任务")
    assert queue.mark_running("job_nope") is None
    assert queue.load()[0].status == PENDING


def test_reset_running_revives_zombies(tmp_path):
    queue = _queue(tmp_path)
    task = queue.enqueue("半路被 kill 的任务")
    queue.mark_running(task.id)
    assert queue.load()[0].status == RUNNING

    # 重启后必须能捡回来，否则任务会永久卡在 running。
    assert queue.reset_running() == 1
    revived = queue.load()[0]
    assert revived.status == PENDING
    assert "中断" in revived.error
    assert revived.started == ""


def test_reset_running_leaves_done_alone(tmp_path):
    queue = _queue(tmp_path)
    task = queue.enqueue("已完成")
    queue.mark_done(task.id, "/tmp/x.md", "ok")
    assert queue.reset_running() == 0
    assert queue.load()[0].status == DONE


# ── queue: durability ────────────────────────────────────────────────────


def test_corrupt_queue_is_quarantined(tmp_path, capsys):
    queue = _queue(tmp_path)
    queue.directory.mkdir(parents=True, exist_ok=True)
    queue.path.write_text("{not json", encoding="utf-8")

    assert queue.load() == []
    assert "队列文件损坏" in capsys.readouterr().err
    # 坏文件必须留证，不能静默删掉用户数据。
    backups = list(queue.directory.glob("queue.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json"


def test_non_list_queue_is_quarantined(tmp_path, capsys):
    queue = _queue(tmp_path)
    queue.directory.mkdir(parents=True, exist_ok=True)
    queue.path.write_text('{"a": 1}', encoding="utf-8")
    assert queue.load() == []
    assert "队列文件损坏" in capsys.readouterr().err


def test_load_skips_entries_without_prompt(tmp_path):
    queue = _queue(tmp_path)
    queue.directory.mkdir(parents=True, exist_ok=True)
    queue.path.write_text(
        json.dumps(
            [
                {"id": "job_1", "prompt": "有效"},
                {"id": "job_2"},
                {"id": "job_3", "prompt": "   "},
                "not-a-dict",
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert [t.id for t in queue.load()] == ["job_1"]


def test_missing_id_gets_filled(tmp_path):
    queue = _queue(tmp_path)
    queue.directory.mkdir(parents=True, exist_ok=True)
    queue.path.write_text(json.dumps([{"prompt": "没写 id"}]), encoding="utf-8")
    task = queue.load()[0]
    assert task.id.startswith("job_")
    assert len(task.id) == len("job_") + 8


def test_save_leaves_no_tmp_file(tmp_path):
    queue = _queue(tmp_path)
    queue.enqueue("任务")
    assert not list(queue.directory.glob("*.tmp"))


def test_write_result_creates_file(tmp_path):
    queue = _queue(tmp_path)
    task = queue.enqueue("写个函数")
    path = queue.write_result(task.id, "## 结果\n\n写好了", task.prompt)
    text = open(path, encoding="utf-8").read()  # noqa: SIM115 - 测试里直接读
    assert f"task: {task.id}" in text
    assert "写个函数" in text
    assert "写好了" in text
    assert os.path.isfile(path)


# ── summary ──────────────────────────────────────────────────────────────


def test_summary_prefers_informative_line():
    text = "K，结果如下：\n\n**tests 目录下共 64 个 .py 文件**\n"
    assert _summarize(text) == "**tests 目录下共 64 个 .py 文件**"


def test_summary_skips_blank_lines():
    assert _summarize("\n\n\n") == ""


def test_summary_falls_back_to_longest_short_line():
    assert _summarize("好的\n完成了\n") == "完成了"


def test_summary_truncates():
    long_line = "x " * 400
    assert len(_summarize(long_line)) <= 280


# ── daemon helpers ───────────────────────────────────────────────────────


def test_oneline_collapses_and_truncates():
    assert _oneline("a\n\nb   c") == "a b c"
    assert len(_oneline("x" * 200)) == 80
    assert _oneline("x" * 200).endswith("...")


def test_pid_alive_for_self():
    assert _pid_alive(os.getpid())


def test_pid_alive_for_impossible_pid():
    # 0 与负数在 Windows/Linux 都不可能是「活着的普通进程」。
    assert not _pid_alive(999_999_999)


# ── daemon: lock ─────────────────────────────────────────────────────────


def _daemon(tmp_path, **kwargs) -> Daemon:
    from lcode.config import Config, ProviderConfig

    cfg = Config(
        providers=[
            ProviderConfig(name="p", protocol="openai", api_key="k", model="m"),
        ]
    )
    # notify=False：测试里绝不能真弹系统横幅。
    kwargs.setdefault("notify", False)
    return Daemon(cfg, DaemonOptions(root=str(tmp_path), quiet=True, **kwargs))


def test_lock_acquire_and_release(tmp_path):
    daemon = _daemon(tmp_path)
    assert daemon.acquire_lock()
    lock = tmp_path / ".lcode" / "resident" / "daemon.lock"
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    daemon.release_lock()
    assert not lock.exists()


def test_lock_blocks_second_instance(tmp_path):
    first = _daemon(tmp_path)
    assert first.acquire_lock()
    # 同一个 pid 重入视为自己，允许（否则 finally 重跑会误判）。
    assert first.acquire_lock()
    first.release_lock()


def test_lock_takes_over_stale_pid(tmp_path):
    lock = tmp_path / ".lcode" / "resident" / "daemon.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999999", encoding="utf-8")

    daemon = _daemon(tmp_path)
    assert daemon.acquire_lock()  # 僵尸锁必须能被接管，否则重启后永远起不来
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_lock_with_garbage_content_is_taken_over(tmp_path):
    lock = tmp_path / ".lcode" / "resident" / "daemon.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("not-a-pid", encoding="utf-8")

    daemon = _daemon(tmp_path)
    assert daemon.acquire_lock()


def test_release_does_not_delete_foreign_lock(tmp_path):
    daemon = _daemon(tmp_path)
    lock = tmp_path / ".lcode" / "resident" / "daemon.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("12345", encoding="utf-8")

    daemon.release_lock()
    # 锁不属于我们，不能替别人删。
    assert lock.exists()


# ── daemon: task execution ───────────────────────────────────────────────


class _StubRunner:
    """替身：不碰真实 LLM，只记录被调用的 prompt 与权限模式。"""

    def __init__(self, result="ok", raises: Exception | None = None) -> None:
        self.result = result
        self.raises = raises
        self.prompts: list[str] = []
        self.modes: list[object] = []

    async def run(self, prompt: str, mode=None) -> str:
        self.prompts.append(prompt)
        self.modes.append(mode)
        if self.raises is not None:
            raise self.raises
        return self.result

    def describe(self) -> str:
        return "stub"

    async def close(self) -> None:
        pass


async def test_run_once_processes_pending(tmp_path):
    daemon = _daemon(tmp_path)
    daemon._runner = _StubRunner("完成了")  # noqa: SLF001 - 测试替身
    task = daemon.queue.enqueue("任务甲")

    assert await daemon.run_once() == 1
    saved = daemon.queue.load()[0]
    assert saved.status == DONE
    assert saved.result_file
    assert os.path.isfile(saved.result_file)
    assert task.id == saved.id


async def test_run_once_respects_batch_limit(tmp_path):
    daemon = _daemon(tmp_path, max_tasks_per_round=2)
    daemon._runner = _StubRunner()  # noqa: SLF001
    for i in range(5):
        daemon.queue.enqueue(f"任务 {i}")

    assert await daemon.run_once() == 2
    statuses = [t.status for t in daemon.queue.load()]
    assert statuses == [DONE, DONE, PENDING, PENDING, PENDING]


async def test_run_once_marks_failed_on_exception(tmp_path):
    daemon = _daemon(tmp_path)
    daemon._runner = _StubRunner(raises=RuntimeError("模型炸了"))  # noqa: SLF001
    daemon.queue.enqueue("会失败")

    assert await daemon.run_once() == 1
    saved = daemon.queue.load()[0]
    assert saved.status == FAILED
    assert "RuntimeError" in saved.error
    assert "模型炸了" in saved.error


async def test_run_once_continues_after_failure(tmp_path):
    daemon = _daemon(tmp_path)

    class _Flaky(_StubRunner):
        async def run(self, prompt: str, mode=None) -> str:
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise RuntimeError("第一条挂了")
            return "第二条成功"

    daemon._runner = _Flaky()  # noqa: SLF001
    daemon.queue.enqueue("第一条")
    daemon.queue.enqueue("第二条")

    assert await daemon.run_once() == 2
    statuses = [t.status for t in daemon.queue.load()]
    assert statuses == [FAILED, DONE]


async def test_run_once_no_pending_returns_zero(tmp_path):
    daemon = _daemon(tmp_path)
    daemon._runner = _StubRunner()  # noqa: SLF001
    assert await daemon.run_once() == 0


async def test_run_once_requires_start(tmp_path):
    daemon = _daemon(tmp_path)
    with pytest.raises(RuntimeError):
        await daemon.run_once()


async def test_stop_breaks_loop(tmp_path):
    daemon = _daemon(tmp_path)
    daemon._runner = _StubRunner()  # noqa: SLF001
    daemon.queue.enqueue("任务")
    daemon.stop()
    assert await daemon.run_once() == 0


async def test_log_written_to_file(tmp_path):
    daemon = _daemon(tmp_path)
    daemon.log("hello")
    log = tmp_path / ".lcode" / "resident" / "daemon.log"
    assert "hello" in log.read_text(encoding="utf-8")


# ── 通知：任务结果不能静默 ────────────────────────────────────────────────


class _FakeNotifier:
    def __init__(self, channels=("log", "desktop")) -> None:
        self.sent: list[tuple[str, str, str, str]] = []
        self.excludes: list[tuple[str, ...]] = []
        self._channels = list(channels)

    def send(
        self,
        title: str,
        message: str,
        level: str = "info",
        source: str = "",
        exclude: tuple[str, ...] = (),
    ):
        self.sent.append((title, message, level, source))
        self.excludes.append(tuple(exclude))
        return [c for c in self._channels if c not in exclude]


async def test_success_sends_ok_notification(tmp_path):
    daemon = _daemon(tmp_path)
    daemon._runner = _StubRunner("## 结论\n\n全部 7 个工具都注册好了")  # noqa: SLF001
    fake = _FakeNotifier()
    daemon._notifier = fake  # noqa: SLF001
    task = daemon.queue.enqueue("检查工具注册表")

    await daemon.run_once()

    assert len(fake.sent) == 1
    title, message, level, source = fake.sent[0]
    assert level == "ok"
    assert source == task.id
    assert "完成" in title
    # 正文要带上结果文件位置，否则横幅只能告诉你「完事了」。
    assert ".md" in message


async def test_failure_sends_error_notification(tmp_path):
    daemon = _daemon(tmp_path)
    daemon._runner = _StubRunner(raises=RuntimeError("模型炸了"))  # noqa: SLF001
    fake = _FakeNotifier()
    daemon._notifier = fake  # noqa: SLF001
    task = daemon.queue.enqueue("注定失败的任务")

    await daemon.run_once()

    assert len(fake.sent) == 1
    title, message, level, source = fake.sent[0]
    assert level == "error"
    assert source == task.id
    assert "失败" in title
    assert "RuntimeError" in message  # 失败原因要能直接在横幅里看到


async def test_timeout_sends_error_notification(tmp_path):
    daemon = _daemon(tmp_path, timeout=1.0)
    daemon._runner = _StubRunner(raises=TimeoutError())  # noqa: SLF001
    fake = _FakeNotifier()
    daemon._notifier = fake  # noqa: SLF001
    daemon.queue.enqueue("超时任务")

    await daemon.run_once()

    assert fake.sent and fake.sent[0][2] == "error"
    assert "超时" in fake.sent[0][0]


async def test_empty_queue_sends_nothing(tmp_path):
    # 每天定时跑一轮但队列是空的：不能天天弹一条「没有任务」。
    daemon = _daemon(tmp_path)
    daemon._runner = _StubRunner()  # noqa: SLF001
    fake = _FakeNotifier()
    daemon._notifier = fake  # noqa: SLF001

    assert await daemon.run_once() == 0
    assert fake.sent == []


async def test_notify_failure_does_not_break_task(tmp_path):
    class _ExplodingNotifier:
        def send(self, *args, **kwargs):
            raise RuntimeError("通知通道炸了")

    daemon = _daemon(tmp_path)
    daemon._runner = _StubRunner("完成了")  # noqa: SLF001
    daemon._notifier = _ExplodingNotifier()  # noqa: SLF001
    daemon.queue.enqueue("任务")

    assert await daemon.run_once() == 1
    assert daemon.queue.load()[0].status == DONE


def test_notify_disabled_leaves_no_notifier(tmp_path):
    daemon = _daemon(tmp_path, notify=True)
    assert daemon._notifier is not None  # noqa: SLF001 - 默认开启
    off = _daemon(tmp_path)
    assert off._notifier is None  # noqa: SLF001


def test_cli_parses_no_notify():
    from lcode.cli import _build_parser

    assert _build_parser().parse_args([]).no_notify is False
    assert _build_parser().parse_args(["--no-notify"]).no_notify is True


# ── safeio：pythonw / GBK 终端兜底 ────────────────────────────────────────


def test_warn_survives_none_stderr(monkeypatch, capsys):
    # 计划任务用 pythonw 且不带管道时 sys.stderr 为 None。
    from lcode.resident.safeio import warn

    monkeypatch.setattr("sys.stderr", None)
    warn("这条不该让进程崩掉")  # 不抛异常即通过


def test_warn_survives_encoding_error(monkeypatch):
    from lcode.resident.safeio import warn

    class _Gbk:
        encoding = "gbk"

        def write(self, _text):
            raise UnicodeEncodeError("gbk", "x", 0, 1, "illegal multibyte sequence")

        def flush(self):
            pass

    monkeypatch.setattr("sys.stderr", _Gbk())
    warn("带 emoji 的提示 🚀")  # GBK 终端写不出去也不能崩


def test_warn_writes_to_stderr(capsys):
    from lcode.resident.safeio import warn

    warn("正常提示")
    assert "正常提示" in capsys.readouterr().err


async def test_daemon_log_does_not_crash_without_stderr(tmp_path, monkeypatch):
    # pythonw 场景：日志文件必须照写，stderr 缺失只能让终端提示消失。
    monkeypatch.setattr("sys.stderr", None)
    daemon = _daemon(tmp_path)
    daemon.log("pythonw 下也要落盘")
    log = tmp_path / ".lcode" / "resident" / "daemon.log"
    assert "pythonw 下也要落盘" in log.read_text(encoding="utf-8")


# ── cli surface ──────────────────────────────────────────────────────────


def test_cli_parses_resident_flags():
    from lcode.cli import _build_parser

    args = _build_parser().parse_args(
        ["--resident-once", "--interval", "5", "--resident-mode", "acceptEdits"]
    )
    assert args.resident_once is True
    assert args.interval == 5.0
    assert args.resident_mode == "acceptEdits"


def test_cli_parses_enqueue_and_tasks():
    from lcode.cli import _build_parser

    args = _build_parser().parse_args(["--enqueue", "做点事"])
    assert args.enqueue == "做点事"
    assert _build_parser().parse_args(["--tasks"]).resident_tasks is True


def test_cli_resident_batch_defaults_and_override():
    from lcode.cli import _build_parser

    assert _build_parser().parse_args([]).resident_batch == 5
    assert _build_parser().parse_args(["--resident-batch", "2"]).resident_batch == 2


# ── 拒绝文案：常驻模式不能说「用户拒绝」 ──────────────────────────────────


def test_default_denied_text_is_backwards_compatible():
    from lcode.agent import DEFAULT_DENIED_TEXT

    assert DEFAULT_DENIED_TEXT == "用户拒绝执行"


def test_resident_denied_text_explains_absence_of_human():
    from lcode.resident.runner import RESIDENT_DENIED_TEXT

    # 不能把「没人在场」说成「用户否决」，否则模型会误判并重试。
    assert "用户拒绝" not in RESIDENT_DENIED_TEXT
    assert "无人" in RESIDENT_DENIED_TEXT or "没有人在场" in RESIDENT_DENIED_TEXT
    assert "权限" in RESIDENT_DENIED_TEXT


def test_resident_system_suffix_warns_about_no_approval():
    from lcode.resident.runner import RESIDENT_SYSTEM_SUFFIX

    assert "无人值守" in RESIDENT_SYSTEM_SUFFIX
    assert "自动拒绝" in RESIDENT_SYSTEM_SUFFIX
    # 必须明确禁止假装成功，这是无人值守最容易出的问题。
    assert "不要" in RESIDENT_SYSTEM_SUFFIX


def test_agent_accepts_custom_denied_text():
    import inspect

    from lcode.agent import DEFAULT_DENIED_TEXT, Agent

    sig = inspect.signature(Agent.__init__)
    assert sig.parameters["denied_text"].default == DEFAULT_DENIED_TEXT


def test_task_defaults_are_sane():
    task = Task(id="", prompt="x")
    assert task.status == PENDING
    assert task.created
    assert task.id.startswith("job_")


# ── 收件箱 → 队列 → 回信 ──────────────────────────────────────────────────


def _mail_cfg(**kwargs):
    from lcode.mailbox import MailConfig

    base = {
        "enabled": True,
        "imap_host": "imap.qq.com",
        "user": "me@qq.com",
        "auth_code": "secret",
        "senders": ["k@qq.com"],
    }
    base.update(kwargs)
    return MailConfig(**base).normalized()


def _mail(uid="1", sender="k@qq.com", subject="[Lcode] 跑测试", body="请跑一下"):
    from lcode.mailbox import IncomingMail

    tail = subject[len("[Lcode]") :].strip() if subject.startswith("[Lcode]") else subject
    return IncomingMail(
        uid=uid,
        sender=sender,
        subject=subject,
        body=body,
        message_id=f"<{uid}@qq.com>",
        instruction="\n\n".join(part for part in (tail, body) if part),
    )


def _mail_daemon(tmp_path, *, fetch_result=None, allow_write=False, reply=True):
    """构造一个带假收件箱的 daemon：不联网、不发真邮件。"""
    from lcode.mailbox import FetchResult as _FetchResult
    from lcode.mailbox import MailConfig
    from lcode.notify import EmailConfig

    daemon = _daemon(tmp_path, mail=True, mail_allow_write=allow_write)
    daemon._mail_cfg = MailConfig(  # noqa: SLF001
        enabled=True,
        imap_host="imap.qq.com",
        user="me@qq.com",
        auth_code="secret",
        senders=["k@qq.com"],
        allow_write=allow_write,
    ).normalized()
    daemon._reply_cfg = (  # noqa: SLF001
        EmailConfig(
            enabled=True,
            smtp_host="smtp.qq.com",
            user="me@qq.com",
            auth_code="secret",
            to="me@qq.com",
        )
        if reply
        else None
    )
    result = fetch_result if fetch_result is not None else _FetchResult((_mail(),), ())
    return daemon, result


async def test_mail_ingested_as_task(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    daemon, result = _mail_daemon(tmp_path)
    monkeypatch.setattr(mailbox_mod, "fetch_new", lambda _cfg, skip_ids=None: result)
    daemon._runner = _StubRunner("跑完了")  # noqa: SLF001
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)

    assert await daemon.run_once() == 1
    task = daemon.queue.load()[0]
    assert task.origin == "mail"
    assert task.reply_to == "k@qq.com"
    assert "跑测试" in task.prompt
    assert task.status == DONE


async def test_mail_task_runs_read_only(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod
    from lcode.permission import Mode

    daemon, result = _mail_daemon(tmp_path)
    monkeypatch.setattr(mailbox_mod, "fetch_new", lambda _cfg, skip_ids=None: result)
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)
    stub = _StubRunner("ok")
    daemon._runner = stub  # noqa: SLF001

    await daemon.run_once()
    # 邮件正文是不可信输入：即使 --resident-mode bypassPermissions 也必须降级。
    assert stub.modes == [Mode.DEFAULT]


async def test_mail_task_read_only_can_be_opened(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    daemon, result = _mail_daemon(tmp_path, allow_write=True)
    monkeypatch.setattr(mailbox_mod, "fetch_new", lambda _cfg, skip_ids=None: result)
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)
    stub = _StubRunner("ok")
    daemon._runner = stub  # noqa: SLF001

    await daemon.run_once()
    assert stub.modes == [None]  # 用 daemon 的默认模式


async def test_mail_reply_sent_with_thread_headers(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    daemon, result = _mail_daemon(tmp_path)
    monkeypatch.setattr(mailbox_mod, "fetch_new", lambda _cfg, skip_ids=None: result)
    daemon._runner = _StubRunner("## 结果\n\n7 个测试通过")  # noqa: SLF001

    sent: list[dict] = []

    def fake_send(cfg, to, subject, body, in_reply_to=""):
        sent.append({"to": to, "subject": subject, "body": body, "thread": in_reply_to})
        return True

    monkeypatch.setattr("lcode.notify.send_mail", fake_send)

    await daemon.run_once()

    assert len(sent) == 1
    assert sent[0]["to"] == "k@qq.com"
    assert sent[0]["subject"].startswith("Re: ")
    assert sent[0]["thread"] == "<1@qq.com>"  # 保持同一会话线程
    assert "7 个测试通过" in sent[0]["body"]
    assert "任务：" in sent[0]["body"]


async def test_mail_reply_on_failure_says_failed(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    daemon, result = _mail_daemon(tmp_path)
    monkeypatch.setattr(mailbox_mod, "fetch_new", lambda _cfg, skip_ids=None: result)
    daemon._runner = _StubRunner(raises=RuntimeError("模型炸了"))  # noqa: SLF001
    sent: list[str] = []
    monkeypatch.setattr(
        "lcode.notify.send_mail",
        lambda cfg, to, subject, body, in_reply_to="": sent.append(body) or True,
    )

    await daemon.run_once()
    # 用户等到一封信却不知道失败了，比没回信更糟。
    assert sent and "失败" in sent[0]


async def test_reply_failure_does_not_break_task(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    daemon, result = _mail_daemon(tmp_path)
    monkeypatch.setattr(mailbox_mod, "fetch_new", lambda _cfg, skip_ids=None: result)
    daemon._runner = _StubRunner("ok")  # noqa: SLF001

    def boom(*args, **kwargs):
        raise RuntimeError("SMTP 炸了")

    monkeypatch.setattr("lcode.notify.send_mail", boom)
    assert await daemon.run_once() == 1
    assert daemon.queue.load()[0].status == DONE


async def test_known_message_ids_passed_to_fetch(tmp_path, monkeypatch):
    """去重靠 Message-ID 而不是已读标记。

    手机 / 网页版邮箱会把信标成已读，靠 UNSEEN 会整封信漏掉；
    Message-ID 才是信本身的身份，队列里有的就不再取。
    """
    from lcode import mailbox as mailbox_mod

    daemon, result = _mail_daemon(tmp_path)
    daemon.queue.enqueue("老任务", origin="mail", message_id="<old@qq.com>")
    passed: list[set[str]] = []

    def fake_fetch(_cfg, skip_ids=None):
        passed.append(set(skip_ids or ()))
        return result

    monkeypatch.setattr(mailbox_mod, "fetch_new", fake_fetch)
    daemon._runner = _StubRunner("ok")  # noqa: SLF001
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)

    await daemon.run_once()
    assert "<old@qq.com>" in passed[0]
    assert daemon.queue.load()[0].origin == "mail"


async def test_duplicate_message_not_enqueued_twice(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    daemon, result = _mail_daemon(tmp_path)
    daemon.queue.enqueue("已存在", origin="mail", message_id="<1@qq.com>")
    monkeypatch.setattr(mailbox_mod, "fetch_new", lambda _cfg, skip_ids=None: result)
    daemon._runner = _StubRunner("ok")  # noqa: SLF001

    assert daemon.ingest_mail() == 0
    assert len(daemon.queue.load()) == 1


async def test_mail_disabled_ingests_nothing(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    daemon = _daemon(tmp_path)  # mail=False
    called: list[int] = []
    monkeypatch.setattr(mailbox_mod, "fetch_new", lambda _cfg, skip_ids=None: called.append(1) or FetchResult())
    assert daemon.ingest_mail() == 0
    assert called == []


async def test_mail_fetch_error_does_not_kill_round(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    daemon, result = _mail_daemon(tmp_path)

    def boom(_cfg, skip_ids=None):
        raise RuntimeError("IMAP 炸了")

    monkeypatch.setattr(mailbox_mod, "fetch_new", boom)
    daemon._runner = _StubRunner("ok")  # noqa: SLF001
    daemon.queue.enqueue("原有任务")

    # 收件箱不可用只该让「没有新邮件」，原有任务照跑。
    assert await daemon.run_once() == 1
    assert daemon.queue.load()[0].status == DONE


async def test_mail_task_skips_email_notification(tmp_path, monkeypatch):
    """邮件任务已经要回信了，不该再发一条「任务完成」通知邮件。

    两个理由：
    1. 同一件事说两遍。
    2. 那条通知的主题没有前缀，会被我们自己的收件箱过滤器退回，
       daemon.log 里每跑一条邮件任务就多一行噪声。
    """
    from lcode import mailbox as mailbox_mod

    daemon, result = _mail_daemon(tmp_path)
    monkeypatch.setattr(mailbox_mod, "fetch_new", lambda _cfg, skip_ids=None: result)
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)
    daemon._runner = _StubRunner("跑完了")  # noqa: SLF001
    fake = _FakeNotifier()
    daemon._notifier = fake  # noqa: SLF001

    await daemon.run_once()

    assert fake.excludes == [("email",)]


async def test_cli_task_keeps_email_notification(tmp_path):
    """命令行入队的任务没有回信，email 通道必须保留。"""
    daemon = _daemon(tmp_path)
    daemon._runner = _StubRunner("跑完了")  # noqa: SLF001
    fake = _FakeNotifier()
    daemon._notifier = fake  # noqa: SLF001
    daemon.queue.enqueue("命令行任务")

    await daemon.run_once()

    assert fake.excludes == [()]


async def test_notify_exclude_helper():
    from lcode.resident.daemon import _notify_exclude
    from lcode.resident.queue import ORIGIN_CLI, ORIGIN_MAIL, Task

    assert _notify_exclude(Task(id="x", prompt="p", origin=ORIGIN_MAIL)) == ("email",)
    assert _notify_exclude(Task(id="x", prompt="p", origin=ORIGIN_CLI)) == ()


def test_cli_parses_mail_flags():
    from lcode.cli import _build_parser

    args = _build_parser().parse_args(["--mail-watch", "--mail-allow-write"])
    assert args.mail_watch is True
    assert args.mail_allow_write is True
    assert _build_parser().parse_args(["--check-mail"]).check_mail is True
    # 默认必须是最保守的：不收信、不允许写。
    defaults = _build_parser().parse_args([])
    assert defaults.mail_watch is False
    assert defaults.check_mail is False
    assert defaults.mail_allow_write is False


# ── 计划任务轮询的轻量预检 ────────────────────────────────────────────────
#
# 每 3 分钟触发一次。完整启动要拉 MCP 子进程（实测 27s vs 预检 1.7s），
# 大部分轮次没有新邮件，不该为此付启动开销。


def test_has_pending_reflects_queue(tmp_path):
    daemon, _ = _mail_daemon(tmp_path)
    assert daemon.has_pending() is False
    daemon.queue.enqueue("任务")
    assert daemon.has_pending() is True


def test_idle_mail_poll_skips_runner(tmp_path, monkeypatch):
    """没有新邮件也没有待办任务时，不该启动 TaskRunner。

    为什么要测：计划任务每 3 分钟触发一次，完整启动要拉 MCP 子进程
    （实测 27s，轻量预检 1.7s）。不跳过的话一天要白烧几小时 CPU。
    """
    import asyncio

    from lcode import mailbox as mailbox_mod
    from lcode.cli import _build_parser, _run_resident

    empty = mailbox_mod.FetchResult()
    monkeypatch.setattr(mailbox_mod, "fetch_new", lambda _cfg, skip_ids=None: empty)

    started: list[int] = []

    async def fake_start(self):
        started.append(1)
        self._runner = None

    monkeypatch.setattr("lcode.resident.daemon.Daemon.start", fake_start)

    args = _build_parser().parse_args(["--resident-once", "--mail-watch"])
    monkeypatch.chdir(tmp_path)
    # 让 daemon 建到 tmp_path 下，不碰真实项目队列。
    code = asyncio.run(_run_resident(_minimal_config(), args))

    assert code == 0
    assert started == []  # 预检拦截，没有进 start()


def _minimal_config():
    from lcode.config import Config, ProviderConfig

    return Config(
        providers=[ProviderConfig(name="p", protocol="openai", api_key="k", model="m")]
    )
