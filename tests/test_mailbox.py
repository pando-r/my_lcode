"""收件箱（IMAP）的测试。

重点不是「能连上 QQ 邮箱」，而是四个判定：
1. 三道闸门都挡住不该进来的邮件；
2. 解析能处理中文主题 / multipart / HTML；
3. 队列层能记住回信地址，并挡住重复投递；
4. 邮件来源的任务默认只读。
"""

from __future__ import annotations

import email.message

import pytest

from lcode import mailbox as mailbox_mod
from lcode.mailbox import (
    FetchResult,
    IncomingMail,
    MailConfig,
    Rejected,
    classify,
    extract_body,
    is_allowed,
    load_config,
    strip_prefix,
    to_incoming,
)

# ── 地址与主题过滤 ────────────────────────────────────────────────────────


def test_normalize_address_keeps_only_mailbox():
    assert mailbox_mod._normalize_address("K <K@QQ.com>") == "k@qq.com"
    assert mailbox_mod._normalize_address("  a@b.com  ") == "a@b.com"
    assert mailbox_mod._normalize_address("") == ""


def test_allowed_exact_match():
    assert is_allowed("3952613547@qq.com", ["3952613547@qq.com"]) is True
    assert is_allowed("evil@qq.com", ["3952613547@qq.com"]) is False


def test_allowed_is_case_and_display_name_insensitive():
    # From 头常写成「昵称 <地址>」，也可能大小写乱来。
    assert is_allowed("K <K@QQ.COM>", ["k@qq.com"]) is True


def test_allowed_domain_wildcard():
    assert is_allowed("anyone@qq.com", ["@qq.com"]) is True
    assert is_allowed("anyone@evil.com", ["@qq.com"]) is False


def test_empty_whitelist_rejects_everything():
    # 忘记配置时必须失败关闭，而不是全部放行。
    assert is_allowed("k@qq.com", []) is False
    assert is_allowed("", ["k@qq.com"]) is False


def test_strip_prefix_requires_exact_prefix():
    assert strip_prefix("[Lcode] 看下日志", "[Lcode]") == "看下日志"
    assert strip_prefix("[lcode] 看下日志", "[Lcode]") == "看下日志"
    assert strip_prefix("看下日志", "[Lcode]") == ""


def test_strip_prefix_without_prefix_configured():
    assert strip_prefix("随便什么", "") == "随便什么"


# 回复我的邮件是最自然的交互方式，主题会多出 Re:，不能因此拒收。
@pytest.mark.parametrize(
    "subject",
    [
        "[Lcode] 跑测试",
        "Re: [Lcode] 跑测试",
        "RE: [Lcode] 跑测试",
        "Re: Re: [Lcode] 跑测试",
        "回复: [Lcode] 跑测试",
        "答复：[Lcode] 跑测试",
    ],
)
def test_reply_subjects_still_match_prefix(subject):
    assert mailbox_mod.has_prefix(subject, "[Lcode]") is True
    tail = strip_prefix(subject, "[Lcode]")
    assert tail == "跑测试"


def test_reply_prefix_does_not_create_false_positive():
    # 只有回复标记、没有前缀，仍然要拒收。
    assert mailbox_mod.has_prefix("Re: 普通邮件", "[Lcode]") is False
    assert strip_prefix("Re: 普通邮件", "[Lcode]") == ""


def test_strip_reply_prefix_keeps_inner_text():
    assert mailbox_mod.strip_reply_prefix("Re: 你好") == "你好"
    assert mailbox_mod.strip_reply_prefix("没有任何前缀") == "没有任何前缀"


# ── 回信循环防护（RFC 3834）───────────────────────────────────────────────
#
# 我们自己发出的回信带 Auto-Submitted: auto-replied，而它会落进同一个收件箱。
# 放行前缀修复之后，Re: [Lcode] x 会匹配前缀 → 变成任务 → 再回信 → 死循环。


def test_auto_submitted_detects_auto_reply():
    assert mailbox_mod.is_auto_submitted("auto-replied") is True
    assert mailbox_mod.is_auto_submitted("auto-generated") is True
    assert mailbox_mod.is_auto_submitted("Auto-Replied") is True
    # "no" 是人工发送的显式声明，必须放行。
    assert mailbox_mod.is_auto_submitted("no") is False
    assert mailbox_mod.is_auto_submitted("") is False


def test_classify_rejects_auto_submitted_mail():
    mail = IncomingMail(
        uid="21",
        sender="k@qq.com",
        subject="Re: [Lcode] append 自测",
        body="收到，收信链路正常。",
        auto_submitted="auto-replied",
    )
    result = classify([mail], _cfg())
    # 即使主题能匹配前缀、发件人在白名单里，自动回复也不能当任务。
    assert result.accepted == ()
    assert "自动回复" in result.rejected[0].reason


def test_to_incoming_reads_auto_submitted_header():
    message = email.message.EmailMessage()
    message["From"] = "k@qq.com"
    message["Subject"] = "Re: [Lcode] x"
    message["Auto-Submitted"] = "auto-replied"
    message.set_content("正文")
    assert to_incoming("1", bytes(message)).auto_submitted == "auto-replied"


def test_human_reply_still_accepted():
    # 人工回复不带 Auto-Submitted，必须正常处理——这是修前缀的初衷。
    mail = IncomingMail(
        uid="22",
        sender="k@qq.com",
        subject="Re: [Lcode] 跑测试",
        body="顺便看下覆盖率",
    )
    result = classify([mail], _cfg())
    assert len(result.accepted) == 1
    assert "跑测试" in result.accepted[0].prompt()


# ── 解析 ──────────────────────────────────────────────────────────────────


def _build_message(
    *,
    sender: str = "K <k@qq.com>",
    subject: str = "[Lcode] 你好",
    body: str = "正文",
    message_id: str = "<abc@qq.com>",
    html: bool = False,
) -> bytes:
    message = email.message.EmailMessage()
    message["From"] = sender
    message["To"] = "me@qq.com"
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(body, subtype="html" if html else "plain")
    return bytes(message)


def test_to_incoming_extracts_fields():
    mail = to_incoming("7", _build_message())
    assert mail.uid == "7"
    assert mail.sender == "k@qq.com"
    assert mail.subject == "[Lcode] 你好"
    assert mail.body.strip() == "正文"
    assert mail.message_id == "<abc@qq.com>"


def test_to_incoming_decodes_chinese_subject():
    # 真实邮件里中文主题是 RFC 2047 编码的，不解析会变成 =?utf-8?b?...?=
    raw = _build_message(subject="[Lcode] 今天天气很好")
    mail = to_incoming("1", raw)
    assert "今天天气很好" in mail.subject


def test_extract_body_prefers_plain_over_html():
    message = email.message.EmailMessage()
    message["From"] = "k@qq.com"
    message["Subject"] = "[Lcode] x"
    message.set_content("纯文本版本")
    message.add_alternative("<p>HTML 版本</p>", subtype="html")
    assert "纯文本版本" in extract_body(message)


def test_extract_body_strips_html_when_only_html():
    message = email.message.EmailMessage()
    message["From"] = "k@qq.com"
    message["Subject"] = "[Lcode] x"
    message.set_content("<p>只有 <b>HTML</b></p>", subtype="html")
    body = extract_body(message)
    assert "<p>" not in body
    assert "只有" in body and "HTML" in body


def test_extract_body_handles_gbk_payload():
    # 国内邮箱常见的 GBK 编码，解错就是一堆乱码。
    message = email.message.EmailMessage()
    message["From"] = "k@qq.com"
    message["Subject"] = "[Lcode] x"
    message.set_payload("中文正文".encode("gb18030"))
    message.set_param("charset", "gb18030")
    assert "中文正文" in extract_body(message)


# ── 分拣 ──────────────────────────────────────────────────────────────────


def _cfg(**kwargs) -> MailConfig:
    base = {
        "enabled": True,
        "imap_host": "imap.qq.com",
        "user": "me@qq.com",
        "auth_code": "secret",
        "senders": ["k@qq.com"],
    }
    base.update(kwargs)
    return MailConfig(**base).normalized()


def _mail(sender: str = "k@qq.com", subject: str = "[Lcode] 跑测试", body: str = "请跑") -> IncomingMail:
    return IncomingMail(uid="1", sender=sender, subject=subject, body=body)


def test_classify_accepts_matching_mail():
    result = classify([_mail()], _cfg())
    assert len(result.accepted) == 1
    assert result.rejected == ()


def test_classify_builds_instruction_from_subject_and_body():
    result = classify([_mail(subject="[Lcode] 跑测试", body="顺便看下覆盖率")], _cfg())
    instruction = result.accepted[0].prompt()
    # 主题去掉前缀 + 正文，两段都要在，否则会丢掉用户的意图。
    assert "跑测试" in instruction
    assert "顺便看下覆盖率" in instruction
    assert "[Lcode]" not in instruction


def test_classify_rejects_unknown_sender():
    result = classify([_mail(sender="stranger@evil.com")], _cfg())
    assert result.accepted == ()
    assert result.rejected[0].reason == "发件人不在白名单"


def test_classify_rejects_missing_prefix():
    result = classify([_mail(subject="普通邮件")], _cfg())
    assert result.accepted == ()
    assert result.rejected[0].reason == "主题缺少前缀"


def test_classify_rejects_empty_body():
    result = classify([_mail(subject="[Lcode]", body="   ")], _cfg())
    assert result.accepted == ()
    assert result.rejected[0].reason == "正文为空"


def test_classify_caps_per_poll():
    mails = [_mail() for _ in range(10)]
    result = classify(mails, _cfg(max_per_poll=3))
    assert len(result.accepted) == 3


def test_classify_mixed_batch_keeps_good_and_reports_bad():
    mails = [_mail(), _mail(sender="x@evil.com"), _mail(subject="无前缀")]
    result = classify(mails, _cfg())
    assert len(result.accepted) == 1
    assert len(result.rejected) == 2


# ── 配置 ──────────────────────────────────────────────────────────────────


def test_config_missing_file_is_disabled(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.enabled is False
    assert cfg.ready() is False


def test_config_reads_yaml(tmp_path):
    path = tmp_path / ".lcode" / "mail.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "enabled: true\n"
        "imap_host: imap.163.com\n"
        "senders:\n  - K <k@qq.com>\n"
        "subject_prefix: '[Lcode]'\n"
        "allow_write: true\n"
        "max_per_poll: 2\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.enabled is True
    assert cfg.imap_host == "imap.163.com"
    assert cfg.senders == ["k@qq.com"]  # 昵称已被剥掉
    assert cfg.allow_write is True
    assert cfg.max_per_poll == 2


def test_config_accepts_comma_separated_senders(tmp_path):
    path = tmp_path / ".lcode" / "mail.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("senders: 'a@x.com, b@y.com'\n", encoding="utf-8")
    assert load_config(tmp_path).senders == ["a@x.com", "b@y.com"]


def test_config_inherits_account_from_notify_yaml(tmp_path):
    (tmp_path / ".lcode").mkdir(parents=True)
    (tmp_path / ".lcode" / "notify.yaml").write_text(
        "email:\n"
        "  enabled: true\n"
        "  smtp_host: smtp.qq.com\n"
        "  user: k@qq.com\n"
        "  auth_code: inherited-secret\n"
        "  to: k@qq.com\n",
        encoding="utf-8",
    )
    (tmp_path / ".lcode" / "mail.yaml").write_text(
        "enabled: true\nsenders:\n  - k@qq.com\n", encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    # 同一个账号同一个授权码，让人填两遍只会填错一遍。
    assert cfg.user == "k@qq.com"
    assert cfg.auth_code == "inherited-secret"
    assert cfg.ready() is True


def test_ready_requires_whitelist(tmp_path):
    cfg = MailConfig(enabled=True, imap_host="imap.qq.com", user="a@b.com", auth_code="x")
    assert cfg.normalized().ready() is False


# ── IMAP IO（用假客户端，不联网）─────────────────────────────────────────


class _FakeImap:
    def __init__(self, messages: dict[str, bytes], *, fail_login: bool = False) -> None:
        self._messages = messages
        self._fail_login = fail_login
        self.stored: list[tuple[str, str]] = []
        self.fetched: list[tuple[str, str]] = []
        self.search_args: tuple = ()
        self.logged_out = False

    def login(self, user, password):
        if self._fail_login:
            raise mailbox_mod.imaplib.IMAP4.error("LOGIN failed")

    def select(self, folder, readonly=False):
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        # 现在用 `SINCE <date>` 而不是 `UNSEEN`：已读状态不可靠。
        self.search_args = criteria
        return "OK", [b" ".join(uid.encode() for uid in self._messages)]

    def fetch(self, uid, spec):
        spec_text = spec.decode() if isinstance(spec, bytes) else str(spec)
        self.fetched.append((uid, spec_text))
        raw = self._messages.get(uid)
        if raw is None:
            return "NO", [None]
        return "OK", [(b"1 (RFC822 {123}", raw), b")"]

    def store(self, uid, flag, value):
        self.stored.append((uid, value))
        return "OK", [b""]

    def logout(self):
        self.logged_out = True


def _patch_imap(monkeypatch, client) -> None:
    monkeypatch.setattr(mailbox_mod.imaplib, "IMAP4_SSL", lambda *a, **k: client)


def test_fetch_new_returns_accepted(monkeypatch):
    client = _FakeImap({"1": _build_message()})
    _patch_imap(monkeypatch, client)
    result = mailbox_mod.fetch_new(_cfg())
    assert isinstance(result, FetchResult)
    assert len(result.accepted) == 1
    assert client.logged_out is True


def test_fetch_new_searches_by_date_not_unseen(monkeypatch):
    """按日期发现，而不是按未读。

    已有的信在我们读之前就可能被手机标记成已读——已实测因此漏掉三封。
    """
    client = _FakeImap({"1": _build_message()})
    _patch_imap(monkeypatch, client)
    mailbox_mod.fetch_new(_cfg())
    assert client.search_args[0] == "SINCE"
    assert "UNSEEN" not in str(client.search_args)


def test_fetch_new_includes_already_seen_mail(monkeypatch):
    """已读的信照样要能进来——这正是改成按日期的原因。"""
    client = _FakeImap({"7": _build_message(subject="[Lcode] 已读的信")})
    client.select("INBOX")  # 不关心已读标记
    _patch_imap(monkeypatch, client)
    result = mailbox_mod.fetch_new(_cfg())
    assert len(result.accepted) == 1
    assert result.accepted[0].uid == "7"


def test_fetch_new_skips_known_message_ids(monkeypatch):
    client = _FakeImap({"1": _build_message(message_id="<dup@qq.com>")})
    _patch_imap(monkeypatch, client)
    result = mailbox_mod.fetch_new(_cfg(), skip_ids={"<dup@qq.com>"})
    assert result.accepted == ()


def test_fetch_new_prefilters_before_downloading_body(monkeypatch):
    """白名单外的信只取头部，不该把正文也拖下来。

    收件箱里大部分是营销邮件，每轮全量下载正文纯属浪费带宽和时间。
    """
    client = _FakeImap({"1": _build_message(sender="marketing@spam.com")})
    _patch_imap(monkeypatch, client)
    mailbox_mod.fetch_new(_cfg())
    specs = [spec for _uid, spec in client.fetched]
    assert specs, "至少要取头部"
    assert all("HEADER.FIELDS" in spec for spec in specs)
    assert not any("BODY.PEEK[]" in spec for spec in specs)


def test_fetch_new_fetches_body_only_for_candidates(monkeypatch):
    client = _FakeImap({"1": _build_message(sender="k@qq.com")})
    _patch_imap(monkeypatch, client)
    mailbox_mod.fetch_new(_cfg())
    specs = [spec for _uid, spec in client.fetched]
    assert any("HEADER.FIELDS" in spec for spec in specs)
    assert any("BODY.PEEK[]" in spec for spec in specs)


def test_fetch_new_never_marks_mail_seen(monkeypatch):
    """不改用户的已读状态。

    `\\Seen` 是用户自己的阅读状态，擅自修改是一种冒犯；而且它一变，
    别的客户端也会以为这封信被读过了。
    """
    client = _FakeImap({"1": _build_message()})
    _patch_imap(monkeypatch, client)
    mailbox_mod.fetch_new(_cfg())
    assert client.stored == []


def test_fetch_new_skips_when_not_ready(monkeypatch):
    called = []
    monkeypatch.setattr(
        mailbox_mod.imaplib, "IMAP4_SSL", lambda *a, **k: called.append(1) or _FakeImap({})
    )
    result = mailbox_mod.fetch_new(MailConfig())
    # 配置不全就干脆不连，别让每次轮询白等一个超时。
    assert result.accepted == ()
    assert called == []


def test_fetch_new_survives_login_failure(monkeypatch):
    _patch_imap(monkeypatch, _FakeImap({}, fail_login=True))
    # 授权码错了只该导致「本轮没有新任务」，不该让常驻循环崩掉。
    assert mailbox_mod.fetch_new(_cfg()).accepted == ()


def test_fetch_new_survives_connection_error(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("连不上")

    monkeypatch.setattr(mailbox_mod.imaplib, "IMAP4_SSL", boom)
    assert mailbox_mod.fetch_new(_cfg()).accepted == ()


def test_fetch_new_silences_auto_submitted(monkeypatch):
    """自动回复静默丢弃，不算「退回」。

    这类邮件是可预期的：我们自己的回信（收件人恰好是同邮箱时会落回 INBOX）、
    别人的假期自动回复。把它们报成退回只是噪声，还容易盖住真正的问题。
    """
    message = email.message.EmailMessage()
    message["From"] = "k@qq.com"
    message["Subject"] = "Re: [Lcode] 你好"
    message["Auto-Submitted"] = "auto-replied"
    message.set_content("自动回复")
    client = _FakeImap({"1": bytes(message)})
    _patch_imap(monkeypatch, client)

    result = mailbox_mod.fetch_new(_cfg())
    assert result.accepted == ()
    assert result.rejected == ()  # 不报告


def test_fetch_new_still_reports_real_rejections(monkeypatch):
    client = _FakeImap({"1": _build_message(sender="stranger@evil.com")})
    _patch_imap(monkeypatch, client)
    result = mailbox_mod.fetch_new(_cfg())
    # 白名单外的信要留证：用户得知道「我发了为什么不理我」。
    assert len(result.rejected) == 1
    assert result.rejected[0].reason == "发件人不在白名单"


def test_since_date_uses_english_month(monkeypatch):
    """IMAP 的 SINCE 只认英文月份缩写，写 11 月或 Nov. 都不行。"""
    value = mailbox_mod._since_date(3)
    assert value.endswith("2026")
    assert value[3:6] in mailbox_mod._MONTHS


# ── 队列集成 ──────────────────────────────────────────────────────────────


def test_queue_remembers_reply_target(tmp_path):
    from lcode.resident.queue import ORIGIN_MAIL, TaskQueue

    queue = TaskQueue(tmp_path)
    queue.enqueue(
        "跑测试",
        origin=ORIGIN_MAIL,
        reply_to="k@qq.com",
        subject="[Lcode] 跑测试",
        message_id="<m1@qq.com>",
    )
    task = queue.load()[0]
    assert task.origin == ORIGIN_MAIL
    assert task.reply_to == "k@qq.com"
    assert task.message_id == "<m1@qq.com>"


def test_queue_detects_duplicate_message_id(tmp_path):
    from lcode.resident.queue import TaskQueue

    queue = TaskQueue(tmp_path)
    queue.enqueue("一", message_id="<m1@qq.com>")
    assert queue.has_message("<m1@qq.com>") is True
    assert queue.has_message("<m2@qq.com>") is False
    assert queue.has_message("") is False


def test_queue_backward_compatible_without_new_fields(tmp_path):
    import json

    from lcode.resident.queue import TaskQueue

    # 旧版 queue.json 没有 origin/reply_to，读出来必须还能用。
    (tmp_path / "queue.json").write_text(
        json.dumps([{"id": "job_1", "prompt": "老任务", "status": "pending"}]),
        encoding="utf-8",
    )
    task = TaskQueue(tmp_path).load()[0]
    assert task.prompt == "老任务"
    assert task.origin == "cli"
    assert task.reply_to == ""


# ── 退信去重 ──────────────────────────────────────────────────────────────


def _rejected(uid: str, sender: str = "noise@x.com") -> Rejected:
    return Rejected(sender, "营销邮件", "发件人不在白名单", uid)


def test_rejection_log_reports_each_uid_once(tmp_path):
    log = mailbox_mod.RejectionLog(tmp_path / "mail-rejected.json")
    assert log.take_new((_rejected("1"), _rejected("2"))) == [_rejected("1"), _rejected("2")]
    # 同一封未读邮件下一轮还会被拉出来，但不能再报一遍。
    assert log.take_new((_rejected("1"), _rejected("2"))) == []


def test_rejection_log_reports_new_uid_again(tmp_path):
    log = mailbox_mod.RejectionLog(tmp_path / "mail-rejected.json")
    log.take_new((_rejected("1"),))
    # 用户改正后重发，主题常常一样但 uid 是新的，这时必须再提醒一次。
    assert len(log.take_new((_rejected("1"), _rejected("9")))) == 1


def test_rejection_log_survives_corrupt_state(tmp_path):
    path = tmp_path / "mail-rejected.json"
    path.write_text("{not json", encoding="utf-8")
    log = mailbox_mod.RejectionLog(path)
    # 状态文件坏了顶多多报一次，不能因此漏掉退信。
    assert log.take_new((_rejected("1"),)) == [_rejected("1")]


def test_rejection_log_trims_to_limit(tmp_path):
    import json

    log = mailbox_mod.RejectionLog(tmp_path / "mail-rejected.json")
    log.take_new(tuple(_rejected(str(i)) for i in range(mailbox_mod.REPORTED_LIMIT + 20)))
    stored = json.loads((tmp_path / "mail-rejected.json").read_text(encoding="utf-8"))
    assert len(stored) == mailbox_mod.REPORTED_LIMIT


def test_rejection_log_ignores_empty_input(tmp_path):
    log = mailbox_mod.RejectionLog(tmp_path / "mail-rejected.json")
    assert log.take_new(()) == []
    assert not (tmp_path / "mail-rejected.json").exists()


def test_classify_attaches_uid_to_rejection():
    result = classify([_mail(sender="x@evil.com")], _cfg())
    # uid 是去重的锚点，丢了去重就失效。
    assert result.rejected[0].uid == "1"
