---
type: project_knowledge
title: lcode Windows Toast 需自注册 AUMID
created: '2026-11-20T21:04:28.604489+08:00'
updated: '2026-11-20T21:04:28.604489+08:00'
---
Windows Toast 通知走 CreateToastNotifier('lcode')，AUMID 未注册时 Show() 会静默丢弃（返回成功、注册表 LastNotificationAddedTime 也更新，但既不弹横幅也不进通知中心），代码侧完全看不出区别。
src/lcode/notify.py 现已在 _toast_windows() 前置调用 _ensure_aumid_registered()，在 HKCU\Software\Classes\AppUserModelId\lcode 下写 DisplayName=REG_SZ 和 ShowInSettings=REG_DWORD 1。
设计取舍：只补缺不覆盖（用户改过 DisplayName 就保留）；失败返回 False 不抛，横幅照发；模块级 _aumid_ready 保证每进程只写一次；ShowInSettings=1 才能出现在「设置 → 通知」列表里单独关闭。
测试用内存版假 winreg（monkeypatch sys.modules['winreg']）覆盖，不碰真实 HKCU。真机验证方法：reg delete 该键 → 发一条通知 → reg query 确认被补回。
