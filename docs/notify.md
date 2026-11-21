# 通知通道（缺口一：反馈回路）

## 解决的问题

常驻任务在无人值守时跑，结果原本只落盘到 `.lcode/resident/results/`。
任务失败了也只有主动翻 `daemon.log` 才会发现——`job_2bd72829` 就是这样
静默失败在 20 轮上限上的。

现在每个任务的三个出口（完成 / 失败 / 超时）都会推一条通知，整轮异常也会。

## 四个通道

| 通道 | 何时生效 | 用途 |
|---|---|---|
| `log` | 永远开 | 写 `.lcode/notifications.jsonl`，可查证的证据链 |
| `desktop` | `desktop: true`（默认） | Windows Toast / macOS 通知中心 / Linux notify-send |
| `email` | `email.enabled: true` 且四项字段齐全 | SMTP 发邮件，推送到 QQ 邮箱 / 手机 |
| `webhook` | 配置了 `webhook` | POST JSON 到指定 URL，人不在电脑前时用 |

个人 QQ、微信没有官方发消息 API，邮件是唯一不依赖第三方中转、也不用逆向协议的
通道；在微信里开「QQ邮箱提醒」，一封邮件就能同时覆盖 QQ 和微信。

`log` 永远开是刻意的：桌面通知可能被系统关掉、webhook 可能没人接，
JSONL 是"你到底有没有告诉过我"的最后凭据。

## 配置

`<项目根>/.lcode/notify.yaml`（模板见 `src/lcode/templates/notify.yaml.example`）：

```yaml
desktop: true
webhook: ""
min_level: info        # info < ok < warn < error
webhook_timeout: 5

email:
  enabled: true
  smtp_host: smtp.qq.com
  smtp_port: 465            # 465 = SSL；587 = STARTTLS（Gmail 用 587）
  user: 123456@qq.com       # 发件人
  auth_code: ""             # 授权码，不是登录密码
  to: 123456@qq.com         # 收件人，通常就是自己
  timeout: 10
```

`auth_code` 可以留空，改用环境变量 `LCODE_NOTIFY_EMAIL_AUTH` —— 设了它会自动启用
`email` 段。授权码等同密码，不建议明文写进可能被提交的文件。

环境变量优先级更高：

```powershell
$env:LCODE_NOTIFY_DESKTOP = "0"      # 临时静音
$env:LCODE_NOTIFY_WEBHOOK = "https://..."  # 临时接管推送
$env:LCODE_NOTIFY_MIN_LEVEL = "error"      # 只收失败
$env:LCODE_NOTIFY_EMAIL_AUTH = "授权码"    # 填了自动启用邮件通道
```

命令行还有一个总开关：`--resident-once --no-notify`。

## 两个入口

**常驻任务自动通知**（`resident/daemon.py`）——不需要任何配置就会发：

```
Lcode 任务完成：job_3d278e15
  结论摘要（一行）
  结果已保存：.../results/job_3d278e15.md
```

失败/超时会带 `level=error` 和失败原因，横幅里直接能看到 `RuntimeError: ...`。

**Agent 主动通知**（`tool/notify.py`）——注册进默认注册表，模型自己决定何时调用。
常驻模式的系统提示里已经明确告知它：需要用户尽快知道的事用 `notify`，
不要用它替代完整回答。

## 关键设计决策

**1. notify 工具的权限分类是 READ，不是 EXEC。**
`permission/settings.py` 的 `categorize` 里有一条显式判断。理由：如果它归 EXEC，
`default` 模式下会变成 ASK → 无人值守时直接 DENY，而这恰恰是通知最需要生效的场景
（常驻任务失败要有人知道）。代价是它在 Plan 模式的工具列表里也会出现——
`read_only=False`，所以不会被当成只读工具并发执行。

**2. 通知失败绝不能让任务失败。**
`Notifier.send` 对每个通道都包了 `_safe_channel`；`Daemon.notify` 再包一层 try/except。
这是契约，不是巧合——通知是增强功能，不允许反向影响主流程。

**3. Windows Toast 必须用 `-EncodedCommand`。**
`powershell -Command` 的字符串从 Python 传下去会经过 ANSI 代码页，中文直接变乱码。
base64(UTF-16LE) 同时解决编码和引号转义两个问题。

**4. WinRT 类型必须带程序集限定名。**
Windows PowerShell 5.1 里 `New-Object Windows.Data.Xml.Dom.XmlDocument` 会报
"找不到类型"，必须写成
`[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]`。
这个坑已实测过（见 `_PS_TOAST_TYPES`）。

**5. 桌面通道用 Popen 不 wait，webhook 同步。**
`--resident-once` 跑完就退出：桌面横幅交给独立子进程，父进程退出后照样弹；
webhook 反而必须同步（带超时），否则进程结束时请求还没发出去。

**6. 本地 webhook 绕过系统代理。**
开着 VPN 时 `urllib` 会把 127.0.0.1 也丢给代理，导致本地端口明明活着却连不上。
`_opener_for` 对本机地址强制直连，外网地址仍沿用系统代理。

**7. Windows Toast 的 AUMID 必须先注册。**
`CreateToastNotifier('Lcode')` 在 AUMID 未注册时行为没有保证——实测同一台机器上
`Show()` 返回成功、注册表 `LastNotificationAddedTime` 也更新了，但通知既没弹横幅
也没进通知中心，失败完全静默。所以 `_ensure_aumid_registered()` 在每次发送前
把 `HKCU\Software\Classes\AppUserModelId\Lcode` 补齐：只补缺、不覆盖用户改过的
`DisplayName`，失败返回 False 而不是抛出，进程内只写一次。

**8. 邮件同步发送、配置不全就不连。**
和 webhook 同理，`--resident-once` 跑完就退，异步发等于没发。
`EmailConfig.ready()` 要求 host/user/auth_code/to 四项齐全，缺一项直接跳过——
否则每次通知都要白等一个连接超时。

## 测试

`tests/test_notify.py`（43 个）+ `tests/test_resident.py` 里的通知段。
重点覆盖的是约束而不是"横幅真的弹了"：

- 通道全挂时不抛异常，任务状态仍然是 DONE
- `min_level` 过滤生效，且被过滤的通知连日志都不写
- webhook 用真 HTTP server 验证收到的 JSON 结构
- 空队列不通知（否则每天定时跑一轮会天天弹"没有任务"）

## 反向通道

通知是出向的。如果想反过来用邮件给 Lcode 派任务，见 [mailbox.md](mailbox.md)——
同样走 SMTP 授权码，但加了白名单、主题前缀和只读三道闸门。
