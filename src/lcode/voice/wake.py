"""语音唤醒主循环：常驻监听麦克风，喊一句把它叫起来，它用语音回答你。

全链路：本地 vosk 小模型（42MB）听 → Lcode 答 → TTS 念（默认在线神经语音，可换回本机 SAPI）。
识别在本机，朗读默认要联网（音色像真人）；只有中间的「回答」会走 Lcode 的模型。

用法（通过 CLI，不直接用本模块）::

    lcode --voice                 # 唤醒 → 提问 → 念回答（就是 Siri 那样）
    lcode --voice-list-devices    # 列音频设备
    lcode --voice-selftest        # 录 4 秒，验证麦克风 + 识别

## 原始版本与本版本的结构差异

原版是一个 686 行的脚本，所有状态（``state`` / ``deadline`` / ``rec``）靠
嵌套函数 + ``nonlocal`` 传递，靠 ``globals()["QUIET_IO"] = ...`` 改全局开关。
搬进核心代码时改成 :class:`VoiceAssistant` 一个类：

- 状态变成字段，``--simulate`` 回放时也不用再改全局变量（那在库代码里是毒药——
  两次调用会互相污染）。
- 每个方法都能单独构造和测试，不用先起麦克风。
- ``sys.exit`` 全部改成抛异常，由 CLI 层决定退出码（库不该直接杀进程）。

业务逻辑（唤醒词剥离、部分结果提前唤醒、TTS 期间暂停麦克风并重建识别器、
超时收尾、模拟回放）逐行保留——那些是实机调出来的，改动只会引入回归。
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from lcode.voice import bridge, dsp, speech
from lcode.voice.config import (
    AWAIT_TIMEOUT,
    DEFAULT_WAKE_NEAR_MISSES,
    FOLLOWUP_TIMEOUT,
    GREETING,
    MAX_SPEECH_BLOCKS,
    MIN_COMMAND_CHARS,
    THREAD_IDLE,
    VOICE_HINT,
    WATCHDOG_STALE,
    WATCHDOG_TICK,
    VoiceConfig,
    default_model_dir,
    voice_data_dir,
)
from lcode.voice.devices import BLOCK, RATE, open_input, pick_input_device

IDLE, LISTENING = "idle", "listening"

# greet（念一个「在」）期间主循环丢弃音频块的最长秒数。
# 正常 0.5~2 秒就念完了（进程退出 = 播放已返回；在线语音还要算合成时间）；
# 万一 PowerShell 卡死，也不能一直吞音频——那等于把麦克风关掉了。
GREET_MUTE_MAX = 6.0

# 念答案前最多等 greet 的「在」多久（秒）。
# 在线语音合成要 1~2 秒，用户往往在「在」还没出声时就把指令说完了；
# 不等一下就是两个声音叠在一起，谁都听不清。上限压短：宁可叠音也别卡住主流程。
DRAIN_TTS_MAX = 3.0

# 麦克风重开失败后歇多久再试。设备被独占/蓝牙耳机还没连回来时，
# 立刻重试只会连着撞几十次墙，日志也全是噪音。
REOPEN_BACKOFF = 10.0

# 单实例锁用「绑定本地端口」而不是锁文件：进程被强杀时操作系统会立刻释放，
# 不会像 pid 文件那样留下一个骗人的锁。
LOCK_PORT = 47311

RUN_LOG_NAME = "wake.log"
COMMAND_LOG_NAME = "commands.jsonl"


class VoiceError(Exception):
    """缺依赖、缺模型、麦克风打不开——都是「可以拿给人看」的失败。"""


@dataclass
class VoiceOptions:
    """一次运行的选项。CLI 参数覆盖 :class:`VoiceConfig` 后塞进来。"""

    project: Path
    wake_words: list[str] = field(default_factory=list)
    # 唤醒词的常见误听（整句只有它们时算「只是喊了一声」）。
    # 默认跟着 config 走，直接构造 VoiceOptions 也不会漏掉这条规则。
    wake_near_misses: list[str] = field(default_factory=lambda: list(DEFAULT_WAKE_NEAR_MISSES))
    device: int | None = None
    threshold: float = 400.0
    filter: bool = True
    cutoff: float = 180.0
    model_dir: Path = field(default_factory=default_model_dir)
    speak_answer: bool = True
    speak_limit: int = 420
    answer_timeout: float = 240.0
    tts_voice: str = ""
    # 朗读引擎：edge（在线神经语音，像真人）/ sapi（Windows 自带，离线）
    tts_engine: str = speech.EDGE
    # 连续对话：念完回答后再开一小段监听窗口，追问不用重新喊唤醒词
    conversation: bool = True
    followup: float = FOLLOWUP_TIMEOUT
    # 精转写后端：off / sensevoice（本地，免费）/ cloud（audio 段，按秒计费）
    refine: str = "off"
    refine_min_seconds: float = 0.6
    refine_fallback: bool = True
    # 看门狗：多少秒读不到音频块就重建麦克风流（0 = 关）
    watchdog: float = WATCHDOG_STALE
    # 同一段会话的空闲上限：超过这么久没说话，下一句算新话题
    thread_idle: float = THREAD_IDLE
    quiet: bool = False
    debug: bool = False
    simulate: str = ""
    simulate_audio: bool = False
    auto_run: bool = True

    @classmethod
    def from_config(cls, cfg: VoiceConfig | None, *, project: Path | None = None) -> VoiceOptions:
        cfg = cfg or VoiceConfig()
        return cls(
            project=project or cfg.effective_project(),
            wake_words=cfg.effective_wake_words(),
            wake_near_misses=cfg.effective_wake_near_misses(),
            device=cfg.device,
            threshold=cfg.threshold,
            filter=cfg.filter,
            cutoff=cfg.cutoff,
            model_dir=cfg.effective_model_dir(),
            speak_answer=cfg.speak,
            speak_limit=cfg.speak_limit,
            answer_timeout=cfg.answer_timeout,
            tts_voice=cfg.tts_voice,
            tts_engine=cfg.tts_engine,
            conversation=cfg.conversation,
            refine=cfg.effective_refine(),
            refine_min_seconds=cfg.refine_min_seconds,
            refine_fallback=cfg.refine_fallback,
            watchdog=cfg.watchdog,
        )


# ── 日志 ──────────────────────────────────────────────────────────────


class VoiceLog:
    """同时写 stderr 和 ``~/.lcode/voice/wake.log``。

    pythonw（计划任务/开机自启）下 stdout/stderr 是 None，只靠 print 什么都留不下；
    而语音功能恰恰是「没人看着终端」的场景，日志是唯一的事后证据。
    """

    def __init__(self, path: Path | None = None, quiet: bool = False) -> None:
        self._path = path or (voice_data_dir() / RUN_LOG_NAME)
        self._quiet = quiet
        self._lock = threading.Lock()
        self._trim()

    def _trim(self) -> None:
        # 超过 2MB 就从头来过：语音日志只有最近一两天有意义
        try:
            if self._path.exists() and self._path.stat().st_size > 2 * 1024 * 1024:
                self._path.unlink()
        except OSError:
            pass

    def __call__(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        if not self._quiet:
            stream = sys.stderr
            if stream is not None:
                try:
                    print(line, file=stream, flush=True)
                except (UnicodeEncodeError, ValueError, OSError):
                    pass
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass


# ── 纯函数：唤醒词剥离 ────────────────────────────────────────────────


def _squash(text: str) -> str:
    """只留字母数字和汉字。

    vosk 会在词间插空格（「小可 小可」），识别结果还常带标点，
    比对之前必须先把这些噪音拿掉。
    """
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def strip_wake(
    text: str,
    wakes: list[str],
    near_misses: list[str] | tuple[str, ...] = (),
) -> tuple[bool, str]:
    """看文本里有没有唤醒词，返回 (是否命中, 剩下的指令)。

    先只保留字母数字和汉字，因为 vosk 会在词间插空格
    （「小可 小可」），还可能带标点。按长度倒序尝试，让「小可小可」
    优先于「小可」——否则会把「小可小可」拆成「小可」+「小可」指令。

    ``near_misses`` 是唤醒词的常见误听（「小口」「削个」…）。它们**只在整句
    除了误听词什么都不剩时**才算命中：否则「削个苹果」会被当成唤醒 + 指令
    「苹果」发出去。整句都是误听时剩余指令为空，调用方据此延长等待，
    而不是把这句话入队——这正是「小口小口」被当成指令、白跑一轮的 bug。
    """
    flat = _squash(text)
    if not flat:
        return False, ""
    low = flat.lower()
    for wake in sorted(wakes, key=len, reverse=True):
        if wake and wake.lower() in low:
            idx = low.index(wake.lower())
            return True, flat[idx + len(wake) :]
    rest = low
    for miss in sorted((m for m in near_misses if m), key=len, reverse=True):
        rest = rest.replace(miss.lower(), "")
    if rest != low and not rest:
        return True, ""
    return False, ""


def strip_leading_wake(
    text: str,
    words: list[str] | tuple[str, ...],
    min_repeat: int = 1,
) -> tuple[bool, str]:
    """反复剥掉**开头**的唤醒词/误听词，返回 (剥过东西没, 剩下的)。

    用在「已经在听指令」的状态里：「小口小口，现在几点」是一口气说的，
    识别成一句时 ``strip_wake`` 只剥掉中间那个「小可」，剩下的
    「小可现在几点」还得再剥一层。

    只认开头是有意的——句子中间出现的「小口」可能是正常词（「瓶口小」）。
    ``min_repeat`` 要求同一个词连续出现几次才剥：误听词用 2，
    因为单个「削个」可能是「削个木头」这块正常指令的一部分，
    而「小口小口」这类叠词在中文里几乎不可能是正常句子。
    """
    flat = _squash(text)
    ordered = sorted((w for w in words if w), key=len, reverse=True)
    hit = False
    while flat:
        low = flat.lower()
        best: tuple[int, str] | None = None
        for word in ordered:
            w = word.lower()
            count, rest = 0, low
            while rest.startswith(w):
                rest = rest[len(w) :]
                count += 1
            if count and (best is None or count > best[0]):
                best = (count, word)
        if best is None or best[0] < min_repeat:
            break
        count, word = best
        flat = flat[len(word) * count :]
        hit = True
    return hit, flat


def acquire_instance_lock() -> socket.socket | None:
    """抢单实例锁；抢不到返回 None。

    两个实例同时开麦克风，两边都会听到同一句话并各答一遍——必须挡住。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
    except OSError:
        s.close()
        return None
    return s


# ── 模型 ──────────────────────────────────────────────────────────────


def load_model(model_dir: Path, log: Callable[[str], None] = lambda _m: None):
    if not model_dir.is_dir():
        raise VoiceError(
            f"vosk 模型不存在：{model_dir}\n"
            "下载：python -m lcode.voice.download\n"
            "或手动解压到该目录（文件夹名必须是 vosk-model-small-cn-0.22）"
        )
    try:
        from vosk import Model, SetLogLevel
    except ImportError as exc:
        raise VoiceError("缺少 vosk。装语音依赖：pip install lcode[voice]") from exc
    SetLogLevel(-1)
    log("加载模型…")
    model = Model(str(model_dir))
    log("模型就绪")
    return model


# ── 语音助手 ──────────────────────────────────────────────────────────


class VoiceAssistant:
    """一个常驻的语音实例。构造不碰硬件，``run()`` 才开麦克风。"""

    def __init__(
        self,
        opts: VoiceOptions,
        log: Callable[[str], None] | None = None,
        say: Callable[[str], None] | None = None,
        asr: Callable[[bytes], str] | None = None,
    ) -> None:
        self.opts = opts
        self.log = log or VoiceLog(quiet=opts.quiet)
        # ``say`` 可注入：测试里换成收集器，就不用真的调 PowerShell 出声
        self._say_impl = say
        # ``asr`` 可注入：云端精转写。None = 只认 vosk（默认，音频不出本机）。
        self._asr = asr
        self._voice_cache: str | None = None
        self._model = None
        # 回放模式默认静音：模拟问题可能触发送稿，调试时被自己的电脑念出来很烦。
        # 和 opts.quiet 分开是因为它只在 run() 里按 simulate 决定，属于运行时状态。
        self._mute = False
        self._state = IDLE
        self._deadline = 0.0
        self._speech_blocks = 0
        # 连续对话的话题键与「第几轮」。两个一起用：
        # 前者的生命周期由空闲时间决定（daemon 靠它取上下文），
        # 后者的作用是让还挂在后台的旧回答别把状态抢回 LISTENING。
        self._thread = ""
        self._heard_at = 0.0
        self._turn = 0
        # 开过几段对话。话题键里带它，同一秒内重开一段也不会撞车。
        self._sessions = 0
        # greet 的朗读进程；它活着的时候主循环读到的音频块要丢掉（见 run）
        self._speak_proc: Any = None
        self._speak_until = 0.0
        # greet 的「正在合成」标记。在线语音要先联网合成 1~2 秒才拿到 mp3，
        # 这段没有进程可 poll，但同样不该把麦克风里的声音喂给识别器。
        self._tts_pending = False
        # vosk 没有 type stub，标注 Any 才能让 mypy 通过（否则推成 None 类型）
        self._rec: Any = None
        # 音频流同样标 Any：__init__ 里是 None，run()/看门狗会给它换上新流
        self._stream: Any = None
        # 当前这一句的原始 PCM（高通之后、喂识别器之前）。
        # 云端精转写要的是完整句子，所以从识别器重置那一刻开始攒，
        # 到 done 时整段取走——在 process_text 之前替换文本，
        # 这样「vosk 只认出两个字」的句子也有救。
        self._utter = bytearray()
        self._threads: list[threading.Thread] = []
        self._audio_lock = threading.RLock()
        self._command_log = voice_data_dir() / COMMAND_LOG_NAME
        # 看门狗用的三个状态：
        # ``_beat`` 是「主循环最后一次真的读到音频」的时刻（心跳），
        # ``_speaking`` 是「正在同步朗读」的计数——这期间麦克风本来就被 say() 停了，
        #   不能算卡死；
        # ``_stalled`` 是看门狗的举手：它已经把流 abort 了，请主线程换一条。
        self._beat = time.monotonic()
        self._speaking = 0
        self._stalled = False
        self._recoveries = 0
        self._watchdog_stop = threading.Event()
        # 巡检间隔做成实例字段，测试里能压到 10ms（常量仍是 5 秒）
        self._watchdog_tick = WATCHDOG_TICK

    # ── 硬件无关的小动作 ──────────────────────────────────────────────

    def beep(self, up: bool = True) -> None:
        if self._silent():
            return
        try:
            import winsound

            if up:
                winsound.Beep(880, 80)
                winsound.Beep(1320, 110)
            else:
                winsound.Beep(1200, 80)
        except Exception:  # noqa: BLE001 - 没声音比崩掉好
            pass

    def _silent(self) -> bool:
        """完全不出声：``--voice-quiet`` 或回放模式。"""
        return self.opts.quiet or self._mute

    def voice_name(self) -> str:
        """只挑一次音色，别每次念都去问一遍引擎。"""
        if self._voice_cache is None:
            if self.tts_engine() == speech.EDGE:
                # edge 的音色名是写死的字符串，不用问系统（问也问不到）
                self._voice_cache = self.opts.tts_voice or speech.DEFAULT_EDGE_VOICE
            else:
                self._voice_cache = self.opts.tts_voice or speech.pick_voice()
            if self._voice_cache:
                self.log(f"音色：{self._voice_cache}（{self.tts_engine()}）")
            else:
                self.log("! 没装中文 TTS 语音（只有英文），回答可能念得不对")
        return self._voice_cache

    def tts_engine(self) -> str:
        """实际会用的朗读引擎。

        edge 缺包（或不在 Windows）时这里就降成 sapi，别等到念的时候才发现——
        ``--voice-list-voices`` 和日志都要看到真实的那个。
        """
        if self.opts.tts_engine == speech.EDGE and not speech.edge_available():
            return speech.SAPI
        return self.opts.tts_engine

    def greet(self) -> None:
        """短反馈（「在」）——不阻塞主循环。

        这里刻意不用 ``say``：等 PowerShell 起进程要几百毫秒，同步念会把
        主循环卡住，麦克风缓冲随之溢出。代价是这时候音频流还开着，会把
        自己说的「在」录回去——所以主循环在朗读进程存活期间丢弃音频块
        （见 ``run`` 里的 ``_tts_running``），识别器因此也拿不到这段假指令。

        合成放在后台线程：在线语音光合成就要 1 秒多，同步做等于每喊一次
        都卡一下主循环。合成期间 ``_tts_pending`` 让 ``_tts_running()`` 照样
        返回真——没出声的时候也不该把麦克风内容当成指令。
        """
        if self._silent():
            return
        self._speak_until = time.monotonic() + GREET_MUTE_MAX
        self._tts_pending = True

        def run() -> None:
            try:
                # 挑音色也放线程里：sapi 那条要起 PowerShell 问系统装了哪些音色，
                # 第一次调用能花半秒，放在主线程就等于每喊一次卡一下。
                self._speak_proc = speech.speak_async(
                    GREETING, voice=self.voice_name(), engine=self.tts_engine()
                )
            finally:
                self._tts_pending = False

        threading.Thread(target=run, name="voice-greet", daemon=True).start()

    def _tts_running(self) -> bool:
        """greet 是否还在出声（或还在合成）。

        判据是「播放进程还活着」：播放是同步的，进程退出就意味着真的念完了，
        比自己估一个时长可靠。合成阶段没有进程，用 ``_tts_pending`` 顶上。
        """
        if self._tts_pending and time.monotonic() < self._speak_until:
            return True
        if self._speak_proc is None:
            return False
        if self._speak_proc.poll() is None and time.monotonic() < self._speak_until:
            return True
        # 念完了，或者进程卡死超时：两种情况都不能再吞音频
        self._speak_proc = None
        return False

    def _drain_tts(self, timeout: float = DRAIN_TTS_MAX) -> None:
        """等 greet 的「在」说完（或超时）再念下一句。

        在线语音合成要 1~2 秒，用户常常在「在」还没出声时就把指令说完了。
        不在这里等一下，`say("好")` 会和「在」叠在一起——两个声音同时响，
        哪个都听不清。上限压到 3 秒：宁可偶尔叠音，也不能把主流程卡住。
        """
        deadline = time.monotonic() + timeout
        while self._tts_running() and time.monotonic() < deadline:
            time.sleep(0.05)

    def say(self, text: str) -> None:
        """念答案：先关麦克风。

        否则自己的 TTS 会被麦克风收回去 → 触发唤醒词 → 自己把自己叫醒。
        所以：暂停流 → 等「在」念完 → 同步念完 → 重新开流、丢掉尾部残余 →
        重建识别器。全程持 audio_lock，主循环此时读不到麦克风，也就不会误唤醒。
        """
        if self._silent() or not text:
            return
        if self._say_impl is not None:
            # 注入了 say 就不碰音频流：测试里用它收集文本，不真的出声
            self._say_impl(text)
            return
        with self._audio_lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._drain_tts()
            # 念的这段可能长达 80 秒（420 字），期间主循环读不到音频是正常的：
            # 打个标记让看门狗别把「自己在念」当成「麦克风卡死」。
            self._speaking += 1
            try:
                speech.speak(text, voice=self.voice_name(), engine=self.tts_engine())
            finally:
                self._speaking -= 1
                if self._stream is not None:
                    try:
                        self._stream.start()
                        for _ in range(3):  # 丢掉尾部残余，清一下缓冲
                            self._stream.read(BLOCK)
                    except Exception:  # noqa: BLE001
                        pass
                self._new_recognizer()
            # 念完刚清过缓冲，心跳从这里重新起算
            self._beat = time.monotonic()

    # ── 状态切换 ──────────────────────────────────────────────────────

    def _new_recognizer(self) -> None:
        from vosk import KaldiRecognizer

        assert self._model is not None
        self._rec = KaldiRecognizer(self._model, RATE)
        # 新识别器 = 新句子：之前攒的 PCM 已经没人要了
        self._utter = bytearray()

    def wake_up(self) -> None:
        """切到「听指令」状态。

        注意**不**重建识别器：如果把已经含唤醒词的那句话的识别器重置掉，
        用户接着说（可能还在同一个 utterance 里）就接不上了。
        """
        self.beep(True)
        self.greet()
        self._state = LISTENING
        self._deadline = time.monotonic() + AWAIT_TIMEOUT
        self._speech_blocks = 0
        self.log(f"→ 请说指令（{AWAIT_TIMEOUT:.0f} 秒内）")

    # ── 精转写（vosk 只当触发器） ──────────────────────────────────────

    def _cloud_ready(self) -> bool:
        """精转写是否可用（本地 SenseVoice 或云端，取决于 ``opts.refine``）。"""
        return self._transcriber() is not None

    def _transcriber(self) -> Callable[[bytes], str] | None:
        """就绪的精转写函数；不可用返回 None。

        单独抽出来是为了让「能拿到」和「真的调用」在代码上是同一处判断——
        ``_cloud_ready()`` + ``self._asr(...)`` 那种写法 mypy 看不出
        ``self._asr`` 非空（跨方法调用的收窄不成立），运行期也可能被并发改掉。
        """
        if self.opts.refine == "off" or self._asr is None:
            return None
        # 注入的函数带 ready()（cli 里那份会检查 audio 段配全没配全）；
        # 测试里的假函数没有这个属性，视作就绪。
        check = getattr(self._asr, "ready", None)
        if callable(check) and not check():
            return None
        return self._asr

    def refine(self, text: str) -> str:
        """把这一句交给更强的模型重转一遍；不可用/失败时原样返回 vosk 的结果。

        只在「这句话里已经有唤醒词、或者已经在听指令」时才值得调用——
        对着环境噪声转发既费 CPU 又添延迟。
        """
        asr = self._transcriber()
        if asr is None:
            return text
        audio = bytes(self._utter)
        self._utter = bytearray()
        seconds = len(audio) / (RATE * 2)
        if seconds < self.opts.refine_min_seconds:
            if self.opts.debug:
                self.log(f"  （片段只有 {seconds:.1f} 秒，不值得精转写）")
            return text

        self.log(f"→ 精转写（{self.opts.refine}，{seconds:.1f} 秒音频）")
        try:
            refined = (asr(audio) or "").strip()
        except Exception as exc:  # noqa: BLE001 - 兜底：语音不能因为转写挂掉
            refined = ""
            self.log(f"! 精转写异常：{type(exc).__name__}: {exc}")

        if not refined:
            return text if self.opts.refine_fallback else ""

        # 精转写输出的是整句，通常带着唤醒词前缀（「小可小可，现在几点」）。
        # 这里先剥掉，后面的状态机就还能按「有指令」处理。
        hits, rest = strip_wake(refined, self.opts.wake_words, self.opts.wake_near_misses)
        if hits:
            _more, rest = strip_leading_wake(
                rest, [*self.opts.wake_words, *self.opts.wake_near_misses]
            )
            refined = rest
        refined = refined.strip("，。,.!！?？ ")
        if not refined:
            # 只转出了唤醒词：这句确实没有指令，退回 vosk 的判断
            return text
        if refined != _squash(text).strip():
            self.log(f"  精转写：{refined}（vosk 原为「{text}」）")
        return refined

    def _should_refine(self, text: str) -> bool:
        """这句值不值得花一次精转写。

        「已经在听指令」或者「这句话里有唤醒词」= 用户确实在跟它说话；
        其余情况（电视声、聊天声）不该送进去转。
        """
        if self._state == LISTENING:
            return True
        hits, _rest = strip_wake(text, self.opts.wake_words, self.opts.wake_near_misses)
        return hits

    # ── 指令处理 ──────────────────────────────────────────────────────

    def handle_command(self, text: str, spawn: Callable[[Callable[[], None]], None]) -> None:
        text = text.strip("，。,.!！?？ ")
        if not text:
            return
        self.log(f"??  {text}")

        # 一句话太短多半是识别噪声。实测「现在在」这种会被识别成两三个字，
        # 交给 Agent 只会换来一篇莫名其妙的长回答，不如让用户重说一遍。
        if len(text) < MIN_COMMAND_CHARS:
            self.log(f"太短（只有 {len(text)} 个字），当作没听见")
            self.say("没听清，再说一次。")
            return

        self._log_command(text)

        # 一句问话 = 一轮。这个计数有两个用处：给话题键加后缀保证唯一，
        # 以及让还在后台等回答的旧轮次别把状态抢回 LISTENING（见 _open_followup）。
        self._turn += 1
        thread = self._current_thread()

        if self.opts.speak_answer:
            spawn(lambda: self.answer_voice(text, thread))
        else:
            try:
                jid = bridge.enqueue(text, self.opts.project, thread=thread)
                self.log(f"→ 已交给 Lcode 常驻队列 {jid}")
            except bridge.BridgeError as exc:
                self.log(f"! {exc}")

    def _current_thread(self) -> str:
        """当前这段语音对话的话题键。

        daemon 靠 ``thread`` 把上一轮问答拼进上下文（``_prompt_with_thread``），
        所以连续追问能听懂「那它呢」；而空闲超过 ``thread_idle`` 就换一个新键，
        免得「半小时后再问一句」被塞进一堆无关的旧对话里。

        键里带 ``_sessions`` 是为了同一秒内重开一段对话也不会撞车。
        """
        now = time.monotonic()
        if not self._thread or now - self._heard_at > self.opts.thread_idle:
            self._sessions += 1
            self._thread = f"voice-{datetime.now():%Y%m%d-%H%M%S}-{self._sessions}"
        self._heard_at = now
        return self._thread

    def _log_command(self, text: str) -> None:
        try:
            self._command_log.parent.mkdir(parents=True, exist_ok=True)
            with self._command_log.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {"time": datetime.now().isoformat(timespec="seconds"), "text": text},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            pass

    def answer_voice(self, text: str, thread: str = "") -> None:
        """提问 → 入队 → 催一轮 → 等结果 → 念出来。

        为什么先「好」再等：LLM 要几十秒，中间没有任何反馈会让人以为它没听见。

        ``thread`` 透传进队列，决定这一轮能不能看到上一轮问答（连续对话）。
        念完之后如果没有更新的一轮在跑，就再开一小段监听窗口，
        让「接着问」不用重新喊唤醒词。
        """
        project = self.opts.project
        my_turn = self._turn
        try:
            job = bridge.enqueue(VOICE_HINT + "\n\n" + text, project, thread=thread, say=text)
        except bridge.BridgeError as exc:
            self.log(f"! {exc}")
            self.say("连不上 Lcode 的队列，没听进去。")
            return

        self.log(f"→ {job} 已入队，开始等回答")
        self.say("好")
        if self.opts.auto_run:
            bridge.trigger_run_once(project)

        status, body = bridge.wait_for(
            project,
            job,
            timeout=self.opts.answer_timeout,
            on_tick=lambda s: self.log(f"   状态：{s}"),
        )

        if status == "done":
            spoken = speech.clip(speech.sanitize(body), self.opts.speak_limit)
            self.log(f"→ 回答：{spoken[:120]}{'…' if len(spoken) > 120 else ''}")
            self.say(spoken or "答完了，但没什么可说，去终端看看。")
        elif status == "failed":
            detail = speech.clip(speech.sanitize(body), 120)
            self.log(f"! 失败：{detail}")
            self.say("这次没跑成。" + detail)
        else:
            self.log("! 等超时了，任务还在跑")
            self.say("还没答完，过一会儿去终端看结果")

        self._open_followup(my_turn)

    def _open_followup(self, turn: int) -> None:
        """念完回答再开一小段监听窗口：追问不用重新喊唤醒词。

        三个前提缺一不可：

        - ``conversation`` 没被关掉（``--voice-no-conversation`` / 配置）；
        - 真的在听麦克风（回放模式没有后续输入，静音模式听不到答案）；
        - ``turn`` 还是当前轮：如果这期间用户又唤醒并问了新问题，
          就不能把状态抢回 LISTENING——那会打断正在处理的新一轮。

        用 ``beep(False)`` 而不是唤醒时的上行双音：一声轻响表示「轮到你说」，
        两种提示音区分开，人不用看日志也知道现在是什么阶段。
        """
        if not self.opts.conversation:
            return
        if self._silent() or self._stream is None:
            return
        if turn != self._turn:
            return
        with self._audio_lock:
            self._new_recognizer()
        # 先设 deadline 再改 state：主循环看到 LISTENING 时窗口必须已经算好，
        # 否则会用上一轮的过期 deadline 立刻判超时。
        self._speech_blocks = 0
        self._deadline = time.monotonic() + self.opts.followup
        self._state = LISTENING
        self.beep(False)
        self.log(f"→ 接着说就行（{self.opts.followup:.0f} 秒内）")

    # ── 文本 → 状态机 ────────────────────────────────────────────────

    def process_text(self, text: str, spawn: Callable[[Callable[[], None]], None]) -> None:
        """拿到一句完整识别结果，按状态决定它是什么。"""
        text = text.strip()
        if not text:
            return
        wakes = self.opts.wake_words
        near = self.opts.wake_near_misses

        if self._state == LISTENING:
            hits, rest = strip_wake(text, wakes, near)
            if hits:
                # 再剥一层开头的残留：「小口小口，现在几点」会被识别成一句，
                # strip_wake 只剥掉中间那个「小可」，剩下的还有误听词开头。
                _hit_more, rest = strip_leading_wake(rest, [*wakes, *near])
                cmd = rest
            else:
                # 没正式唤醒词：只剥「连喊两遍」的误听词（「小口小口」）。
                # 单遍的「削个」可能是「削个木头」的开头，不能动。
                _hit_more, cmd = strip_leading_wake(_squash(text), near, min_repeat=2)
            if cmd:
                self._state = IDLE
                self._new_recognizer()  # 这句已经消费掉了，别影响下一句
                self.handle_command(cmd, spawn)
            else:
                # 只听到了唤醒词（或它的误听），没听到指令：再给一次机会。
                # 这里**不能**把整句当指令发出去——「小口小口」四个字发给 Agent
                # 只会换回一句「我没听清」，白跑几十秒（实机日志里反复出现）。
                self._deadline = time.monotonic() + AWAIT_TIMEOUT
                self._speech_blocks = 0
                self.log("→ 还在等")
            return

        hits, rest = strip_wake(text, wakes, near)
        if not hits:
            # 认不出正式唤醒词时，再看开头有没有连喊两遍的误听词：
            # 「小口小口，现在几点」很可能就是喊了唤醒词，只是整句都被听错。
            hits, rest = strip_leading_wake(_squash(text), near, min_repeat=2)
        if not hits:
            if self.opts.debug:
                self.log(f"  （不是唤醒词，略过：{text}）")
            return
        if rest:
            # 「小可，现在几点」一句说完
            self.beep(True)
            self.greet()
            self.handle_command(rest, spawn)
        else:
            self.wake_up()

    # ── 线程 ──────────────────────────────────────────────────────────

    def _spawn(self, job: Callable[[], None]) -> None:
        """回答放后台线程：主循环继续转，还能继续唤醒。"""

        def runner() -> None:
            try:
                job()
            except Exception as exc:  # noqa: BLE001 - 后台线程炸了不该影响主循环
                self.log(f"! 应答线程出错：{type(exc).__name__}: {exc}")

        t = threading.Thread(target=runner, daemon=True)
        self._threads.append(t)
        t.start()
        self._threads[:] = [x for x in self._threads if x.is_alive()]

    # ── 看门狗：麦克风流卡死时自救 ────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """每 ``watchdog_tick`` 秒查一次心跳，卡死就把麦克风流捅醒。

        为什么需要它：麦克风流卡死的样子是「进程还活着、端口还占着、日志一句
        不写」——从外面看跟「安静地在听」完全一样。2026-11-23 实机就这样聋了
        一个半小时：15:54 之后零日志，喊什么都不应，进程却一直占着单实例锁，
        连「重启一份」都会被拒。
        """
        while not self._watchdog_stop.wait(self._watchdog_tick):
            if self.opts.watchdog <= 0 or self._stream is None:
                continue
            if self._speaking or self._stalled:
                # 自己正在念（麦克风本来就被 say() 停了），或者已经举过手了
                continue
            stale = time.monotonic() - self._beat
            if stale < self.opts.watchdog:
                continue
            self._stalled = True
            self._recoveries += 1
            self.log(
                f"! 看门狗：{stale:.0f} 秒没读到音频块（麦克风流卡死），"
                f"第 {self._recoveries} 次重建"
            )
            self._abort_stream()

    def _abort_stream(self) -> None:
        """从别的线程把卡住的 read() 捅醒。

        必须绕开 ``_audio_lock``：主线程此刻正抱着那把锁卡在 read 里，
        这里再去要锁就是自己跟自己死锁。``abort()`` 是 sounddevice 里唯一
        能从外部打断阻塞读的办法（``stop()`` 兜底，有些后端只认其中一个）。
        """
        stream = self._stream
        if stream is None:
            return
        for name in ("abort", "stop"):
            fn = getattr(stream, name, None)
            if not callable(fn):
                continue
            try:
                fn()
            except Exception:  # noqa: BLE001 - 流已经烂了，报错也没意义
                pass

    def _open_stream(self):
        """打开一条新的麦克风流（单独抽出来，测试里整个替换掉）。"""
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise VoiceError("缺少 sounddevice。装语音依赖：pip install lcode[voice]") from exc
        return open_input(sd, self.opts.device, rate=RATE, block=BLOCK, log=self.log)

    def _reopen_stream(self) -> bool:
        """关掉旧流、重开一条，识别器一并重置。失败返回 False。

        只有主线程会走到这里，所以 ``self._stream`` 的创建与关闭都归主线程管；
        看门狗只负责 ``abort()``，不碰这条流的所有权。
        """
        old, self._stream = self._stream, None
        # 心跳从这一刻先刷一次：重开要花几百毫秒到几秒（open_input 内部还会重试），
        # 不先刷新的话，看门狗会在这个窗口里再举一次手——实测会连着建两条流。
        self._beat = time.monotonic()
        if old is not None:
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            _dev, stream = self._open_stream()
        except Exception as exc:  # noqa: BLE001 - 打不开就等下一轮
            self.log(f"! 麦克风重开失败：{type(exc).__name__}: {exc}")
            self._beat = time.monotonic()
            # 牌要放回去：否则看门狗以为「已经在处理了」，永远不会再试第二次
            self._stalled = False
            return False
        with self._audio_lock:
            self._stream = stream
            self._new_recognizer()
        self._stalled = False
        self._beat = time.monotonic()
        self.log("麦克风已重建，继续听")
        return True

    def _backoff(self) -> None:
        """重开也失败了：歇一会儿再来，别把 CPU 烧在重启循环上。"""
        self._beat = time.monotonic()
        time.sleep(REOPEN_BACKOFF)

    def _read_block(self) -> bytes | None:
        """读一片音频；流坏了就换一条。返回 None 表示这一轮没有可用数据。

        三种「流坏了」的形态都在这里收口：read 抛异常（设备被拔）、
        看门狗已经 abort 过（``_stalled``）、流已经没了（重开失败后留在 None）。
        """
        stream = self._stream
        if stream is None:
            # 上一次重开失败了：接着试，不然麦克风就永远没了
            if not self._reopen_stream():
                self._backoff()
            return None
        try:
            with self._audio_lock:
                data, overflowed = stream.read(BLOCK)
        except Exception as exc:  # noqa: BLE001 - 读失败一律按「换一条流」处理
            if not self._stalled:
                # 看门狗已经写过一行「卡死」，这里再报「读失败」只是重复噪音
                self.log(f"! 读麦克风失败：{type(exc).__name__}: {exc}")
            if not self._reopen_stream():
                self._backoff()
            return None
        self._beat = time.monotonic()
        if self._stalled:
            # 看门狗已经把这个流打断了：这一片不可信（可能是排空缓冲的残余）
            if not self._reopen_stream():
                self._backoff()
            return None
        if overflowed:
            self.log("! 音频缓冲区溢出（CPU 太忙，可忽略）")
        return bytes(data)

    # ── 音频输入 + 主循环 ─────────────────────────────────────────────

    def _wav_blocks(self, path: Path):
        """把 wav 读成 16k 单声道 int16 的分片（回放测试用，不需要麦克风）。"""
        import numpy as np

        try:
            with wave.open(str(path), "rb") as fh:
                rate = fh.getframerate()
                channels = fh.getnchannels()
                width = fh.getsampwidth()
                frames = fh.readframes(fh.getnframes())
        except OSError as exc:
            raise VoiceError(f"读不了回放文件 {path}：{exc}") from exc
        if width != 2:
            raise VoiceError(f"只支持 16bit wav，实际 {width * 8}bit")
        arr = np.frombuffer(frames, dtype=np.int16)
        if channels > 1:
            arr = arr.reshape(-1, channels)[:, 0]
        if rate != RATE:
            target_len = int(len(arr) * RATE / rate)
            idx = (np.arange(target_len) * rate / RATE).astype(np.int64)
            arr = arr[idx]
        data = arr.astype(np.int16).tobytes()
        step = BLOCK * 2  # int16 = 2 字节
        for start in range(0, len(data), step):
            yield data[start : start + step]

    def run(self, model) -> int:
        """开麦克风（或回放 wav）跑主循环，直到 Ctrl+C。"""
        import numpy as np

        self._model = model
        self._new_recognizer()
        hp = dsp.HighPass(RATE, self.opts.cutoff) if self.opts.filter else None
        simulate = Path(self.opts.simulate) if self.opts.simulate else None
        # 「在听麦克风」这件事必须显式记下来，不能靠 self._stream is not None 推断：
        # 重开失败时那条流会短暂变成 None，若按「没流 = 回放」分支走，
        # 就会去 next(None) 直接 TypeError 崩掉主循环。
        mic_mode = simulate is None

        if simulate:
            # 回放时默认静音：「现在几点」这类模拟问题可能触发送稿，
            # 让人在调试时被自己的电脑念出来。需要出声再加 --voice-simulate-audio
            self._mute = not self.opts.simulate_audio
            self.log(f"回放模式：{simulate}")
            source = self._wav_blocks(simulate)
        else:
            _dev, self._stream = self._open_stream()
            self.log(f"唤醒词：{' / '.join(self.opts.wake_words)}")
            if self.opts.speak_answer:
                engine = self.tts_engine()
                self.log(f"朗读：{engine}（{self.voice_name()}）")
                if engine != self.opts.tts_engine:
                    self.log("! 配置要的是 edge，但没装 edge-tts，已降级为 Windows 自带语音")
                    self.log("  装一下：pip install edge-tts（或 pip install lcode[voice]）")
            if self._cloud_ready():
                where = (
                    "本地 SenseVoice，不联网"
                    if self.opts.refine == "sensevoice"
                    else "audio 段的后端（音频会离开本机）"
                )
                self.log(f"精转写：开（{self.opts.refine}，{where}）")
                # 本地模型加载要 3 秒：现在先建好，别让第一句话白等
                warmup = getattr(self._asr, "warmup", None)
                if callable(warmup):
                    try:
                        warmup()
                    except Exception as exc:  # noqa: BLE001 - 预热失败不该拦住监听
                        self.log(f"! 精转写预热失败：{type(exc).__name__}: {exc}")
            self.log("正在听（Ctrl+C 退出）")
            # 心跳从麦克风真正打开那一刻起算（模型加载/预热那几秒不算）
            self._beat = time.monotonic()
            if self.opts.watchdog > 0:
                threading.Thread(
                    target=self._watchdog_loop, name="voice-watchdog", daemon=True
                ).start()
                self.log(f"看门狗：开（{self.opts.watchdog:.0f} 秒没读到音频块就重建麦克风）")
            else:
                self.log("看门狗：关（麦克风卡死只能手动重启）")
            source = None

        last_partial = ""
        try:
            while True:
                if self._state == LISTENING and time.monotonic() > self._deadline:
                    self.log("→ 等指令超时，回到待命")
                    self._state, self._speech_blocks = IDLE, 0
                    self._new_recognizer()

                if mic_mode:
                    raw = self._read_block()
                    if raw is None:
                        continue  # 流被重建/退避中：这一轮没有音频可处理
                else:
                    raw = next(source, None)
                    if raw is None:
                        # 文件读完了：把识别器里剩下的吐出来。
                        # 不 flush 会丢掉最后一句——麦克风流不会遇到这问题
                        # （一直有输入），但回放测试必须补一下。
                        with self._audio_lock:
                            self._rec.AcceptWaveform(b"")
                            tail = json.loads(self._rec.FinalResult()).get("text", "")
                        if tail and self._should_refine(tail):
                            tail = self.refine(tail)
                        self.process_text(tail, self._spawn)
                        break

                # 自己正在念「在」：这段音频不是用户说的话。喂给识别器会被
                # 转成「小口」「逍客」这种伪指令，甚至把自己叫醒一遍。
                # 所以进程活着期间只读不吃（继续 read 是为了排空缓冲，否则溢出）。
                if self._tts_running():
                    if self.opts.debug:
                        self.log("  （自己正在念「在」，丢弃这一段）")
                    continue

                # 高通放在喂识别器之前：压掉低频嗡声，识别率立刻能上来
                if hp is not None:
                    arr_in = np.frombuffer(raw, dtype=np.int16)
                    raw = np.clip(hp.process(arr_in), -32768, 32767).astype(np.int16).tobytes()

                # 攒这一句的原始音频，供云端精转写用。上限 30 秒：
                # 万一识别器长时间不吐 done（持续噪声），也不能无限吃内存。
                self._utter.extend(raw)
                if len(self._utter) > RATE * 2 * 30:
                    del self._utter[: len(self._utter) - RATE * 2 * 30]

                with self._audio_lock:
                    done = self._rec.AcceptWaveform(raw)
                    text = json.loads(self._rec.Result()).get("text", "") if done else ""
                    partial = (
                        ""
                        if done
                        else json.loads(self._rec.PartialResult()).get("partial", "").strip()
                    )
                if done:
                    last_partial = ""
                    if text and self._should_refine(text):
                        text = self.refine(text)
                    self.process_text(text, self._spawn)
                    continue

                # 部分结果里听见唤醒词就提前响应（不用等整句说完）
                if partial and partial != last_partial:
                    last_partial = partial
                    if self.opts.debug:
                        self.log(f"  …{partial}")
                    if (
                        self._state == IDLE
                        and strip_wake(partial, self.opts.wake_words, self.opts.wake_near_misses)[0]
                    ):
                        self.wake_up()

                if self._state == LISTENING:
                    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                    rms = float(np.sqrt(np.mean(arr**2))) if arr.size else 0.0
                    if rms > self.opts.threshold:
                        self._speech_blocks += 1
                    if self._speech_blocks > MAX_SPEECH_BLOCKS:
                        self.log("→ 说太久了，强制收尾")
                        with self._audio_lock:
                            tail = json.loads(self._rec.FinalResult()).get("text", "").strip()
                            self._new_recognizer()
                        self._state, self._speech_blocks, self._deadline = IDLE, 0, 0.0
                        if tail:
                            _hits, rest = strip_wake(
                                tail, self.opts.wake_words, self.opts.wake_near_misses
                            )
                            # 超长收尾这一段音频太庞杂（可能是念了篇文章），
                            # 不走精转写：传上去又贵又不准。
                            self._utter = bytearray()
                            if rest or not _hits:
                                self.handle_command(rest or tail, self._spawn)
        except KeyboardInterrupt:
            self.log("再见")
        finally:
            self._watchdog_stop.set()
            if simulate:
                # 回放没有后续输入，等后台回答说完了再退出，方便看结果
                for t in list(self._threads):
                    t.join(timeout=self.opts.answer_timeout + 30)
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:  # noqa: BLE001
                    pass
        return 0

    # ── 自检 ──────────────────────────────────────────────────────────

    def selftest(self, model) -> int:
        """录 4 秒 → 识别 → 打印。用来判断「麦克风到底通不通」。"""
        import numpy as np
        from vosk import KaldiRecognizer

        try:
            import sounddevice as sd
        except ImportError as exc:
            raise VoiceError("缺少 sounddevice。装语音依赖：pip install lcode[voice]") from exc

        dev = self.opts.device
        if dev is None:
            dev = pick_input_device(sd, RATE, BLOCK, self.log)
        seconds = 4.0
        self.log(f"录 {seconds:.0f} 秒，请随便说一句话……")
        rec = KaldiRecognizer(model, RATE)
        levels: list[float] = []
        with sd.RawInputStream(
            samplerate=RATE, blocksize=BLOCK, dtype="int16", channels=1, device=dev
        ) as stream:
            for _ in range(int(seconds * RATE / BLOCK)):
                data, _ = stream.read(BLOCK)
                raw = bytes(data)
                rec.AcceptWaveform(raw)
                arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                levels.append(float(np.sqrt(np.mean(arr**2))) if arr.size else 0.0)
        rec.AcceptWaveform(b"")
        text = json.loads(rec.FinalResult()).get("text", "")
        peak = max(levels) if levels else 0.0
        avg = sum(levels) / len(levels) if levels else 0.0
        self.log(f"平均 RMS={avg:.0f}  峰值 RMS={peak:.0f}")
        if peak < 200:
            self.log("! 基本没听见声音。换 --voice-device N 试试，或检查系统麦克风是否被静音")
        self.log(f"识别结果：{text or '(空)'}")
        return 0


__all__ = [
    "IDLE",
    "LISTENING",
    "LOCK_PORT",
    "VoiceAssistant",
    "VoiceError",
    "VoiceLog",
    "VoiceOptions",
    "acquire_instance_lock",
    "load_model",
    "strip_leading_wake",
    "strip_wake",
]
