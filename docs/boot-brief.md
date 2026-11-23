# 开机简报（缺口三：主动开口）

## 解决的问题

前面几条通道都是「你说，它答」：`notify` 是它推给你、`mailbox` 是你推给它。
但真正的贾维斯会在你还没开口时先说话——**「早，今天下雨，出门带伞」**。

这一条就是它主动开口：每次开机登录时，自己查天气、给建议，然后把一句话弹到桌面上。

```
登录 Windows
   ↓ 等 60 秒（让桌面、杀软、云同步先抢完资源）
   ↓ 等网络就绪（刚开机 DNS 还没起来）
   ↓ 把 .lcode/brief.md 入队（UTF-8 文件，不走命令行）
   ↓ 跑一轮常驻任务（只读模式，search_web 够用）
桌面横幅：简报正文
```

**每次登录都发，不做去重**——这是刻意的，理由见下面「为什么不做去重」。

## 安装

一条命令注册计划任务（不需要管理员权限）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\boot_brief.ps1
```

输出：

```
Registered scheduled task: lcode-boot-brief
  trigger   : at logon of <你的机器>\<你的用户名>
  prompt    : D:\...\.lcode\brief.md
  workdir   : D:\...\my_mecode
  python    : D:\...\.venv\Scripts\python.exe
  dedupe    : none (one brief per logon)
  delay     : 60s, then wait up to 180s for network
```

想调参数：

```powershell
# 慢机器：多等一会儿再开始
... -DelaySeconds 120 -NetworkWaitSeconds 300

# 先看会注册什么命令，不真注册
... -DryRun
```

管理：

```powershell
Get-ScheduledTaskInfo -TaskName lcode-boot-brief   # 上次运行时间/结果
Start-ScheduledTask -TaskName lcode-boot-brief     # 立刻跑一次（测试用）
Get-Content .lcode\resident\boot-brief.log         # 脚本自己的日志
Get-Content .lcode\resident\daemon.log -Tail 20    # 任务执行日志
powershell -ExecutionPolicy Bypass -File scripts\boot_brief.ps1 -Remove
```

## 简报长什么样

真实产出（2026-11-22 00:08，衡阳）：

```
K，现在已经是零点后了，晚上好。刚开机，先看一眼今天的天气。

今天衡阳是周日，天气由阴转小雨，气温 7~18℃，风力为微风，由南风转东北风，
后半夜降雨可能性更高。温度区间来自两个来源：中央气象台 nmc.cn 给出 17/10，
360 天气网给出 18/7；两个来源不一致，因此按 7~18℃ 理解。总体是白天阴、
晚上有雨，出门带伞。

建议：要办的事尽量上午办完，下午起雨就来了。早晚温差 11℃，穿一件能脱的外套。
洗晒今天不划算，改成室内。晚上不方便出门，正好继续弄 lcode；如果你有需求，
我随时可以开工。
```

三段：**问候 → 天气 → 建议**。200 字以内，一屏能看完。

## 问候语：时段由代码算，措辞由你定

「早上好还是下午好」看着简单，但交给模型自己从 `HH:MM` 推算有两个问题：
11:58 / 12:01 这种边界上不同模型答案不同，同一模型换温度也会抖；而问候语
是简报第一句，说错最刺眼。

所以时段在代码里算死（`src/lcode/prompt/environment.py: _time_of_day`），
直接跟在时间后面给出来：

```
Time: 14:58 (下午)
```

分档规则（左闭右开，集中在一张表里，改一处就够）：

| 时段 | 范围 | brief.md 里对应的问候语 |
|---|---|---|
| 早上 | 05:00–08:59 | 早上好 |
| 上午 | 09:00–11:59 | 上午好 |
| 中午 | 12:00–13:59 | 中午好 |
| 下午 | 14:00–17:59 | 下午好 |
| 晚上 | 18:00–22:59 | 晚上好 |
| 深夜 | 23:00–04:59 | 别说「晚上好」，用「这个点还没睡」这类 |

边界有 13 个参数化测试锁着（`test_time_of_day_boundaries`），含每小时 59 分
不能跳档。**措辞不写死**：`brief.md` 里是一张「时段 → 问候语」的表，你想把
「上午好」改成「上午好啊」直接改那个文件，不用动代码。

### 横幅里到底显示什么

横幅正文由 `src/lcode/resident/daemon.py` 的 `_summarize_for_notify` 拼出来，
顺序是**答复 → 任务标签 → 结果文件**：

```
下午好，K。周日下午的这点时间，正好归你自己。
衡阳今天阴转中雨，7～18℃（全国天气网）；weather.com.cn 记的是 9～18℃……带伞。
要出门就赶在 17 点前，之后雨势偏大……这天气正适合坐在电脑前。
结果已保存：D:\...\.lcode\resident\results\job_42529b24.md
```

这里踩过两个坑，都跟「横幅第一行」有关：

1. **prompt 不能进横幅。** 早期正文是 `_oneline(task.prompt, 60)` 打头，于是开机
   简报的横幅第一行是「你是 K 的贾维斯。他刚开机，你主动开口。 写一份简短的
   晨间简报…」——用户看到的是自己写的提示词，答复被挤到第二行。现在超过
   `PROMPT_LABEL_LIMIT`（60 字）的 prompt 整个不显示；一句话的手动任务仍保留
   `任务：整理 docs 目录` 这种标签。
2. **答复不能只取一行。** 早期用 `summarize()` 挑「第一个够长的行」，简报只剩
   「下午好，K。」，天气和建议全丢。简报本来就是按「一屏看完」写的，所以整段
   带上（`NOTIFY_EXCERPT_LIMIT` = 320 字），只有长报告才截断 + 指向结果文件。

横幅不渲染 Markdown，所以 `**加粗**`、反引号、`##` 会在拼装时去掉。

## 提示词在文件里，不在代码里

`.lcode/brief.md` 就是那段提示词，直接编辑它就能改简报的口吻和内容。

为什么它必须是一个**独立的 UTF-8 文件**而不是命令行参数：

计划任务的动作是一整条命令字符串。Windows PowerShell 5.1 的 `-Argument` 会经过
OEM 代码页，中文直接变乱码。所以脚本用 `lcode --enqueue-file .lcode/brief.md`
把提示词从文件读进来——读的是确定的 UTF-8，不受代码页影响。

同理，`scripts/boot_brief*.ps1` 两个脚本都刻意保持**纯 ASCII**：PS 5.1 会用
OEM 代码页读取无 BOM 文件，非 ASCII 字符会让整个脚本解析失败。

## 三个不显然的设计选择

**1. 先等 60 秒，再等网络。**

刚登录时 shell、杀软、云同步全在抢磁盘和网络。这时候查天气大概率超时，
而失败是**静默的**——那一天就没简报。宁可慢一分钟，也不要偶尔没有。

网络检测用 `Test-NetConnection www.baidu.com:443`，最多等 180 秒；等不到也照跑
（可能只是 ICMP/TCP 被拦），把情况写进日志。

**2. 为什么不做去重（两次都踩坑了）。**

要求是「每次开机都打一次招呼」。这里试过两种去重键，全部在本机实测失败：

*方案 A：日期戳（原实现）* — 凌晨 00:21 重启一次就占掉了全天名额，同一天
11:20 正式开机时反而一条都没收到。K 就是这么漏掉当天简报的：
`last-brief.txt` 写了 `2026-11-22`，11:21 那次登录直接 `-- skip`。

*方案 B：开机时刻* — 换成 `LastBootUpTime.Ticks`，逻辑上「每次开机变一次、
注销重登不变」完全正确。但这台机器 `HiberbootEnabled = 1`（快速启动开着），
用「关机」菜单关机再按电源键，Windows 走的是休眠恢复，**内核会话被还原，
`LastBootUpTime` 不变**。也就是说那个版本会在最常见的关机方式下静默不发。

所以最终方案是什么都不做：每次登录都发。代价只是同一次开机里注销再登录会多收
一条；而任何「看起来更聪明」的去重键都可能让简报彻底消失——两害相权，

```
漏发（用户以为坏了） > 多发一条（用户划掉就行）
```

唯一保留的守卫是「同一条 prompt 已经挂在 pending」，它只防失败重试堆积队列，
和时间无关。

**3. 失败会重试，但不会堆积。**

失败退出（比如被锁挡住）时不留任何时间戳，下次登录照发。为了不让重试堆积任务，
入队前先查队列里有没有**同样的 prompt 还挂着 pending**，有就跳过入队。

这一条踩过坑：比较前必须把 `\r` 去掉。Python 的 `read_text()` 会把 CRLF 统一成
LF 再入队，而 PowerShell 的 `Get-Content -Raw` 保留原样——直接比字符串在 CRLF
检出的文件上永远不相等（实测 624 vs 597 字符，内容一样）。

## 两个踩过的坑

**坑 1：`$ErrorActionPreference = "Stop"` + 原生命令的 stderr = 假失败。**

PS 5.1 会把原生命令写到 stderr 的任何内容变成 `NativeCommandError`，在 `Stop`
下设成终止性错误。第一次跑就是这个原因失败的：

```
[2026-11-22 00:05:20] enqueue: 已入队 job_1b73ea26: ...
（然后就没下文了——exit code 变成 1，后边的 resident 根本没跑）
```

改成 `cmd /c "... > out 2>&1"` 把输出重定向到文件，再读文件写日志。

**坑 2：`--resident-once` 撞锁会静默失败。**

`.lcode/resident/daemon.lock` 保证同一队列只有一个守护进程在跑（否则任务会被
重复执行）。这是对的，但开机那一刻可能和邮件轮询任务撞上：

```
[2026-11-22 00:07:18] 已有常驻进程在运行（pid=37896），本次退出
[2026-11-22 00:07:18] boot brief: FAILED (exit 1), will retry on next logon
```

现在会重试一次（等 90 秒），且只在输出里出现 `pid=` 时才重试——
其它错误（配置坏了、模型连不上）重试也没用。

## 依赖

- 天气靠内置的 `mcp__web__search_web`（Tavily → Bing → DuckDuckGo）。
  想有稳定结果就配 `TAVILY_API_KEY`（`.lcode.yaml` 里有说明）。
- 桌面横幅靠 `lcode.notify`（Windows Toast，AUMID 会自注册）。
  想让简报也进微信，在 `.lcode/notify.yaml` 配 webhook 或 email。
- 任务以 `--resident-mode default`（只读）运行。查天气只需要读权限，
  不给写权限是刻意的：**简报不需要动你的文件**。

## 排查

简报没来，按这个顺序看：

```powershell
# 1. 计划任务跑了没
Get-ScheduledTaskInfo -TaskName lcode-boot-brief
# LastTaskResult: 0 = 成功

# 2. 脚本走到哪一步
Get-Content .lcode\resident\boot-brief.log -Tail 20

# 3. 任务执行成功了吗
Get-Content .lcode\resident\daemon.log -Tail 20

# 4. 手动从头跑一遍（跳过等待）
powershell -ExecutionPolicy Bypass -File scripts\boot_brief_run.ps1 `
  -WorkDir (Get-Location).Path `
  -Python (Join-Path (Get-Location).Path ".venv\Scripts\python.exe") `
  -PromptFile (Join-Path (Get-Location).Path ".lcode\brief.md") `
  -DelaySeconds 0 -NetworkWaitSeconds 30
```

常见原因：

| 现象 | 原因 | 处理 |
|---|---|---|
| 日志停在「network NOT ready」 | 网络没起来 | 调大 `-NetworkWaitSeconds` |
| `enqueue exit=2` | 提示词文件路径不对 | 检查 `.lcode/brief.md` 是否在 |
| `resident exit=1` 且含 `pid=` | 别的守护进程占着锁 | 会自动重试；持续出现就查邮件任务 |
| 任务成功但没弹横幅 | Toast 被系统静音 | 设置 → 通知 → 找到 Lcode 打开 |
| 日志里完全没有当天记录 | 计划任务没被触发 | `Get-ScheduledTaskInfo -TaskName lcode-boot-brief` 看 `LastRunTime`；也可能是「关机」走快速启动、根本没重新登录 |

## 测试

`tests/test_boot_brief.py`（28 个）覆盖两个底层契约：

- `--enqueue-file`：中文原样入队、相对路径、文件缺失、空文件、
  不挤掉老的 `--enqueue`
- `gather_environment`：带 `Time:`/时段/星期、`time` 为空时字段整体省略、
  星期中英双写映射、**时段边界 13 个参数化用例**
- `.lcode/brief.md` 的必备要素（三段结构、必须真去查、不许编数据）

横幅正文的拼装在 `tests/test_resident.py`（`── 通知正文：答复在前 ──` 一节）：
答复占第一行、长 prompt 整个不出现、短 prompt 留作标签、多段答复不被压成一行、
长报告截断后仍带结果路径、Markdown 记号被去掉。

PS 脚本本身没法在 pytest 里跑，所以实测靠日志：完整链路已在本机验证
（enqueue → resident → 横幅）。去重退场后连着跑三次，三次都各自入队并发通知：

```
[14:36:40] enqueue exit=0: 已入队 job_26e16778 ... → boot brief: done
[14:38:02] enqueue exit=0: 已入队 job_b543de02 ... → boot brief: done
[14:38:15] enqueue exit=0: 已入队 job_8b3b484b ... → boot brief: done
```

（三次都没有 skip —— 这就是「每次登录都发」。）
