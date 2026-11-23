"""看门狗联调：用「卡死的假麦克风」跑真实的 run() 主循环。

为什么要有这个脚本：看门狗防的是**静默失聪**（进程活着、日志不写、喊什么都不应），
这种故障没法靠等它自然发生来验证。单测只跑 ``_watchdog_loop``，跑不到
``_read_block`` → ``_reopen_stream`` → 重新听 这条完整路径，所以补一个最小联调。

用法（不需要真的拔麦克风）::

    .venv\\Scripts\\python.exe scripts\\voice_watchdog_probe.py
    .venv\\Scripts\\python.exe scripts\\voice_watchdog_probe.py --watchdog 1.0

期望输出（``recoveries=1``、只重建一次）::

    streams_handed=['stalled', 'good']
    old_stream_aborted=True
    recoveries=1
    ...
    ! 看门狗：2 秒没读到音频块（麦克风流卡死），第 1 次重建
    麦克风已重建，继续听

脚本需要真实的 vosk 模型（``python -m lcode.voice.download`` 下过就有）。
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from lcode.voice import wake
from lcode.voice.wake import VoiceOptions


class StalledStream:
    """卡死的麦克风：``read()`` 永远不返回——这就是现场那个故障的样子。"""

    def __init__(self) -> None:
        self.aborted = False
        self.closed = False

    def read(self, block: int):  # noqa: ANN201
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not self.aborted:
            time.sleep(0.01)
        # 真实情况是 abort() 让阻塞的 read 抛异常退出
        raise RuntimeError("stream aborted")

    def stop(self) -> None:
        self.aborted = True

    def abort(self) -> None:
        self.aborted = True

    def start(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class GoodStream:
    """重开之后的那条流：正常吐静音，读够次数就用 KeyboardInterrupt 收尾。"""

    def __init__(self, stop_after: int) -> None:
        self.reads = 0
        self.stop_after = stop_after

    def read(self, block: int):  # noqa: ANN201
        self.reads += 1
        if self.reads > self.stop_after:
            raise KeyboardInterrupt
        time.sleep(0.005)
        return b"\x00\x00" * block, False

    def stop(self) -> None:
        pass

    def abort(self) -> None:
        pass

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="看门狗联调（假卡死麦克风）")
    parser.add_argument("--watchdog", type=float, default=2.0, help="看门狗阈值秒数")
    parser.add_argument("--model-dir", default="", help="vosk 模型目录（默认用配置里的默认值）")
    args = parser.parse_args()

    lines: list[str] = []
    stalled = StalledStream()
    good = GoodStream(stop_after=40)
    handed: list[str] = []

    def fake_open_input(sd, device, **kwargs):  # noqa: ANN001, ANN003
        if not handed:
            handed.append("stalled")
            return 0, stalled
        handed.append("good")
        return 0, good

    wake.open_input = fake_open_input  # type: ignore[assignment]

    opts = VoiceOptions(
        project=Path.cwd(),
        wake_words=["小可"],
        quiet=True,
        watchdog=args.watchdog,
        refine="off",
    )
    assistant = wake.VoiceAssistant(opts, log=lines.append)
    # 巡检间隔压到 50ms：不然等默认的 5 秒太慢
    assistant._watchdog_tick = 0.05

    model_dir = Path(args.model_dir) if args.model_dir else opts.model_dir
    model = wake.load_model(model_dir, lines.append)

    thread = threading.Thread(target=assistant.run, args=(model,), daemon=True)
    thread.start()
    thread.join(timeout=args.watchdog * 10 + 20)

    ok = assistant._recoveries == 1 and stalled.aborted and good.reads > 0
    report = [
        f"streams_handed={handed}",
        f"old_stream_aborted={stalled.aborted}",
        f"good_stream_reads={good.reads}",
        f"recoveries={assistant._recoveries}",
        f"result={'OK' if ok else 'FAIL'}",
        "--- log ---",
        *lines,
    ]
    print("\n".join(report))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
