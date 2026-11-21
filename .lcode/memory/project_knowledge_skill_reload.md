---
type: project_knowledge
title: lcode Skill 加载器需手动 reload
created: '2026-11-19T23:16:22.167854+08:00'
updated: '2026-11-19T23:16:22.167854+08:00'
---
lcode 的 Skill 加载器在会话开始时扫描一次 skill 目录；会话进行中新安装的 skill 不会被自动识别，LoadSkill 会返回「未知 Skill: xxx」。解决：执行 `/skill reload` 重新扫描，之后 LoadSkill 和 `/skill名` 斜杠命令才生效。类似 Skill 名称也可能因拼写/前缀不符而查不到（报错信息里会列出当前可用 skill 列表）。
