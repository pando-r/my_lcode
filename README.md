# Lcode

一个多协议 LLM 终端对话客户端（Claude Code 风格）。第一块基石：在终端里与大模型进行
流式多轮对话，同时支持 Anthropic 与 OpenAI 两种协议（含自定义兼容端点）。

## 快速开始

macOS / Linux：

```bash
curl -LsSf --max-time 30 https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.sh | sh
```

Windows（PowerShell）：

```powershell
$ProgressPreference='SilentlyContinue'
irm https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1 -TimeoutSec 30 -OutFile "$env:TEMP\lcode-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\lcode-install.ps1"
```

> **一定要带 `--max-time` / `-TimeoutSec`。** 国内网络下 `raw.githubusercontent.com` 常见
> 表现是「连不上也不报错」，TCP 一直重试；命令不带超时就会**卡住不动**，看起来像死机。
> 加上超时后最多等 30 秒就会明确报错，而不是无限期挂起。

> **为什么不写成 `irm ... | iex`？** 脚本里有大量中文提示，文件必须是**带 BOM 的 UTF-8**：
> 去掉 BOM 后 PowerShell 5.1 会按系统代码页（中文 Windows 是 GBK）读，直接报几十条语法错误。
> 而带 BOM 的文本送进 `iex` 时，开头的 U+FEFF 会让首行 `#` 注释失效，报
> `无法将 Windows 项识别为 cmdlet`。**下载成文件再 `-File` 执行**同时避开这两个坑，
> 而且报错留在窗口里不会一闪而过。

国内网络下这条命令的主机本身可能就不通，换镜像（内容一致，只换 URL）：

```powershell
# jsDelivr（国内一般可达）
irm 'https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.ps1' -TimeoutSec 30 -OutFile "$env:TEMP\lcode-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\lcode-install.ps1"
```

```powershell
# ghfast 代理
irm 'https://ghfast.top/https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1' -TimeoutSec 30 -OutFile "$env:TEMP\lcode-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\lcode-install.ps1"
```

```bash
# macOS / Linux
curl -LsSf --max-time 30 https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.sh | sh
```

三个地址都不确定哪个通时，用这段自带回退的引导（每个地址都有超时，不会卡）：

```powershell
$ProgressPreference='SilentlyContinue'
$f="$env:TEMP\lcode-install.ps1"
$urls=@(
  'https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.ps1',
  'https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1',
  'https://ghfast.top/https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1')
$ok=$false
foreach($u in $urls){
  try{
    Write-Host "尝试 $u"
    irm $u -UseBasicParsing -TimeoutSec 25 -OutFile $f
    # 校验内容再执行：别把半截错误页面当成脚本跑
    if((Get-Item $f).Length -gt 5000 -and (Select-String -Path $f -Pattern 'LCODE_' -Quiet -Encoding UTF8)){
      $ok=$true; break
    }
    Write-Host "  内容不对，换下一个"
  }catch{ Write-Host "  失败：$($_.Exception.Message)" }
}
if(-not $ok){ throw '三个地址都下载失败，请检查网络/代理' }
powershell -NoProfile -ExecutionPolicy Bypass -File $f
```

脚本会自动挑安装器：有 `uv` 用 `uv`、有 `pipx` 用 `pipx`、否则用你已有的 **Python 3.12+**
的 `pip`（**不会强制下载 uv**）。只有机器上完全找不到 Python 时，才会安装 uv 来自带 Python。
装好后会生成 `~/.lcode/config.yaml`。之后只需要两步：

```bash
# 1) 把 providers[0].api_key 换成你的真实密钥
$EDITOR ~/.lcode/config.yaml     # Windows: notepad %USERPROFILE%\.lcode\config.yaml

# 2) 在任意项目目录启动
lcode
```

### 装之前先体检（推荐，尤其在国内网络）

装不上时不要反复重试，先让脚本自己报一下环境：

```powershell
# Windows：只体检，不安装
$env:LCODE_DOCTOR='1'
irm 'https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.ps1' -TimeoutSec 30 -OutFile "$env:TEMP\lcode-doctor.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\lcode-doctor.ps1"
```

```bash
# macOS / Linux
LCODE_DOCTOR=1 curl -LsSf --max-time 30 https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.sh | sh
```

它会依次打印 git / python / uv / pipx 的位置、Python 版本，以及 `github.com`、
`raw.githubusercontent.com`、`pypi.org`、`astral.sh`、`cdn.jsdelivr.net`、`ghfast.top`
六个站点的连通性（**每个地址最长 8 秒，不会卡**），并给出对应的处理建议。


> **上面的命令只能在 PowerShell 里跑**。贴进 `cmd.exe` 会直接报
> `'irm' 不是内部或外部命令`。cmd 用户请用：
>
> ```powershell
> powershell -NoProfile -ExecutionPolicy Bypass -Command "irm 'https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.ps1' -TimeoutSec 30 -OutFile \"$env:TEMP\lcode-install.ps1\"; powershell -NoProfile -ExecutionPolicy Bypass -File \"$env:TEMP\lcode-install.ps1\""
> ```

不想装到全局？临时跑一次（二选一，装了 pipx 的话不需要 uv）：

```bash
uvx --from git+https://github.com/pando-r/my_mecode.git lcode
pipx run --spec git+https://github.com/pando-r/my_mecode.git lcode
```

配置查找优先级：`--config` > `LCODE_CONFIG` 环境变量 > `./.lcode/config.yaml` >
`~/.lcode/config.yaml`。用户级放在前面那一条，保证装在任意目录都能直接跑；项目级方便
不同项目用不同模型。`lcode --init` 可重新生成模板，`lcode --version` 查看版本。

> 安全提示：Lcode 会调用 `bash` 执行命令并读写文件，所有写操作都会经过权限引擎。
> 首次在陌生仓库里使用时，建议逐条确认权限弹窗，不要盲目选择"全部允许"。

## 不用 uv 的安装方式（pip / pipx）

Lcode 是标准 PEP 517 包（hatchling 后端），`pip` 可以直接装，不需要 uv。
前提只有 **Python 3.12+**；`git` 可选（没有 git 时用 GitHub 的 zip 源码包）。

```bash
# 推荐：用 pipx 装成隔离的全局命令（pipx 本身可 pip install --user pipx）
pipx install "git+https://github.com/pando-r/my_mecode.git"

# 只用 pip：装到用户目录，Windows 上不需要管理员权限
pip install --user "git+https://github.com/pando-r/my_mecode.git"

# 没有 git 也行，直接装 zip 源码包
pip install --user https://github.com/pando-r/my_mecode/archive/refs/heads/main.zip

# 升级
pipx upgrade lcode        # 或 pip install --user --upgrade <上面的地址>
```

装完执行 `lcode --init` 生成配置模板，编辑好 api_key 后直接 `lcode` 启动。

- `pip install --user` 会把可执行文件放到用户脚本目录（Linux/macOS 一般是 `~/.local/bin`，
  Windows 是 `%APPDATA%\Python\Python3XX\Scripts`），首次可能需新开终端或手动加 PATH。
- Debian/Ubuntu 等的系统 Python 有 `externally-managed-environment` 保护，请改用 `pipx`，
  或建独立虚拟环境：
  `python3 -m venv ~/.lcode/venv && ~/.lcode/venv/bin/pip install "git+https://github.com/pando-r/my_mecode.git"`
- 一行脚本也支持显式指定安装器：
  `LCODE_INSTALLER=pip curl -LsSf --max-time 30 https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.sh | sh`
  （Windows：`$env:LCODE_INSTALLER='pip'` 后重跑安装命令）。

## 装不上怎么办

**先跑一次体检**（`LCODE_DOCTOR=1`，见上文），它会直接指出是工具缺失还是网络不通，
比逐条猜快得多。下面是几种最常见的情况：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| **`无法将 Windows 项识别为 cmdlet`** | 用 `irm ... \| iex` 直接跑远程脚本，脚本带 UTF-8 BOM，`iex` 把首字符 U+FEFF 当标识符，首行注释失效 | 改用本文档的「下载到 `$env:TEMP` 再 `-File`」写法；**不要**对带 BOM 的脚本用 `iex` |
| **`无法将 System.Byte[] 转换为 Command`** | 用了 `iex (iwr ...).Content`，jsDelivr 回 `application/octet-stream`，`.Content` 是字节数组 | 同上，下载成文件再执行 |
| **几十条 `Missing closing '}'` / 中文全是乱码** | 脚本被存成**无 BOM** 的 UTF-8，PowerShell 5.1 按 GBK 读，中文字符串把引号吃掉 | 别删脚本的 BOM；用官方下载命令原样落盘，别手动「另存为无 BOM」 |
| **命令卡住不动，没有任何输出** | 域名被黑洞，TCP 一直重试；`irm`/`curl` 没设超时 | 命令加 `-TimeoutSec 30` / `--max-time 30`；换镜像地址；跑 `LCODE_DOCTOR=1` |
| **卡在 `python` 那一步**（Windows） | `python` 是应用商店的 0 字节占位符（`WindowsApps\python.exe`），执行它会拉起商店并阻塞 | 脚本已自动跳过它并改用真解释器；仍不行就装 Python 3.12+，或 `LCODE_INSTALLER='uv'` 让 uv 自带解释器 |
| 卡在 `git clone` / 要求输入用户名密码 | 仓库不可见（私有或改名），或被网络劫持 | 确认仓库公开可访问；`git` 已设 `GIT_TERMINAL_PROMPT=0` 会快速失败而不是等待输入 |
| `'irm' 不是内部或外部命令` | 在 cmd 里跑了 PowerShell 命令 | 换 PowerShell，或用上面那条 `powershell -Command "..."` |
| `未找到 Python 3.12+` | 系统 Python 太老或没装 | 装 Python 3.12+，或装 `uv`（自带解释器） |
| `ReadTimeoutError` / `SSL` 错误 | 直连 pypi.org 或 GitHub 超时 | 加镜像：`LCODE_PIP_INDEX=...`、`LCODE_GH_PROXY=...`（见下） |
| `uv` 下载失败 | 公司/校园网拦 astral.sh | 脚本会先用 `pip install uv` 走镜像；仍失败就 `LCODE_INSTALLER=pip` |
| `--version` 输出不是 `Lcode` | 机器上有另一个同名包（PyPI 的 `lcode` 是等离子体仿真包） | `pip uninstall lcode` 后用 `pipx`/`uv` 隔离安装 |
| 装完 `lcode` 找不到 | 用户脚本目录不在 PATH | 脚本会尝试写入用户 PATH，重开终端即可 |
| `lcode --init` 报 `unrecognized arguments: - - i n i t` | 已修复（旧版脚本的单元素数组被 PowerShell 按字符拆开） | 更新到最新脚本重跑 |

国内网络走镜像：

```bash
# macOS / Linux（管道形式：变量要放在 sh 这一侧）
curl -LsSf --max-time 30 https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.sh \
  | LCODE_PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/ sh

# Windows PowerShell（先设变量，再跑安装命令）
$env:LCODE_PIP_INDEX='https://mirrors.aliyun.com/pypi/simple/'
$ProgressPreference='SilentlyContinue'
irm 'https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.ps1' -TimeoutSec 30 -OutFile "$env:TEMP\lcode-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\lcode-install.ps1"
```

安装脚本的环境变量：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `LCODE_REPO` | `https://github.com/pando-r/my_mecode` | 源码仓库（fork 后自建分发用） |
| `LCODE_CHANNEL` | `git` | `git` 从仓库装，`pypi` 从 PyPI 装 |
| `LCODE_INSTALLER` | `auto` | `auto` / `uv` / `pipx` / `pip` |
| `LCODE_PIP_INDEX` | 空 | 自定义 PyPI 镜像，同时喂给 pip 与 uv |
| `LCODE_GH_PROXY` | `ghfast.top` / `gh-proxy.com` / `ghproxy.net` | GitHub 代理前缀，直连不通时自动依次尝试；设 `none` 只用直连 |
| `LCODE_SOURCE_URL` | 空 | 直接指定安装源，跳过所有探测（排查用） |
| `LCODE_PYPI_NAME` | `lcode` | `pypi` 渠道使用的发行包名 |
| `LCODE_NO_PATH` | 空 | Windows 下设 1 则不修改用户 PATH |
| `LCODE_DRY_RUN` | 空 | 设 1 只打印命令不执行（排查用，不产生任何改动） |
| `LCODE_DOCTOR` | 空 | 设 1 只做环境体检，不安装 |

脚本的「不卡死」保证（都是这一版加的）：

- 所有 HTTP 操作都带硬超时；探测单个地址最长 **8 秒**，`irm`/`curl` 建议外挂 30 秒。
- `git` 设 `GIT_TERMINAL_PROMPT=0`，遇到私有库/凭据请求立即失败而不等待输入。
- `pip` 带 `--timeout 30 --retries 2`，`uv` 设 `UV_HTTP_TIMEOUT=60`。
- Windows 上自动识别并跳过应用商店的 0 字节 `python.exe` 占位符。
- 失败时用 `throw` 而不是 `exit`：报错留在屏幕上，窗口不会一闪而过被关掉。

## 特性

- 流式逐字输出，结束后以 Markdown 美化定型。
- 单会话内维护完整多轮上下文。
- 启动时支持多 provider 选择（方向键列表）。
- 响应计时（"Imagining… (Ns)"）与可恢复的错误反馈。
- 多行输入：`Alt+Enter` 换行，`Enter` 提交；`/exit` 或 `Ctrl+C` 退出。
- 输出可鼠标拖选，`Ctrl+C` 复制到系统剪贴板；`/copy` 一键复制最近回复或整屏。

## 复制文本

对话区的输出（含网址、代码块）可以直接复制出来：

| 操作 | 效果 |
| --- | --- |
| 鼠标拖选 → `Ctrl+C` | 复制选中文本（中文/emoji 按显示宽度对齐，不会切半） |
| 双击某个组件 / 三击整块 | Textual 原生全选，再 `Ctrl+C` |
| `/copy` | 复制最近一条助手回复 |
| `/copy all` | 复制整个屏幕的纯文本 |
| `/copy code` | 复制最近回复里的所有代码块内容 |

已经选中文本时，空闲状态下的 `Ctrl+C` 是"复制"（复制后选区自动清掉，再按一次才是退出）；
回答仍在流式输出或等待审批时，`Ctrl+C` 仍然优先中断本轮，避免手滑丢回答。

复制同时走两条路：Textual 的 OSC 52（SSH / 远程终端也有效）＋ 平台原生剪贴板
（Windows `CF_UNICODETEXT`、macOS `pbcopy`、Linux `wl-copy`/`xclip`/`xsel`）。
终端不支持 OSC 52 时靠后者兜底，失败也只是静默降级，不影响对话。

> tmux 里想用 OSC 52 需要在 `~/.tmux.conf` 打开 `set -g set-clipboard on`；
> 原生剪贴板路径在本地终端里不需要任何配置。

## 网页搜索（MCP）

Lcode 自带一个零依赖的 MCP server `lcode.mcp_servers.web_search`：cli 启动时按
`.lcode.yaml` 的配置以 stdio 方式拉起它，把 `search_web` 工具注册进工具表
（完整工具名 `mcp__web__search_web`）。

```yaml
# .lcode.yaml
mcp_servers:
  web:
    type: stdio
    command: "${LCODE_PYTHON}"          # cli 注入 = 当前运行 lcode 的解释器
    args: ["-m", "lcode.mcp_servers.web_search"]
```

- 后端直接解析 DuckDuckGo 的结果页，**不需要 API key**；html 端点被拦或改版时自动退回
  lite 端点，两个端点用同一个解析器（按文档顺序配对标题与摘要）。
- 参数：`query`（支持 `site:` 等语法）、`max_results`（1–10，默认 5）；返回标题 / URL / 摘要。
- 工具声明了 `read_only_hint`，因此按 READ 类处理：Plan 模式下也可用、可与其它只读调用并发，
  后台子 Agent 同样适用（`mcp__` 前缀本来就在后台白名单里）。
- 配置里的 `${VAR}` 不只作用于 `env` / `headers`，`command` 与 `args` 也会展开，所以换解释器、
  换脚本路径都不用把绝对路径写死。
- 想换成搜索质量更好的 Tavily 之类，按同样格式再加一个 `type: http` 的 server 即可，
  合并规则（项目级覆盖用户级）不变。

## 常驻模式（无人值守）

前面各节都是「你敲回车、它才动」的前台模式。常驻模式补上另一半：**任务排队在磁盘上，
由外部触发执行，结果写成报告**。

```bash
# 1) 入队一条任务（不立刻执行）
lcode --enqueue "统计 tests 目录的测试数量并列出最慢的三个测试文件"

# 2) 查看队列
lcode --tasks

# 3) 立即处理一轮（供计划任务 / CI 调用）
lcode --resident-once

# 4) 或者前台常驻，每 5 分钟轮询一次
lcode --resident --interval 300
```

**为什么是「落盘队列 + 外部触发」，而不是一直开着进程：**

Windows 关机或休眠后长驻进程就没了。队列存在
`.lcode/resident/queue.json`，所以任何一次 `--resident-once` 都能捡起
**所有**待办任务。这比维护一个守护进程可靠得多，也让「每天跑一轮」变成一行计划任务。

```powershell
# 注册每天 09:00 自动跑一轮（不需要管理员权限）
powershell -ExecutionPolicy Bypass -File scripts\resident_task.ps1 -Time 09:00

# 立即跑一次 / 查看 / 删除
Start-ScheduledTask -TaskName lcode-resident
Get-ScheduledTaskInfo -TaskName lcode-resident
powershell -ExecutionPolicy Bypass -File scripts\resident_task.ps1 -Remove
```

### 权限语义（重要）

常驻模式**没有人在终端前审批**，所以默认收紧：

| 模式 | 只读工具 | 写文件 | 执行命令 |
| --- | --- | --- | --- |
| `default`（默认） | ✅ 放行 | ❌ 自动拒绝 | ❌ 自动拒绝 |
| `acceptEdits` | ✅ | ✅ | ❌ |
| `bypassPermissions` | ✅ | ✅ | ✅ |

```bash
lcode --resident-once --resident-mode acceptEdits
```

黑名单（`rm -rf /`、`dd of=/dev/...`）与路径沙箱在**任何**模式下都照旧生效，常驻模式
不能绕过它们。常驻模式还额外禁用了 `Agent` / `TeamCreate` / `SendMessage` 等需要人在场的
工具，需要时用 `--resident-allow` 单独放开。

由于没有人否决，「需要审批」的工具会被记成「用户拒绝执行」。Runner 会通过系统提示
告诉模型真实原因，让它在报告里写明**还缺什么权限**，而不是反复重试或假装成功。

### 落盘产物

```
.lcode/resident/
├── queue.json          # 任务状态（原子写入，损坏自动隔离为 .corrupt-*）
├── results/<id>.md     # 每轮完整报告
├── daemon.log          # 运行日志（UTF-8，pythonw 下也照写）
└── daemon.lock         # 单实例锁（记录 pid，僵尸锁自动接管）
```

- 任务严格 FIFO；单轮最多处理几条由 `--resident-batch`（默认 5），
  单条任务内最多迭代几轮由 `--resident-turns`（默认 20）。
- 进程被强杀时，`running` 状态会在下次启动时**自动放回待办**（`reset_running`）。
- 单条任务超时 `--resident-timeout`（默认 900s）记为失败，不影响后续任务。
- 同一时刻只允许一个常驻进程；锁文件里的 pid 已消失时会自动接管，
  硬杀留下的残留锁不会导致「永远起不来」。

## Skill 系统

Skill 是一段可复用的 SOP（Markdown + YAML frontmatter），既能在主对话中内联执行，
也能在隔离的 fork 中执行。

```markdown
---
name: review
description: 审查一段代码
mode: fork          # inline | fork
context: recent     # full | recent | none
---
请审查以下代码并给出改进建议：$ARGUMENTS
```

- 发现顺序：项目 `.lcode/skills/` 优先于用户 `~/.lcode/skills/`，同名以项目为准。
  支持目录 `skill-name/SKILL.md` 与单文件 `skill-name.md`。
- 启动时自动注册为 slash 命令（`/review`），并出现在补全菜单与 `/help` 中；
  `/skill list`、`/skill info <name>`、`/skill reload` 用于查看与热重载。
- 修改 `SKILL.md` 后无需重启，下次执行时自动重读。
- 也可以在对话里让模型调用 `LoadSkill` 激活 SOP，或用 `InstallSkill` 从
  skills.sh / GitHub 安装到 `~/.lcode/skills/`。
- inline 会把渲染后的 SOP 固定在环境上下文中并触发一轮主对话；fork 使用独立
  Conversation 执行，结果作为助手消息回流。`/clear` 会清除已激活的 Skill。

## Hook 生命周期挂钩

Hook 允许在 Agent 生命周期的关键节点执行脚本、注入提醒或发送 HTTP 通知。规则写在
项目级 `.lcode/hooks.yaml` 或用户级 `~/.lcode/hooks.yaml`，两者合并加载；
同名规则以先加载的项目级为准。

```yaml
hooks:
  - name: guard-dangerous-writes
    event: PreToolUse            # 11 个事件之一
    if:
      all_of:
        - field: tool_name
          match: {type: exact, value: Write}
        - field: tool_input.path
          match: {type: regex, value: "^src/"}
    action:
      type: shell
      command: "echo 'src/ 只读' >&2; exit 2"   # exit 2 = 拦截（仅拦截类事件）
    only_once: false
    timeout: 10s

  - name: notify-stop
    event: Stop
    async: true
    action:
      type: http
      url: https://example.com/hook
      headers: {Content-Type: application/json}
```

- 事件：`SessionStart`、`SessionEnd`、`SessionResume`、`UserPromptSubmit`、`Stop`、
  `PreUserMessage`、`PreToolUse`、`PostToolUse`、`PreCompact`、`PostCompact`、
  `Notification`。只有 `PreToolUse` / `UserPromptSubmit` 可拦截。
- 条件：`all_of` 与 `any_of` 二选一；`match.type` 支持 `exact`、`glob`、`regex`、
  `not`（`not` 通过 `inner` 递归）。字段用点路径读取，如 `tool_input.command`。
- 动作：`shell`（stdin 收到按键排序的 JSON；`sh -c` 执行，退出码 2 拦截）、
  `prompt`（文本进入下一轮 LLM 请求的 reminder，不写入对话历史）、`http`
  （POST JSON，可返回 `{"decision":"block","reason":"..."}`）、`subagent`（占位）。
- `async: true` 的 Hook 在后台执行，不能用于拦截类事件；`only_once` 在
  `/clear`、`/resume` 后重新生效。
- 配置/执行错误只输出到 stderr，不会阻止启动或中断对话。`/hooks` 可查看当前
  已加载的规则、触发事件、`[once]`/`[async]` 标记与来源文件。

## SubAgent 机制

主 Agent 可以通过 `Agent` 工具把独立任务交给子 Agent，避免长任务污染主对话上下文。

- **定义式**：指定 `subagent_type`，子 Agent 从空白对话 + 角色 system prompt 启动。
  内置角色：`general-purpose`（全工具）、`Explore`（只读探索）、`Plan`（只读规划）。
- **Fork 式**：不指定 `subagent_type`，深拷贝当前对话并注入 `<fork_boilerplate>`，
  首次请求复用父对话前缀以命中 prompt cache；Fork 路径强制后台执行。
- 角色文件为 Markdown + YAML frontmatter，放在 `.lcode/agents/*.md`
  （项目级）或 `~/.lcode/agents/*.md`（用户级），优先级：项目 > 用户 > 内置。

```markdown
---
name: wc-counter
description: 行数统计专家
disallowedTools: [write_file, edit_file]
model: inherit                # haiku | sonnet | opus | inherit
maxTurns: 5
permissionMode: dontAsk       # default | acceptEdits | plan | bypassPermissions | dontAsk
---

你是一个专门统计代码行数的 Agent，只能用 bash 跑 wc -l。
```

- 子 Agent 拥有独立 `Conversation` / `SessionRuntime`，共享 provider、工具注册表、
  权限 Engine 与 Hook Engine；定义式 Agent 看不到 `Agent` 工具，从根上阻断嵌套。
- 权限决策：父对话已批准规则 → 角色 `permissionMode` 兜底（`dontAsk` 自动放行
  Ask 级工具）→ 仍为 ASK 时升级到主 TUI 审批弹窗（标注 `[来自 SubAgent]`）。
- 后台任务：`run_in_background:true` 立即返回 `task_id`；前台子 Agent 超过
  120 秒自动转后台。任务完成后以 `<task-notification>` 注入主对话 reminder。
- 主 Agent 可用 `TaskList` / `TaskGet` / `TaskStop` 查询与操控任务，用
  `SendMessage` 给已完成的后台 Agent 续派任务。配置项
  `enable_subagent_background: false` 可强制全部前台（此时 Fork 不可用）。

> 本期未实现 ESC 手动切后台，仅支持显式后台与超时自动切后台。

## Worktree 隔离

并行场景下，主 Agent 与后台子 Agent 共享同一工作目录会产生文件冲突。Lcode 用
Git Worktree 做**空间维度**隔离：同一仓库多个工作目录、共享版本库、各自分支。

- 定义式 SubAgent 在 frontmatter 加 `isolation: worktree`，启动时自动创建临时
  Worktree（`agent-a<7hex>`），注入 `<worktree-context>` 提示，工具调用以该目录为
  explicit cwd；任务结束后无变更自动删除，有变更保留路径与分支并写回结果。
- Worktree 位于 `.lcode/worktrees/<flat_slug>/`，分支 `worktree-<flat_slug>`
  （嵌套 slug 的 `/` 变为 `+`）。临时 Worktree 超过 24 小时会在启动时自动清理，
  有未提交修改或未推送 commit 的目录 fail-closed 保留。
- 手动管理：`/worktree create <slug>`、`/worktree list`、`/worktree enter <slug>`、
  `/worktree exit [--remove] [--discard]`、`/worktree remove <slug> [--discard]`。
  进入后主 Agent 的后续工具调用以 Worktree 目录解析相对路径（进程 cwd 不变）。
- 创建时 best-effort 复制 `.lcode/config.yaml` / `settings.local.yaml`、配置
  git hooks、软链 `node_modules` / `.venv` / `vendor`，并按 `.worktreeinclude`
  复制被忽略的运行文件。session 保存在 `.lcode/worktree_session.json`。

```markdown
---
name: worktree-writer
description: 在 Worktree 内写文件的 Agent
permissionMode: dontAsk
maxTurns: 5
isolation: worktree
---
直接用 write_file 完成任务，不要询问。
```

## Agent Team

在 SubAgent / Worktree 之上，Lcode 支持把一个 Lead 与多个长期队员组成 Team：

- 主 Agent 调 `TeamCreate(team_name=...)` 创建团队；后端按
  `tmux → iTerm2 → in-process` 一次性自动检测，不做运行时回退。
- 用 `Agent(team_name=..., name="alice", subagent_type=...)` 派队员。每个队员有
  独立 Worktree（`.lcode/worktrees/team-<team>+<member>/`）与独立 session；
  队员之间通过共享任务（`TaskCreate` / `TaskGet` / `TaskList` / `TaskUpdate`）和
  文件邮箱（`SendMessage`）协作，不经过 Lead 中转。
- 队员结束后自动把 `is_active=False` 与 `[idle]` 通知写入 Lead 邮箱；TUI 每秒
  轮询 Lead 邮箱，空闲时自动发起一轮处理（`<team-update>` reminder）。
- `SendMessage` 支持 `to="alice"`、`to="<agent_id>"`、`to="*"` 广播与 `to="lead"`；
  结构化消息包括 `shutdown_request/response` 与 `plan_approval_request/response`。
- `plan_mode_required` 队员以 plan 模式起步，Lead 批准后切回 default 继续执行。
- 手动管理：`/team list`、`/team info <name>`、`/team delete <name> [--force]`、
  `/team kill <member>`。

Coordinator Mode（独立开关）需要同时满足配置与环境的双锁：

```yaml
features:
  coordinator_mode: true
  fork_teammate: false
```

```bash
LCODE_COORDINATOR_MODE=1 uv run lcode
```

开启后 Lead 工具集收窄为调度/只读/bash（无 `write_file` / `edit_file`），系统提示词
引导 Research → Synthesis → Implementation → Verification 四阶段，并要求派完队员
后停手等待汇报。收敛由 Lead 用 `bash git merge worktree-team-<team>+<member>` 完成。

## 从源码开发

```bash
uv sync          # 安装依赖（含 dev 依赖）
cp .lcode/config.yaml.example .lcode/config.yaml   # 编辑填入真实密钥
uv run lcode   # 或 uv run python -m lcode
```

不用 uv 的等价做法（需要 Python 3.12+）：

```bash
python -m venv .venv
. .venv/bin/activate                  # Windows: .venv\Scripts\activate
pip install -e .                      # 装 lcode 本体与运行依赖
pip install pytest pytest-asyncio ruff mypy   # dev 依赖
python -m lcode
```

## 发布到 PyPI

```bash
uv build                 # 生成 dist/lcode-<version>-*.whl
uv publish               # 需要 UV_PUBLISH_TOKEN，或配置 Trusted Publishing
```

没有 uv 时用标准工具，效果相同：

```bash
pip install build twine
python -m build          # 生成 dist/*.whl 与 *.tar.gz
twine upload dist/*
```

发布后安装脚本可切到 PyPI 渠道（默认仍走 GitHub 源码）：

```bash
LCODE_CHANNEL=pypi curl -LsSf --max-time 30 \
  https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.sh | sh
```

> **注意：PyPI 上的 `lcode` 名字不是本项目。** 那个 `lcode`（0.4.0）属于
> lcodePy-team 的等离子体尾场加速仿真包，`pip install lcode` 会**静默装成一个
> 完全无关的程序**。因此安装脚本在 `LCODE_CHANNEL=pypi` 时会先查 PyPI 元数据，
> 发现名字不属于本项目就直接报错退出，不会继续装错包。
>
> 真要走 PyPI 渠道，需要先换一个没被占用的发行名（`lcode-cli` / `lcode-agent`
> 也已被别的项目占用），然后：
>
> ```bash
> LCODE_CHANNEL=pypi LCODE_PYPI_NAME=<你的发行名> \
>   sh scripts/install.sh     # 或在安装命令前设这两个环境变量
> ```
>
> 发行名与顶层包名可以不同：`pyproject.toml` 里 `project.name` 改成新名字，
> `packages = ["src/lcode"]` 与 `[project.scripts] lcode = ...` 保持不变，
> `import lcode` 不受影响。

## 测试

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

没有 uv 时（已在虚拟环境里装好 dev 依赖）：

```bash
pytest
ruff check .
ruff format --check .
```
