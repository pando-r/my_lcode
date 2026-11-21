---
type: reference_material
title: lcode 中 dataclass field() 用法速查
created: '2026-11-16T18:27:34.382357+08:00'
updated: '2026-11-16T18:27:34.382357+08:00'
---
项目中 field() 主要用于给 dataclass 字段补充配置，核心是 default_factory（可变默认值不能直接写 = []，会共享同一对象）。三种用法：
1) 可变默认值：messages: list[Message] = field(default_factory=list)（llm/__init__.py）
2) 需调用函数生成默认值：_reminder_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)（agent/runtime.py）——repr=False 打印时不显示，compare=False 比较时不参与
3) 嵌套 dataclass：system: System = field(default_factory=System)（llm/__init__.py）
