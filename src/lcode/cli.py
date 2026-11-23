"""Entry point: load config, connect MCP servers, then launch the TUI."""

# 按固定顺序把散落的部件——配置、工具注册表、MCP 连接、权限引擎——组装成一个可运行的 TUI 应用。
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from lcode import __version__
from lcode import config as config_mod
from lcode import instructions as instructions_pkg
from lcode import memory as memory_pkg
from lcode import session as session_pkg
from lcode import skills as skills_pkg
from lcode.compact import new_session_context
from lcode.config import ConfigError, load
from lcode.permission import new_engine
from lcode.tool import new_default_registry

# 配置准备结果
STATUS_READY = "ready"
STATUS_INIT = "init"
STATUS_NEEDS_KEY = "needs_key"


def _prepare_config(explicit: str | None, init_only: bool) -> tuple[Path | None, str]:
    """决定本次运行用哪份配置；缺失时生成用户级模板。

    优先级：--config > LCODE_CONFIG > ./.lcode/config.yaml > ~/.lcode/config.yaml。
    都找不到时写一份模板到 ~/.lcode/config.yaml，让「装完直接敲 lcode」也能得到明确指引。
    """
    from lcode import bootstrap

    if explicit:
        return Path(explicit).expanduser(), STATUS_READY

    found = config_mod.resolve()
    if found is not None:
        if init_only:
            # --init 的语义是「生成模板后退出」：配置已存在时必须直接退出，
            # 否则会顺着后面的分支启动 TUI——安装脚本最后一步就会把人丢进全屏界面。
            print(f"配置已存在，未做改动: {found}")
            return None, STATUS_INIT
        return found, STATUS_READY

    path, created = bootstrap.ensure_user_config()
    if created:
        print(bootstrap.banner(path), file=sys.stderr)
    if init_only:
        print(f"配置模板已就绪: {path}")
        return None, STATUS_INIT
    return path, STATUS_NEEDS_KEY


def _resident_dir(root: str) -> Path:
    return Path(root) / ".lcode" / "resident"


async def _resident_early_exit(args) -> int | None:
    """处理 --enqueue / --tasks 这类一次性命令；不是它们就返回 None 继续正常流程。"""
    from lcode.resident.queue import TaskQueue

    root = str(Path.cwd().resolve())

    text = getattr(args, "enqueue", "") or ""
    # 来源标记由调用方声明（语音助手传 voice），daemon 据此决定要不要发邮件通知。
    origin = getattr(args, "enqueue_origin", "") or "cli"
    if text.strip():
        task = TaskQueue(_resident_dir(root)).enqueue(text, origin=origin)
        print(f"已入队 {task.id}: {task.prompt.strip()[:80]}")
        return 0

    # --enqueue-file：把长指令（尤其是中文）放进 UTF-8 文件再入队。
    # 理由：命令行参数会经过 Windows 的 OEM 代码页与计划任务的参数拼接，
    # 中文很容易变成乱码；文件读进来是确定的 UTF-8，不受这些环节影响。
    prompt_file = getattr(args, "enqueue_file", "") or ""
    if prompt_file.strip():
        path = Path(prompt_file).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"读不了指令文件 {path}: {exc}", file=sys.stderr)
            return 2
        if not body.strip():
            print(f"指令文件是空的: {path}", file=sys.stderr)
            return 2
        task = TaskQueue(_resident_dir(root)).enqueue(body, origin=origin)
        print(f"已入队 {task.id}: {task.prompt.strip()[:80]}")
        return 0

    if getattr(args, "resident_tasks", False):
        queue = TaskQueue(_resident_dir(root))
        tasks = queue.load()
        if not tasks:
            print(f"队列为空（{queue.path}）")
            return 0
        print(f"{'ID':<14} {'状态':<8} {'创建时间':<20} 任务")
        for task in tasks:
            print(
                f"{task.id:<14} {task.status:<8} {task.created:<20} "
                f"{' '.join(task.prompt.split())[:60]}"
            )
            if task.result_file:
                print(f"{'':<14} {'':<8} → {task.result_file}")
            if task.error:
                print(f"{'':<14} {'':<8} ! {task.error}")
        return 0

    return None


async def _check_mail(root: str) -> int:
    """--check-mail：拉一次收件箱并入队，然后退出。

    刻意不执行任务：拉信和执行分开，出错时能立刻分清是「连不上邮箱」
    还是「模型跑挂了」，也让任务计划程序可以每 5 分钟只做一次廉价检查。
    """
    from lcode.mailbox import REPORTED_NAME, RejectionLog, fetch_new, load_config
    from lcode.resident.queue import ORIGIN_MAIL, TaskQueue

    cfg = load_config(root)
    if not cfg.ready():
        print(
            "收件箱未启用或配置不全：需要 .lcode/mail.yaml 里的 imap_host / "
            "senders / user / auth_code（auth 可继承 notify.yaml）",
            file=sys.stderr,
        )
        return 2

    queue = TaskQueue(_resident_dir(root))
    known = {task.message_id for task in queue.load() if task.message_id}
    result = fetch_new(cfg, skip_ids=known)

    # 退信留在收件箱里（不改用户的已读状态），每轮都会重新看到；只报第一次。
    log = RejectionLog(_resident_dir(root) / REPORTED_NAME)
    for item in log.take_new(result.rejected):
        print(f"退回：{item.sender} / {item.subject[:60]}（{item.reason}）")

    added: list[str] = []
    for mail in result.accepted:
        if mail.message_id and queue.has_message(mail.message_id):
            continue
        try:
            task = queue.enqueue(
                mail.prompt(),
                origin=ORIGIN_MAIL,
                reply_to=mail.sender,
                subject=mail.subject,
                message_id=mail.message_id,
                thread=mail.thread_key,
            )
        except ValueError as exc:
            print(f"转任务失败：{exc}", file=sys.stderr)
            continue
        added.append(f"{task.id}  {' '.join(mail.prompt().split())[:60]}")

    if not added:
        print("没有新的可执行邮件")
        return 0
    print("\n".join([f"已入队 {len(added)} 条：", *added]))
    if not cfg.allow_write:
        print("（邮件任务以只读模式执行；需要写权限请加 --mail-allow-write 或改 mail.yaml）")
    return 0


async def _mail_peek(root: str, limit: int = 8, outbox: bool = False) -> int:
    """--mail-peek：把最近邮件被解析成什么样原样打出来。

    存在的理由：「我明明在正文里写了，它却像只看到主题」这种问题，
    光看 daemon.log 是查不出来的——日志只写指令前 60 字。这里把每封信的
    主题、正文长度、话题键、最终指令都摊开，一眼能看出是正文没发出来
    （长度 0）还是被过滤/被引用块挤掉了。

    ``outbox=True`` 时改看**发件**记录（谁让它发过什么、发没发出去）。
    """
    from dataclasses import replace

    from lcode.mailbox import OUTBOX_NAME, fetch_new, load_config

    root_path = Path(root)
    if outbox:
        return _print_outbox(root_path, limit)

    cfg = load_config(root)
    if not cfg.ready():
        print(
            "收件箱未启用或配置不全：需要 .lcode/mail.yaml 里的 imap_host / "
            "senders / user / auth_code（后两项可继承 notify.yaml）",
            file=sys.stderr,
        )
        return 2

    result = fetch_new(replace(cfg, max_per_poll=max(1, limit)))
    print(f"收件箱 {cfg.user} · 回看 {cfg.catchup_days} 天 · 主题前缀 {cfg.subject_prefix}")
    print(f"通过闸门 {len(result.accepted)} 封，退回 {len(result.rejected)} 封")
    for mail in result.accepted:
        print("")
        print(f"[收] {mail.uid}  {mail.sender}  {mail.subject}")
        print(f"     正文 {len(mail.body.strip())} 字 · 话题键「{mail.thread_key}」")
        preview = " ".join(mail.prompt().split())
        print(f"     指令 {len(mail.prompt())} 字：{preview[:160]}")
        if not mail.body.strip():
            print("     ！正文是空的：这封信只在主题里写了内容，或客户端没把正文发出来。")
    for item in result.rejected:
        print(f"[退] {item.uid}  {item.sender}  {item.subject}  ← {item.reason}")
    if not result.accepted and not result.rejected:
        print("（没有新的可读邮件）")
    allowed = "、".join(cfg.send_to) if cfg.send_to else "（空，Agent 不能主动发信）"
    print(f"\n发信白名单：{allowed}")
    print(f"发件记录：lcode --mail-peek --outbox（{OUTBOX_NAME}）")
    return 0


def _mail_allow_to(root: str, address: str, *, inbound: bool = False) -> int:
    """--mail-allow-to / --mail-allow-from：改 mail.yaml 的两个白名单。

    为什么要有这个入口：白名单决定了 Agent 能不能把数据发出去、以及谁能给它派活。
    手改 YAML 时很容易顺手写成 ``- @qq.com``——那个写法会让整份配置作废，而表现是
    「邮件突然一封都不收了」，排查成本极高。走这里就顺带处理了引号。

    ``inbound=True`` 时改的是 ``senders``（收信白名单）：只给发信白名单、不给收信
    白名单，会出现「我请对方回信、对方回信却被当陌生地址退回」的断裂。
    """
    from lcode.mailbox import CONFIG_NAME, add_send_to, add_senders, load_config

    root_path = Path(root)
    path = root_path / ".lcode" / CONFIG_NAME
    changed, message = (add_senders if inbound else add_send_to)(path, address)
    if not changed:
        print(message, file=sys.stderr)
        return 1
    cfg = load_config(root_path)
    print(message)
    if inbound:
        allowed = "、".join(cfg.senders) if cfg.senders else "（空）"
        print(f"当前收信白名单：{allowed}")
        print(f"现在他发一封主题以 {cfg.subject_prefix} 开头的邮件过来，就会变成任务。")
    else:
        allowed = "、".join(cfg.send_to) if cfg.send_to else "（空）"
        print(f"当前发信白名单：{allowed}")
        print("现在可以对 Agent 说「给 <地址> 发一封邮件…」了。")
    return 0


def _mail_allowed(root: str) -> int:
    """--mail-allowed：列出发信白名单与 SMTP 是否就绪。"""
    from lcode.mailbox import CONFIG_NAME, load_config

    cfg = load_config(Path(root))

    from lcode.notify import load_config as load_notify_config

    email_cfg = load_notify_config(root).email
    path = Path(root) / ".lcode" / CONFIG_NAME

    if not cfg.send_to:
        print("发信白名单是空的：Agent 不能主动发信。")
        print("  加一个：lcode --mail-allow-to someone@qq.com")
        print(f"  配置文件：{path}")
    else:
        print(f"发信白名单（{path}）：")
        for item in cfg.send_to:
            print(f"  - {item}")
    if email_cfg.smtp_ready():
        print(f"SMTP：{email_cfg.smtp_host}:{email_cfg.smtp_port} / {email_cfg.user}（可用）")
    else:
        print("SMTP：未就绪，需要 .lcode/notify.yaml 的 email 段配全")
        print("      （enabled / smtp_host / user / auth_code）")
    return 0


def _print_outbox(root: Path, limit: int) -> int:
    """打印最近几次主动发信尝试。

    「它到底给谁发过什么」是发信功能唯一的问责手段，所以这个入口必须存在。
    被白名单挡掉的尝试也在里面——「想发但没发」同样是有用的信息。
    """
    import json

    from lcode.mailbox import OUTBOX_NAME

    path = root / ".lcode" / OUTBOX_NAME
    if not path.is_file():
        print(f"还没有发件记录（{path}）")
        return 0
    try:
        entries = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
        ]
    except (OSError, ValueError) as exc:
        print(f"发件记录读取失败: {exc}", file=sys.stderr)
        return 1
    print(f"发件记录 {path}（共 {len(entries)} 条，显示最近 {min(limit, len(entries))} 条）")
    for entry in entries[-max(1, limit) :]:
        flag = "已发" if entry.get("ok") else "未发"
        print("")
        print(f"[{flag}] {entry.get('at', '')}  → {entry.get('to', '')}")
        print(f"       主题：{entry.get('subject', '')}")
        if entry.get("reason"):
            print(f"       原因：{entry['reason']}")
        preview = " ".join(str(entry.get("body_preview", "")).split())
        print(f"       正文：{preview[:120]}")
    return 0


def _voice_options(cfg, args):
    """把配置里的 voice 段和命令行覆盖合成一份 VoiceOptions。

    ``cfg`` 可能为 None：``--voice-list-devices`` 在读完配置之前就分流了
    （麦克风坏的时候，配置多半也是坏的，不该因为缺 api_key 就看不到设备列表）。
    """
    from lcode.voice.wake import VoiceOptions

    voice_cfg = getattr(cfg, "voice", None)
    opts = VoiceOptions.from_config(voice_cfg, project=Path.cwd().resolve())
    words = [w for w in (getattr(args, "voice_wake", None) or []) if w.strip()]
    if words:
        opts.wake_words = words
    if getattr(args, "voice_device", None) is not None:
        opts.device = int(args.voice_device)
    if getattr(args, "voice_project", ""):
        opts.project = Path(args.voice_project).expanduser().resolve()
    if getattr(args, "voice_no_speak", False):
        opts.speak_answer = False
    if getattr(args, "voice_tts_engine", ""):
        opts.tts_engine = args.voice_tts_engine.strip().lower()
    if getattr(args, "voice_tts_voice", ""):
        opts.tts_voice = args.voice_tts_voice.strip()
    if getattr(args, "voice_no_conversation", False):
        opts.conversation = False
    if getattr(args, "voice_refine", ""):
        opts.refine = args.voice_refine.strip().lower()
    opts.quiet = bool(getattr(args, "voice_quiet", False))
    opts.debug = bool(getattr(args, "voice_debug", False))
    if getattr(args, "voice_watchdog", None) is not None:
        opts.watchdog = max(0.0, float(args.voice_watchdog))
    opts.simulate = getattr(args, "voice_simulate", "") or ""
    opts.simulate_audio = bool(getattr(args, "voice_simulate_audio", False))
    if getattr(args, "voice_no_auto_run", False):
        opts.auto_run = False
    if getattr(args, "voice_model_dir", ""):
        opts.model_dir = Path(args.voice_model_dir).expanduser()
    return opts


def _voice_asr(cfg, opts, log):
    """按配置装配精转写；返回 None 表示「只用 vosk」。

    三种后端：

    - ``off``：纯 vosk，42MB 小模型，装完就能用；
    - ``sensevoice``：本地 SenseVoice-Small（228MB），不联网、不要 key、实测 80ms；
    - ``cloud``：走 ``audio`` 段（qwen3-asr-flash 等），按秒计费，音频离开本机。

    返回的 callable 永远不抛异常（失败返回空串），并挂 ``ready()`` / ``warmup()``。
    """
    backend = getattr(opts, "refine", "off")
    if backend in ("", "off"):
        return None

    if backend == "sensevoice":
        from lcode.voice import local_asr

        asr = local_asr.make_transcriber(
            log=log,
            num_threads=int(getattr(opts, "refine_threads", local_asr.DEFAULT_THREADS)),
        )
        if not asr.ready():  # type: ignore[attr-defined]
            for line in local_asr.SenseVoice(log=log).problems():
                log(f"! {line}")
            log("  退回本地 vosk（识别质量会下降）")
            return None
        return asr

    if backend == "cloud":
        from lcode.voice import cloud

        audio_cfg = getattr(cfg, "audio", None)
        asr = cloud.make_transcriber(audio_cfg, log)
        if not asr.ready():  # type: ignore[attr-defined]
            log("! 开了 refine: cloud，但 audio 段没配全（需要 backend/api_key），退回本地 vosk")
            return None
        return asr

    log(f"! 未知的 voice.refine: {backend!r}（可选 off / sensevoice / cloud），退回 vosk")
    return None


def _voice_list_voices(cfg, args) -> int:
    """--voice-list-voices：把能选的音色摊开，标出当前会用的那个。

    不需要模型、不需要麦克风——「换个不人机的音色」这件事不该被
    vosk 模型缺失挡住（模型 42MB，很多人根本没下）。
    """
    from lcode.voice import speech

    opts = _voice_options(cfg, args)
    engine = opts.tts_engine
    print(f"当前引擎：{engine}（配置 voice.tts_engine）")
    if engine == speech.EDGE and not speech.edge_available():
        print("  ! 没装 edge-tts，实际会落回 sapi（音色回到人机档）")
        print("    安装：pip install edge-tts")

    want_edge = speech.EDGE_VOICES
    current = opts.tts_voice or (
        speech.DEFAULT_EDGE_VOICE if engine == speech.EDGE else speech.pick_voice()
    )
    print("\nedge（微软在线神经语音，要联网；听起来像真人）：")
    for name, desc in want_edge:
        mark = "*" if name == current else " "
        print(f" {mark} {name:<36} {desc}")

    print("\nsapi（Windows 自带，离线；中文通常只有一档，人机感就来自这里）：")
    installed = speech.list_voices()
    if not installed:
        print("   （没装任何 SAPI 语音）")
    for name in installed:
        mark = "*" if name == current else " "
        print(f" {mark} {name}")

    print('\n试听：lcode --voice-say "我是小可，现在可以说话了"')
    print('换一个：lcode --voice-say "我是小可" --voice-tts-voice zh-CN-XiaoyiNeural')
    return 0


def _voice_say_text(cfg, args, text: str) -> int:
    """--voice-say：把一句话念出来，用来试听音色。

    存在的理由：音色这事只能靠耳朵定。让人去改配置、重启常驻实例、
    再喊一声唤醒词来判断，成本高到不会有人真去调——最后就一直用人机音。
    """
    from lcode.voice import speech

    opts = _voice_options(cfg, args)
    engine = opts.tts_engine
    voice = opts.tts_voice or (
        speech.DEFAULT_EDGE_VOICE if engine == speech.EDGE else speech.pick_voice()
    )
    print(f"引擎 {engine} · 音色 {voice or '（无）'}")
    if not speech.available():
        print("只有 Windows 支持朗读。", file=sys.stderr)
        return 2
    ok = speech.speak(text, voice=voice, engine=engine, timeout=120)
    if not ok:
        print("没念出来（edge 连不上会回退 sapi，两个都不行就是真没声音）", file=sys.stderr)
        return 1
    return 0


async def _run_voice(cfg, args) -> int:
    """--voice / --voice-selftest / --voice-list-devices / --voice-list-voices。

    几条路都先做一次依赖体检：缺包缺模型时说人话，不扔 ImportError 栈。
    """
    from lcode.voice import check
    from lcode.voice.wake import VoiceLog

    if getattr(args, "voice_list_devices", False):
        try:
            import sounddevice as sd
        except ImportError:
            print("缺少 sounddevice。安装：pip install lcode[voice]", file=sys.stderr)
            return 2
        from lcode.voice.devices import list_devices

        print(list_devices(sd))
        return 0

    # 音色相关两条路只碰 TTS，不碰麦克风和模型：先分流，别让缺模型拦住试听
    if getattr(args, "voice_list_voices", False):
        return _voice_list_voices(cfg, args)
    say_text = getattr(args, "voice_say", "") or ""
    if say_text.strip():
        return _voice_say_text(cfg, args, say_text)

    av = check(getattr(cfg, "voice", None))
    if getattr(args, "voice_selftest", False):
        # 自检只要求「依赖 + 模型」，不要求 TTS：先确认麦克风通不通
        if av.missing_packages:
            print("缺少 Python 包：" + "、".join(av.missing_packages), file=sys.stderr)
            print("安装：pip install lcode[voice]", file=sys.stderr)
            return 2
        if not av.model_ok:
            print(f"缺少 vosk 模型：{av.model_dir}", file=sys.stderr)
            print("下载：python -m lcode.voice.download", file=sys.stderr)
            return 2

    if not av.ready:
        for line in av.problems():
            print(line, file=sys.stderr)
        return 2

    from lcode.voice import wake

    opts = _voice_options(cfg, args)
    log = VoiceLog(quiet=opts.quiet)

    # TTS 缺失只是念不出来，不该拦住整个功能——说清楚就行
    if not av.tts_ok:
        log("! 没有中文 TTS 语音，回答只会写进日志、念不出来")

    try:
        model = wake.load_model(opts.model_dir, log)
    except wake.VoiceError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    assistant = wake.VoiceAssistant(opts, log=log, asr=_voice_asr(cfg, opts, log))
    try:
        if getattr(args, "voice_selftest", False):
            return assistant.selftest(model)
        # 回放模式不抢单实例锁：可以一边让常驻的在听，一边回放测试
        lock = None
        if not opts.simulate:
            lock = wake.acquire_instance_lock()
            if lock is None:
                log("已经有一个语音实例在监听，本次退出（同一时间只能有一个）")
                return 0
        try:
            return assistant.run(model)
        finally:
            if lock is not None:
                lock.close()
    except wake.VoiceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        log("语音退出")


async def _run_resident(cfg, args) -> int:
    """--resident / --resident-once：常驻循环或单轮执行。"""
    from lcode.permission import parse_mode
    from lcode.resident import Daemon, DaemonOptions

    root = str(Path.cwd().resolve())
    mode_name = (getattr(args, "resident_mode", "") or "default").strip()
    mode, ok = parse_mode(mode_name)
    if not ok:
        print(
            f"未知权限模式: {mode_name}（可选 default / acceptEdits / plan / bypassPermissions）",
            file=sys.stderr,
        )
        return 2

    opts = DaemonOptions(
        root=root,
        interval=max(1.0, float(getattr(args, "interval", 300.0) or 300.0)),
        max_tasks_per_round=max(1, int(getattr(args, "resident_batch", 5) or 5)),
        max_turns=max(1, int(getattr(args, "resident_turns", 20) or 20)),
        timeout=max(1.0, float(getattr(args, "resident_timeout", 900.0) or 900.0)),
        mode=mode,
        provider_index=max(0, int(getattr(args, "provider_index", 0) or 0)),
        extra_allowed=list(getattr(args, "resident_allow", None) or []),
        notify=not bool(getattr(args, "no_notify", False)),
        mail=bool(getattr(args, "mail_watch", False)),
        mail_allow_write=bool(getattr(args, "mail_allow_write", False)),
    )
    daemon = Daemon(cfg, opts)
    if not daemon.acquire_lock():
        return 1
    once = bool(getattr(args, "resident_once", False))
    try:
        if once and opts.mail:
            # 轻量预检：先只连 IMAP，不启动 TaskRunner。
            # TaskRunner 要拉起 MCP 子进程等，空跑约 4 秒；补信轮询每 3 分钟一次，
            # 大部分轮次没有新邮件，没必要为此付启动开销。
            daemon.ingest_mail()
            if not daemon.has_pending():
                return 0
        await daemon.start()
        if once:
            handled = await daemon.run_once()
            daemon.log(f"一次性运行结束，处理 {handled} 条任务")
        else:
            await daemon.run_forever()
    except KeyboardInterrupt:
        daemon.log("收到 Ctrl+C，退出")
    finally:
        await daemon.close()
        daemon.release_lock()
    return 0


async def _amain(member_args=None) -> int:
    if member_args is not None and getattr(member_args, "worktree", ""):
        os.chdir(member_args.worktree)

    # 常驻模式的三个入口都在 TUI 之前处理：它们不需要界面，也不加载 textual。
    if member_args is not None:
        early = await _resident_early_exit(member_args)
        if early is not None:
            return early
        if getattr(member_args, "check_mail", False):
            # 拉信不需要 LLM 配置，所以放在读配置文件之前：邮箱填对了但 api_key
            # 还没填时，也应该能先确认收信链路是通的。
            return await _check_mail(str(Path.cwd().resolve()))
        if getattr(member_args, "mail_peek", False):
            # 同上：纯 IMAP / 本地文件诊断，不碰模型，也不入队。
            return await _mail_peek(
                str(Path.cwd().resolve()),
                max(1, int(getattr(member_args, "mail_limit", 8) or 8)),
                outbox=bool(getattr(member_args, "mail_outbox", False)),
            )
        allow_to = getattr(member_args, "mail_allow_to", "") or ""
        if allow_to.strip():
            # 改本地配置即可，不需要模型，也不必先有 api_key。
            return _mail_allow_to(str(Path.cwd().resolve()), allow_to.strip())
        allow_from = getattr(member_args, "mail_allow_from", "") or ""
        if allow_from.strip():
            return _mail_allow_to(str(Path.cwd().resolve()), allow_from.strip(), inbound=True)
        if getattr(member_args, "mail_allowed", False):
            return _mail_allowed(str(Path.cwd().resolve()))
        if getattr(member_args, "voice_list_devices", False):
            # 列音频设备不需要 LLM 配置：麦克风坏了的时候，配置多半也是坏的
            return await _run_voice(None, member_args)

    explicit = getattr(member_args, "config", "") or None
    init_only = bool(getattr(member_args, "init", False))
    try:
        cfg_path, status = _prepare_config(explicit, init_only)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if status == STATUS_INIT:
        return 0
    if status == STATUS_NEEDS_KEY:
        print(f'填入 api_key 后重新运行: lcode --config "{cfg_path}"', file=sys.stderr)
        return 1

    try:
        cfg = load(str(cfg_path))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # 语音模式：同样不加载 TUI。放在常驻之前，因为语音本身就是走常驻队列。
    if member_args is not None and (
        getattr(member_args, "voice", False)
        or getattr(member_args, "voice_selftest", False)
        or getattr(member_args, "voice_list_voices", False)
        or (getattr(member_args, "voice_say", "") or "").strip()
    ):
        return await _run_voice(cfg, member_args)

    # 常驻模式：不加载 TUI，直接跑无头循环。
    if member_args is not None and (
        getattr(member_args, "resident", False) or getattr(member_args, "resident_once", False)
    ):
        return await _run_resident(cfg, member_args)

    # Imported lazily so a config error is reported before pulling in the TUI stack.
    from lcode import mcp as mcp_client
    from lcode.hook import load as load_hooks
    from lcode.tui.app import LcodeApp

    registry = new_default_registry(cfg.vision, cfg.audio)
    root = str(Path.cwd().resolve())

    # 内置 stdio MCP server（如 lcode.mcp_servers.web_search）需要「装着 lcode 的解释器」，
    # 允许 .lcode.yaml 里写 command: "${LCODE_PYTHON}"，不必把绝对路径写死。
    os.environ.setdefault("LCODE_PYTHON", sys.executable)

    mcp_cfg = mcp_client.load_config(root)
    mcp_mgr = await mcp_client.new_manager(mcp_cfg, __version__)

    # 项目指令 + 长期记忆索引（启动时加载一次）。
    instruction_text = instructions_pkg.Loader(root).load()
    mem_mgr = memory_pkg.Manager(
        project_dir=str(Path(root) / ".lcode" / "memory"),
        user_dir=str(Path.home() / ".lcode" / "memory"),
    )
    memory_text = mem_mgr.load_index()

    # Skill catalog：启动期扫描项目级与用户级目录。
    from lcode import coordinator
    from lcode.agent.agent_tool import AgentTool
    from lcode.subagent import load_catalog
    from lcode.task import Manager, SendMessageTool, TaskGetTool, TaskListTool, TaskStopTool
    from lcode.team import AgentNameRegistry
    from lcode.team import Manager as TeamManager
    from lcode.team.tools import team_tools
    from lcode.tool.install_skill import InstallSkillTool
    from lcode.tool.load_skill import LoadSkillTool

    skill_loader = skills_pkg.SkillLoader(root)
    skill_loader.load_all()
    load_skill_tool = LoadSkillTool()
    install_skill_tool = InstallSkillTool()

    # SubAgent：三层角色定义 + 后台任务管理器 + 5 个内置工具。
    subagent_catalog = load_catalog(root)
    task_mgr = Manager()
    name_reg = AgentNameRegistry()
    task_mgr.set_name_registry(name_reg)
    coordinator_mode = coordinator.is_enabled(cfg)

    # Worktree：非 Git 仓库/异常时降级为 None，不阻断启动。
    from lcode import worktree as worktree_pkg

    try:
        worktree_mgr = worktree_pkg.Manager(root)
    except Exception as exc:  # noqa: BLE001 - worktree is optional
        print(f"Worktree 管理器降级: {exc}", file=sys.stderr)
        worktree_mgr = None
    if worktree_mgr is not None:
        _warn_missing_worktree_ignores(root)

    team_mgr = TeamManager(
        home_dir=Path.home(),
        project_root=root,
        wt_mgr=worktree_mgr,
        task_mgr=task_mgr,
        name_reg=name_reg,
        catalog=subagent_catalog,
        fork_teammate_enabled=cfg.features.fork_teammate,
    )

    async def _on_task_done(task_id: str) -> None:
        await team_mgr.handle_task_done(task_id)

    task_mgr.on_task_done(_on_task_done)

    agent_tool = AgentTool(
        subagent_catalog,
        task_mgr,
        parent=None,
        bg_enabled=cfg.effective_enable_subagent_background(),
        worktree_mgr=worktree_mgr,
        team_hook=team_mgr,
    )

    # Hook：项目级 + 用户级 YAML；任何加载错误只报 stderr，不影响启动（N1）。
    try:
        hook_engine = load_hooks(root)
    except Exception as exc:  # noqa: BLE001 - hooks must never block startup
        print(f"hooks 加载失败: {exc}", file=sys.stderr)
        hook_engine = None

    # 新会话目录 + JSONL 写入器。
    sessions_dir = str(Path(root) / ".lcode" / "sessions")
    ses_ctx = new_session_context(root)
    writer = session_pkg.Writer(ses_ctx.session_dir)

    # 过期会话清理在后台执行，不阻塞启动（F26）。
    cleanup_task = asyncio.create_task(
        asyncio.to_thread(session_pkg.clean_expired, sessions_dir, timedelta(days=30))
    )
    sweep_task = (
        asyncio.create_task(worktree_mgr.sweep_stale(datetime.now() - timedelta(hours=24)))
        if worktree_mgr is not None
        else None
    )

    try:
        for tool in mcp_mgr.tools():
            registry.register(tool)
        for tool in (
            load_skill_tool,
            install_skill_tool,
            TaskStopTool(task_mgr),
            *team_tools(
                team_mgr,
                TaskListTool(task_mgr),
                TaskGetTool(task_mgr),
                SendMessageTool(task_mgr),
            ),
            agent_tool,
        ):
            if registry.get(tool.name()) is None:
                registry.register(tool)

        engine, err = new_engine(root)
        if err is not None:
            print("权限引擎降级:", err, file=sys.stderr)

        if member_args is not None and getattr(member_args, "team_member", False):
            from lcode.team.member import run_team_member

            await run_team_member(
                member_args,
                team_mgr=team_mgr,
                catalog=subagent_catalog,
                registry=registry,
                engine=engine,
                hook_engine=hook_engine,
                cfg=cfg,
            )
            return 0

        app = LcodeApp(
            cfg.providers,
            __version__,
            registry,
            engine,
            writer=writer,
            mem_mgr=mem_mgr,
            instruction_text=instruction_text,
            memory_text=memory_text,
            sessions_dir=sessions_dir,
            workspace=root,
            ses_ctx=ses_ctx,
            skill_loader=skill_loader,
            load_skill_tool=load_skill_tool,
            install_skill_tool=install_skill_tool,
            hook_engine=hook_engine,
            task_mgr=task_mgr,
            subagent_catalog=subagent_catalog,
            worktree_mgr=worktree_mgr,
            team_mgr=team_mgr,
            coordinator_mode=coordinator_mode,
        )
        try:
            try:
                await app.run_async()
            except Exception as exc:  # noqa: BLE001 - report startup/runtime errors cleanly
                print(f"Lcode 启动失败: {exc}", file=sys.stderr)
                return 1
        finally:
            # TUI 正常退出时 action_quit 已派发；这里兜底异常/强制退出路径。
            await app.ensure_session_end("exit")
    finally:
        writer.close()
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        if sweep_task is not None:
            sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task
        await mcp_mgr.close()
        if hook_engine is not None:
            await hook_engine.aclose()
    return 0


def _warn_missing_worktree_ignores(root: str) -> None:
    # F35：只警告，不擅自修改用户的 .gitignore。
    path = Path(root) / ".gitignore"
    if not path.is_file():
        return
    lines = set(path.read_text(encoding="utf-8", errors="replace").splitlines())
    missing = [
        entry
        for entry in (".lcode/worktrees/", ".lcode/worktree_session.json")
        if entry not in lines
    ]
    if missing:
        print(
            "Worktree 提示: .gitignore 缺少 " + "、".join(missing),
            file=sys.stderr,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lcode", description="Lcode 终端 Agent")
    parser.add_argument("--version", action="version", version=f"Lcode {__version__}")
    parser.add_argument(
        "--init", action="store_true", help="生成用户级配置模板 ~/.lcode/config.yaml 后退出"
    )
    parser.add_argument("--config", default="", help="指定配置文件路径（覆盖自动查找）")
    parser.add_argument("--team-member", action="store_true", help="以 Team 队员模式运行")
    parser.add_argument("--team", default="", help="Team 名")
    parser.add_argument("--member", default="", help="队员名")
    parser.add_argument("--agent-id", default="", help="队员 agent_id")
    parser.add_argument("--session-dir", default="", help="队员 session 目录")
    parser.add_argument("--worktree", default="", help="队员 worktree 目录")
    parser.add_argument("--agent-type", default="", help="Agent 角色名")
    parser.add_argument("--model", default="", help="模型覆盖")
    parser.add_argument("--plan-mode", action="store_true", help="以 plan 模式启动")

    resident = parser.add_argument_group("常驻模式")
    resident.add_argument("--enqueue", default="", help="把一条任务加入常驻队列后退出")
    resident.add_argument(
        "--enqueue-origin",
        dest="enqueue_origin",
        default="",
        help="任务来源标记（cli / voice），语音来源不发邮件通知",
    )
    resident.add_argument(
        "--enqueue-file",
        dest="enqueue_file",
        default="",
        metavar="PATH",
        help="从 UTF-8 文件读指令并入队后退出（中文长指令走这条，避开命令行编码）",
    )
    resident.add_argument(
        "--tasks", dest="resident_tasks", action="store_true", help="列出常驻队列后退出"
    )
    resident.add_argument("--resident", action="store_true", help="常驻循环：轮询队列并执行任务")
    resident.add_argument(
        "--resident-once",
        action="store_true",
        help="只处理一轮就退出（供任务计划程序 / CI 调度）",
    )
    resident.add_argument(
        "--interval", type=float, default=300.0, help="常驻轮询间隔秒数（默认 300）"
    )
    resident.add_argument(
        "--resident-mode",
        default="default",
        help="常驻任务的权限模式：default(只读) / acceptEdits / plan / bypassPermissions",
    )
    resident.add_argument(
        "--resident-turns", type=int, default=20, help="单条任务最大迭代轮数（默认 20）"
    )
    resident.add_argument(
        "--resident-batch",
        type=int,
        default=5,
        help="单轮最多处理几条任务（默认 5）",
    )
    resident.add_argument(
        "--resident-timeout", type=float, default=900.0, help="单条任务超时秒数（默认 900）"
    )
    resident.add_argument(
        "--resident-allow",
        action="append",
        default=[],
        metavar="TOOL",
        help="额外允许的工具名（可重复）",
    )
    resident.add_argument(
        "--no-notify",
        dest="no_notify",
        action="store_true",
        help="不发送任务完成/失败通知（桌面横幅），只写日志",
    )

    voice = parser.add_argument_group("语音")
    voice.add_argument(
        "--voice",
        action="store_true",
        help="语音唤醒常驻：喊唤醒词 → 提问 → 念回答（需要 pip install lcode[voice]）",
    )
    voice.add_argument(
        "--voice-selftest",
        dest="voice_selftest",
        action="store_true",
        help="录 4 秒验证麦克风与识别，不入队",
    )
    voice.add_argument(
        "--voice-list-devices",
        dest="voice_list_devices",
        action="store_true",
        help="列出音频设备后退出",
    )
    voice.add_argument(
        "--voice-device", dest="voice_device", type=int, default=None, help="麦克风设备编号"
    )
    voice.add_argument(
        "--voice-wake",
        dest="voice_wake",
        action="append",
        default=[],
        metavar="WORD",
        help="唤醒词（可重复；覆盖配置里的 wake_words）",
    )
    voice.add_argument(
        "--voice-project",
        dest="voice_project",
        default="",
        metavar="PATH",
        help="语音任务的作用根目录（默认当前目录）",
    )
    voice.add_argument(
        "--voice-model-dir",
        dest="voice_model_dir",
        default="",
        metavar="PATH",
        help="vosk 模型目录（默认 ~/.lcode/voice/models/...）",
    )
    voice.add_argument(
        "--voice-no-speak",
        dest="voice_no_speak",
        action="store_true",
        help="只把问题入队，不念回答",
    )
    voice.add_argument(
        "--voice-no-conversation",
        dest="voice_no_conversation",
        action="store_true",
        help="念完回答不再自动接着听：每次提问都要重新喊唤醒词",
    )
    voice.add_argument(
        "--voice-refine",
        dest="voice_refine",
        default="",
        choices=["", "off", "sensevoice", "cloud"],
        help="精转写后端：off(纯 vosk) / sensevoice(本地免费) / cloud(audio 段，按秒计费)",
    )
    voice.add_argument(
        "--voice-no-auto-run",
        dest="voice_no_auto_run",
        action="store_true",
        help="不自动催 --resident-once（已有常驻 daemon 时用）",
    )
    voice.add_argument(
        "--voice-simulate",
        dest="voice_simulate",
        default="",
        metavar="WAV",
        help="回放一个 16bit wav 代替麦克风（调参用）",
    )
    voice.add_argument(
        "--voice-simulate-audio",
        dest="voice_simulate_audio",
        action="store_true",
        help="回放时也真的出声（默认静音）",
    )
    voice.add_argument(
        "--voice-debug", dest="voice_debug", action="store_true", help="打印识别片段"
    )
    voice.add_argument(
        "--voice-watchdog",
        dest="voice_watchdog",
        type=float,
        default=None,
        metavar="SECONDS",
        help="多少秒读不到音频就重建麦克风（默认 90；0 = 关掉看门狗）",
    )
    voice.add_argument(
        "--voice-quiet", dest="voice_quiet", action="store_true", help="不打印、不出声"
    )
    voice.add_argument(
        "--voice-tts-engine",
        dest="voice_tts_engine",
        default="",
        choices=["", "edge", "sapi"],
        help="朗读引擎：edge(在线神经语音，像真人，默认) / sapi(Windows 自带，离线)",
    )
    voice.add_argument(
        "--voice-tts-voice",
        dest="voice_tts_voice",
        default="",
        metavar="NAME",
        help="音色名，如 zh-CN-XiaoxiaoNeural（edge）或 Microsoft Huihui Desktop（sapi）",
    )
    voice.add_argument(
        "--voice-list-voices",
        dest="voice_list_voices",
        action="store_true",
        help="列出可选音色（edge + 系统 SAPI）后退出",
    )
    voice.add_argument(
        "--voice-say",
        dest="voice_say",
        default="",
        metavar="TEXT",
        help="直接用当前音色念一句话后退出（试听音色用）",
    )

    mail = parser.add_argument_group("收件箱")
    mail.add_argument(
        "--mail-watch",
        dest="mail_watch",
        action="store_true",
        help="常驻时每轮先拉收件箱，把 [前缀] 邮件变成任务并回信",
    )
    mail.add_argument(
        "--check-mail",
        dest="check_mail",
        action="store_true",
        help="只拉一次收件箱并入队，然后退出（供任务计划程序调度）",
    )
    mail.add_argument(
        "--mail-peek",
        dest="mail_peek",
        action="store_true",
        help="诊断：打印最近邮件被解析出的正文/话题键/指令，不入队",
    )
    mail.add_argument(
        "--mail-limit",
        dest="mail_limit",
        type=int,
        default=8,
        help="--mail-peek 一次查看几封（默认 8）",
    )
    mail.add_argument(
        "--outbox",
        dest="mail_outbox",
        action="store_true",
        help="配合 --mail-peek：改看发件记录（Agent 主动发过什么）",
    )
    mail.add_argument(
        "--mail-allow-to",
        dest="mail_allow_to",
        default="",
        metavar="ADDRESS",
        help="把地址加进发信白名单（send_to），然后退出",
    )
    mail.add_argument(
        "--mail-allow-from",
        dest="mail_allow_from",
        default="",
        metavar="ADDRESS",
        help="把地址加进收信白名单（senders），允许它给 Agent 派任务，然后退出",
    )
    mail.add_argument(
        "--mail-allowed",
        dest="mail_allowed",
        action="store_true",
        help="列出发信白名单与 SMTP 是否就绪，然后退出",
    )
    mail.add_argument(
        "--mail-allow-write",
        dest="mail_allow_write",
        action="store_true",
        help="允许邮件来源的任务写文件/执行命令（默认只读）",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(_amain(args)))
    except KeyboardInterrupt:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
