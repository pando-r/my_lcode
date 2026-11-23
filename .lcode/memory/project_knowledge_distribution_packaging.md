---
type: project_knowledge
title: lcode 分发/打包现状与隐私风险
created: '2026-11-22T00:17:55.604228+08:00'
updated: '2026-11-22T00:17:55.604228+08:00'
---
lcode 的打包/分发现状（D:\Project\AgentProject\my_mecode）：

1) wheel 只含 Python 包：pyproject.toml 里 [tool.hatch.build.targets.wheel] packages = ["src/lcode"]，所以 scripts/*.ps1、docs/、.lcode/ 都不进包。pip/uv/pipx 装的用户拿不到 boot_brief.ps1、mail_task.ps1、brief.md 等。
2) install.ps1 / install.sh 不注册任何计划任务：findstr boot|brief|Register-ScheduledTask 无输出。boot brief、mail_task 都是纯手动跑，且默认不应该自动装（会往用户机器塞计划任务，且 brief.md 内容绑死个人身份）。
3) 配置模板在 src/lcode/templates/（config.yaml.example / mail.yaml.example / notify.yaml.example），由 bootstrap.py 的 ensure_user_config() 复制到用户目录；要新增内置模板（如 brief.md）应走同一套路径。
4) 隐私风险：.gitignore 已忽略 .lcode/config.yaml、settings.local.yaml、sessions/、resident/、notify.yaml、mail-outbox.jsonl，但 **.lcode/mail.yaml 与 .lcode/memory/ 仍被 git 跟踪**，mail.yaml 里含真实邮箱（含朋友地址），HEAD 已有提交。公开 push 前需 git rm --cached + .gitignore，或换成占位符。
