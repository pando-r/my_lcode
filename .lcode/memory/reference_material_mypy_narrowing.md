---
type: reference_material
title: mypy 不跨方法收窄 Optional 属性（lcode wake.py 示例）
created: '2026-11-23T13:29:18.852178+08:00'
updated: '2026-11-23T13:29:18.852178+08:00'
---
`if self._asr is None: return False` 写在 `_cloud_ready()` 里，紧接着在 `refine()` 里调 `self._asr(...)`，mypy 仍报 `"None" not callable`——跨方法调用的类型收窄不成立（属性还可能被并发改动）。

修法：把「能拿到实例」和「判断就绪」合成一个返回非 None 对象的 helper：

```python
def _transcriber(self) -> Callable[[bytes], str] | None:
    if not self.opts.cloud_asr or self._asr is None:
        return None
    ...
    return self._asr

def refine(self, text: str) -> str:
    asr = self._transcriber()   # 局部变量收窄才有效
    if asr is None:
        return text
```

另外 `src/lcode/voice` 上 mypy 的两条既有告警（vosk、sounddevice 缺 stub / `import-untyped`）不用管，是第三方没 py.typed。
