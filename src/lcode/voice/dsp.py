"""音频前处理：高通滤波，切掉麦克风收进来的低频嗡声。

为什么需要（实测数据）：
常见的台式机两路 MME 麦克风在**完全安静**时 RMS ≈ 1000（约 -30dBFS），
频谱一分析：97% 能量在 300Hz 以下，主峰约 34Hz —— 是低频嗡声
（风扇/机箱共振/电气干扰那一类），不是宽带白噪。
人声主要在 300~3400Hz，所以一个 180Hz 高通几乎不伤语音，
却能把嗡声压掉 30dB 以上，ASR 立刻从「什么都认不出」变成「基本能认」。

实现：级联 N 个一阶 IIR 高通
    y[n] = x[n] - a * x[n-1],  a = exp(-2*pi*fc/fs)
一阶只有 6dB/oct，所以默认级联 2 级（12dB/oct）。
好处是**整块向量化**——不需要逐样本递推，0.25 秒一块（4000 样本）
在 numpy 里是微秒级，16k 实时毫无压力。

numpy 只在函数内部导入：装了 ``lcode[voice]`` 才有，别的地方 import 本模块
不该因为缺 numpy 而失败。
"""

from __future__ import annotations

DEFAULT_CUTOFF = 180.0
DEFAULT_STAGES = 2


class HighPass:
    """有状态的流式高通：逐块处理，块与块之间状态连续。"""

    def __init__(
        self,
        sample_rate: int = 16000,
        cutoff_hz: float = DEFAULT_CUTOFF,
        stages: int = DEFAULT_STAGES,
    ) -> None:
        import numpy as np

        self.rate = sample_rate
        self.cutoff = cutoff_hz
        self.stages = max(1, stages)
        self._a = float(np.exp(-2.0 * np.pi * cutoff_hz / sample_rate))
        self._prev = [0.0] * self.stages  # 每级各自的上一个输入样本

    def process(self, samples):
        """输入 int16 或 float 一维数组，返回 float32（同长度）。"""
        import numpy as np

        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return x
        for i in range(self.stages):
            stage_in_last = float(x[-1])  # 这一级的「最后一个输入」= 下一块的状态
            prev = np.empty_like(x)
            prev[0] = self._prev[i]
            prev[1:] = x[:-1]
            x = x - self._a * prev
            self._prev[i] = stage_in_last
        return x

    def level(self, samples) -> float:
        """滤波后的 RMS，单位与输入相同（int16 时约 0~3000）。

        判断「有没有人说话」用这个值，比原始 RMS 准得多：
        原始 RMS 被嗡声抬到 1000 左右，人声很难超过它；
        滤波后静音只剩十几，正常说话能到 300~2000。
        """
        import numpy as np

        y = self.process(samples)
        if y.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(y**2)))

    def reset(self) -> None:
        self._prev = [0.0] * self.stages


def highpass_offline(
    pcm: bytes,
    sample_rate: int = 16000,
    cutoff_hz: float = DEFAULT_CUTOFF,
    stages: int = DEFAULT_STAGES,
) -> bytes:
    """对整段 int16 PCM 做高通（离线批处理，测试/对比时用）。"""
    import numpy as np

    arr = np.frombuffer(pcm, dtype=np.int16) if pcm else np.zeros(0, dtype=np.int16)
    if arr.size == 0:
        return pcm
    hp = HighPass(sample_rate, cutoff_hz, stages)
    out = hp.process(arr.astype(np.float32))
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


# ── 裁静音 + 归一化 ────────────────────────────────────────────────────


def envelope(samples, sample_rate: int = 16000, window_ms: float = 30.0):
    """滑动窗口 RMS 包络（不重叠），用来找「哪一段是人声」。"""
    import numpy as np

    x = np.asarray(samples, dtype=np.float32)
    win = max(1, int(sample_rate * window_ms / 1000))
    count = len(x) // win
    if count == 0:
        return np.zeros(0, dtype=np.float32)
    frames = x[: count * win].reshape(count, win)
    return np.sqrt(np.mean(frames**2, axis=1))


def trim_and_normalize(
    pcm: bytes,
    sample_rate: int = 16000,
    noise_ratio: float = 2.5,
    pad_ms: float = 220.0,
    peak: float = 0.92,
) -> bytes:
    """裁掉首尾静音，再把音量拉到满幅附近。

    为什么这两步对识别率帮助很大（实测）：
    - 麦克风增益偏低时，人声只有几千个量化级，模型「听不清」；
      归一化到 0.9 满幅等于免费给了 +6~12dB。
    - 前后静音太长会让模型在静音里乱猜，裁掉就干净了。
    噪声门限取「全段能量的 20 分位 × noise_ratio」，自适应底噪，
    不用手调阈值。
    """
    import numpy as np

    arr = np.frombuffer(pcm, dtype=np.int16) if pcm else np.zeros(0, dtype=np.int16)
    if arr.size == 0:
        return pcm
    x = arr.astype(np.float32)
    env = envelope(x, sample_rate)
    if env.size == 0:
        return pcm

    floor = float(np.percentile(env, 20))
    gate = max(floor * noise_ratio, float(np.max(env)) * 0.06, 60.0)
    active = np.nonzero(env > gate)[0]
    if active.size == 0:
        return pcm

    win = max(1, int(sample_rate * 30 / 1000))
    pad = int(sample_rate * pad_ms / 1000)
    start = max(0, active[0] * win - pad)
    end = min(len(x), (active[-1] + 1) * win + pad)
    cut = x[start:end]

    top = float(np.max(np.abs(cut)))
    if top > 1.0:
        cut = cut * (peak * 32767.0 / top)
    return np.clip(cut, -32768, 32767).astype(np.int16).tobytes()


def prepare_for_asr(
    pcm: bytes,
    sample_rate: int = 16000,
    cutoff_hz: float = DEFAULT_CUTOFF,
    stages: int = DEFAULT_STAGES,
) -> bytes:
    """识别前的标准处理：高通 → 裁静音 → 归一化。"""
    return trim_and_normalize(highpass_offline(pcm, sample_rate, cutoff_hz, stages), sample_rate)


__all__ = [
    "DEFAULT_CUTOFF",
    "DEFAULT_STAGES",
    "HighPass",
    "envelope",
    "highpass_offline",
    "prepare_for_asr",
    "trim_and_normalize",
]
