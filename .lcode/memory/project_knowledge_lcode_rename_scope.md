---
type: project_knowledge
title: lcode 改名 Lcode 的改动范围（仅用户可见层）
created: '2026-11-20T21:51:02.322610+08:00'
updated: '2026-11-20T21:51:02.322610+08:00'
---
项目对外显示名已从 Lcode 改为 Lcode，但只改了「用户看得见」的一层：prompt/modules.py 身份模块、prompt/__init__.py 的 render_banner、command/builtin_local.py 的 /status 标题、command/builtins.py 的 exit 描述、bootstrap.py 首次运行提示、cli.py 的 --description/--version/启动失败提示、resident/daemon.py 的 4 处通知标题、notify.py 新增常量 APP_NAME="Lcode"（Toast AUMID 与 notify-send -a 都用它）。

刻意未改（改了就是破坏性重构）：包名 lcode 及全部 import、CLI 入口命令 lcode、配置目录 .lcode/、环境变量 LCODE_*、类名 LcodeApp（含 6 个测试文件）。若要彻底改名为 lcode，需要迁移 .lcode/ 目录并保留旧环境变量兼容。
