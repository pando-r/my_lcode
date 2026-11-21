"""通知通道（缺口一）的测试。

重点不是「横幅弹出来了」，而是三条约束：
1. 通道失败绝不能让调用方失败；
2. 配置/级别过滤按预期工作；
3. 常驻任务成功/失败都会写一条通知（不静默）。
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from lcode import notify as notify_mod
from lcode.notify import LEVELS, Notification, Notifier, NotifyConfig, load_config


def _no_desktop(monkeypatch) -> list[tuple[str, str]]:
    """测试里绝不真弹横幅：把桌面通道换成记录器。"""
    calls: list[tuple[str, str]] = []

    def fake_desktop(title: str, message: str) -> bool:
        calls.append((title, message))
        return True

    monkeypatch.setattr(
        Notifier,
        "_send_desktop",
        lambda self, note: fake_desktop(note.title, note.message),
    )
    return calls


# ── config ───────────────────────────────────────────────────────────────


def test_default_config_when_file_missing(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.desktop is True
    assert cfg.webhook == ""
    assert cfg.min_level == "info"


def test_config_reads_yaml(tmp_path):
    path = tmp_path / ".lcode" / "notify.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "desktop: false\nwebhook: http://example/hook\nmin_level: warn\nwebhook_timeout: 2\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.desktop is False
    assert cfg.webhook == "http://example/hook"
    assert cfg.min_level == "warn"
    assert cfg.webhook_timeout == 2.0


def test_broken_yaml_falls_back_to_defaults(tmp_path):
    path = tmp_path / ".lcode" / "notify.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("desktop: [unclosed", encoding="utf-8")
    assert load_config(tmp_path).desktop is True


def test_unknown_min_level_normalized(tmp_path, monkeypatch):
    monkeypatch.setenv("LCODE_NOTIFY_MIN_LEVEL", "LOUD")
    assert load_config(tmp_path).min_level == "info"


def test_env_overrides_yaml(tmp_path, monkeypatch):
    path = tmp_path / ".lcode" / "notify.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("desktop: false\n", encoding="utf-8")

    monkeypatch.setenv("LCODE_NOTIFY_DESKTOP", "1")
    monkeypatch.setenv("LCODE_NOTIFY_WEBHOOK", "http://env/hook")
    cfg = load_config(tmp_path)
    assert cfg.desktop is True
    assert cfg.webhook == "http://env/hook"


def test_env_can_disable_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("LCODE_NOTIFY_DESKTOP", "0")
    assert load_config(tmp_path).desktop is False


# ── notification object ──────────────────────────────────────────────────


def test_unknown_level_downgraded_to_info():
    note = Notification(title="t", message="m", level="loud")
    assert note.level == "info"


def test_every_level_is_representable():
    for level in LEVELS:
        assert Notification(title="t", message="m", level=level).level == level


def test_as_dict_shape():
    data = Notification(title="t", message="m", level="ok", source="job_1").as_dict()
    assert set(data) == {"title", "message", "level", "source", "at"}
    assert data["level"] == "ok"


# ── channels ─────────────────────────────────────────────────────────────


def test_log_channel_always_written(tmp_path, monkeypatch):
    _no_desktop(monkeypatch)
    notifier = Notifier(tmp_path)
    delivered = notifier.send("标题", "正文", level="ok", source="job_x")

    assert "log" in delivered
    lines = notifier.log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["title"] == "标题"
    assert record["source"] == "job_x"
    assert record["level"] == "ok"


def test_log_appends_not_overwrites(tmp_path, monkeypatch):
    _no_desktop(monkeypatch)
    notifier = Notifier(tmp_path)
    notifier.send("一", "1")
    notifier.send("二", "2")
    lines = notifier.log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_desktop_channel_receives_title_and_message(tmp_path, monkeypatch):
    calls = _no_desktop(monkeypatch)
    notifier = Notifier(tmp_path)
    assert "desktop" in notifier.send("任务完成", "结果已保存")
    assert calls == [("任务完成", "结果已保存")]


def test_desktop_disabled_by_config(tmp_path, monkeypatch):
    calls = _no_desktop(monkeypatch)
    notifier = Notifier(tmp_path, NotifyConfig(desktop=False))
    delivered = notifier.send("标题", "正文")
    assert "desktop" not in delivered
    assert calls == []


def test_min_level_filters_low_notifications(tmp_path, monkeypatch):
    calls = _no_desktop(monkeypatch)
    notifier = Notifier(tmp_path, NotifyConfig(min_level="error"))
    assert notifier.send("提示", "普通信息", level="info") == []
    assert calls == []
    # 被过滤的通知连日志都不写，否则「只想被失败打扰」就失去意义。
    assert not notifier.log_path.exists()


def test_min_level_lets_error_through(tmp_path, monkeypatch):
    _no_desktop(monkeypatch)
    notifier = Notifier(tmp_path, NotifyConfig(min_level="error"))
    assert notifier.send("失败", "炸了", level="error") == ["log", "desktop"]


def test_desktop_failure_does_not_raise(tmp_path, monkeypatch):
    def boom(self, note):
        raise RuntimeError("桌面通道炸了")

    monkeypatch.setattr(Notifier, "_send_desktop", boom)
    delivered = Notifier(tmp_path).send("标题", "正文")
    # 桌面失败只影响自己，日志照写。
    assert delivered == ["log"]


def test_log_write_failure_does_not_raise(tmp_path, monkeypatch):
    _no_desktop(monkeypatch)
    notifier = Notifier(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("磁盘满了")

    monkeypatch.setattr("pathlib.Path.open", boom)
    delivered = notifier.send("标题", "正文")
    assert delivered == ["desktop"]  # 只是少一个通道，不抛异常


# ── webhook ──────────────────────────────────────────────────────────────


class _Hook(BaseHTTPRequestHandler):
    received: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802 - 标准库要求的名字
        length = int(self.headers.get("Content-Length", 0))
        _Hook.received.append(self.rfile.read(length))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def hook_server():
    _Hook.received = []
    server = HTTPServer(("127.0.0.1", 0), _Hook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _Hook.received
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_webhook_posts_json(tmp_path, hook_server, monkeypatch):
    server, received = hook_server
    _no_desktop(monkeypatch)
    port = server.server_address[1]
    notifier = Notifier(tmp_path, NotifyConfig(webhook=f"http://127.0.0.1:{port}/hook"))

    delivered = notifier.send("任务失败", "MaxTurnsReached", level="error", source="job_1")
    assert "webhook" in delivered
    assert len(received) == 1
    payload = json.loads(received[0].decode("utf-8"))
    assert payload["title"] == "任务失败"
    assert payload["level"] == "error"
    assert payload["source"] == "job_1"


def test_webhook_failure_does_not_raise(tmp_path, monkeypatch):
    _no_desktop(monkeypatch)
    # 127.0.0.1:9 基本不会有人监听，等连接失败即可。
    notifier = Notifier(
        tmp_path, NotifyConfig(webhook="http://127.0.0.1:9/hook", webhook_timeout=0.5)
    )
    delivered = notifier.send("标题", "正文")
    assert "webhook" not in delivered
    assert "log" in delivered


# ── platform backends ────────────────────────────────────────────────────


def test_toast_script_escapes_xml():
    script = notify_mod._toast_script("a&b", "<c>")
    assert "a&amp;b" in script
    assert "&lt;c&gt;" in script


def test_toast_script_qualifies_winrt_types():
    """Windows PowerShell 5.1 必须带程序集限定名，否则 New-Object 报「找不到类型」。"""
    script = notify_mod._toast_script("t", "m")
    assert "ContentType = WindowsRuntime" in script
    assert "New-Object Windows.Data.Xml.Dom.XmlDocument" in script


def test_opener_for_localhost_bypasses_proxy(monkeypatch):
    import urllib.request

    calls: list[tuple] = []
    real_build = urllib.request.build_opener

    def spy(*handlers):
        calls.append(handlers)
        return real_build()

    monkeypatch.setattr(urllib.request, "build_opener", spy)
    notify_mod._opener_for("http://127.0.0.1:9000/hook")

    # 本机地址必须塞一个空代理 handler，否则开着 VPN 时本地 webhook 连不上。
    assert len(calls) == 1
    assert len(calls[0]) == 1
    handler = calls[0][0]
    assert isinstance(handler, urllib.request.ProxyHandler)
    assert handler.proxies == {}


def test_opener_for_remote_keeps_system_proxy(monkeypatch):
    import urllib.request

    calls: list[tuple] = []
    real_build = urllib.request.build_opener

    def spy(*handlers):
        calls.append(handlers)
        return real_build()

    monkeypatch.setattr(urllib.request, "build_opener", spy)
    notify_mod._opener_for("https://example.com/hook")
    # 外网地址不传任何 handler → 沿用系统代理设置。
    assert calls == [()]


def test_opener_for_localhost_name_variants(monkeypatch):
    import urllib.request

    calls: list[tuple] = []
    real_build = urllib.request.build_opener
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *h: (calls.append(h), real_build())[1]
    )
    for url in ("http://localhost:1/x", "http://[::1]:1/x"):
        notify_mod._opener_for(url)
    assert all(len(handlers) == 1 for handlers in calls)


def test_spawn_returns_false_for_missing_binary(monkeypatch):
    # 不存在的可执行文件不该抛异常，只返回 False。
    assert notify_mod._spawn(["definitely-not-a-real-binary-xyz"]) is False


# ── AUMID self-registration ──────────────────────────────────────────────


class _FakeWinreg:
    """替身 winreg：把注册表操作记在内存里，测试不去碰真实 HKCU。"""

    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_WRITE = 2
    REG_SZ = 1
    REG_DWORD = 4

    def __init__(self, existing: dict | None = None, fail: bool = False) -> None:
        self.existing = dict(existing or {})
        self.fail = fail
        self.created = 0
        self.writes: list[tuple[str, int, object]] = []

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        self.created += 1
        if self.fail:
            raise OSError("拒绝访问")
        return contextlib.nullcontext(self)

    def QueryValueEx(self, key, name):
        if name in self.existing:
            return (self.existing[name], self.REG_SZ)
        raise FileNotFoundError(name)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.writes.append((name, kind, value))


def _fake_winreg(monkeypatch, fake: _FakeWinreg) -> _FakeWinreg:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(notify_mod, "_aumid_ready", False)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return fake


def test_aumid_registered_on_first_use(monkeypatch):
    fake = _fake_winreg(monkeypatch, _FakeWinreg())
    assert notify_mod._ensure_aumid_registered() is True

    written = {name: value for name, _kind, value in fake.writes}
    assert written == {"DisplayName": notify_mod.APP_NAME, "ShowInSettings": 1}
    assert notify_mod.APP_NAME == "Lcode"


def test_aumid_registration_only_runs_once(monkeypatch):
    fake = _fake_winreg(monkeypatch, _FakeWinreg())
    notify_mod._ensure_aumid_registered()
    notify_mod._ensure_aumid_registered()
    # 第二次直接吃缓存：写注册表比弹横幅贵，没必要每次发送都写。
    assert fake.created == 1


def test_aumid_registration_preserves_user_values(monkeypatch):
    fake = _fake_winreg(monkeypatch, _FakeWinreg(existing={"DisplayName": "我的助手"}))
    notify_mod._ensure_aumid_registered()
    # 用户手工改过的名字不能被覆盖，缺的 ShowInSettings 仍然补上。
    assert [name for name, _k, _v in fake.writes] == ["ShowInSettings"]


def test_aumid_registration_failure_is_silent(monkeypatch):
    _fake_winreg(monkeypatch, _FakeWinreg(fail=True))
    assert notify_mod._ensure_aumid_registered() is False


def test_aumid_registration_skipped_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(notify_mod, "_aumid_ready", False)
    assert notify_mod._ensure_aumid_registered() is False


def test_toast_windows_registers_before_showing(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(
        notify_mod, "_ensure_aumid_registered", lambda: order.append("register") or True
    )
    monkeypatch.setattr(notify_mod, "_spawn", lambda argv: order.append("spawn") or True)
    assert notify_mod._toast_windows("t", "m") is True
    assert order == ["register", "spawn"]


# ── email ────────────────────────────────────────────────────────────────


def _email_cfg(**kwargs) -> object:
    from lcode.notify import EmailConfig

    base = {
        "enabled": True,
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "user": "me@qq.com",
        "auth_code": "secret",
        "to": "me@qq.com",
    }
    base.update(kwargs)
    return EmailConfig(**base)


class _FakeSmtp:
    """替身 SMTP 客户端：记录调用顺序，不发真邮件。"""

    instances: list["_FakeSmtp"] = []

    def __init__(self, host, port, timeout=None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls: list[str] = []
        self.messages: list = []
        _FakeSmtp.instances.append(self)

    def __enter__(self):
        self.calls.append("enter")
        return self

    def __exit__(self, *exc):
        self.calls.append("exit")
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(f"login:{user}")

    def send_message(self, message):
        self.calls.append("send")
        self.messages.append(message)


def _no_network(monkeypatch) -> type[_FakeSmtp]:
    _FakeSmtp.instances = []
    monkeypatch.setattr(notify_mod.smtplib, "SMTP_SSL", _FakeSmtp)
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", _FakeSmtp)
    _no_desktop(monkeypatch)
    return _FakeSmtp


def test_email_disabled_by_default(tmp_path):
    from lcode.notify import NotifyConfig

    assert NotifyConfig().email.enabled is False
    assert NotifyConfig().email.ready() is False


def test_email_ready_requires_everything():
    assert _email_cfg().ready() is True
    for key in ("smtp_host", "user", "auth_code", "to"):
        assert _email_cfg(**{key: ""}).ready() is False, key
    assert _email_cfg(enabled=False).ready() is False


def test_email_config_reads_yaml(tmp_path):
    path = tmp_path / ".lcode" / "notify.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "email:\n"
        "  enabled: true\n"
        "  smtp_host: smtp.qq.com\n"
        "  smtp_port: 587\n"
        "  user: a@qq.com\n"
        "  auth_code: abc\n"
        "  to: b@qq.com\n"
        "  timeout: 3\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path).email
    assert cfg.enabled is True
    assert cfg.smtp_port == 587
    assert cfg.user == "a@qq.com"
    assert cfg.to == "b@qq.com"
    assert cfg.timeout == 3.0


def test_email_config_ignores_bad_types(tmp_path):
    path = tmp_path / ".lcode" / "notify.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("email:\n  enabled: 'yes'\n  smtp_port: 465.5\n  to: 42\n", encoding="utf-8")
    cfg = load_config(tmp_path).email
    # 类型不对就当没写，回落到默认值，而不是让整个通知通道崩掉。
    assert cfg.enabled is False
    assert cfg.smtp_port == 465
    assert cfg.to == ""


def test_email_auth_from_env_enables_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("LCODE_NOTIFY_EMAIL_AUTH", "env-secret")
    path = tmp_path / ".lcode" / "notify.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "email:\n  smtp_host: smtp.qq.com\n  user: a@qq.com\n  to: a@qq.com\n", encoding="utf-8"
    )
    cfg = load_config(tmp_path).email
    # 授权码等同密码：能只放在环境变量里，配置文件中留空。
    assert cfg.auth_code == "env-secret"
    assert cfg.enabled is True


def test_email_sent_over_ssl_on_465(tmp_path, monkeypatch):
    fake = _no_network(monkeypatch)
    notifier = Notifier(tmp_path, NotifyConfig(email=_email_cfg()))
    assert "email" in notifier.send("跑完了", "3 个测试通过", level="ok", source="job_7")

    client = fake.instances[-1]
    assert (client.host, client.port) == ("smtp.qq.com", 465)
    # 465 是隐式 TLS，绝不能再 starttls。
    assert client.calls == ["enter", "login:me@qq.com", "send", "exit"]


def test_email_uses_starttls_on_587(tmp_path, monkeypatch):
    fake = _no_network(monkeypatch)
    notifier = Notifier(tmp_path, NotifyConfig(email=_email_cfg(smtp_port=587)))
    notifier.send("标题", "正文")
    assert fake.instances[-1].calls == ["enter", "starttls", "login:me@qq.com", "send", "exit"]


def test_email_body_carries_metadata(tmp_path, monkeypatch):
    fake = _no_network(monkeypatch)
    notifier = Notifier(tmp_path, NotifyConfig(email=_email_cfg()))
    notifier.send("任务失败", "MaxTurnsReached", level="error", source="job_1")

    message = fake.instances[-1].messages[0]
    assert message["Subject"] == "任务失败"
    assert message["To"] == "me@qq.com"
    body = message.get_content()
    assert "MaxTurnsReached" in body
    assert "error" in body
    assert "job_1" in body


def test_notification_email_marks_itself_automatic(tmp_path, monkeypatch):
    """通知邮件必须声明是机器发的。

    否则我们自己的收件箱扫描会把它当候选，每次都在日志里留一条
    「主题缺少前缀」的退回记录——纯噪声，而且会掩盖真正的问题。
    """
    fake = _no_network(monkeypatch)
    Notifier(tmp_path, NotifyConfig(email=_email_cfg())).send("标题", "正文")
    message = fake.instances[-1].messages[0]
    assert message["Auto-Submitted"] == "auto-generated"
    # 这条头必须能被收件箱闸门认出来。
    from lcode.mailbox import is_auto_submitted

    assert is_auto_submitted(message["Auto-Submitted"]) is True


def test_email_subject_rejects_header_injection():
    from lcode.notify import Notification

    # 标题里塞换行等于能伪造邮件头，必须被压平。
    message = notify_mod._build_email(
        Notification(title="正常\r\nBcc: evil@x.com", message="正文"), _email_cfg()
    )
    assert message.get("Bcc") is None
    assert message["Subject"] == "正常 Bcc: evil@x.com"


def test_email_failure_does_not_raise(tmp_path, monkeypatch):
    _no_desktop(monkeypatch)

    class _Boom(_FakeSmtp):
        def login(self, user, password):
            raise notify_mod.smtplib.SMTPAuthenticationError(535, b"bad auth")

    monkeypatch.setattr(notify_mod.smtplib, "SMTP_SSL", _Boom)
    delivered = Notifier(tmp_path, NotifyConfig(email=_email_cfg())).send("标题", "正文")
    # 授权码错了只影响自己，日志照写，异常不外泄。
    assert "email" not in delivered
    assert "log" in delivered


def test_email_not_attempted_when_incomplete(tmp_path, monkeypatch):
    fake = _no_network(monkeypatch)
    notifier = Notifier(tmp_path, NotifyConfig(email=_email_cfg(auth_code="")))
    delivered = notifier.send("标题", "正文")
    # 配置不全就干脆不连，别让每次通知都白等一个超时。
    assert "email" not in delivered
    assert fake.instances == []


# ── notify tool ──────────────────────────────────────────────────────────


async def test_notify_tool_sends(tmp_path, monkeypatch):
    from lcode.tool.ctx import with_cwd

    calls = _no_desktop(monkeypatch)
    from lcode.tool.notify import NotifyTool

    with with_cwd(str(tmp_path)):
        result = await NotifyTool().execute(
            json.dumps({"title": "跑完了", "message": "3 个测试通过", "level": "ok"})
        )
    assert not result.is_error
    assert "log" in result.content
    assert calls == [("跑完了", "3 个测试通过")]


async def test_notify_tool_requires_title_and_message():
    from lcode.tool.notify import NotifyTool

    tool = NotifyTool()
    assert (await tool.execute(json.dumps({"title": "只有标题"}))).is_error
    assert (await tool.execute(json.dumps({"message": "只有正文"}))).is_error
    assert (await tool.execute("not json")).is_error


async def test_notify_tool_rejects_blank_values():
    from lcode.tool.notify import NotifyTool

    tool = NotifyTool()
    assert (await tool.execute(json.dumps({"title": "  ", "message": "x"}))).is_error


async def test_notify_tool_bad_level_falls_back(tmp_path, monkeypatch):
    from lcode.tool.ctx import with_cwd
    from lcode.tool.notify import NotifyTool

    _no_desktop(monkeypatch)
    with with_cwd(str(tmp_path)):
        result = await NotifyTool().execute(
            json.dumps({"title": "t", "message": "m", "level": "screaming"})
        )
    assert not result.is_error
    record = json.loads(
        (tmp_path / ".lcode" / "notifications.jsonl").read_text(encoding="utf-8").strip()
    )
    assert record["level"] == "info"


def test_notify_is_not_read_only_but_never_gated():
    """notify 有副作用（写日志/弹横幅），但权限层必须放行——否则无人值守时通知发不出去。"""
    from lcode.permission import Category
    from lcode.permission.settings import categorize
    from lcode.tool.notify import NotifyTool

    assert NotifyTool().read_only is False
    assert categorize("notify", False) is Category.READ
