"""回信与话题续接（邮件作为对话通道）。

用户的原话是「好像不能回信」和「只能从主题写内容」。这个文件把两条链路钉住：

1. 回复同一话题的信，必须带上之前的问答——否则 Agent 每轮都像失忆；
2. 回信发不出去时，必须用桌面 / webhook 兜底通知——否则结果彻底黑洞。

正文解析本身（空 plain 回退 html、HTML 实体、引用块剥离）在 test_mailbox.py。
"""

from __future__ import annotations

import pytest

from lcode.resident.daemon import Daemon, DaemonOptions
from lcode.resident.queue import Task, TaskQueue

# ── 复用 test_resident.py 里的替身 ────────────────────────────────────────


class _StubRunner:
    """替身：不碰真实 LLM，只记录被调用的 prompt 与权限模式。"""

    def __init__(self, result: str = "ok", raises: Exception | None = None) -> None:
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


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str]] = []
        self.excludes: list[tuple[str, ...]] = []

    def send(self, title, message, level="info", source="", exclude=()):
        self.sent.append((title, message, level, source))
        self.excludes.append(tuple(exclude))
        return ["log"]


def _daemon(tmp_path, **kwargs) -> Daemon:
    from lcode.config import Config, ProviderConfig

    cfg = Config(
        providers=[ProviderConfig(name="p", protocol="openai", api_key="k", model="m")]
    )
    kwargs.setdefault("notify", False)  # 测试里绝不能真弹系统横幅
    return Daemon(cfg, DaemonOptions(root=str(tmp_path), quiet=True, **kwargs))


def _queue(tmp_path) -> TaskQueue:
    return TaskQueue(tmp_path / "resident")


def _write_notify_yaml(root, *, enabled=True):
    (root / ".lcode").mkdir(parents=True, exist_ok=True)
    (root / ".lcode" / "notify.yaml").write_text(
        "email:\n"
        f"  enabled: {'true' if enabled else 'false'}\n"
        "  smtp_host: smtp.qq.com\n"
        "  user: me@qq.com\n"
        "  auth_code: secret\n"
        "  to: me@qq.com\n",
        encoding="utf-8",
    )


def _mail_daemon(tmp_path, *, allow_write=False, reply=True):
    """带假收件箱的 daemon：不联网、不发真邮件。"""
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
    return daemon


def _incoming(*, uid="1", subject="[Lcode] 跑测试", body="请跑一下", thread="跑测试"):
    from lcode.mailbox import IncomingMail

    return IncomingMail(
        uid=uid,
        sender="k@qq.com",
        subject=subject,
        body=body,
        message_id=f"<{uid}@qq.com>",
        instruction=f"{thread}\n\n{body}",
        thread_key=thread,
    )


def _fetch_returning(mails):
    from lcode.mailbox import FetchResult

    return lambda _cfg, skip_ids=None: FetchResult(tuple(mails), ())


# ── 队列层：话题历史与结果回读 ────────────────────────────────────────────


def test_thread_history_only_includes_done(tmp_path):
    queue = _queue(tmp_path)
    done = queue.enqueue("第一问", origin="mail", thread="t1")
    queue.mark_done(done.id, "", "答一")
    queue.enqueue("第二问", origin="mail", thread="t1")  # pending，还没答
    queue.enqueue("别的活", origin="mail", thread="t2")

    history = queue.thread_history("t1")
    # 只取已完成的：待办和失败的任务没有可用的回答，喂进去只会误导。
    assert [task.prompt for task in history] == ["第一问"]


def test_thread_history_excludes_self(tmp_path):
    queue = _queue(tmp_path)
    task = queue.enqueue("问题", origin="mail", thread="t1")
    queue.mark_done(task.id, "", "答案")
    assert queue.thread_history("t1", exclude_id=task.id) == []


def test_thread_history_respects_limit(tmp_path):
    queue = _queue(tmp_path)
    for index in range(5):
        task = queue.enqueue(f"第{index}问", origin="mail", thread="t1")
        queue.mark_done(task.id, "", f"第{index}答")
    # 聊了一周的话题不能把每封新邮件的上下文都撑爆。
    assert len(queue.thread_history("t1", limit=2)) == 2


def test_thread_history_without_thread_is_empty(tmp_path):
    queue = _queue(tmp_path)
    task = queue.enqueue("无话题", origin="mail")
    queue.mark_done(task.id, "", "答")
    assert queue.thread_history("") == []


def test_result_text_prefers_full_result_over_summary(tmp_path):
    queue = _queue(tmp_path)
    task = queue.enqueue("问题")
    path = queue.write_result(task.id, "## 结论\n\n完整回答\n第二行", "问题")
    queue.mark_done(task.id, path, "完整回答\n第二行")
    loaded = queue.load()[0]
    text = queue.result_text(loaded)
    # 摘要往往只有一句，追问时模型需要的是上一轮到底答了什么。
    assert "完整回答" in text
    assert "第二行" in text


def test_result_text_falls_back_to_summary(tmp_path):
    queue = _queue(tmp_path)
    task = queue.enqueue("问题")
    queue.mark_done(task.id, str(tmp_path / "missing.md"), "只有摘要可用")
    assert queue.result_text(queue.load()[0]) == "只有摘要可用"


def test_result_text_truncates(tmp_path):
    queue = _queue(tmp_path)
    task = queue.enqueue("问题")
    path = queue.write_result(task.id, "字" * 5000, "问题")
    queue.mark_done(task.id, path, "x")
    assert len(queue.result_text(queue.load()[0], limit=100)) <= 100


def test_task_thread_survives_restart(tmp_path):
    queue = _queue(tmp_path)
    queue.enqueue("问题", origin="mail", thread="跑测试")
    # 换个实例读盘 = 模拟进程重启；thread 是回信续接的唯一锚点，丢了就断线。
    assert TaskQueue(tmp_path / "resident").load()[0].thread == "跑测试"


# ── 话题续接 ──────────────────────────────────────────────────────────────


async def test_followup_email_carries_previous_qa(tmp_path, monkeypatch):
    """回信追问必须带上上一轮问答。

    没有这个，用户从手机上回复一句「那覆盖率呢」，Agent 会当成全新任务
    从零开始——表现就是「它不记得刚才说过什么」。
    """
    from lcode import mailbox as mailbox_mod

    daemon = _mail_daemon(tmp_path, reply=False)
    old = daemon.queue.enqueue("第一次提问", origin="mail", thread="跑测试")
    path = daemon.queue.write_result(old.id, "第一次的回答内容", "第一次提问")
    daemon.queue.mark_done(old.id, path, "第一次的回答内容")

    followup = _incoming(uid="2", subject="Re: [Lcode] 跑测试", body="那覆盖率呢")
    monkeypatch.setattr(mailbox_mod, "fetch_new", _fetch_returning([followup]))
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)
    stub = _StubRunner("覆盖率 87%")
    daemon._runner = stub  # noqa: SLF001

    await daemon.run_once()

    prompt = stub.prompts[0]
    assert "第一次提问" in prompt
    assert "第一次的回答内容" in prompt
    assert "那覆盖率呢" in prompt
    # 本次消息要单独标出来，否则模型分不清哪句是要回答的。
    assert "本次新消息" in prompt


async def test_fresh_email_has_no_thread_context(tmp_path, monkeypatch):
    """新话题不能被别的对话污染。"""
    from lcode import mailbox as mailbox_mod

    daemon = _mail_daemon(tmp_path, reply=False)
    old = daemon.queue.enqueue("完全无关的历史", origin="mail", thread="别的事")
    daemon.queue.mark_done(old.id, "", "历史回答")

    monkeypatch.setattr(mailbox_mod, "fetch_new", _fetch_returning([_incoming(thread="新话题")]))
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)
    stub = _StubRunner("ok")
    daemon._runner = stub  # noqa: SLF001

    await daemon.run_once()
    assert "完全无关的历史" not in stub.prompts[0]


async def test_ingest_records_thread_key(tmp_path, monkeypatch):
    """分拣出的 thread_key 必须落到任务上，否则续接整条链路断在第一环。"""
    from lcode import mailbox as mailbox_mod

    daemon = _mail_daemon(tmp_path, reply=False)
    monkeypatch.setattr(mailbox_mod, "fetch_new", _fetch_returning([_incoming(thread="跑测试")]))
    daemon._runner = _StubRunner("ok")  # noqa: SLF001

    daemon.ingest_mail()  # 同步方法：只入队，不执行
    assert daemon.queue.load()[0].thread == "跑测试"


async def test_thread_context_is_bounded(tmp_path, monkeypatch):
    """上下文有上限：长对话不能把每封新邮件都撑成几万字。"""
    from lcode import mailbox as mailbox_mod

    daemon = _mail_daemon(tmp_path, reply=False)
    for index in range(6):
        old = daemon.queue.enqueue(f"第{index}问" + "细节" * 300, origin="mail", thread="t1")
        path = daemon.queue.write_result(old.id, "答" * 3000, "问")
        daemon.queue.mark_done(old.id, path, "答")

    monkeypatch.setattr(mailbox_mod, "fetch_new", _fetch_returning([_incoming(thread="t1")]))
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)
    stub = _StubRunner("ok")
    daemon._runner = stub  # noqa: SLF001

    await daemon.run_once()
    from lcode.resident.daemon import (
        THREAD_ANSWER_LIMIT,
        THREAD_HISTORY_LIMIT,
        THREAD_PROMPT_LIMIT,
    )

    budget = THREAD_HISTORY_LIMIT * (THREAD_PROMPT_LIMIT + THREAD_ANSWER_LIMIT) + 500
    assert len(stub.prompts[0]) < budget


# ── 回信可达性 ────────────────────────────────────────────────────────────


async def test_reply_works_without_mail_watch(tmp_path, monkeypatch):
    """--check-mail 入队 + --resident-once 执行（不带 --mail-watch）也必须回信。

    这是「好像不能回信」的一个真实成因：收信和执行是分开调度的，执行那侧
    opts.mail=False，早期实现因此根本没加载 SMTP 配置，每封邮件任务都静默不回。
    """
    daemon = _daemon(tmp_path)  # mail=False
    assert daemon._reply_cfg is None  # noqa: SLF001
    _write_notify_yaml(tmp_path)

    sent: list[str] = []
    monkeypatch.setattr(
        "lcode.notify.send_mail",
        lambda cfg, to, subject, body, in_reply_to="": sent.append(to) or True,
    )
    daemon._runner = _StubRunner("跑完了")  # noqa: SLF001
    daemon.queue.enqueue("邮件任务", origin="mail", reply_to="k@qq.com")

    await daemon.run_once()
    assert sent == ["k@qq.com"]


async def test_reply_failure_sends_fallback_notification(tmp_path, monkeypatch):
    """回信失败必须用非邮件通道喊一声。

    SMTP 挂了、授权码过期时如果只是安静返回 False，用户会以为 Agent 没理他，
    而这恰恰是最该被知道的情况。
    """
    from lcode import mailbox as mailbox_mod

    daemon = _mail_daemon(tmp_path)
    monkeypatch.setattr(mailbox_mod, "fetch_new", _fetch_returning([_incoming()]))
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: False)
    daemon._runner = _StubRunner("跑完了")  # noqa: SLF001
    fake = _FakeNotifier()
    daemon._notifier = fake  # noqa: SLF001

    await daemon.run_once()

    titles = [item[0] for item in fake.sent]
    assert any("回信失败" in title for title in titles)
    # 兜底通知不能再走 email：刚试过，坏的就是它。
    assert ("email",) in fake.excludes


async def test_reply_success_sends_no_fallback(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    daemon = _mail_daemon(tmp_path)
    monkeypatch.setattr(mailbox_mod, "fetch_new", _fetch_returning([_incoming()]))
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)
    daemon._runner = _StubRunner("跑完了")  # noqa: SLF001
    fake = _FakeNotifier()
    daemon._notifier = fake  # noqa: SLF001

    await daemon.run_once()
    assert not any("回信失败" in item[0] for item in fake.sent)


def test_reply_body_tells_user_they_can_follow_up(tmp_path, monkeypatch):
    """回信里要说明「直接回复就能追问」，否则没人知道能这么用。"""
    daemon = _mail_daemon(tmp_path)
    sent: list[str] = []
    monkeypatch.setattr(
        "lcode.notify.send_mail",
        lambda cfg, to, subject, body, in_reply_to="": sent.append(body) or True,
    )
    daemon.reply(
        Task(
            id="job_1",
            prompt="问",
            origin="mail",
            reply_to="k@qq.com",
            subject="[Lcode] 问",
            message_id="<m@qq.com>",
        ),
        "答",
    )
    assert sent and "追问" in sent[0]


def test_reply_without_account_logs_reason(tmp_path, caplog):
    """没配 SMTP 时要写进日志，而不是静默返回 False。"""
    daemon = _mail_daemon(tmp_path)
    daemon._reply_cfg = None  # noqa: SLF001 - 模拟配置缺失
    task = Task(id="job_1", prompt="问", origin="mail", reply_to="k@qq.com")
    assert daemon.reply(task, "答") is False
    log = (tmp_path / ".lcode" / "resident" / "daemon.log").read_text(encoding="utf-8")
    assert "无法回信" in log


# ── 权限参数 ──────────────────────────────────────────────────────────────


def test_mail_allow_write_flag_overrides_config(tmp_path):
    """--mail-allow-write 必须真的生效。

    早期它只存在 DaemonOptions 里，从没被读取过——配置文件里的 allow_write
    才是唯一来源。于是用户加了这个参数、还在邮件里说「我给你权限」，
    写文件照样被拒，看起来就像参数没接线。
    """
    daemon = _daemon(tmp_path, mail=True, mail_allow_write=True)
    cfg = daemon._ensure_mail_cfg()
    assert cfg is not None and cfg.allow_write is True


async def test_mail_allow_write_makes_task_use_daemon_mode(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    daemon = _mail_daemon(tmp_path, allow_write=True)
    monkeypatch.setattr(mailbox_mod, "fetch_new", _fetch_returning([_incoming()]))
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)
    stub = _StubRunner("ok")
    daemon._runner = stub  # noqa: SLF001

    await daemon.run_once()
    assert stub.modes == [None]


async def test_mail_task_still_read_only_by_default(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod
    from lcode.permission import Mode

    daemon = _mail_daemon(tmp_path)
    monkeypatch.setattr(mailbox_mod, "fetch_new", _fetch_returning([_incoming()]))
    monkeypatch.setattr("lcode.notify.send_mail", lambda *a, **k: True)
    stub = _StubRunner("ok")
    daemon._runner = stub  # noqa: SLF001

    await daemon.run_once()
    assert stub.modes == [Mode.DEFAULT]


# ── CLI 开关 ──────────────────────────────────────────────────────────────


def test_cli_parses_mail_peek_flag():
    from lcode.cli import _build_parser

    args = _build_parser().parse_args(["--mail-peek", "--mail-limit", "3"])
    assert args.mail_peek is True
    assert args.mail_limit == 3
    assert _build_parser().parse_args([]).mail_peek is False


@pytest.mark.parametrize("flag", ["--check-mail", "--mail-peek"])
def test_diagnostic_flags_do_not_start_runner(tmp_path, monkeypatch, flag):
    """两个诊断入口都不启动 Agent：不碰模型、不发通知。"""
    import asyncio

    from lcode.cli import _amain, _build_parser

    _write_notify_yaml(tmp_path)
    monkeypatch.chdir(tmp_path)

    from lcode.mailbox import MailConfig

    monkeypatch.setattr(
        "lcode.mailbox.load_config",
        lambda _root: MailConfig(
            enabled=True,
            imap_host="imap.qq.com",
            user="me@qq.com",
            auth_code="x",
            senders=["k@qq.com"],
        ).normalized(),
    )
    monkeypatch.setattr("lcode.mailbox.fetch_new", _fetch_returning([_incoming()]))

    args = _build_parser().parse_args([flag])
    assert asyncio.run(_amain(args)) == 0
    # --check-mail 的职责就是入队（所以队列会存在）；--mail-peek 只诊断，不该留痕。
    queue_path = tmp_path / ".lcode" / "resident" / "queue.json"
    if flag == "--check-mail":
        assert queue_path.exists()
    else:
        assert not queue_path.exists()
