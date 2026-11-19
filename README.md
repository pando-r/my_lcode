# mewcode

一个多协议 LLM 终端对话客户端（Claude Code 风格）。第一块基石：在终端里与大模型进行
流式多轮对话，同时支持 Anthropic 与 OpenAI 两种协议（含自定义兼容端点）。

## 快速开始

macOS / Linux：

```bash
curl -LsSf https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.sh | sh
```

Windows（PowerShell）：

```powershell
irm https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1 | iex
```

脚本会检测并自动安装 `uv`（**无需自己准备 Python**），把 mewcode 装进隔离环境，并生成
`~/.mewcode/config.yaml`。之后只需要两步：

```bash
# 1) 把 providers[0].api_key 换成你的真实密钥
$EDITOR ~/.mewcode/config.yaml     # Windows: notepad %USERPROFILE%\.mewcode\config.yaml

# 2) 在任意项目目录启动
mewcode
```

不想装到全局？临时跑一次：

```bash
uvx --from git+https://github.com/pando-r/my_mecode.git mewcode
```

配置查找优先级：`--config` > `MEWCODE_CONFIG` 环境变量 > `./.mewcode/config.yaml` >
`~/.mewcode/config.yaml`。用户级放在前面那一条，保证装在任意目录都能直接跑；项目级方便
不同项目用不同模型。`mewcode --init` 可重新生成模板，`mewcode --version` 查看版本。

> 安全提示：mewcode 会调用 `bash` 执行命令并读写文件，所有写操作都会经过权限引擎。
> 首次在陌生仓库里使用时，建议逐条确认权限弹窗，不要盲目选择"全部允许"。

## 特性

- 流式逐字输出，结束后以 Markdown 美化定型。
- 单会话内维护完整多轮上下文。
- 启动时支持多 provider 选择（方向键列表）。
- 响应计时（"Imagining… (Ns)"）与可恢复的错误反馈。
- 多行输入：`Alt+Enter` 换行，`Enter` 提交；`/exit` 或 `Ctrl+C` 退出。

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

- 发现顺序：项目 `.mewcode/skills/` 优先于用户 `~/.mewcode/skills/`，同名以项目为准。
  支持目录 `skill-name/SKILL.md` 与单文件 `skill-name.md`。
- 启动时自动注册为 slash 命令（`/review`），并出现在补全菜单与 `/help` 中；
  `/skill list`、`/skill info <name>`、`/skill reload` 用于查看与热重载。
- 修改 `SKILL.md` 后无需重启，下次执行时自动重读。
- 也可以在对话里让模型调用 `LoadSkill` 激活 SOP，或用 `InstallSkill` 从
  skills.sh / GitHub 安装到 `~/.mewcode/skills/`。
- inline 会把渲染后的 SOP 固定在环境上下文中并触发一轮主对话；fork 使用独立
  Conversation 执行，结果作为助手消息回流。`/clear` 会清除已激活的 Skill。

## Hook 生命周期挂钩

Hook 允许在 Agent 生命周期的关键节点执行脚本、注入提醒或发送 HTTP 通知。规则写在
项目级 `.mewcode/hooks.yaml` 或用户级 `~/.mewcode/hooks.yaml`，两者合并加载；
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
- 角色文件为 Markdown + YAML frontmatter，放在 `.mewcode/agents/*.md`
  （项目级）或 `~/.mewcode/agents/*.md`（用户级），优先级：项目 > 用户 > 内置。

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

并行场景下，主 Agent 与后台子 Agent 共享同一工作目录会产生文件冲突。MewCode 用
Git Worktree 做**空间维度**隔离：同一仓库多个工作目录、共享版本库、各自分支。

- 定义式 SubAgent 在 frontmatter 加 `isolation: worktree`，启动时自动创建临时
  Worktree（`agent-a<7hex>`），注入 `<worktree-context>` 提示，工具调用以该目录为
  explicit cwd；任务结束后无变更自动删除，有变更保留路径与分支并写回结果。
- Worktree 位于 `.mewcode/worktrees/<flat_slug>/`，分支 `worktree-<flat_slug>`
  （嵌套 slug 的 `/` 变为 `+`）。临时 Worktree 超过 24 小时会在启动时自动清理，
  有未提交修改或未推送 commit 的目录 fail-closed 保留。
- 手动管理：`/worktree create <slug>`、`/worktree list`、`/worktree enter <slug>`、
  `/worktree exit [--remove] [--discard]`、`/worktree remove <slug> [--discard]`。
  进入后主 Agent 的后续工具调用以 Worktree 目录解析相对路径（进程 cwd 不变）。
- 创建时 best-effort 复制 `.mewcode/config.yaml` / `settings.local.yaml`、配置
  git hooks、软链 `node_modules` / `.venv` / `vendor`，并按 `.worktreeinclude`
  复制被忽略的运行文件。session 保存在 `.mewcode/worktree_session.json`。

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

在 SubAgent / Worktree 之上，mewcode 支持把一个 Lead 与多个长期队员组成 Team：

- 主 Agent 调 `TeamCreate(team_name=...)` 创建团队；后端按
  `tmux → iTerm2 → in-process` 一次性自动检测，不做运行时回退。
- 用 `Agent(team_name=..., name="alice", subagent_type=...)` 派队员。每个队员有
  独立 Worktree（`.mewcode/worktrees/team-<team>+<member>/`）与独立 session；
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
MEWCODE_COORDINATOR_MODE=1 uv run mewcode
```

开启后 Lead 工具集收窄为调度/只读/bash（无 `write_file` / `edit_file`），系统提示词
引导 Research → Synthesis → Implementation → Verification 四阶段，并要求派完队员
后停手等待汇报。收敛由 Lead 用 `bash git merge worktree-team-<team>+<member>` 完成。

## 从源码开发

```bash
uv sync          # 安装依赖（含 dev 依赖）
cp .mewcode/config.yaml.example .mewcode/config.yaml   # 编辑填入真实密钥
uv run mewcode   # 或 uv run python -m mewcode
```

## 发布到 PyPI

```bash
uv build                 # 生成 dist/mewcode-<version>-*.whl
uv publish               # 需要 UV_PUBLISH_TOKEN，或配置 Trusted Publishing
```

发布后安装脚本可切到 PyPI 渠道（默认仍走 GitHub 源码）：

```bash
MEWCODE_CHANNEL=pypi curl -LsSf .../scripts/install.sh | sh
```

## 测试

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
