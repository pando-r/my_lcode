"""语音通道的测试：唤醒词剥离、朗读稿清洗、状态机、队列桥。

重点覆盖**约束**而不是「麦克风真的能听」——后者在这台机器上依赖硬件，
CI 里根本跑不了。真正靠实机验证的部分（增益、噪声门限、唤醒词误报率）
写在 docs/voice.md 的实测记录里。

依赖 vosk/numpy 的用例用 ``importorskip`` 跳过：核心包不装 ``lcode[voice]``
时整份文件仍应全绿，而不是一堆 ImportError。
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from lcode.config import ConfigError, load
from lcode.voice import Availability, bridge, speech
from lcode.voice import config as voice_config
from lcode.voice import wake as wake_mod
from lcode.voice.devices import BLOCK
from lcode.voice.wake import (
    IDLE,
    LISTENING,
    VoiceAssistant,
    VoiceOptions,
    strip_leading_wake,
    strip_wake,
)


def _silent_log(_msg: str) -> None:
    pass


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


BASE = """
providers:
  - {name: P, protocol: openai, api_key: a, model: m}
"""


# ── 配置解析 ───────────────────────────────────────────────────────────


def test_voice_absent_is_none(tmp_path):
    assert load(_write(tmp_path, BASE)).voice is None


def test_voice_section_full(tmp_path):
    path = _write(
        tmp_path,
        BASE
        + """
voice:
  wake_words: ["你好派蒙", "派蒙"]
  device: 3
  threshold: 260
  filter: false
  cutoff: 150
  model_dir: /models/cn
  speak: false
  speak_limit: 200
  answer_timeout: 90
  project: /work
  tts_voice: Microsoft Huihui Desktop
""",
    )
    v = load(path).voice
    assert v is not None
    assert v.wake_words == ["你好派蒙", "派蒙"]
    assert v.device == 3
    assert v.threshold == 260.0
    assert v.filter is False
    assert v.cutoff == 150.0
    assert v.model_dir == "/models/cn"
    assert v.speak is False
    assert v.speak_limit == 200
    assert v.answer_timeout == 90.0
    assert v.project == "/work"
    assert v.tts_voice == "Microsoft Huihui Desktop"


def test_voice_defaults(tmp_path):
    v = load(_write(tmp_path, BASE + "voice: {}\n")).voice
    assert v is not None
    assert v.device is None
    assert v.threshold == 400.0
    assert v.filter is True
    assert v.cutoff == 180.0
    assert v.speak is True
    assert v.speak_limit == 420
    assert v.answer_timeout == 240.0
    # 没写唤醒词就用内置那组音近词
    assert v.effective_wake_words() == list(voice_config.DEFAULT_WAKE_WORDS)


def test_voice_wake_words_accept_comma_string(tmp_path):
    # 手写 YAML 时更容易写成一行逗号分隔；中文逗号也要认
    v = load(_write(tmp_path, BASE + 'voice:\n  wake_words: "甲，乙, 丙"\n')).voice
    assert v is not None
    assert v.wake_words == ["甲", "乙", "丙"]


def test_voice_not_a_mapping_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="voice"):
        load(_write(tmp_path, BASE + "voice: [1, 2]\n"))


def test_voice_ignores_wrong_types(tmp_path):
    # 类型不对的键静默忽略而不是整份配置报错：语音是可选插件，
    # 不该因为一个阈值写成字符串就让人启动不了 Lcode。
    v = load(
        _write(
            tmp_path,
            BASE + 'voice:\n  threshold: "很高"\n  device: "默认"\n  speak_limit: -1\n',
        )
    ).voice
    assert v is not None
    assert v.threshold == 400.0
    assert v.device is None
    assert v.speak_limit == 420


def test_voice_options_from_config(tmp_path):
    cfg = load(_write(tmp_path, BASE + "voice:\n  speak: false\n  speak_limit: 100\n")).voice
    opts = VoiceOptions.from_config(cfg, project=tmp_path)
    assert opts.project == tmp_path
    assert opts.speak_answer is False
    assert opts.speak_limit == 100


def test_voice_conversation_on_by_default(tmp_path):
    # 连续对话是默认行为：不写就等于「念完接着听」
    cfg = load(_write(tmp_path, BASE + "voice: {}\n")).voice
    assert cfg is not None
    assert cfg.conversation is True
    assert VoiceOptions.from_config(cfg, project=tmp_path).conversation is True


def test_voice_conversation_can_be_disabled(tmp_path):
    cfg = load(_write(tmp_path, BASE + "voice:\n  conversation: false\n")).voice
    assert cfg is not None
    assert cfg.conversation is False
    assert VoiceOptions.from_config(cfg, project=tmp_path).conversation is False


def test_voice_refine_off_by_default(tmp_path):
    # 默认不精转写：装 lcode[voice] 只带 vosk，这是能用的最小集
    cfg = load(_write(tmp_path, BASE + "voice: {}\n")).voice
    assert cfg is not None
    assert cfg.effective_refine() == "off"
    assert VoiceOptions.from_config(cfg, project=tmp_path).refine == "off"


def test_voice_refine_sensevoice_parsed(tmp_path):
    path = _write(
        tmp_path,
        BASE
        + "voice:\n  refine: sensevoice\n  refine_min_seconds: 1.5\n  refine_fallback: false\n",
    )
    cfg = load(path).voice
    assert cfg is not None
    assert cfg.effective_refine() == "sensevoice"
    assert cfg.refine_min_seconds == 1.5
    assert cfg.refine_fallback is False
    opts = VoiceOptions.from_config(cfg, project=tmp_path)
    assert opts.refine == "sensevoice"
    assert opts.refine_min_seconds == 1.5
    assert opts.refine_fallback is False


def test_voice_legacy_cloud_asr_still_works(tmp_path):
    """老配置只写 cloud_asr: true 时按 cloud 处理，不能静默失效。"""
    cfg = load(_write(tmp_path, BASE + "voice:\n  cloud_asr: true\n")).voice
    assert cfg is not None
    assert cfg.effective_refine() == "cloud"
    assert VoiceOptions.from_config(cfg, project=tmp_path).refine == "cloud"


def test_voice_refine_wins_over_legacy_cloud_asr(tmp_path):
    cfg = load(_write(tmp_path, BASE + "voice:\n  refine: sensevoice\n  cloud_asr: true\n")).voice
    assert cfg is not None
    assert cfg.effective_refine() == "sensevoice"


def test_voice_refine_rejects_unknown_value(tmp_path):
    # 写错了静默忽略（回到 off），不能让 Lcode 启动不起来
    cfg = load(_write(tmp_path, BASE + "voice:\n  refine: whisper\n")).voice
    assert cfg is not None
    assert cfg.effective_refine() == "off"


def test_voice_near_misses_default_to_builtin(tmp_path):
    cfg = load(_write(tmp_path, BASE + "voice: {}\n")).voice
    assert cfg is not None
    misses = cfg.effective_wake_near_misses()
    # 实机日志里出现过的误听必须在默认表里，否则修了个寂寞
    assert "小口" in misses and "削个" in misses
    # 也不能漏进正式唤醒词：那会把「小口」当成一次完整唤醒
    assert "小口" not in cfg.effective_wake_words()


def test_voice_near_misses_accept_comma_string(tmp_path):
    cfg = load(_write(tmp_path, BASE + "voice:\n  wake_near_misses: 小口，削个\n")).voice
    assert cfg is not None
    assert cfg.effective_wake_near_misses() == ["小口", "削个"]


def test_voice_near_misses_empty_list_disables(tmp_path):
    # 显式写成 [] 是「我知道这条规则，关掉它」；和「没写」不一样
    cfg = load(_write(tmp_path, BASE + "voice:\n  wake_near_misses: []\n")).voice
    assert cfg is not None
    assert cfg.effective_wake_near_misses() == []
    assert VoiceOptions.from_config(cfg, project=tmp_path).wake_near_misses == []


def test_voice_watchdog_on_by_default(tmp_path):
    # 「进程活着但聋了」这件事必须默认有人管，不能让人先踩坑再开
    cfg = load(_write(tmp_path, BASE + "voice: {}\n")).voice
    assert cfg is not None
    assert cfg.watchdog == voice_config.WATCHDOG_STALE
    assert VoiceOptions.from_config(cfg, project=tmp_path).watchdog == voice_config.WATCHDOG_STALE


def test_voice_watchdog_can_be_disabled(tmp_path):
    # 0 是「明确关掉」：调试麦克风时不想被自动重建干扰
    cfg = load(_write(tmp_path, BASE + "voice:\n  watchdog: 0\n")).voice
    assert cfg is not None
    assert cfg.watchdog == 0.0
    assert VoiceOptions.from_config(cfg, project=tmp_path).watchdog == 0.0


def test_voice_watchdog_ignores_negative(tmp_path):
    # 负数当写错处理，回到默认值（不然看门狗会每 tick 都判定卡死）
    cfg = load(_write(tmp_path, BASE + "voice:\n  watchdog: -5\n")).voice
    assert cfg is not None
    assert cfg.watchdog == voice_config.WATCHDOG_STALE


# ── 唤醒词剥离 ─────────────────────────────────────────────────────────


WAKES = ["小可小可", "小可"]


def test_strip_wake_bare():
    assert strip_wake("小可", WAKES) == (True, "")


def test_strip_wake_with_command():
    assert strip_wake("小可现在几点", WAKES) == (True, "现在几点")


def test_strip_wake_prefers_longest():
    # 「小可小可」必须先于「小可」匹配，否则剩下的指令会变成「小可…」
    hit, rest = strip_wake("小可小可现在几点", WAKES)
    assert hit is True
    assert rest == "现在几点"


def test_strip_wake_ignores_spaces_and_punctuation():
    # vosk 会在词间插空格，识别结果里也可能带标点
    assert strip_wake("小可，现在 几点？", WAKES) == (True, "现在几点")


def test_strip_wake_no_match():
    assert strip_wake("今天天气不错", WAKES) == (False, "")


def test_strip_wake_empty_words_are_ignored():
    assert strip_wake("随便说点", ["", "小可"]) == (False, "")


# ── 唤醒词误听（「小口小口」不能被当成指令） ───────────────────────────

NEAR = ["小口", "削个"]


def test_strip_wake_near_miss_whole_sentence():
    # 整句都是误听词 = 用户只是喊了一声，没有指令
    assert strip_wake("小口小口", WAKES, NEAR) == (True, "")


def test_strip_wake_near_miss_needs_whole_sentence():
    # 「削个苹果」是正常句子：不能拆成唤醒 + 指令「苹果」发出去
    assert strip_wake("削个苹果", WAKES, NEAR) == (False, "")


def test_strip_wake_near_miss_absent_keeps_old_behavior():
    assert strip_wake("小口小口", WAKES) == (False, "")


def test_strip_leading_wake_repeats():
    hit, rest = strip_leading_wake("小口小可现在几点", [*WAKES, *NEAR])
    assert hit is True
    assert rest == "现在几点"


def test_strip_leading_wake_only_at_start():
    # 句子中间的「小口」可能是正常词（「瓶口小」），不能动
    hit, rest = strip_leading_wake("帮我看瓶口小不小", ["小口"])
    assert hit is False
    assert rest == "帮我看瓶口小不小"


def test_strip_leading_wake_min_repeat():
    # 单遍误听词不剥：「削个木头」本身就是一句正常指令
    hit, rest = strip_leading_wake("削个木头", ["削个"], min_repeat=2)
    assert hit is False
    assert rest == "削个木头"


# ── 朗读稿清洗 ─────────────────────────────────────────────────────────


def test_sanitize_removes_markdown():
    out = speech.sanitize("**结论**：没问题\n\n- 第一点\n- 第二点")
    assert "*" not in out
    assert "- " not in out
    assert "结论" in out
    assert "第一点" in out


def test_sanitize_drops_code_block():
    out = speech.sanitize("看这里：\n```python\nprint(1)\n```\n就这些")
    assert "print" not in out
    assert "代码省略" in out


def test_sanitize_drops_table_and_url():
    out = speech.sanitize("| 项目 | 状态 |\n|---|---|\n| a | ok |\n详见 https://example.com/x")
    assert "|" not in out
    assert "https" not in out


def test_sanitize_keeps_link_text_only():
    assert "文档" in speech.sanitize("见[文档](https://example.com)")


def test_sanitize_clock_becomes_spoken_form():
    assert "4 点 19 分" in speech.sanitize("就定在 4:19 吧")


def test_sanitize_heading_marks_removed():
    assert "#" not in speech.sanitize("## 标题\n正文")


def test_clip_short_text_untouched():
    assert speech.clip("很短", 100) == "很短"


def test_clip_cuts_at_sentence_boundary():
    text = "第一句话很长很长。" * 20
    out = speech.clip(text, 40)
    assert out.endswith("更详细的结果在终端里。")
    # 不能在半句中间断：截断点之前必须刚好是句号
    assert "。……" in out


# ── 队列桥 ─────────────────────────────────────────────────────────────


def test_bridge_enqueue_writes_queue(tmp_path):
    job = bridge.enqueue("现在几点", tmp_path)
    assert job.startswith("job_")
    raw = json.loads(bridge.queue_path(tmp_path).read_text(encoding="utf-8"))
    assert raw[0]["id"] == job
    assert raw[0]["status"] == "pending"
    # 来源必须是 voice：daemon 靠它跳过 email 通知
    assert raw[0]["origin"] == "voice"


def test_bridge_enqueue_rejects_empty(tmp_path):
    with pytest.raises(bridge.BridgeError):
        bridge.enqueue("   ", tmp_path)


def test_bridge_enqueue_records_thread(tmp_path):
    # thread 决定 daemon 会不会把上一轮问答拼进上下文，必须落进队列
    bridge.enqueue("那它呢", tmp_path, thread="voice-1")
    raw = json.loads(bridge.queue_path(tmp_path).read_text(encoding="utf-8"))
    assert raw[0]["thread"] == "voice-1"


def test_bridge_enqueue_records_say(tmp_path):
    # say 是用户原话：prompt 带着语音提示词前缀，拼上下文时要用 say 才不重复
    bridge.enqueue("【语音提问】不要用列表\n\n现在几点", tmp_path, thread="v1", say="现在几点")
    raw = json.loads(bridge.queue_path(tmp_path).read_text(encoding="utf-8"))
    assert raw[0]["say"] == "现在几点"
    assert raw[0]["prompt"].startswith("【语音提问】")


def test_bridge_read_task_missing_queue(tmp_path):
    assert bridge.read_task(tmp_path, "job_dead") == {}


def test_bridge_wait_for_timeout(tmp_path):
    status, body = bridge.wait_for(tmp_path, "job_dead", timeout=0.2)
    assert status == "timeout"
    assert body == ""


def test_bridge_result_text_prefers_result_section(tmp_path):
    result = tmp_path / "r.md"
    result.write_text(
        "---\ntask: t\n---\n\n## 任务\n\n问\n\n## 结果\n\n答案在这\n", encoding="utf-8"
    )
    assert bridge.result_text({"result_file": str(result)}) == "答案在这"


def test_bridge_result_text_falls_back_to_summary(tmp_path):
    assert bridge.result_text({"result_file": "", "summary": "摘要"}) == "摘要"


# ── 状态机 ─────────────────────────────────────────────────────────────


def _assistant(tmp_path, **kwargs) -> VoiceAssistant:
    opts = VoiceOptions(project=tmp_path, wake_words=["小可"], **{"quiet": True, **kwargs})
    a = VoiceAssistant(opts, log=_silent_log)
    # 状态机测试不需要真识别器
    a._new_recognizer = lambda: None
    # 指令流水默认落在 ~/.lcode/voice/commands.jsonl（用户级数据）。
    # 测试必须改到 tmp_path，否则每跑一次 pytest 就往用户的真实记录里
    # 掺几条「现在几点」——排查误唤醒时那些假记录会把人带偏。
    a._command_log = tmp_path / "commands.jsonl"
    return a


def test_idle_ignores_non_wake_text(tmp_path):
    a = _assistant(tmp_path)
    spawned: list = []
    a.process_text("今天天气不错", spawned.append)
    assert a._state == IDLE
    assert spawned == []


def test_bare_wake_word_enters_listening(tmp_path):
    a = _assistant(tmp_path)
    a.process_text("小可", lambda job: None)
    assert a._state == LISTENING
    assert a._deadline > 0


def test_wake_word_with_command_handled_directly(tmp_path):
    a = _assistant(tmp_path)
    seen: list = []
    a.handle_command = lambda text, spawn: seen.append(text)  # type: ignore[method-assign]
    a.process_text("小可现在几点", lambda job: None)
    assert seen == ["现在几点"]
    assert a._state == IDLE


def test_listening_state_takes_whole_sentence(tmp_path):
    a = _assistant(tmp_path)
    seen: list = []
    a.handle_command = lambda text, spawn: seen.append(text)  # type: ignore[method-assign]
    a.process_text("小可", lambda job: None)
    a.process_text("帮我看看磁盘", lambda job: None)
    assert seen == ["帮我看看磁盘"]
    assert a._state == IDLE


def test_listening_state_bare_wake_extends_deadline(tmp_path):
    a = _assistant(tmp_path)
    seen: list = []
    a.handle_command = lambda text, spawn: seen.append(text)  # type: ignore[method-assign]
    a.process_text("小可", lambda job: None)
    first = a._deadline
    a.process_text("小可", lambda job: None)  # 又只说了唤醒词
    assert seen == []
    assert a._state == LISTENING
    assert a._deadline >= first


def test_listening_state_wake_echo_is_not_a_command(tmp_path):
    # 实机日志里的真实故障：喊完唤醒词，vosk 把同一句识别成「小口小口」，
    # 旧逻辑把它当指令发给 Agent，白跑几十秒换回一句「我没听清」。
    a = _assistant(tmp_path)
    seen: list = []
    a.handle_command = lambda text, spawn: seen.append(text)  # type: ignore[method-assign]
    a.process_text("小可", lambda job: None)  # partial 先命中，进 LISTENING
    a.process_text("小口小口", lambda job: None)  # 同一句的 final 结果
    assert seen == []
    assert a._state == LISTENING


def test_listening_state_strips_near_miss_prefix(tmp_path):
    a = _assistant(tmp_path)
    seen: list = []
    a.handle_command = lambda text, spawn: seen.append(text)  # type: ignore[method-assign]
    a.process_text("小可", lambda job: None)
    a.process_text("小口小口现在几点", lambda job: None)
    assert seen == ["现在几点"]


def test_idle_state_wakes_on_near_miss_only(tmp_path):
    # 整句只有误听词：说明人喊的确实是唤醒词，只是被听错——照样叫醒它
    a = _assistant(tmp_path)
    a.process_text("削个", lambda job: None)
    assert a._state == LISTENING


def test_idle_state_near_miss_with_command_in_one_breath(tmp_path):
    # 「小可小可，现在几点」一口气说完、整句都被听错：照样叫醒 + 取后半句
    a = _assistant(tmp_path)
    seen: list = []
    a.handle_command = lambda text, spawn: seen.append(text)  # type: ignore[method-assign]
    a.process_text("小口小口现在几点", lambda job: None)
    assert seen == ["现在几点"]


def test_too_short_command_is_rejected(tmp_path):
    a = _assistant(tmp_path)
    said: list = []
    a.say = said.append  # type: ignore[method-assign]
    a.handle_command("在", lambda job: None)
    # 不交给 Agent，但要说一句让人重说
    assert said == ["没听清，再说一次。"]


def test_handle_command_without_speak_enqueues(tmp_path):
    a = _assistant(tmp_path, speak_answer=False)
    a.handle_command("现在几点", lambda job: None)
    raw = json.loads(bridge.queue_path(tmp_path).read_text(encoding="utf-8"))
    assert raw[0]["prompt"] == "现在几点"


def test_handle_command_with_speak_spawns(tmp_path):
    a = _assistant(tmp_path, speak_answer=True)
    jobs: list = []
    a.handle_command("现在几点", jobs.append)
    assert len(jobs) == 1
    assert a._command_log.exists()


# ── 连续对话：话题键 + 追问窗口 ─────────────────────────────────────────


def test_thread_reused_until_idle(tmp_path):
    a = _assistant(tmp_path)
    first = a._current_thread()
    assert first.startswith("voice-")
    # 紧接着再说一句：同一段对话，键不变（上下文要接得上）
    assert a._current_thread() == first
    # 空闲超过上限：换新键（旧问答不该再被带进上下文）
    a._heard_at = time.monotonic() - 10000
    assert a._current_thread() != first


def test_handle_command_without_speak_uses_thread(tmp_path):
    a = _assistant(tmp_path, speak_answer=False)
    a.handle_command("现在几点", lambda job: None)
    raw = json.loads(bridge.queue_path(tmp_path).read_text(encoding="utf-8"))
    assert raw[0]["thread"].startswith("voice-")


def test_handle_command_passes_thread_to_answer(tmp_path, monkeypatch):
    seen: list[dict] = []

    def fake_enqueue(text, project, **kwargs):
        seen.append({"text": text, **kwargs})
        return "job_x"

    monkeypatch.setattr(bridge, "enqueue", fake_enqueue)
    monkeypatch.setattr(bridge, "wait_for", lambda *a, **k: ("done", "答案"))
    monkeypatch.setattr(bridge, "trigger_run_once", lambda *a, **k: None)
    a = _assistant(tmp_path, quiet=False)
    _speech_ready(a)
    a.handle_command("现在几点", lambda job: job())
    assert seen[0]["thread"] == a._thread
    assert seen[0]["text"].endswith("现在几点")


def _speech_ready(a: VoiceAssistant) -> None:
    """把 assistant 弄成「有麦克风、不出声、识别器免装」的状态，专测状态机。"""
    a._stream = object()  # 非 None 表示在听麦克风（回放模式是 None）
    a._new_recognizer = lambda: None
    a.beep = lambda up=True: None  # 别在测试机上真的响
    a.say = lambda text: None


def test_answer_opens_followup_window(tmp_path, monkeypatch):
    # 念完回答不该立刻回到待命：直接接着问，不用重新喊唤醒词
    monkeypatch.setattr(bridge, "enqueue", lambda *a, **k: "job_x")
    monkeypatch.setattr(bridge, "wait_for", lambda *a, **k: ("done", "五点四十八分"))
    monkeypatch.setattr(bridge, "trigger_run_once", lambda *a, **k: None)
    a = _assistant(tmp_path, quiet=False)
    _speech_ready(a)
    a._turn = 1
    a.answer_voice("现在几点", thread="voice-t")
    assert a._state == LISTENING
    assert a._deadline > time.monotonic()


def test_followup_skipped_when_conversation_off(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "enqueue", lambda *a, **k: "job_x")
    monkeypatch.setattr(bridge, "wait_for", lambda *a, **k: ("done", "答案"))
    monkeypatch.setattr(bridge, "trigger_run_once", lambda *a, **k: None)
    a = _assistant(tmp_path, quiet=False, conversation=False)
    _speech_ready(a)
    a._turn = 1
    a.answer_voice("现在几点")
    assert a._state == IDLE


def test_followup_skipped_when_newer_turn_started(tmp_path, monkeypatch):
    # 回答期间用户又唤醒问了新问题：旧回答不能在念完之后把状态抢回 LISTENING
    a = _assistant(tmp_path, quiet=False)
    _speech_ready(a)

    def _wait(*_args, **_kwargs):
        a._turn += 1  # 模拟这期间来了新的一轮
        return "done", "答案"

    monkeypatch.setattr(bridge, "enqueue", lambda *a_, **k: "job_x")
    monkeypatch.setattr(bridge, "wait_for", _wait)
    monkeypatch.setattr(bridge, "trigger_run_once", lambda *a_, **k: None)
    a._turn = 1
    a.answer_voice("现在几点")
    assert a._state == IDLE


def test_followup_needs_a_microphone(tmp_path, monkeypatch):
    # 回放模式没有后续输入，开窗口只会白等
    monkeypatch.setattr(bridge, "enqueue", lambda *a, **k: "job_x")
    monkeypatch.setattr(bridge, "wait_for", lambda *a, **k: ("done", "答案"))
    monkeypatch.setattr(bridge, "trigger_run_once", lambda *a, **k: None)
    a = _assistant(tmp_path, quiet=False)
    _speech_ready(a)
    a._stream = None
    a._turn = 1
    a.answer_voice("现在几点")
    assert a._state == IDLE


# ── greet 期间丢弃自己的声音 ────────────────────────────────────────────


class _FakeProc:
    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


def test_greet_marks_tts_running(tmp_path, monkeypatch):
    proc = _FakeProc(True)
    monkeypatch.setattr(speech, "speak_async", lambda *a, **k: proc)
    a = _assistant(tmp_path, quiet=False)
    a._voice_cache = "Microsoft Huihui Desktop"
    a.greet()
    # 麦克风还开着，主循环必须靠这个判断把「在」那一段丢掉
    assert a._tts_running() is True


def test_tts_running_stops_when_process_exits(tmp_path):
    a = _assistant(tmp_path, quiet=False)
    a._speak_proc = _FakeProc(False)
    a._speak_until = time.monotonic() + 10
    assert a._tts_running() is False


def test_tts_running_gives_up_after_timeout(tmp_path):
    # PowerShell 卡住时不能一直吞音频——那等于把麦克风关掉了
    a = _assistant(tmp_path, quiet=False)
    a._speak_proc = _FakeProc(True)
    a._speak_until = time.monotonic() - 1
    assert a._tts_running() is False


# ── 看门狗：麦克风流卡死时自救 ──────────────────────────────────────────
#
# 真实故障（2026-11-23）：进程活着、端口还占着，但 15:54 之后一句日志都不写，
# 喊什么都不应——主循环卡在 stream.read() 上永远不返回。看门狗就是为这个存在的。


class _FakeStream:
    """假麦克风流：记下 abort/close，read 按用例给的剧本演。"""

    def __init__(self, read=None) -> None:
        self.aborted = False
        self.stopped = False
        self.closed = False
        self._read = read or (lambda block: (b"\x00\x01" * block, False))

    def read(self, block):
        return self._read(block)

    def abort(self):
        self.aborted = True

    def stop(self):
        self.stopped = True

    def start(self):
        pass

    def close(self):
        self.closed = True


def _watchdog(a: VoiceAssistant, stream, *, stale: float = 10.0, speaking: int = 0):
    """在后台跑看门狗线程，跑到 ``_stalled`` 变真或超时为止。"""
    a._stream = stream
    a._watchdog_tick = 0.01
    a._beat = time.monotonic() - stale
    a._speaking = speaking
    t = threading.Thread(target=a._watchdog_loop, daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if a._stalled:
                break
            time.sleep(0.01)
    finally:
        a._watchdog_stop.set()
        t.join(timeout=5)


def test_watchdog_aborts_stalled_stream(tmp_path):
    a = _assistant(tmp_path, watchdog=0.05)
    stream = _FakeStream()
    _watchdog(a, stream)
    assert a._stalled is True
    assert stream.aborted is True
    # 举手 = 第几次重建，日志里要看得出反复了几回
    assert a._recoveries == 1


def test_watchdog_leaves_healthy_stream_alone(tmp_path):
    a = _assistant(tmp_path, watchdog=60)
    stream = _FakeStream()
    _watchdog(a, stream, stale=1.0)  # 心跳才过去 1 秒，远没到阈值
    assert a._stalled is False
    assert stream.aborted is False


def test_watchdog_skips_while_speaking(tmp_path):
    # say() 念一段最长能到 80 秒，这期间麦克风是被主动停掉的，不能算卡死
    a = _assistant(tmp_path, watchdog=0.05)
    stream = _FakeStream()
    _watchdog(a, stream, speaking=1)
    assert a._stalled is False
    assert stream.aborted is False


def test_watchdog_can_be_disabled(tmp_path):
    a = _assistant(tmp_path, watchdog=0)
    stream = _FakeStream()
    _watchdog(a, stream)
    assert a._stalled is False
    assert stream.aborted is False


def test_read_block_returns_audio_and_bumps_beat(tmp_path):
    a = _assistant(tmp_path)
    a._stream = _FakeStream()
    a._beat = 0.0
    before = time.monotonic()
    raw = a._read_block()
    assert raw is not None and len(raw) == BLOCK * 2
    assert a._beat >= before


def test_read_block_rebuilds_after_watchdog_abort(tmp_path):
    # 看门狗已经把旧流打断了：这一片数据不可信，必须换一条流再继续
    a = _assistant(tmp_path)
    old = _FakeStream()
    a._stream = old
    a._stalled = True
    fresh = _FakeStream()
    a._open_stream = lambda: (1, fresh)  # type: ignore[method-assign]
    assert a._read_block() is None
    assert a._stream is fresh
    assert old.closed is True
    assert a._stalled is False


def test_read_block_rebuilds_when_read_raises(tmp_path):
    # 设备被拔掉时 read 直接抛异常，同样要换一条流而不是让主循环崩出去
    def boom(_block):
        raise RuntimeError("device lost")

    a = _assistant(tmp_path)
    a._stream = _FakeStream(read=boom)
    fresh = _FakeStream()
    a._open_stream = lambda: (1, fresh)  # type: ignore[method-assign]
    assert a._read_block() is None
    assert a._stream is fresh


def test_read_block_backs_off_when_reopen_fails(tmp_path):
    # 麦克风彻底打不开：退避，而不是把 CPU 烧在「读失败 → 重开」的循环里
    def boom(_block):
        raise RuntimeError("device lost")

    def fail():
        raise RuntimeError("麦克风始终打不开")

    a = _assistant(tmp_path)
    a._stream = _FakeStream(read=boom)
    a._open_stream = fail  # type: ignore[method-assign]
    slept: list[bool] = []
    a._backoff = lambda: slept.append(True)  # type: ignore[method-assign]
    assert a._read_block() is None
    assert slept == [True]
    assert a._stream is None


def test_reopen_failure_releases_stall_flag(tmp_path):
    # 牌不放回去，看门狗会以为「已经在处理」，从此再也不试第二次——
    # 麦克风就一直没了
    a = _assistant(tmp_path)
    a._stalled = True
    a._open_stream = lambda: (_ for _ in ()).throw(RuntimeError("no mic"))  # type: ignore[method-assign]
    assert a._reopen_stream() is False
    assert a._stalled is False


def test_read_block_reopens_when_stream_is_missing(tmp_path):
    # 重开失败后 _stream 是 None：主循环必须继续试着开，
    # 不能当成「回放模式」走到 next(None) 上崩掉
    a = _assistant(tmp_path)
    a._stream = None
    fresh = _FakeStream()
    a._open_stream = lambda: (1, fresh)  # type: ignore[method-assign]
    assert a._read_block() is None
    assert a._stream is fresh


def test_read_block_does_not_double_log_watchdog_abort(tmp_path):
    # 看门狗已经写过「卡死」，read 抛出来的同一个故障不该再报一次「读失败」
    logged: list[str] = []

    def boom(_block):
        raise RuntimeError("stream aborted")

    a = _assistant(tmp_path)
    a.log = logged.append  # type: ignore[method-assign]
    a._stream = _FakeStream(read=boom)
    a._stalled = True
    a._open_stream = lambda: (1, _FakeStream())  # type: ignore[method-assign]
    a._read_block()
    assert not any("读麦克风失败" in line for line in logged)


def test_reopen_refreshes_beat_before_opening(tmp_path):
    # 重开要先刷新心跳：open_input 内部会重试好几秒，
    # 不先刷的话看门狗在这个窗口里会再举一次手（实测连建两条流）
    a = _assistant(tmp_path)
    a._beat = 0.0
    seen: list[float] = []

    def slow_open():
        seen.append(a._beat)
        return 1, _FakeStream()

    a._open_stream = slow_open  # type: ignore[method-assign]
    before = time.monotonic()
    assert a._reopen_stream() is True
    assert seen[0] >= before


def test_abort_stream_handles_dead_stream(tmp_path):
    # abort() 自己抛异常也不能把看门狗线程带崩
    class _Broken:
        def abort(self):
            raise RuntimeError("already closed")

        def stop(self):
            raise RuntimeError("already closed")

    a = _assistant(tmp_path)
    a._stream = _Broken()
    a._abort_stream()  # 不抛就算过
    a._stream = None
    a._abort_stream()


def test_open_stream_uses_configured_device(tmp_path, monkeypatch):
    # 重建时必须沿用 --voice-device，不能随手换成「探测到的最响那个」
    pytest.importorskip("sounddevice")
    seen: list = []

    def fake_open_input(sd, device, **kwargs):
        seen.append(device)
        return device, _FakeStream()

    monkeypatch.setattr(wake_mod, "open_input", fake_open_input)
    a = _assistant(tmp_path, device=3)
    a._open_stream()
    assert seen == [3]


def test_run_does_not_fall_into_replay_branch_when_stream_is_none(tmp_path):
    """麦克风模式下 `_stream` 变成 None 时，主循环不能去 next(None)。

    真实故障路径：重开麦克风失败会让 `_stream` 短暂是 None。若主循环用
    ``self._stream is not None`` 判断「在听麦克风还是回放 wav」，就会走回放
    分支去 `next(source)`，而 source 是 None → TypeError，整个语音实例崩掉。
    """
    a = _assistant(tmp_path, watchdog=0)
    a._new_recognizer = lambda: None  # type: ignore[method-assign]
    a._rec = _FakeRec()
    a._open_stream = lambda: (0, _FakeStream())  # type: ignore[method-assign]

    calls = {"n": 0}

    def read_block():
        calls["n"] += 1
        if calls["n"] > 3:
            raise KeyboardInterrupt  # 联调结束
        a._stream = None  # 假装重开失败，流没了
        return None

    a._read_block = read_block  # type: ignore[method-assign]
    assert a.run(object()) == 0  # 不抛 TypeError
    assert calls["n"] == 4


class _FakeRec:
    """假 vosk 识别器：run() 会调这几个方法，测试里只需要它们不炸。"""

    def AcceptWaveform(self, _raw):  # noqa: ANN001, N802
        return False

    def Result(self):  # noqa: N802
        return '{"text": ""}'

    def PartialResult(self):  # noqa: N802
        return '{"partial": ""}'

    def FinalResult(self):  # noqa: N802
        return '{"text": ""}'


# ── 依赖体检 ───────────────────────────────────────────────────────────


def test_availability_problems_lists_missing_packages():
    av = Availability(
        missing_packages=("vosk",), model_dir="/nowhere", model_ok=False, tts_ok=False
    )
    problems = av.problems()
    assert av.ready is False
    assert any("vosk" in p for p in problems)
    assert any("/nowhere" in p for p in problems)
    assert any("TTS" in p for p in problems)


def test_availability_ready_when_all_present():
    av = Availability(missing_packages=(), model_dir="/m", model_ok=True, tts_ok=True)
    assert av.ready is True
    assert av.problems() == []


# ── 音频前处理（需要 numpy） ───────────────────────────────────────────


def test_highpass_attenuates_dc():
    np = pytest.importorskip("numpy")
    from lcode.voice import dsp

    hp = dsp.HighPass(16000, 180.0, 2)
    dc = np.full(4000, 1000, dtype=np.int16)
    out = hp.process(dc)
    # 直流/低频应该被压掉；后段剩余幅度远小于输入
    assert abs(float(out[-100:].mean())) < 100


def test_trim_and_normalize_crops_silence():
    np = pytest.importorskip("numpy")
    from lcode.voice import dsp

    # 2 秒静音 + 0.5 秒响 + 2 秒静音
    quiet = np.zeros(16000 * 2, dtype=np.int16)
    loud = (np.sin(np.linspace(0, 4000, 8000)) * 3000).astype(np.int16)
    pcm = quiet.tobytes() + loud.tobytes() + quiet.tobytes()
    out = dsp.trim_and_normalize(pcm, 16000)
    assert 0 < len(out) < len(pcm)


def test_pick_input_device_skips_silent(monkeypatch):
    # 假 sounddevice：0 号静音、1 号有声音 → 应该选 1
    from lcode.voice import devices

    class FakeSD:
        def query_devices(self, index=None):
            data = [
                {"name": "speakers", "max_input_channels": 0, "hostapi": 0},
                {"name": "silent mic", "max_input_channels": 1, "hostapi": 0},
                {"name": "real mic", "max_input_channels": 1, "hostapi": 0},
            ]
            return data if index is None else data[index]

        def query_hostapis(self):
            return [{"name": "MME"}]

    levels = {1: 12.0, 2: 900.0}

    def fake_probe(sd, i, rate=None, block=None):
        return levels.get(i, -1.0)

    monkeypatch.setattr(devices, "probe_level", fake_probe)
    assert devices.pick_input_device(FakeSD()) == 2


def test_pick_input_device_falls_back_to_none(monkeypatch):
    from lcode.voice import devices

    class FakeSD:
        def query_devices(self, index=None):
            data = [{"name": "silent", "max_input_channels": 1, "hostapi": 0}]
            return data if index is None else data[index]

        def query_hostapis(self):
            return [{"name": "MME"}]

    monkeypatch.setattr(devices, "probe_level", lambda *a, **k: 5.0)
    assert devices.pick_input_device(FakeSD()) is None


# ── 云端精转写（vosk 只当触发器） ───────────────────────────────────────


def _refine_assistant(tmp_path, asr, backend="sensevoice", **kwargs) -> VoiceAssistant:
    opts = VoiceOptions(
        project=tmp_path,
        wake_words=["小可"],
        refine=backend,
        quiet=True,
        **kwargs,
    )
    a = VoiceAssistant(opts, log=_silent_log, asr=asr)
    a._new_recognizer = lambda: None
    a._command_log = tmp_path / "commands.jsonl"
    return a


def _pcm(seconds: float) -> bytes:
    return b"\x00\x01" * int(16000 * seconds)


def test_cloud_asr_off_means_no_transcriber(tmp_path):
    # 默认关：没显式打开时连 _asr 都不该被调用（音频不出本机）
    calls: list = []
    opts = VoiceOptions(project=tmp_path, wake_words=["小可"], quiet=True)
    a = VoiceAssistant(opts, log=_silent_log, asr=lambda pcm: calls.append(pcm) or "现在几点")
    a._utter = bytearray(_pcm(2))
    assert a.refine("现在几") == "现在几"
    assert calls == []


def test_cloud_asr_replaces_vosk_text(tmp_path):
    a = _refine_assistant(tmp_path, lambda pcm: "现在几点")
    a._utter = bytearray(_pcm(2))
    assert a.refine("现在几") == "现在几点"


def test_cloud_asr_strips_wake_prefix(tmp_path):
    # 云端转的是整句，唤醒词也在里面：剥掉之后才是指令
    a = _refine_assistant(tmp_path, lambda pcm: "小可小可，现在几点")
    a._utter = bytearray(_pcm(2))
    assert a.refine("现在几点") == "现在几点"


def test_cloud_asr_keeps_vosk_text_when_cloud_returns_nothing(tmp_path):
    a = _refine_assistant(tmp_path, lambda pcm: "")
    a._utter = bytearray(_pcm(2))
    assert a.refine("现在几点") == "现在几点"


def test_cloud_asr_drops_text_when_fallback_disabled(tmp_path):
    # 关掉回落 = 宁可没答也不要答错
    a = _refine_assistant(tmp_path, lambda pcm: "", refine_fallback=False)
    a._utter = bytearray(_pcm(2))
    assert a.refine("现在几") == ""


def test_cloud_asr_survives_transcriber_exception(tmp_path):
    def boom(_pcm_bytes):
        raise RuntimeError("网络断了")

    a = _refine_assistant(tmp_path, boom)
    a._utter = bytearray(_pcm(2))
    assert a.refine("现在几点") == "现在几点"


def test_cloud_asr_skips_short_fragment(tmp_path):
    calls: list = []
    a = _refine_assistant(tmp_path, lambda pcm: calls.append(pcm) or "现在几点")
    a._utter = bytearray(_pcm(0.2))  # 比 cloud_min_seconds 短
    assert a.refine("现在几") == "现在几"
    assert calls == []


def test_cloud_asr_buffer_is_consumed(tmp_path):
    # 一句话只能转一次：重复调用不该把同一段音频再发一遍
    calls: list = []
    a = _refine_assistant(tmp_path, lambda pcm: calls.append(pcm) or "现在几点")
    a._utter = bytearray(_pcm(2))
    a.refine("现在几")
    a.refine("现在几")
    assert len(calls) == 1


def test_should_refine_only_when_user_is_talking(tmp_path):
    a = _refine_assistant(tmp_path, lambda pcm: "x")
    # 待命状态下听到的闲聊不往云端发
    assert a._should_refine("今天天气不错") is False
    # 带唤醒词的句子、或者已经在听指令：值得转
    assert a._should_refine("小可，现在几点") is True
    a._state = LISTENING
    assert a._should_refine("随便什么") is True


def test_cloud_ready_checks_injected_ready_hook(tmp_path):
    def asr(_pcm_bytes):
        return ""

    asr.ready = lambda: False  # type: ignore[attr-defined]
    a = _refine_assistant(tmp_path, asr)
    assert a._cloud_ready() is False
    asr.ready = lambda: True  # type: ignore[attr-defined]
    assert a._cloud_ready() is True


# ── cloud.py：在运行中的事件循环里也必须能用 ────────────────────────────


def test_transcribe_pcm_survives_running_event_loop(monkeypatch, tmp_path):
    """语音主循环本身跑在 asyncio 里（cli.main = asyncio.run(_amain(...))）。

    第一版直接把 ``asyncio.run`` 放在 transcribe_pcm 里，实机报
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``。
    这个用例就是那个故障的复现：在外层协程里调用同步函数。
    """
    import asyncio
    from types import SimpleNamespace

    from lcode.voice import cloud

    calls: list = []

    async def fake_transcribe(cfg, path, language=""):
        calls.append((cfg, language))
        return SimpleNamespace(text="  现在几点  ")

    monkeypatch.setattr("lcode.audio.transcribe", fake_transcribe)

    async def outer():
        # 关键：此刻有运行中的事件循环
        return cloud.transcribe_pcm(None, b"\x00\x01" * 3200, tmp_path / "a.wav", "zh")

    assert asyncio.run(outer()) == "现在几点"
    assert calls == [(None, "zh")]


def test_transcribe_pcm_writes_readable_wav(tmp_path):
    import wave

    from lcode.voice import cloud

    target = tmp_path / "u.wav"
    cloud.write_wav(b"\x00\x01" * 16000, target)  # 1 秒
    with wave.open(str(target), "rb") as fh:
        assert fh.getframerate() == cloud.PCM_RATE == 16000
        assert fh.getnchannels() == 1
        assert fh.getsampwidth() == 2
        assert fh.getnframes() == 16000


# ── 本地 SenseVoice（免费、不联网） ─────────────────────────────────────


def test_sensevoice_default_dir_is_under_voice_data():
    from lcode.voice import local_asr
    from lcode.voice.config import voice_data_dir

    # 跟着「代码在 D、数据在 C」的约定：模型在 ~/.lcode/voice/models 下
    assert local_asr.default_model_dir() == voice_data_dir() / "models" / "sense-voice"


def test_sensevoice_reports_missing_model(tmp_path):
    from lcode.voice import local_asr

    engine = local_asr.SenseVoice(model_dir=tmp_path)
    assert engine.ready is False
    problems = engine.problems()
    assert any("model.int8.onnx" in p for p in problems)
    # 缺模型时要说清下载命令，否则用户只能去翻代码
    assert any("--sensevoice" in p for p in problems)


def test_sensevoice_reports_missing_tokens(tmp_path):
    from lcode.voice import local_asr

    (tmp_path / "model.int8.onnx").write_bytes(b"x" * 100)
    engine = local_asr.SenseVoice(model_dir=tmp_path)
    assert engine.ready is False
    assert any("tokens.txt" in p for p in engine.problems())


def test_sensevoice_transcribe_rejects_when_not_installed(tmp_path):
    from lcode.voice import local_asr

    engine = local_asr.SenseVoice(model_dir=tmp_path)
    with pytest.raises(local_asr.SenseVoiceError):
        engine.transcribe(b"\x00\x00" * 16000)


def test_sensevoice_empty_pcm_returns_empty(tmp_path):
    from lcode.voice import local_asr

    # 空音频不该触发模型加载，直接返回空串
    assert local_asr.SenseVoice(model_dir=tmp_path).transcribe(b"") == ""


def test_sensevoice_clean_strips_tags():
    from lcode.voice.local_asr import _clean

    # SenseVoice 的输出可能带语种/情感/事件标签，念出来是噪音
    assert _clean("<|zh|><|NEUTRAL|><|Speech|><|woitn|>现在几点") == "现在几点"
    assert _clean("▁小可小可，现在几点？") == "小可小可，现在几点？"
    assert _clean("") == ""
    # 正常的尖括号不能被吃掉（虽然少见，但别让它变成破坏性清洗）
    assert _clean("a > b") == "a > b"


def test_sensevoice_make_transcriber_never_raises(tmp_path):
    from lcode.voice import local_asr

    logged: list[str] = []
    asr = local_asr.make_transcriber(model_dir=tmp_path, log=logged.append)
    # 模型没装：约定是返回空串 + 记一条日志，不能抛
    assert asr(b"\x00\x00" * 16000) == ""
    assert any("本地精转写失败" in line for line in logged)
    assert asr.ready() is False  # type: ignore[attr-defined]


def test_sensevoice_make_transcriber_ready_hook(tmp_path):
    from lcode.voice import local_asr

    (tmp_path / "model.int8.onnx").write_bytes(b"x" * 100)
    (tmp_path / "tokens.txt").write_text("a 0\n", encoding="utf-8")
    asr = local_asr.make_transcriber(model_dir=tmp_path)
    # 文件齐了就算就绪（真加载要 sherpa-onnx，这里只验证判断逻辑）
    assert asr.ready() is True  # type: ignore[attr-defined]


# ── 朗读音色：默认走在线神经语音，可回退 SAPI ───────────────────────────
#
# K 的原话是「音色有点人机」。Windows 自带的中文 TTS 只有 Huihui 一档拼接音，
# 换音色的唯一出路是换引擎，所以这些用例锁的是「默认必须是 edge」这条决定。


def test_tts_engine_defaults_to_edge(tmp_path):
    cfg = load(_write(tmp_path, BASE + "voice: {}\n")).voice
    assert cfg is not None
    assert cfg.tts_engine == "edge"
    # 选项层也要跟着，否则 CLI 传下去的还是旧的空值
    assert VoiceOptions.from_config(cfg, project=tmp_path).tts_engine == "edge"


def test_tts_engine_can_be_sapi(tmp_path):
    # 不想联网 / 没装 edge-tts 的人要能明确退回离线引擎
    cfg = load(_write(tmp_path, BASE + "voice:\n  tts_engine: sapi\n")).voice
    assert cfg is not None
    assert cfg.tts_engine == "sapi"


def test_tts_engine_rejects_unknown_value(tmp_path):
    # 写错静默回到默认值，不能让 Lcode 起不来（语音是可选插件）
    cfg = load(_write(tmp_path, BASE + "voice:\n  tts_engine: azure\n")).voice
    assert cfg is not None
    assert cfg.tts_engine == "edge"


def test_tts_voice_passes_through(tmp_path):
    cfg = load(_write(tmp_path, BASE + "voice:\n  tts_voice: zh-CN-YunxiNeural\n")).voice
    assert cfg is not None
    assert VoiceOptions.from_config(cfg, project=tmp_path).tts_voice == "zh-CN-YunxiNeural"


def test_edge_voice_name_falls_back_on_sapi_name():
    # 照抄配置里的 SAPI 名给 edge 会 400；这里必须静默换回默认音色，
    # 否则表现只是「念不出来」，人根本看不出是音色名不匹配
    from lcode.voice import speech

    assert speech._edge_voice("Microsoft Huihui Desktop") == speech.DEFAULT_EDGE_VOICE
    assert speech._edge_voice("") == speech.DEFAULT_EDGE_VOICE
    assert speech._edge_voice("zh-CN-YunxiNeural") == "zh-CN-YunxiNeural"


def test_default_voice_by_engine():
    from lcode.voice import speech

    assert speech.default_voice(speech.EDGE) == speech.DEFAULT_EDGE_VOICE
    # sapi 走系统查询（CI 上可能是空串，只要不是 edge 的名字就算对）
    assert speech.default_voice(speech.SAPI) != speech.DEFAULT_EDGE_VOICE


def test_edge_voices_catalog_has_alternatives():
    from lcode.voice import speech

    names = [name for name, _desc in speech.EDGE_VOICES]
    assert speech.DEFAULT_EDGE_VOICE in names
    # 只有一档就等于没得换，这条用例防的是「清单被删空」
    assert len(names) >= 5
    assert len(set(names)) == len(names)


def test_voice_assistant_reports_actual_engine(tmp_path, monkeypatch):
    """配置 edge 但没装 edge-tts 时，tts_engine() 必须报 sapi。

    否则日志和 --voice-list-voices 都会说「在用 edge」，实际却在放 Huihui。
    """
    from lcode.voice import speech

    monkeypatch.setattr(speech, "edge_available", lambda: False)
    a = _assistant(tmp_path, tts_engine="edge")
    assert a.tts_engine() == "sapi"
    # 音色也要按降级后的引擎挑，不能把 edge 的音色名塞给 SAPI
    assert a.voice_name() != speech.DEFAULT_EDGE_VOICE

    a2 = _assistant(tmp_path, tts_engine="edge")
    monkeypatch.setattr(speech, "edge_available", lambda: True)
    a2._voice_cache = None
    assert a2.tts_engine() == "edge"
    assert a2.voice_name() == speech.DEFAULT_EDGE_VOICE


def test_speak_passes_engine_through(tmp_path, monkeypatch):
    # say() 必须把引擎传下去：只传音色的话，配置换了引擎也还在走 SAPI
    from lcode.voice import speech

    seen: list[dict] = []

    def fake_speak(text, voice="", rate=0, engine="edge", timeout=180.0):
        seen.append({"text": text, "engine": engine, "voice": voice})
        return True

    monkeypatch.setattr(speech, "speak", fake_speak)
    a = _assistant(tmp_path, quiet=False, tts_engine="sapi", tts_voice="Microsoft Huihui Desktop")
    a.say("现在几点")
    assert seen[0]["engine"] == "sapi"
    assert seen[0]["voice"] == "Microsoft Huihui Desktop"


def test_greet_does_not_block_main_loop(tmp_path, monkeypatch):
    """合成要 1 秒多（在线语音），greet 必须立刻返回。

    第一版在 greet 里同步调 speak_async，于是每喊一次唤醒词主循环就卡一下，
    麦克风缓冲跟着溢出。
    """
    import threading

    from lcode.voice import speech

    started = threading.Event()
    release = threading.Event()

    def slow_speak(*_args, **_kwargs):
        started.set()
        release.wait(2)
        return _FakeProc(True)

    monkeypatch.setattr(speech, "speak_async", slow_speak)
    a = _assistant(tmp_path, quiet=False)
    begin = time.monotonic()
    a.greet()
    elapsed = time.monotonic() - begin
    assert elapsed < 0.5  # 没等合成
    # 合成期间也要吞自己的音频，否则准备阶段的麦克风内容会被当指令
    assert a._tts_running() is True
    assert started.wait(2) is True
    release.set()


def test_tts_running_survives_pending_timeout(tmp_path):
    # 合成线程卡死也不能一直吞音频：超时后必须放行
    a = _assistant(tmp_path, quiet=False)
    a._tts_pending = True
    a._speak_until = time.monotonic() - 1
    assert a._tts_running() is False


def test_say_drains_greeting_first(tmp_path, monkeypatch):
    """「在」还没念完就开始念答案，两个声音会叠在一起。

    在线语音合成要 1~2 秒，用户往往在「在」出声前就说完了指令——所以 say()
    必须先等一下 greet，且这个等待要有上限（不能把主流程卡死）。
    """
    from lcode.voice import speech

    spoken: list[str] = []
    monkeypatch.setattr(speech, "speak", lambda text, **kw: spoken.append(text) or True)

    a = _assistant(tmp_path, quiet=False)
    a._stream = _FakeStream()
    a._new_recognizer = lambda: None
    a._tts_pending = True
    a._speak_until = time.monotonic() + 30  # 合成中

    def finish_later() -> None:
        time.sleep(0.2)
        a._tts_pending = False

    import threading

    threading.Thread(target=finish_later, daemon=True).start()
    begin = time.monotonic()
    a.say("好")
    waited = time.monotonic() - begin
    assert spoken == ["好"]
    assert waited >= 0.15  # 真的等了，不是直接抢话


def test_say_does_not_wait_forever(tmp_path, monkeypatch):
    # 合成线程彻底卡死：最多等 DRAIN_TTS_MAX，答案照念
    from lcode.voice import speech

    spoken: list[str] = []
    monkeypatch.setattr(speech, "speak", lambda text, **kw: spoken.append(text) or True)

    a = _assistant(tmp_path, quiet=False)
    a._stream = _FakeStream()
    a._new_recognizer = lambda: None
    a._tts_pending = True
    a._speak_until = time.monotonic() + 30

    begin = time.monotonic()
    a.say("好")
    waited = time.monotonic() - begin
    assert spoken == ["好"]
    assert waited < wake_mod.DRAIN_TTS_MAX + 1.0
