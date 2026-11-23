"""麦克风探测与选择。

为什么值得单独一个模块（而不是塞在 wake.py 里）：
「打不开麦克风」是语音功能最常见的失败，而且失败原因千奇百怪——
设备被独占、增益太低、静音、开了降噪后本机录不到、MME 和 WASAPI 表现不同。
把探测逻辑独立出来，``lcode --voice-selftest`` 才能在不启动唤醒词循环的情况下
把「到底是哪一环坏了」直接打给人看。

实测经验（写进代码的那部分）：
- 优先 MME 主机 API。16k 采样率在 MME 上几乎所有设备都支持；
  走 WASAPI 时共享模式经常要 44.1k/48k，重采样由驱动做，延迟和掉帧都不稳。
- 选设备不看名字看**电平**：很多机器有多个「立体声混音」「虚拟设备」，
  名字看不出谁能用。静音状态下 RMS < 100 的直接跳过。
"""

from __future__ import annotations

from collections.abc import Callable

RATE = 16000
BLOCK = 4000  # 0.25 秒一片
CALIBRATE_SECONDS = 0.6

# 静音时的电平门限：低于这个值认定「这个设备录不到人声」
_SILENT_RMS = 100.0


def _noop(_msg: str) -> None:
    pass


def probe_level(sd, index: int, rate: int = RATE, block: int = BLOCK) -> float:
    """录一小段，返回峰值 RMS；打不开就返回 -1。"""
    import numpy as np

    try:
        info = sd.query_devices(index)
        if info.get("max_input_channels", 0) < 1:
            return -1.0
        with sd.RawInputStream(
            samplerate=rate, blocksize=block, dtype="int16", channels=1, device=index
        ) as stream:
            peak = 0.0
            for _ in range(int(CALIBRATE_SECONDS * rate / block)):
                data, _ = stream.read(block)
                arr = np.frombuffer(bytes(data), dtype=np.int16).astype(np.float32)
                if arr.size:
                    peak = max(peak, float(np.sqrt(np.mean(arr**2))))
            return peak
    except Exception:  # noqa: BLE001 - 任何设备异常都只是「这个不能用」
        return -1.0


def _groups(sd) -> list[list[int]]:
    """按主机 API 把输入设备分两组：MME 优先，其余在后。"""
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    groups: list[list[int]] = [[], []]
    for index, info in enumerate(devices):
        if info.get("max_input_channels", 0) < 1:
            continue
        api = info.get("hostapi", 0)
        name = hostapis[api]["name"] if api < len(hostapis) else ""
        groups[0 if "MME" in name else 1].append(index)
    return groups


def pick_input_device(
    sd, rate: int = RATE, block: int = BLOCK, log: Callable[[str], None] = _noop
) -> int | None:
    """挑一个真正能录到声音的输入设备；找不到返回 None（交给系统默认）。"""
    devices = sd.query_devices()
    for rank, group in enumerate(_groups(sd)):
        levels = [(probe_level(sd, i, rate, block), i) for i in group]
        levels = [(lv, i) for lv, i in levels if lv >= 0]
        if not levels:
            continue
        levels.sort(reverse=True)
        best_level, best = levels[0]
        label = "MME" if rank == 0 else "其它主机 API"
        if best_level < _SILENT_RMS:
            log(f"! {label} 设备全部收不到声音（RMS={best_level:.0f}），换一组再试")
            if rank == 0:
                continue
        log(f"麦克风：[{best}] {devices[best]['name']}（RMS={best_level:.0f}）")
        return best
    log("! 没找到有信号的麦克风，改用系统默认设备")
    return None


def list_devices(sd) -> str:
    """列出所有音频设备，末尾标出默认输入设备。"""
    body = str(sd.query_devices())
    return f"{body}\n\n默认输入设备：{sd.default.device[0]}"


def open_input(
    sd,
    device: int | None,
    attempts: int = 8,
    delay: float = 5.0,
    rate: int = RATE,
    block: int = BLOCK,
    log: Callable[[str], None] = _noop,
):
    """打开麦克风；打不开就等一会儿再试。

    为什么重试：开机自启时 Windows 音频服务可能还没就绪，第一次必然打不开。
    以前这里直接抛异常退出，表现就是「语音助手开机后从来没反应」——
    而它其实只是比音频服务早起了几秒。
    """
    last: Exception | None = None
    for i in range(attempts):
        dev = device if device is not None else pick_input_device(sd, rate, block, log)
        try:
            stream = sd.RawInputStream(
                samplerate=rate, blocksize=block, dtype="int16", channels=1, device=dev
            )
            stream.start()
            if i:
                log(f"麦克风在第 {i + 1} 次尝试才打开（开机时音频服务还没就绪）")
            return dev, stream
        except Exception as exc:  # noqa: BLE001
            last = exc
            log(f"! 麦克风打不开，第 {i + 1}/{attempts} 次：{type(exc).__name__}: {exc}")
            if i + 1 < attempts:
                import time

                time.sleep(delay)
    raise RuntimeError(f"麦克风始终打不开，放弃：{last}")


__all__ = [
    "BLOCK",
    "CALIBRATE_SECONDS",
    "RATE",
    "list_devices",
    "open_input",
    "pick_input_device",
    "probe_level",
]
