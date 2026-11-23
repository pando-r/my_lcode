"""send_mail 工具（主动发信）的测试。

这个工具是整个工具层唯一能把数据**送出去**的通道，所以测试重点不是
「SMTP 能连上」，而是三道闸门是否真的挡得住：

1. ``send_to`` 白名单为空 / 地址不在表里 → 一封都不发；
2. 权限层把它归 EXEC（不是像 notify 那样归 READ）；
3. 每次尝试都记一行 outbox，成功失败都留证。
"""

from __future__ import annotations

import json

import pytest

from lcode.mailbox import OUTBOX_NAME, can_send_to, load_config, record_outbox
from lcode.tool import send_mail as send_mail_mod
from lcode.tool.send_mail import BODY_LIMIT, SUBJECT_LIMIT, SendMailTool

# ── 准备 ──────────────────────────────────────────────────────────────────


def _setup(root, *, send_to=(), smtp=True, enabled=True):
    """写 mail.yaml + notify.yaml，模拟一份能发信的配置。

    注意 ``@qq.com`` 这类域名通配必须加引号：YAML 里 ``- @qq.com`` 会让
    PyYAML 抛 ScannerError，整份配置作废（这是个真实踩过的坑）。
    """

    def yaml_item(value: str) -> str:
        return f'  - "{value}"' if value.startswith("@") else f"  - {value}"

    (root / ".lcode").mkdir(parents=True, exist_ok=True)
    lines = ["enabled: true", "senders:", "  - k@qq.com"]
    if send_to:
        lines.append("send_to:")
        lines.extend(yaml_item(item) for item in send_to)
    (root / ".lcode" / "mail.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / ".lcode" / "notify.yaml").write_text(
        "email:\n"
        f"  enabled: {'true' if enabled else 'false'}\n"
        f"  smtp_host: {'smtp.qq.com' if smtp else ''}\n"
        "  user: me@qq.com\n"
        "  auth_code: secret\n"
        "  to: me@qq.com\n",
        encoding="utf-8",
    )


def _outbox(root):
    path = root / ".lcode" / OUTBOX_NAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _args(to="friend@qq.com", subject="进度说明", body="这周做完了三件事。"):
    return json.dumps({"to": to, "subject": subject, "body": body})


@pytest.fixture
def sent(monkeypatch):
    """替身 SMTP 发送函数，返回可检查的调用列表。"""
    calls: list[dict] = []

    def fake(cfg, to, subject, body, in_reply_to=""):
        calls.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(send_mail_mod, "send_mail", fake)
    return calls


# ── 白名单闸门 ────────────────────────────────────────────────────────────


async def test_refuses_when_whitelist_empty(tmp_path, monkeypatch, sent):
    """白名单为空 = 一封都不发，且要告诉用户去改哪个文件。

    这是刻意失败关闭：忘记配置时应该发不出去，而不是「什么都能发」。
    """
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=())

    result = await SendMailTool().execute(_args())

    assert result.is_error
    assert "白名单是空的" in result.content
    assert "send_to" in result.content
    assert "friend@qq.com" in result.content  # 直接给一段能抄的配置
    assert sent == []


async def test_refuses_address_outside_whitelist(tmp_path, monkeypatch, sent):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=["boss@company.com"])

    result = await SendMailTool().execute(_args(to="friend@qq.com"))

    assert result.is_error
    assert "不在发信白名单" in result.content
    assert "boss@company.com" in result.content  # 告诉它现有哪些选项
    assert sent == []


async def test_allows_address_in_whitelist(tmp_path, monkeypatch, sent):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=["friend@qq.com"])

    result = await SendMailTool().execute(_args())

    assert not result.is_error
    assert len(sent) == 1
    assert sent[0]["to"] == "friend@qq.com"
    assert sent[0]["body"] == "这周做完了三件事。"


async def test_whitelist_is_case_insensitive(tmp_path, monkeypatch, sent):
    # 用户手写 YAML 时大小写很难保持一致，地址本身不该区分大小写。
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=["Friend@QQ.com"])

    result = await SendMailTool().execute(_args(to="friend@qq.com"))
    assert not result.is_error and len(sent) == 1


async def test_whitelist_domain_wildcard(tmp_path, monkeypatch, sent):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=["@qq.com"])

    assert not (await SendMailTool().execute(_args(to="a@qq.com"))).is_error
    assert (await SendMailTool().execute(_args(to="a@evil.com"))).is_error
    assert len(sent) == 1


def test_can_send_to_matches_is_allowed_semantics():
    assert can_send_to("a@qq.com", ["a@qq.com"])
    assert can_send_to("a@qq.com", ["@qq.com"])
    assert not can_send_to("a@qq.com", ["b@qq.com"])
    assert not can_send_to("", ["a@qq.com"])
    assert not can_send_to("a@qq.com", [])  # 空表 = 谁都不发


# ── SMTP 可用性 ───────────────────────────────────────────────────────────


async def test_refuses_when_smtp_not_configured(tmp_path, monkeypatch, sent):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=["friend@qq.com"], smtp=False)

    result = await SendMailTool().execute(_args())

    assert result.is_error
    assert "notify.yaml" in result.content
    assert sent == []


async def test_send_failure_is_reported_and_logged(tmp_path, monkeypatch):
    """发不出去必须明确说「没发出去」，不能让模型以为成功了。"""
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=["friend@qq.com"])
    monkeypatch.setattr(send_mail_mod, "send_mail", lambda *a, **k: False)

    result = await SendMailTool().execute(_args())

    assert result.is_error
    assert "发送失败" in result.content
    assert _outbox(tmp_path)[-1]["ok"] is False


# ── 参数校验 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload, missing",
    [
        ({"subject": "s", "body": "b"}, "to"),
        ({"to": "a@b.com", "body": "b"}, "subject"),
        ({"to": "a@b.com", "subject": "s"}, "body"),
    ],
)
async def test_missing_required_field(tmp_path, monkeypatch, sent, payload, missing):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=["a@b.com"])

    result = await SendMailTool().execute(json.dumps(payload))
    assert result.is_error and missing in result.content
    assert sent == []


async def test_rejects_bad_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = await SendMailTool().execute("{not json")
    assert result.is_error and "参数解析失败" in result.content


async def test_rejects_non_object_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = await SendMailTool().execute("[1, 2, 3]")
    assert result.is_error and "JSON 对象" in result.content


async def test_rejects_oversized_subject_and_body(tmp_path, monkeypatch, sent):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=["a@b.com"])

    long_subject = await SendMailTool().execute(
        _args(to="a@b.com", subject="x" * (SUBJECT_LIMIT + 1))
    )
    assert long_subject.is_error and "主题太长" in long_subject.content

    long_body = await SendMailTool().execute(_args(to="a@b.com", body="x" * (BODY_LIMIT + 1)))
    assert long_body.is_error and "正文太长" in long_body.content
    assert sent == []


# ── outbox 账本 ───────────────────────────────────────────────────────────


async def test_success_is_logged(tmp_path, monkeypatch, sent):
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=["friend@qq.com"])

    await SendMailTool().execute(_args())

    entry = _outbox(tmp_path)[-1]
    assert entry["to"] == "friend@qq.com"
    assert entry["subject"] == "进度说明"
    assert entry["ok"] is True
    # 正文只存预览：全文可能很长，也没必要另存一份。
    assert "三件事" in entry["body_preview"]


async def test_refusal_is_logged_too(tmp_path, monkeypatch, sent):
    """被拒的尝试也要留痕——「它想发给谁」本身是有价值的信息。"""
    monkeypatch.chdir(tmp_path)
    _setup(tmp_path, send_to=["boss@company.com"])

    await SendMailTool().execute(_args(to="friend@qq.com"))

    entry = _outbox(tmp_path)[-1]
    assert entry["ok"] is False
    assert entry["reason"] == "收件人不在白名单"
    assert entry["to"] == "friend@qq.com"


def test_outbox_preview_is_truncated(tmp_path):
    record_outbox(tmp_path / OUTBOX_NAME, "a@b.com", "s", "字" * 2000, ok=True)
    entry = json.loads((tmp_path / OUTBOX_NAME).read_text(encoding="utf-8"))
    assert len(entry["body_preview"]) == 500


def test_outbox_is_capped(tmp_path, monkeypatch):
    from lcode import mailbox as mailbox_mod

    monkeypatch.setattr(mailbox_mod, "OUTBOX_LIMIT", 5)
    path = tmp_path / OUTBOX_NAME
    for index in range(6):
        record_outbox(path, f"a{index}@b.com", "s", "b", ok=True)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    # 保留最近的：最新那条必须在。
    assert "a5@b.com" in lines[-1]


def test_outbox_survives_write_failure(tmp_path, monkeypatch):
    # 记账失败不该抛出去打断发送链路（磁盘满、目录只读等）。
    from pathlib import Path

    def boom(self, *args, **kwargs):
        raise OSError("磁盘满了")

    monkeypatch.setattr(Path, "write_text", boom)
    record_outbox(tmp_path / OUTBOX_NAME, "a@b.com", "s", "b", ok=True)  # 不抛即通过


# ── 配置读取 ──────────────────────────────────────────────────────────────


def test_config_reads_send_to(tmp_path):
    _setup(tmp_path, send_to=["a@b.com", "B <c@d.com>"])
    cfg = load_config(tmp_path)
    assert cfg.send_to == ["a@b.com", "c@d.com"]  # 昵称被剥掉、转小写


def test_config_accepts_comma_separated_send_to(tmp_path):
    (tmp_path / ".lcode").mkdir(parents=True)
    (tmp_path / ".lcode" / "mail.yaml").write_text(
        "send_to: 'a@x.com, b@y.com'\n", encoding="utf-8"
    )
    assert load_config(tmp_path).send_to == ["a@x.com", "b@y.com"]


def test_config_send_to_defaults_to_empty(tmp_path):
    (tmp_path / ".lcode").mkdir(parents=True)
    (tmp_path / ".lcode" / "mail.yaml").write_text("enabled: true\n", encoding="utf-8")
    # 没配 send_to 就是不发——失败关闭。
    assert load_config(tmp_path).send_to == []


def test_unquoted_domain_wildcard_warns_instead_of_silent(tmp_path, capsys):
    """``- @qq.com`` 会让整份 YAML 作废，必须说出来。

    ``@`` 是 YAML 保留字符，PyYAML 直接抛 ScannerError；而 load_config 原来
    把异常吞掉降级成默认配置——结果是「配置写错了 → senders 空 → 一封都不收」，
    表现却像代码不支持域名通配。这个坑隐蔽到必须有一条回归测试钉住。
    """
    (tmp_path / ".lcode").mkdir(parents=True)
    (tmp_path / ".lcode" / "mail.yaml").write_text(
        "enabled: true\nsenders:\n  - @qq.com\n", encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    # 降级是安全的（不收信），但必须明确告知原因和改法。
    assert cfg.ready() is False
    err = capsys.readouterr().err
    assert "解析失败" in err
    assert '"@qq.com"' in err


def test_quoted_domain_wildcard_works(tmp_path):
    (tmp_path / ".lcode").mkdir(parents=True)
    (tmp_path / ".lcode" / "mail.yaml").write_text(
        'enabled: true\nsenders:\n  - "@qq.com"\n', encoding="utf-8"
    )
    assert load_config(tmp_path).senders == ["@qq.com"]


def test_smtp_ready_ignores_notification_recipient():
    from lcode.notify import EmailConfig

    # 通知要求 to 也配好；主动发信的收件人由调用方给，不该被 to 挡住。
    cfg = EmailConfig(enabled=True, smtp_host="smtp.qq.com", user="a@b.com", auth_code="x")
    assert cfg.smtp_ready() is True
    assert cfg.ready() is False


def test_smtp_ready_requires_enabled():
    from lcode.notify import EmailConfig

    cfg = EmailConfig(enabled=False, smtp_host="smtp.qq.com", user="a@b.com", auth_code="x")
    assert cfg.smtp_ready() is False


# ── 权限归类 ──────────────────────────────────────────────────────────────


def test_send_mail_is_not_read_only():
    # read_only=True 会被权限层直接判 READ，等于自动放行。发信不能这样。
    assert SendMailTool().read_only is False


def test_send_mail_categorizes_as_exec():
    """必须归 EXEC：TUI 里 ASK，无人值守时 DENY。

    notify 归 READ 是因为它的副作用只是「告诉用户自己」；send_mail 把数据
    送出本机且撤不回来，性质完全不同，不能套用同一个待遇。
    """
    from lcode.permission.settings import Category, categorize

    assert categorize("send_mail", False) == Category.EXEC


def test_send_mail_is_registered():
    from lcode.tool import new_default_registry

    names = [item.name for item in new_default_registry().definitions()]
    assert "send_mail" in names
    assert names.index("send_mail") > names.index("notify")  # 排在通知之后


def test_send_mail_has_friendly_name():
    from lcode.permission.settings import friendly_name

    assert friendly_name("send_mail") == "SendMail"


def test_schema_requires_all_three_fields():
    schema = SendMailTool().parameters()
    assert set(schema["required"]) == {"to", "subject", "body"}


# ── 发件记录的查看入口 ────────────────────────────────────────────────────


def test_cli_parses_outbox_flag():
    from lcode.cli import _build_parser

    args = _build_parser().parse_args(["--mail-peek", "--outbox"])
    assert args.mail_peek is True
    assert args.mail_outbox is True
    assert _build_parser().parse_args([]).mail_outbox is False


def _peek_outbox(root, limit=5):
    import asyncio
    import io
    import sys

    from lcode.cli import _mail_peek

    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        code = asyncio.run(_mail_peek(str(root), limit, outbox=True))
    finally:
        sys.stdout = old
    return code, buf.getvalue()


def test_peek_outbox_without_records(tmp_path):
    code, text = _peek_outbox(tmp_path)
    assert code == 0
    assert "还没有发件记录" in text


def test_peek_outbox_shows_sent_and_blocked(tmp_path):
    """发信记账必须能查：发出去的邮件撤不回来，这是唯一的问责凭据。"""
    path = tmp_path / ".lcode" / OUTBOX_NAME
    record_outbox(path, "friend@qq.com", "[Lcode] 周报", "这周做了三件事", ok=True)
    record_outbox(path, "evil@x.com", "泄露", "不该发", ok=False, reason="收件人不在白名单")

    code, text = _peek_outbox(tmp_path)

    assert code == 0
    assert "共 2 条" in text
    assert "[已发]" in text and "friend@qq.com" in text
    assert "[未发]" in text and "收件人不在白名单" in text


def test_peek_outbox_respects_limit(tmp_path):
    path = tmp_path / ".lcode" / OUTBOX_NAME
    for index in range(6):
        record_outbox(path, f"a{index}@b.com", "s", "b", ok=True)
    _code, text = _peek_outbox(tmp_path, limit=2)
    assert "a5@b.com" in text and "a4@b.com" in text
    assert "a0@b.com" not in text


def test_peek_outbox_survives_corrupt_file(tmp_path):
    path = tmp_path / ".lcode" / OUTBOX_NAME
    path.parent.mkdir(parents=True)
    path.write_text("{not json\n", encoding="utf-8")
    code, _text = _peek_outbox(tmp_path)
    assert code == 1  # 报错但不崩


# ── 白名单的修改入口（--mail-allow-to）────────────────────────────────────

MAIL_YAML = """\
# 一段必须被保留的注释
enabled: true
senders:
  - k@qq.com

# 发信白名单说明
send_to: []

# 主题前缀
subject_prefix: "[Lcode]"
"""


def _write_mail(tmp_path, text=MAIL_YAML):
    path = tmp_path / ".lcode" / "mail.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_add_send_to_appends_address(tmp_path):
    from lcode.mailbox import add_send_to

    path = _write_mail(tmp_path)
    changed, message = add_send_to(path, "friend@qq.com")

    assert changed is True and "friend@qq.com" in message
    assert load_config(tmp_path).send_to == ["friend@qq.com"]


def test_add_send_to_preserves_comments(tmp_path):
    """只改 send_to 那一段，别把文件里的说明性注释抹掉。

    这是不用 yaml.safe_dump 重写整份文件的原因：mail.yaml 的价值有一半在注释里
    （为什么默认关闭、为什么必须加引号）。
    """
    from lcode.mailbox import add_send_to

    path = _write_mail(tmp_path)
    add_send_to(path, "friend@qq.com")
    text = path.read_text(encoding="utf-8")

    assert "# 一段必须被保留的注释" in text
    assert "# 发信白名单说明" in text
    assert "# 主题前缀" in text
    assert 'subject_prefix: "[Lcode]"' in text


def test_add_send_to_quotes_domain_wildcard(tmp_path):
    """@qq.com 必须加引号，否则整份 YAML 作废。

    ``- @qq.com`` 会让 PyYAML 抛 ScannerError，结果是「一封都不收」——
    排查成本极高的那种坑。命令行入口顺带把这个引号处理掉。
    """
    from lcode.mailbox import add_send_to

    path = _write_mail(tmp_path)
    changed, _message = add_send_to(path, "@company.com")

    assert changed is True
    assert '- "@company.com"' in path.read_text(encoding="utf-8")
    assert load_config(tmp_path).send_to == ["@company.com"]


def test_add_send_to_is_idempotent(tmp_path):
    from lcode.mailbox import add_send_to

    path = _write_mail(tmp_path)
    add_send_to(path, "friend@qq.com")
    changed, message = add_send_to(path, "friend@qq.com")

    assert changed is False and "已经在" in message
    assert load_config(tmp_path).send_to == ["friend@qq.com"]


def test_add_send_to_is_case_insensitive(tmp_path):
    from lcode.mailbox import add_send_to

    path = _write_mail(tmp_path)
    add_send_to(path, "Friend@QQ.com")
    changed, _message = add_send_to(path, "friend@qq.com")
    assert changed is False


def test_add_send_to_rejects_malformed_address(tmp_path):
    from lcode.mailbox import add_send_to

    path = _write_mail(tmp_path)
    for bad in ("bad-address", "a b@c.com", "@", "@nodot", ""):
        changed, message = add_send_to(path, bad)
        assert changed is False, bad
        assert "不合法" in message
    assert load_config(tmp_path).send_to == []


def test_add_send_to_keeps_existing_entries(tmp_path):
    from lcode.mailbox import add_send_to

    path = _write_mail(tmp_path, MAIL_YAML.replace("send_to: []", "send_to:\n  - boss@corp.com"))
    add_send_to(path, "friend@qq.com")
    assert load_config(tmp_path).send_to == ["boss@corp.com", "friend@qq.com"]


def test_add_send_to_creates_key_when_missing(tmp_path):
    from lcode.mailbox import add_send_to

    path = _write_mail(tmp_path, "enabled: true\nsenders:\n  - k@qq.com\n")
    changed, _message = add_send_to(path, "friend@qq.com")

    assert changed is True
    assert load_config(tmp_path).send_to == ["friend@qq.com"]
    # 原有内容不能丢
    assert load_config(tmp_path).senders == ["k@qq.com"]


def test_add_send_to_creates_file_when_missing(tmp_path):
    from lcode.mailbox import add_send_to

    path = tmp_path / ".lcode" / "mail.yaml"
    changed, _message = add_send_to(path, "friend@qq.com")

    assert changed is True
    assert path.is_file()
    assert load_config(tmp_path).send_to == ["friend@qq.com"]


def test_add_send_to_refuses_broken_config(tmp_path):
    """原本就坏的配置不要碰：再改只会把问题搅得更乱。"""
    from lcode.mailbox import add_send_to

    path = _write_mail(tmp_path, "senders:\n  - @qq.com\n")  # 未加引号 → 解析失败
    changed, message = add_send_to(path, "friend@qq.com")

    assert changed is False
    assert "解析失败" in message
    # 文件原样保留，用户能自己看见坏在哪
    assert path.read_text(encoding="utf-8") == "senders:\n  - @qq.com\n"


def test_yaml_scalar_quotes_reserved_prefixes():
    from lcode.mailbox import yaml_scalar

    assert yaml_scalar("@qq.com") == '"@qq.com"'
    assert yaml_scalar("-weird") == '"-weird"'
    assert yaml_scalar("a@b.com") == "a@b.com"


def test_cli_parses_allow_to_flags():
    from lcode.cli import _build_parser

    args = _build_parser().parse_args(["--mail-allow-to", "a@b.com"])
    assert args.mail_allow_to == "a@b.com"
    assert _build_parser().parse_args(["--mail-allowed"]).mail_allowed is True
    defaults = _build_parser().parse_args([])
    assert defaults.mail_allow_to == ""
    assert defaults.mail_allowed is False


def test_mail_allow_to_command_writes_config(tmp_path, monkeypatch, capsys):
    """端到端：命令跑完，白名单里真的有这个地址。"""
    import asyncio

    from lcode.cli import _amain, _build_parser

    _write_mail(tmp_path)
    monkeypatch.chdir(tmp_path)

    args = _build_parser().parse_args(["--mail-allow-to", "friend@qq.com"])
    assert asyncio.run(_amain(args)) == 0

    assert load_config(tmp_path).send_to == ["friend@qq.com"]
    out = capsys.readouterr().out
    assert "friend@qq.com" in out
    # 要把下一步怎么用说清楚，否则用户还得去翻文档
    assert "发一封邮件" in out


def test_mail_allow_to_command_reports_failure(tmp_path, monkeypatch, capsys):
    import asyncio

    from lcode.cli import _amain, _build_parser

    _write_mail(tmp_path)
    monkeypatch.chdir(tmp_path)

    args = _build_parser().parse_args(["--mail-allow-to", "not-an-address"])
    assert asyncio.run(_amain(args)) == 1
    assert "不合法" in capsys.readouterr().err


def test_mail_allowed_lists_empty_whitelist(tmp_path, monkeypatch, capsys):
    import asyncio

    from lcode.cli import _amain, _build_parser

    _write_mail(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert asyncio.run(_amain(_build_parser().parse_args(["--mail-allowed"]))) == 0
    out = capsys.readouterr().out
    assert "空的" in out
    assert "--mail-allow-to" in out  # 直接给出下一步命令


def test_mail_allowed_lists_entries(tmp_path, monkeypatch, capsys):
    import asyncio

    from lcode.cli import _amain, _build_parser

    _write_mail(tmp_path)
    (tmp_path / ".lcode" / "notify.yaml").write_text(
        "email:\n  enabled: true\n  smtp_host: smtp.qq.com\n  user: me@qq.com\n"
        "  auth_code: x\n  to: me@qq.com\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert asyncio.run(_amain(_build_parser().parse_args(["--mail-allow-to", "a@b.com"]))) == 0
    capsys.readouterr()
    assert asyncio.run(_amain(_build_parser().parse_args(["--mail-allowed"]))) == 0

    out = capsys.readouterr().out
    assert "a@b.com" in out
    assert "可用" in out  # SMTP 也报了就绪状态


# ── 收信白名单的同一套入口（--mail-allow-from）────────────────────────────


def test_add_senders_edits_senders_block(tmp_path):
    """只加发信白名单会出现「我请对方回信，对方回信被当陌生人退回」。

    所以收信白名单也要能从命令行加，而且同样是「只动那一段」。
    """
    from lcode.mailbox import add_senders

    path = _write_mail(tmp_path)
    changed, message = add_senders(path, "Friend@QQ.com")

    assert changed is True and "senders" in message
    cfg = load_config(tmp_path)
    assert cfg.senders == ["k@qq.com", "friend@qq.com"]  # 原有条目保留
    assert cfg.send_to == []  # 不串台
    assert "# 发信白名单说明" in path.read_text(encoding="utf-8")


def test_add_senders_is_idempotent(tmp_path):
    from lcode.mailbox import add_senders

    path = _write_mail(tmp_path)
    add_senders(path, "friend@qq.com")
    changed, message = add_senders(path, "friend@qq.com")
    assert changed is False and "已经在" in message


def test_add_whitelist_entry_rejects_bad_address_for_any_key(tmp_path):
    from lcode.mailbox import add_whitelist_entry

    path = _write_mail(tmp_path)
    changed, message = add_whitelist_entry(path, "senders", "nope")
    assert changed is False and "不合法" in message


def test_cli_parses_allow_from_flag():
    from lcode.cli import _build_parser

    args = _build_parser().parse_args(["--mail-allow-from", "a@b.com"])
    assert args.mail_allow_from == "a@b.com"
    assert _build_parser().parse_args([]).mail_allow_from == ""


def test_mail_allow_from_command_writes_senders(tmp_path, monkeypatch, capsys):
    import asyncio

    from lcode.cli import _amain, _build_parser

    _write_mail(tmp_path)
    monkeypatch.chdir(tmp_path)

    args = _build_parser().parse_args(["--mail-allow-from", "friend@qq.com"])
    assert asyncio.run(_amain(args)) == 0

    assert load_config(tmp_path).senders == ["k@qq.com", "friend@qq.com"]
    out = capsys.readouterr().out
    # 要说清「接下来他能怎么用」，否则用户不知道这一步的意义
    assert "[Lcode]" in out
