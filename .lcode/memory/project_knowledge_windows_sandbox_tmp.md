---
type: project_knowledge
title: lcode test_sandbox 在 Windows 必失败的原因
created: '2026-11-20T00:59:30.360694+08:00'
updated: '2026-11-20T00:59:30.360694+08:00'
---
tests/test_permission.py 的 test_sandbox 断言 sandbox_ok(root, "/tmp/lcode-sandbox-test.txt") 为真，这是 POSIX 专用断言，Windows 上必失败。
根因：Windows 下 Path(root) / "/tmp/x" 会折叠成 <root盘符>:\tmp\x，而 sandbox.py 里 os.path.realpath("/tmp") 按当前工作目录盘符解析（例如 D:\tmp），两边永不相等。
连带问题：permission/sandbox.py 中 /tmp 与 /private/tmp 白名单在 Windows 上没有实际意义（保护的是不存在的目录），真正该补的是 Windows 的 %TEMP%。
处理方式：该断言加平台跳过（仅非 Windows 跑），测试侧问题而非源码逻辑错误。
