"""下载语音模型到 ``~/.lcode/voice/models/``。

用法::

    python -m lcode.voice.download              # 下 vosk 中文小模型（42MB）
    python -m lcode.voice.download --sensevoice # 下 SenseVoice（228MB，识别更准）
    python -m lcode.voice.download --check      # 只看装了没有

为什么模型不打包进 wheel：42MB / 228MB 的二进制塞进 sdist/wheel 会让每次
``pip install`` 都多背一份；而绝大多数用户根本不开语音。放成一条命令按需下载更划算。

两个模型的分工见 :mod:`lcode.voice.local_asr`：vosk 负责流式唤醒词检测
（能边听边吐 partial），SenseVoice 负责整句精转写（更准、也更快）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from lcode.voice.config import default_model_dir, voice_data_dir

MODEL_NAME = "vosk-model-small-cn-0.22"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
# 中文小模型的体积，用来判断下载是否完整（断网/代理劫持会留下半截文件）
EXPECTED_MB = 42

# SenseVoice：只取 int8 onnx + tokens 两个文件。
# 走 hf-mirror 而不是 GitHub：实测这台机器 GitHub release 直连超时。
SENSE_DIR = "sense-voice"
SENSE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSE_BASE = f"https://hf-mirror.com/{SENSE_REPO}/resolve/main"
SENSE_FILES = {
    "model.int8.onnx": 239_233_841,  # 字节数，用来校验完整性
    "tokens.txt": 315_894,
}
SENSE_EXPECTED_MB = 229


def models_root() -> Path:
    return voice_data_dir() / "models"


def is_installed(target: Path | None = None) -> bool:
    path = target or default_model_dir()
    return path.is_dir() and any(path.iterdir())


def download(url: str = MODEL_URL, target: Path | None = None) -> Path:
    """下载并解压。已存在就直接返回（不覆盖，避免把可用模型换成半截的）。"""
    dest = target or default_model_dir()
    if is_installed(dest):
        print(f"模型已存在：{dest}")
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lcode-vosk-") as tmp:
        archive = Path(tmp) / f"{MODEL_NAME}.zip"
        print(f"下载 {url}")
        print(f"  → {archive}（约 {EXPECTED_MB}MB，请稍候）")
        try:
            _fetch(url, archive)
        except Exception as exc:  # noqa: BLE001 - 网络问题要说清是哪一步
            raise SystemExit(f"下载失败：{type(exc).__name__}: {exc}") from exc

        size_mb = archive.stat().st_size / (1024 * 1024)
        print(f"  已下载 {size_mb:.1f}MB")
        if size_mb < EXPECTED_MB * 0.5:
            raise SystemExit(
                f"文件只有 {size_mb:.1f}MB，远小于预期——可能被网络拦截了。"
                "换个网络或手动下载后解压到 "
                f"{dest.parent}"
            )

        try:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest.parent)
        except zipfile.BadZipFile as exc:
            raise SystemExit(f"压缩包损坏（{exc}）。删掉后重新下载") from exc

    extracted = dest.parent / MODEL_NAME
    if extracted != dest and extracted.is_dir():
        # 压缩包里自带同名顶层目录；目标路径不一致时改名过去
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        extracted.rename(dest)
    print(f"完成：{dest}")
    return dest


def sensevoice_dir() -> Path:
    return models_root() / SENSE_DIR


def sensevoice_installed(target: Path | None = None) -> bool:
    d = target or sensevoice_dir()
    return all((d / name).is_file() for name in SENSE_FILES)


def download_sensevoice(target: Path | None = None, base: str = SENSE_BASE) -> Path:
    """下载 SenseVoice 的 int8 onnx + tokens（约 228MB）。

    逐个文件校验字节数：断网、代理劫持、半截文件在没有校验的情况下
    会一直躺在那里，直到 onnxruntime 报一个看不懂的错。
    """
    dest = target or sensevoice_dir()
    if sensevoice_installed(dest):
        print(f"SenseVoice 已存在：{dest}")
        return dest
    dest.mkdir(parents=True, exist_ok=True)

    for name, expected in SENSE_FILES.items():
        path = dest / name
        if path.is_file() and path.stat().st_size == expected:
            print(f"  已存在 {name}（{expected / 1048576:.1f}MB）")
            continue
        url = f"{base}/{name}"
        print(f"下载 {name}（约 {expected / 1048576:.1f}MB）")
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            _fetch(url, tmp)
        except Exception as exc:  # noqa: BLE001 - 要说清是哪一步断了
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"下载失败（{name}）：{type(exc).__name__}: {exc}") from exc
        got = tmp.stat().st_size
        if got != expected:
            tmp.unlink(missing_ok=True)
            raise SystemExit(
                f"{name} 大小不对：期望 {expected} 字节，实际 {got}。"
                "可能被网络拦截或镜像不完整，稍后重试或换网络。"
            )
        tmp.replace(path)
        print(f"  完成 {name}")

    print(f"SenseVoice 就绪：{dest}")
    return dest


def _fetch(url: str, path: Path) -> None:
    # 用 urllib 而不是 requests：语音依赖里没有 requests，也不想为下载多装一个
    request = urllib.request.Request(url, headers={"User-Agent": "lcode-voice/0.1"})
    with urllib.request.urlopen(request, timeout=60) as resp, path.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                sys.stderr.write(f"\r  {pct:3d}%  {done / 1048576:.1f}MB")
                sys.stderr.flush()
    if total:
        sys.stderr.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lcode.voice.download", description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查模型是否已安装")
    parser.add_argument(
        "--sensevoice",
        action="store_true",
        help=f"下 SenseVoice 精转写模型（约 {SENSE_EXPECTED_MB}MB，识别更准且更快）",
    )
    parser.add_argument("--url", default=MODEL_URL, help="自定义 vosk 模型地址")
    parser.add_argument("--target", default="", help="解压目标目录")
    args = parser.parse_args(argv)

    target = Path(args.target).expanduser() if args.target else None

    if args.sensevoice:
        if args.check:
            d = target or sensevoice_dir()
            if sensevoice_installed(d):
                print(f"已安装：{d}")
                return 0
            print(f"未安装。模型应放在：{d}")
            print("下载：python -m lcode.voice.download --sensevoice")
            return 1
        download_sensevoice(target)
        return 0

    if args.check:
        path = target or default_model_dir()
        if is_installed(path):
            print(f"已安装：{path}")
        else:
            print(f"未安装。模型应放在：{path}")
            print("下载：python -m lcode.voice.download")
            return 1
        sv = sensevoice_dir()
        print(f"SenseVoice {'已安装' if sensevoice_installed(sv) else '未安装'}：{sv}")
        return 0

    download(args.url, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
