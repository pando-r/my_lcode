"""通知通道：把「需要人知道的事」推出终端（缺口一：反馈回路）。

为什么需要这一层：
常驻任务在无人值守时跑，结果只落盘到 ``.lcode/resident/results/``，没有任何
推送通道。任务失败了也只有你主动去翻 daemon.log 才知道。贾维斯和现在的差距
首先不是能力，是「它犯了错你不知道」。

四个通道，各有各的职责：
- ``log``     永远开。桌面通知可能被系统关掉、webhook 可能没人接，
  JSONL 是可查证的最后记录。
- ``desktop`` 桌面横幅（Windows Toast / macOS 通知中心 / Linux notify-send），
  给「就在电脑前」的场景。
- ``webhook`` 标准库 urllib POST 一个 JSON，给「人不在电脑前」的场景（手机推送）。
- ``email``   SMTP 发一封邮件，给「桌面横幅没弹、webhook 没人接」时的兜底。
  QQ 邮箱 / 163 / Gmail 都走这条路——个人 QQ、微信没有官方发消息 API，
  邮件是唯一不依赖第三方中转、也不需要逆向协议的通道。

三条设计约束：
1. 通知失败绝不能让任务失败——所有通道异常都在这里吞掉，最多写进日志。
2. 桌面通道用 Popen 不 wait：常驻进程不能因为弹一个横幅卡住几十毫秒以上。
   子进程独立于父进程，父进程（``--resident-once``）退出后它照样能弹出来。
3. webhook / email 同步发送（带超时）：``--resident-once`` 跑完就退，不能交给
   daemon 线程，否则进程结束时请求还没发出去。
"""

from __future__ import annotations

import base64
import json
import os
import smtplib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

import yaml

LEVEL_INFO = "info"
LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"

# 级别只用于过滤和颜色，不做权限判断。
LEVELS: tuple[str, ...] = (LEVEL_INFO, LEVEL_OK, LEVEL_WARN, LEVEL_ERROR)
_RANK: dict[str, int] = {name: index for index, name in enumerate(LEVELS)}

CONFIG_NAME = "notify.yaml"
LOG_NAME = "notifications.jsonl"

DEFAULT_WEBHOOK_TIMEOUT = 5.0
# 用户在系统通知里看到的应用名，也是 Windows AUMID / notify-send 的 -a 参数。
APP_NAME = "Lcode"
_TOAST_APP_ID = APP_NAME


@dataclass
class EmailConfig:
    """``.lcode/notify.yaml`` 的 ``email`` 段。

    默认 ``enabled=False``：多数人没有 SMTP 授权码，默认开会让每次发送都白跑一遍
    连接超时。
    """

    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    user: str = ""
    auth_code: str = ""
    to: str = ""
    timeout: float = 10.0

    def ready(self) -> bool:
        """缺任何一项都无法发送——少了就干脆不连，避免无谓的网络等待。"""
        return bool(self.enabled and self.smtp_host and self.user and self.auth_code and self.to)

    def normalized(self) -> EmailConfig:
        return EmailConfig(
            enabled=bool(self.enabled),
            smtp_host=str(self.smtp_host or "").strip(),
            smtp_port=int(self.smtp_port or 465),
            user=str(self.user or "").strip(),
            auth_code=str(self.auth_code or "").strip(),
            to=str(self.to or "").strip(),
            timeout=max(1.0, float(self.timeout or 10.0)),
        )


@dataclass
class NotifyConfig:
    """``.lcode/notify.yaml`` 的运行时映射。"""

    desktop: bool = True
    webhook: str = ""
    min_level: str = LEVEL_INFO
    webhook_timeout: float = DEFAULT_WEBHOOK_TIMEOUT
    email: EmailConfig = field(default_factory=EmailConfig)

    def normalized(self) -> NotifyConfig:
        # 手写 YAML 容易把 min_level 写成大写或拼错，统一收敛到合法值。
        level = str(self.min_level or LEVEL_INFO).strip().lower()
        if level not in _RANK:
            level = LEVEL_INFO
        return NotifyConfig(
            desktop=bool(self.desktop),
            webhook=str(self.webhook or "").strip(),
            min_level=level,
            webhook_timeout=max(0.5, float(self.webhook_timeout or DEFAULT_WEBHOOK_TIMEOUT)),
            email=self.email.normalized(),
        )


def load_config(root: str | Path) -> NotifyConfig:
    """读 ``<root>/.lcode/notify.yaml``，再叠加环境变量覆盖。

    YAML 格式错误时退回默认配置：通知配置坏了不该连带把任务搞挂。
    """
    cfg = NotifyConfig()
    path = Path(root) / ".lcode" / CONFIG_NAME
    if path.is_file():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError):
            raw = None
        if isinstance(raw, dict):
            if isinstance(raw.get("desktop"), bool):
                cfg.desktop = raw["desktop"]
            if isinstance(raw.get("webhook"), str):
                cfg.webhook = raw["webhook"]
            if isinstance(raw.get("min_level"), str):
                cfg.min_level = raw["min_level"]
            if isinstance(raw.get("webhook_timeout"), (int, float)):
                cfg.webhook_timeout = float(raw["webhook_timeout"])
            if isinstance(raw.get("email"), dict):
                cfg.email = _email_from_raw(raw["email"])
    return _apply_env(cfg).normalized()


def _email_from_raw(raw: dict) -> EmailConfig:
    """把 YAML 的 ``email`` 段读成 EmailConfig；未知/类型不对的键直接忽略。"""
    cfg = EmailConfig()
    if isinstance(raw.get("enabled"), bool):
        cfg.enabled = raw["enabled"]
    for key in ("smtp_host", "user", "auth_code", "to"):
        value = raw.get(key)
        if isinstance(value, str):
            setattr(cfg, key, value)
    port = raw.get("smtp_port")
    if isinstance(port, int) and not isinstance(port, bool):
        cfg.smtp_port = port
    timeout = raw.get("timeout")
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
        cfg.timeout = float(timeout)
    return cfg


def _apply_env(cfg: NotifyConfig) -> NotifyConfig:
    # 环境变量优先级高于 YAML：同一条命令想临时静音/临时接管推送时不必改文件。
    desktop = os.environ.get("LCODE_NOTIFY_DESKTOP", "").strip().lower()
    if desktop in ("0", "false", "no", "off"):
        cfg.desktop = False
    elif desktop in ("1", "true", "yes", "on"):
        cfg.desktop = True

    webhook = os.environ.get("LCODE_NOTIFY_WEBHOOK", "").strip()
    if webhook:
        cfg.webhook = webhook

    level = os.environ.get("LCODE_NOTIFY_MIN_LEVEL", "").strip()
    if level:
        cfg.min_level = level

    # 授权码单独给一个环境变量：它等同于密码，不该明文躺在 yaml 里被提交。
    auth_code = os.environ.get("LCODE_NOTIFY_EMAIL_AUTH", "").strip()
    if auth_code:
        cfg.email.auth_code = auth_code
        cfg.email.enabled = True
    return cfg


@dataclass(frozen=True)
class Notification:
    """一条待发通知。``source`` 用来说明来源（任务 id / 工具名）。"""

    title: str
    message: str
    level: str = LEVEL_INFO
    source: str = ""
    at: str = ""

    def __post_init__(self) -> None:
        # frozen dataclass 只能用 object.__setattr__ 纠正坏输入。
        if self.level not in _RANK:
            object.__setattr__(self, "level", LEVEL_INFO)
        if not self.at:
            object.__setattr__(self, "at", _now())

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "message": self.message,
            "level": self.level,
            "source": self.source,
            "at": self.at,
        }


class Notifier:
    """按配置把通知发到各个通道，返回实际送达的通道名列表。

    失败永不抛出：调用方（常驻 daemon、notify 工具）不该因为通知出问题而失败。
    """

    def __init__(self, root: str | Path, config: NotifyConfig | None = None) -> None:
        self._root = Path(root)
        self._config = (config or load_config(self._root)).normalized()
        self._log_path = self._root / ".lcode" / LOG_NAME

    @property
    def config(self) -> NotifyConfig:
        return self._config

    @property
    def log_path(self) -> Path:
        return self._log_path

    def send(
        self,
        title: str,
        message: str,
        level: str = LEVEL_INFO,
        source: str = "",
        exclude: tuple[str, ...] = (),
    ) -> list[str]:
        """按配置发通知，返回实际送达的通道名。

        ``exclude`` 用来临时跳过某些通道。邮件来源的任务用它跳过 ``email``：
        回信已经送到用户手里了，再发一条「任务完成」通知就是同一件事说两遍，
        而且那条通知还会被我们自己的收件箱过滤器当成无前缀邮件退回。
        """
        note = Notification(title=title, message=message, level=level, source=source)
        if self._below_min_level(note.level):
            return []

        delivered: list[str] = []
        if self._write_log(note):
            delivered.append("log")
        if (
            self._config.desktop
            and "desktop" not in exclude
            and _safe_channel(self._send_desktop, note)
        ):
            delivered.append("desktop")
        if (
            self._config.email.ready()
            and "email" not in exclude
            and _safe_channel(self._send_email, note)
        ):
            delivered.append("email")
        if self._config.webhook and "webhook" not in exclude:
            if _safe_channel(self._send_webhook, note):
                delivered.append("webhook")
        return delivered

    # ── channels ─────────────────────────────────────────────────────────

    def _below_min_level(self, level: str) -> bool:
        return _RANK.get(level, 0) < _RANK.get(self._config.min_level, 0)

    def _write_log(self, note: Notification) -> bool:
        # 同步写一行 JSON：这就是「你有没有告诉过我」的证据链。
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(note.as_dict(), ensure_ascii=False) + "\n")
            return True
        except OSError:
            return False

    def _send_desktop(self, note: Notification) -> bool:
        try:
            if sys.platform == "win32":
                return _toast_windows(note.title, note.message)
            if sys.platform == "darwin":
                return _notify_macos(note.title, note.message)
            return _notify_send(note.title, note.message)
        except Exception:  # noqa: BLE001 - 桌面通知是尽力而为，任何异常都吞掉
            return False

    def _send_webhook(self, note: Notification) -> bool:
        payload = json.dumps(note.as_dict(), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._config.webhook,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        opener = _opener_for(self._config.webhook)
        try:
            with opener.open(request, timeout=self._config.webhook_timeout):
                return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def _send_email(self, note: Notification) -> bool:
        """SMTP 发一封通知邮件。"""
        cfg = self._config.email
        return _deliver(cfg, _build_email(note, cfg))


def _safe_channel(channel, note: Notification) -> bool:
    """跑一个通道，任何异常都折算成「没送达」。

    通道自身（``_send_desktop``）已经有兜底了，这里再包一层是因为
    「通知失败绝不连累调用方」是不能靠某个通道自觉遵守的契约。
    """
    try:
        return bool(channel(note))
    except Exception:  # noqa: BLE001 - 通知是增强功能，不允许冒泡
        return False


def _opener_for(url: str) -> urllib.request.OpenerDirector:
    """本机地址强制直连。

    开着 VPN/代理的机器上，``urllib`` 会把 127.0.0.1 也丢给代理，导致本地
    webhook 明明活着却连不上。外网地址仍然沿用系统代理设置。
    """
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in ("", "localhost", "127.0.0.1", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


# ── email backend ────────────────────────────────────────────────────────


def _header_text(text: str) -> str:
    """压平标题里的换行。

    邮件头的值不允许出现 CR/LF：放行等于让人能把任意头注入进来，而且
    ``EmailMessage`` 自己会直接抛 ValueError。这里统一折成空格。
    """
    return " ".join(str(text).split()) or "(无标题)"


def _build_email(note: Notification, cfg: EmailConfig) -> EmailMessage:
    """把一条通知渲染成邮件：主题是 title，正文是 message + 三行元信息。

    元信息（级别/来源/时间）留在正文里而不是塞进头：收件端要能一眼看出
    这条是「任务失败」还是「跑完了」，光看主题不够。
    """
    message = EmailMessage()
    message["Subject"] = _header_text(note.title)
    message["From"] = cfg.user
    message["To"] = cfg.to
    # 通知是机器发的。声明清楚有两个好处：对方的假期自动回复不会回它；
    # 我们自己的收件箱扫描也会把它当成「自动邮件」跳过，不在日志里刷
    # 「主题缺少前缀」这种无意义的退回记录。
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(
        f"{note.message}\n"
        f"\n"
        f"---\n"
        f"级别：{note.level}\n"
        f"来源：{note.source or '（无）'}\n"
        f"时间：{note.at}\n"
    )
    return message


def _deliver(cfg: EmailConfig, message: EmailMessage) -> bool:
    """把构造好的邮件发出去。

    465 走 SMTP_SSL（QQ 邮箱 / 163 的隐式 TLS），其余端口走明文连接再
    starttls 升级（Gmail 587）。两种都是同步连接、带超时：``--resident-once``
    跑完进程就退，异步发就等于没发。
    """
    try:
        if cfg.smtp_port == 465:
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout) as client:
                client.login(cfg.user, cfg.auth_code)
                client.send_message(message)
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout) as client:
                client.starttls()
                client.login(cfg.user, cfg.auth_code)
                client.send_message(message)
        return True
    except (smtplib.SMTPException, OSError, ValueError):
        # 认证失败、DNS 失败、超时都折算成「没送达」，绝不冒泡。
        return False


def send_mail(
    cfg: EmailConfig,
    to: str,
    subject: str,
    body: str,
    in_reply_to: str = "",
) -> bool:
    """发一封普通邮件（不是通知），用于给来信人回结果。

    和通知通道的区别：收件人由调用方指定，正文不追加元信息，并带上
    ``Auto-Submitted: auto-replied``——两个自动回复系统互相回信会无限循环，
    这个头是让对方的邮件系统知道「别再回了」。
    """
    if not (cfg.smtp_host and cfg.user and cfg.auth_code and to):
        return False
    message = EmailMessage()
    message["Subject"] = _header_text(subject)
    message["From"] = cfg.user
    message["To"] = to
    message["Auto-Submitted"] = "auto-replied"
    if in_reply_to:
        message["In-Reply-To"] = _header_text(in_reply_to)
        message["References"] = _header_text(in_reply_to)
    message.set_content(body)
    return _deliver(cfg, message)


# ── platform backends ────────────────────────────────────────────────────


def _spawn(argv: list[str]) -> bool:
    """拉起一个独立子进程后立即返回；子进程不随父进程退出而消失。"""
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # pythonw / 计划任务场景：绝不能闪出一个控制台窗口。
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        subprocess.Popen(argv, **kwargs)  # noqa: S603 - argv 是我们自己拼的常量
        return True
    except OSError:
        return False


# WinRT 类型在 Windows PowerShell 5.1 里必须显式带上程序集与 ContentType 才能解析，
# 否则 New-Object Windows.Data.Xml.Dom.XmlDocument 会直接报「找不到类型」（已实测）。
# 拆成多段拼接只为让 Python 的物理行不超过 100 列。
_PS_TOAST_TYPES = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType = WindowsRuntime] | Out-Null\n"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
    "ContentType = WindowsRuntime] | Out-Null\n"
)


def _toast_script(title: str, message: str) -> str:
    """生成 Windows Toast 的 PowerShell 脚本。

    单独拆出来是为了能直接同步执行排错（``_toast_windows`` 只拉起子进程不等结果，
    失败时看不到原因）。
    """
    xml = (
        '<toast><visual><binding template="ToastGeneric">'
        f"<text>{_xml_escape(title)}</text>"
        f"<text>{_xml_escape(message)}</text>"
        "</binding></visual></toast>"
    )
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"{_PS_TOAST_TYPES}"
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        "$doc.LoadXml(@'\n"
        f"{xml}\n"
        "'@)\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)\n"
        "[Windows.UI.Notifications.ToastNotificationManager]"
        f"::CreateToastNotifier('{_TOAST_APP_ID}').Show($toast)\n"
    )


def _aumid_key_path() -> str:
    return rf"Software\Classes\AppUserModelId\{_TOAST_APP_ID}"


# 自注册每个进程只做一次：写注册表比弹横幅贵得多，而结果在一个进程里不会变。
_aumid_ready = False


def _ensure_aumid_registered() -> bool:
    """确保 Toast 用的 AppUserModelID 已在 HKCU 下注册，返回是否可用。

    为什么必须注册：``CreateToastNotifier('lcode')`` 在 AUMID 未注册时的行为
    没有保证——实测同一台机器上 ``Show()`` 返回成功、注册表
    ``LastNotificationAddedTime`` 也更新了，但通知既没弹横幅也没进通知中心。
    失败是静默的，代码侧完全看不出区别，所以只能在发送前把前提条件补齐。

    只补缺、不覆盖：用户手工改过 DisplayName（比如想让它叫「我的助手」）就保留。
    任何失败都返回 False 而不是抛出——注册不成功也应该继续尝试发横幅，
    「通知失败绝不连累调用方」在这里同样成立。
    """
    global _aumid_ready
    if _aumid_ready:
        return True
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:  # 理论上 win32 必有，防一手裁剪过的运行时
        return False
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _aumid_key_path(), 0, winreg.KEY_READ | winreg.KEY_WRITE
        ) as key:
            if _registry_value_missing(winreg, key, "DisplayName"):
                winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, _TOAST_APP_ID)
            # ShowInSettings=1 才会出现在「设置 → 通知」的应用列表里，
            # 否则用户想单独关掉 lcode 的通知都找不到入口。
            if _registry_value_missing(winreg, key, "ShowInSettings"):
                winreg.SetValueEx(key, "ShowInSettings", 0, winreg.REG_DWORD, 1)
    except OSError:
        return False
    _aumid_ready = True
    return True


def _registry_value_missing(winreg_mod: Any, key: Any, name: str) -> bool:
    try:
        winreg_mod.QueryValueEx(key, name)
    except FileNotFoundError:
        return True
    except OSError:
        # 读不出来时按「已存在」处理：宁可少写一次，也不要覆盖用户配置。
        return False
    return False


def _toast_windows(title: str, message: str) -> bool:
    # 先补 AUMID：未注册时 Show() 可能静默丢弃，连日志都没有。
    _ensure_aumid_registered()
    # PowerShell 走 -EncodedCommand（UTF-16LE base64）：绕开 ANSI 代码页把中文变乱码，
    # 也顺带免掉引号转义问题——消息里出现引号/反引号都不会破坏脚本。
    encoded = base64.b64encode(_toast_script(title, message).encode("utf-16-le")).decode("ascii")
    return _spawn(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            encoded,
        ]
    )


def _notify_macos(title: str, message: str) -> bool:
    # osascript 的参数用 AppleScript 字符串字面量，双引号要转义成 \"。
    safe_title = _applescript_str(title)
    safe_message = _applescript_str(message)
    return _spawn(
        ["osascript", "-e", f'display notification "{safe_message}" with title "{safe_title}"']
    )


def _applescript_str(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _notify_send(title: str, message: str) -> bool:
    # notify-send 直接收 argv，不需要任何转义。
    return _spawn(["notify-send", "-a", _TOAST_APP_ID, title, message])


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def notify(
    root: str | Path,
    title: str,
    message: str,
    level: str = LEVEL_INFO,
    source: str = "",
) -> list[str]:
    """一次性便利函数：构造 Notifier 并发一条通知，返回送达的通道列表。"""
    return Notifier(root).send(title, message, level=level, source=source)


__all__ = [
    "CONFIG_NAME",
    "DEFAULT_WEBHOOK_TIMEOUT",
    "EmailConfig",
    "LEVELS",
    "LEVEL_ERROR",
    "LEVEL_INFO",
    "LEVEL_OK",
    "LEVEL_WARN",
    "LOG_NAME",
    "Notification",
    "Notifier",
    "NotifyConfig",
    "load_config",
    "notify",
    "send_mail",
]
