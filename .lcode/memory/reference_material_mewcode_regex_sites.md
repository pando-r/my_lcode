---
type: reference_material
title: lcode 中三处正则用法位置
created: '2026-11-17T21:12:01.271491+08:00'
updated: '2026-11-17T21:12:01.271491+08:00'
---
lcode 源码中三处典型的 re.compile 正则，可作为讲解/参考示例：
1) 逐字节精确匹配（黑名单）permission/blacklist.py：r"rm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|~|\$HOME|/\*|\s*$)"（注意 \$HOME 转义字面美元符）
2) 简单提取（LCODE.md 指令）instructions/loader.py：r"^@include\s+(.+?)\s*$"（^ 要求行首，非贪婪 (.+?) 捕获路径）
3) 全文检索（记忆 JSON 数组）memory/manager.py：r"\[.*\]" 配 re.DOTALL，让 . 也能匹配换行。
提示：用户问“这是正则吗”类问题时，可用这三处对比讲解 r"..." 原始字符串、re.compile 预编译与元字符 ^ $ \s () . +? 的含义。
