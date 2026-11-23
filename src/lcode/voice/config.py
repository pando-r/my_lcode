"""语音配置：``.lcode/config.yaml`` 里的 ``voice`` 段。

为什么唤醒词是「小可」而不是「Lcode」：
语音识别用的是中文 vosk 小模型，它没有英文音素——念 "Lcode" 出来会被识别成
「埃欧可德」这类四不像，唤醒词基本用不了。而「小可」在中文里的发音
（xiǎo kě）和 Lcode 的尾音接近，模型识别稳定，所以拿它当**音近唤醒词**。
想换词改 ``voice.wake_words`` 就行，不认字的词需要自己实测。

配置刻意做得很薄：这里只放「必须能改」的东西。采样率、块大小、高通阶数
这些是实测调出来的，写死在代码里比暴露成配置更有用——配置项越多，
越容易出现「配了但没生效」这类最难查的问题（``vision`` 段刻意写全字段
也是同一个理由）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 音近唤醒词的默认集合。中文小模型对「小可」的四种声调/连读都会飘，
# 所以把常见误写一并收进来——漏唤醒比误唤醒更烦人（误唤醒最多多问一遍）。
DEFAULT_WAKE_WORDS = (
    "小可小可",
    "你好小可",
    "嘿小可",
    "黑小可",
    "小可",
    "可可",
)

# 唤醒词的**常见误听**。vosk 小模型对「小可」这两个音节很不稳，实测日志里
# 出现过「小口小口」「削个」「可可」——人明明喊的是唤醒词，识别结果却带着
# 这几个字，最后被当成指令发给 Agent，白跑几十秒换回一句「我没听清」。
#
# 判定刻意保守：只有当**整句除了误听词什么都不剩**时才算唤醒词回音。
# 否则「削个苹果」会被拆成唤醒 + 指令「苹果」发出去。
DEFAULT_WAKE_NEAR_MISSES = (
    "小口",
    "小柯",
    "小科",
    "小客",
    "小刻",
    "小珂",
    "小壳",
    "小颗",
    "小阔",
    "小渴",
    "小块",  # 2026-11-23 实机误唤醒：「小块小块」被当成了唤醒词
    "晓可",
    "肖可",
    "筱可",
    "削个",
    "肖克",
)

GREETING = "在"

# 唤醒后等指令的秒数。太短会「它刚说完在你就超时了」；
# 太长会让它在你没想好时一直占着麦克风。
AWAIT_TIMEOUT = 8.0

# 一句话最长 15 秒，超了强制收尾（防有人对着它念文章）
MAX_SPEECH_BLOCKS = 60

# 回答念完之后的「追问窗口」秒数。比 AWAIT_TIMEOUT 略长：这时候人刚听完
# 一段几十字的回答，正在想要问什么，不该被 8 秒这种短窗口打断。
FOLLOWUP_TIMEOUT = 12.0

# 同一段语音对话的空闲上限（秒）。超过这么久没说话，下一句算新话题：
# 「过了半小时再喊一句」不该被算成接着上一轮，否则上下文里全是无关旧问答。
THREAD_IDLE = 180.0

MIN_COMMAND_CHARS = 3  # 太短的多半是识别噪声，别硬塞给 Agent

# 看门狗：主循环连续这么多秒读不到一个音频块，就认定「麦克风流卡死了」。
# 卡死的样子很骗人：进程活着、端口还占着、日志一句不写，跟「安静地在听」
# 一模一样。2026-11-23 实机就这么聋了 1.5 小时（15:54 之后零日志，喊什么都不应）。
#
# 90 秒是两头折中：一个编码块 0.25 秒，正常情况下一秒该读 4 次；
# 而 say() 念完最长的一段回答（420 字）约 80 秒，期间麦克风本来就是停的
# （那段由 _speaking 标记豁免，不走这个阈值）。
WATCHDOG_STALE = 90.0

# 看门狗自己的巡检间隔。不必更密：卡死不是毫秒级故障，早 3 秒发现没用。
WATCHDOG_TICK = 5.0

# 精转写后端。加新后端时只改这里 + cli._voice_asr 的分支。
REFINE_BACKENDS = ("off", "sensevoice", "cloud")

# 朗读引擎：
#   edge = 微软在线神经语音（Edge「大声朗读」同款，要联网、要 pip install edge-tts），
#          听起来像真人——默认值就是它，因为 SAPI 的「人机感」是语音体验里最刺耳的一环；
#   sapi = Windows 自带 System.Speech，离线、零依赖，默认音色很机械。
TTS_ENGINES = ("edge", "sapi")
DEFAULT_TTS_ENGINE = "edge"

# 语音提问时附加在指令前的提示词。
# Agent 默认按「写文档」的风格回答（大段列表、表格、加粗标题），
# 念出来全是格式噪音。写上这段约束后朗读质量提升非常明显。
VOICE_HINT = (
    "【语音提问】用户在语音里问的，我的回答会被念出来。"
    "请直接用口语回答，两三句话内说清楚，不要用列表、表格、代码块和标题。"
)


@dataclass
class VoiceConfig:
    """``voice`` 段的运行时映射。"""

    # 唤醒词；空表示用 DEFAULT_WAKE_WORDS
    wake_words: list[str] = field(default_factory=list)
    # 唤醒词的常见误听；None = 用 DEFAULT_WAKE_NEAR_MISSES，[] = 关掉这条规则
    wake_near_misses: list[str] | None = None
    # 麦克风设备索引；None = 自动挑一个能录到声音的
    device: int | None = None
    # 「有人说话」的电平门限（滤波后的 RMS）
    threshold: float = 400.0
    # 高通滤波：切掉低频嗡声。几乎总是该开
    filter: bool = True
    cutoff: float = 180.0
    # vosk 模型目录；空 = ~/.lcode/voice/models/vosk-model-small-cn-0.22
    model_dir: str = ""
    # 念出回答（False = 只入队，结果去终端看）
    speak: bool = True
    speak_limit: int = 420
    # 等 Agent 答完的最长秒数
    answer_timeout: float = 240.0
    # 作用根目录；空 = 当前工作目录
    project: str = ""
    # 朗读引擎：edge（在线神经语音，像真人）/ sapi（Windows 自带，离线）
    tts_engine: str = DEFAULT_TTS_ENGINE
    # 音色名：edge 用 zh-CN-XiaoxiaoNeural 这种；sapi 用 Microsoft Huihui Desktop
    # 空 = 自动挑（edge 用默认女声，sapi 挑中文女声）
    tts_voice: str = ""
    # 连续对话：回答念完后再开一小段监听窗口，追问不用重新喊唤醒词
    conversation: bool = True
    # 精转写：vosk 只当触发器，整句交给更强的模型转一次。
    #   off        = 只用 vosk（42MB 小模型，会听错字）
    #   sensevoice = 本地 SenseVoice-Small（228MB，不联网不要 key，实测 80ms）
    #   cloud      = 走 audio 段的后端（qwen3-asr-flash 等，按秒计费）
    # 空串表示「没配」——只有这样才能让 effective_refine() 有机会看老字段
    # cloud_asr。写成 "off" 会让首行判断立刻返回，老配置永远读不到。
    refine: str = ""
    # 旧字段：cloud_asr: true 等价于 refine: cloud。只为兼容老配置保留。
    cloud_asr: bool | None = None
    # 太短的片段不值得精转写（也不值得等）
    refine_min_seconds: float = 0.6
    # 精转写失败时回落到 vosk 的结果；关掉则直接丢这句（宁可没答也不要答错）
    refine_fallback: bool = True
    # SenseVoice 的 onnxruntime 线程数。语音是短句，4 线程在 16 核机器上
    # 已经能到 80ms，再多线程只会和语音主循环抢 CPU。
    refine_threads: int = 4
    # 看门狗：多少秒读不到音频块就重建麦克风流；0 = 关掉。
    # 它修的是「进程活着但聋了」——不是进程崩溃（那种得靠自启/计划任务）。
    watchdog: float = WATCHDOG_STALE

    def effective_refine(self) -> str:
        """兼容老配置：没写 refine 但写了 cloud_asr 时按它推断。"""
        if self.refine in REFINE_BACKENDS:
            return self.refine
        if self.cloud_asr is True:
            return "cloud"
        if self.cloud_asr is False:
            return "off"
        return "off"

    def effective_wake_words(self) -> list[str]:
        words = [w.strip() for w in self.wake_words if w and w.strip()]
        return words or list(DEFAULT_WAKE_WORDS)

    def effective_wake_near_misses(self) -> list[str]:
        if self.wake_near_misses is None:
            return list(DEFAULT_WAKE_NEAR_MISSES)
        return [w for w in self.wake_near_misses if w]

    def effective_model_dir(self) -> Path:
        if self.model_dir:
            return Path(self.model_dir).expanduser()
        return default_model_dir()

    def effective_project(self) -> Path:
        return Path(self.project).expanduser() if self.project else Path.cwd()

    def ready(self) -> bool:
        # 配置层只判断模型在不在；依赖包（vosk/sounddevice）由导入时说话，
        # 因为「装了配置没装包」和「装了包没下模型」是两种完全不同的错误。
        return self.effective_model_dir().is_dir()


def voice_data_dir(project: str | Path | None = None) -> Path:
    """语音数据目录：模型、日志、实例锁。

    跟着「代码在 D、数据在 C」的约定落在 ``~/.lcode/voice``，
    这样升级/重装代码不用重新下 42MB 的模型。
    """
    return Path.home() / ".lcode" / "voice"


def default_model_dir() -> Path:
    return voice_data_dir() / "models" / "vosk-model-small-cn-0.22"


def _word_list(value: object) -> list[str] | None:
    """把 YAML 里的词表解析成 list；类型不对就返回 None（静默忽略）。

    单个词（没有逗号）也认：``wake_near_misses: 小口`` 和
    ``wake_near_misses: [小口]`` 等价——手写配置时很容易少写方括号。
    """
    if isinstance(value, str):
        parts = value.replace("，", ",").split(",")
        return [w.strip() for w in parts if w.strip()]
    if isinstance(value, list):
        return [str(w).strip() for w in value if str(w).strip()]
    return None


def parse_voice(raw: object) -> VoiceConfig | None:
    """解析 ``voice`` 段；没写就返回 None（等于整个语音功能不启用）。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("voice 必须是映射（wake_words / device / model_dir ...）")

    cfg = VoiceConfig()
    # 两种写法都认：YAML 列表，或一行逗号分隔（中文逗号也算）。
    # 逐项 strip——"甲，乙, 丙" 手工写出来的空格会带进唤醒词匹配里。
    if "wake_words" in raw:
        words = _word_list(raw.get("wake_words"))
        if words is not None:
            cfg.wake_words = words
    # 误听词单独判断「键在不在」：写成 [] 是**明确关掉**这条规则，
    # 和「没写 = 用默认值」是两回事（改过唤醒词的人多半想关掉）。
    if "wake_near_misses" in raw:
        misses = _word_list(raw.get("wake_near_misses"))
        if misses is not None:
            cfg.wake_near_misses = misses

    device = raw.get("device")
    if isinstance(device, int) and not isinstance(device, bool):
        cfg.device = device

    threshold = raw.get("threshold")
    if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
        cfg.threshold = float(threshold)

    if isinstance(raw.get("filter"), bool):
        cfg.filter = raw["filter"]

    cutoff = raw.get("cutoff")
    if isinstance(cutoff, (int, float)) and not isinstance(cutoff, bool):
        cfg.cutoff = float(cutoff)

    for key in ("model_dir", "project", "tts_voice"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            setattr(cfg, key, value.strip())

    if isinstance(raw.get("speak"), bool):
        cfg.speak = raw["speak"]

    engine = raw.get("tts_engine")
    if isinstance(engine, str) and engine.strip().lower() in TTS_ENGINES:
        cfg.tts_engine = engine.strip().lower()

    if isinstance(raw.get("conversation"), bool):
        cfg.conversation = raw["conversation"]

    if isinstance(raw.get("cloud_asr"), bool):
        cfg.cloud_asr = raw["cloud_asr"]

    refine = raw.get("refine")
    if isinstance(refine, str) and refine.strip().lower() in REFINE_BACKENDS:
        cfg.refine = refine.strip().lower()

    if isinstance(raw.get("refine_fallback"), bool):
        cfg.refine_fallback = raw["refine_fallback"]
    # 老配置里的 cloud_fallback 也认
    if isinstance(raw.get("cloud_fallback"), bool):
        cfg.refine_fallback = raw["cloud_fallback"]

    for key in ("refine_min_seconds", "cloud_min_seconds"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value > 0:
                cfg.refine_min_seconds = float(value)

    threads = raw.get("refine_threads")
    if isinstance(threads, int) and not isinstance(threads, bool) and threads > 0:
        cfg.refine_threads = threads

    limit = raw.get("speak_limit")
    if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
        cfg.speak_limit = limit

    timeout = raw.get("answer_timeout")
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
        cfg.answer_timeout = float(timeout)

    # 看门狗阈值。0 是「明确关掉」，负数当写错忽略（回到默认值）。
    watchdog = raw.get("watchdog")
    if isinstance(watchdog, (int, float)) and not isinstance(watchdog, bool) and watchdog >= 0:
        cfg.watchdog = float(watchdog)

    return cfg


__all__ = [
    "AWAIT_TIMEOUT",
    "DEFAULT_TTS_ENGINE",
    "DEFAULT_WAKE_NEAR_MISSES",
    "DEFAULT_WAKE_WORDS",
    "FOLLOWUP_TIMEOUT",
    "GREETING",
    "MAX_SPEECH_BLOCKS",
    "MIN_COMMAND_CHARS",
    "REFINE_BACKENDS",
    "THREAD_IDLE",
    "TTS_ENGINES",
    "VOICE_HINT",
    "WATCHDOG_STALE",
    "WATCHDOG_TICK",
    "VoiceConfig",
    "default_model_dir",
    "parse_voice",
    "voice_data_dir",
]
