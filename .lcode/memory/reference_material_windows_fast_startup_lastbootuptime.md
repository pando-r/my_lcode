---
type: reference_material
title: Windows 快速启动会让 LastBootUpTime 不变
created: '2026-11-22T14:40:16.632706+08:00'
updated: '2026-11-22T14:40:16.632706+08:00'
---
K 的机器 `HiberbootEnabled = 1`（控制面板/电源选项里的「启用快速启动」开着）。

后果：用「关机」菜单关机再按电源键，Windows 实际走休眠恢复，**内核会话被还原，`(Get-CimInstance Win32_OperatingSystem).LastBootUpTime` 不变**。只有「重启」或断电重启才会刷新。

因此不能拿 LastBootUpTime 当「每次开机只做一次」的去重键——在最常见的关机方式下它会静默不发。同类需求的可靠替代：要么完全不去重，要么用别的信号。

排查用：`Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled`
