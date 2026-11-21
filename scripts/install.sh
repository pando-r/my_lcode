#!/bin/sh
# lcode 一行安装脚本（macOS / Linux）
#
#   curl -LsSf https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.sh | sh
#
# 可选环境变量：
#   LCODE_REPO       源码仓库地址（默认 https://github.com/pando-r/my_mecode）
#   LCODE_CHANNEL    git（默认，从仓库装）| pypi（发布后从 PyPI 装）
#   LCODE_INSTALLER  auto（默认，自动挑）| uv | pipx | pip
#
# 安装器优先级：uv > pipx > pip。没有 uv 也不会被强制下载 uv——
# 机器上已有 pipx 或 Python 3.12+ 时直接走它们。
set -eu

REPO="${LCODE_REPO:-https://github.com/pando-r/my_lcode}"
CHANNEL="${LCODE_CHANNEL:-git}"
INSTALLER="${LCODE_INSTALLER:-auto}"

say() { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

find_python() {
  for c in python3.13 python3.12 python3 python; do
    if have "$c" &&
      "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      printf '%s' "$c"
      return 0
    fi
  done
  return 1
}

# 1) 选安装器：uv > pipx > pip（都没 Python 才退回装 uv，因为 uv 会自带 Python）
if [ "$INSTALLER" = "auto" ]; then
  if have uv; then
    INSTALLER=uv
  elif have pipx; then
    INSTALLER=pipx
  elif PY="$(find_python)"; then
    INSTALLER=pip
  else
    INSTALLER=uv
  fi
fi

# 2) 解析安装源：仓库渠道优先用 git；没装 git 就退回 zip 源码包
if [ "$CHANNEL" = "pypi" ]; then
  SRC="lcode"
elif have git; then
  SRC="git+${REPO}.git"
else
  SRC="${REPO}/archive/refs/heads/main.zip"
fi

say "==> 安装 lcode（方式：$INSTALLER，来源：$SRC）"

BIN_DIR=""
case "$INSTALLER" in
  uv)
    if ! have uv; then
      say "==> 未检测到 uv / pipx / Python 3.12+，先安装 uv（它会自带 Python）"
      if have curl; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
      else
        wget -qO- https://astral.sh/uv/install.sh | sh
      fi
      for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        if [ -d "$d" ]; then PATH="$d:$PATH"; fi
      done
      export PATH
    fi
    if ! have uv; then
      say "uv 安装失败。可改用 pip：设置 LCODE_INSTALLER=pip 后重试，或参考 https://docs.astral.sh/uv/"
      exit 1
    fi
    uv tool install --force "$SRC"
    # 让 lcode 进入 PATH，新开终端也生效
    uv tool update-shell >/dev/null 2>&1 || true
    BIN_DIR="$(uv tool dir --bin 2>/dev/null || true)"
    ;;
  pipx)
    say "==> 使用已安装的 pipx（隔离环境，无需 uv）"
    if ! pipx install --force "$SRC"; then
      say "pipx 安装失败。可改用 pip install：LCODE_INSTALLER=pip 后重试。"
      exit 1
    fi
    BIN_DIR="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
    if [ -z "$BIN_DIR" ]; then BIN_DIR="$HOME/.local/bin"; fi
    ;;
  pip)
    if [ -z "${PY:-}" ]; then
      if ! PY="$(find_python)"; then
        say "未找到 Python 3.12+。请先安装 Python（https://www.python.org/downloads/），"
        say "或改用一行脚本的默认方式（自动装 uv）。"
        exit 1
      fi
    fi
    say "==> 使用 $PY 的 pip（免 uv）"
    if ! "$PY" -m pip install --user --upgrade "$SRC"; then
      say ""
      say "pip 安装失败。常见原因："
      say "  · externally-managed-environment：系统 Python 受保护，请改用 pipx："
      say "      pipx install \"$SRC\""
      say "  · 没有 git：pip 会自动改走 zip 源码包，也可手动指定 zip 地址"
      exit 1
    fi
    BIN_DIR="$("$PY" -c 'import sysconfig; print(sysconfig.get_path("scripts", "posix_user"))' 2>/dev/null || true)"
    ;;
  *)
    say "未知 LCODE_INSTALLER: $INSTALLER（可选 auto | uv | pipx | pip）"
    exit 1
    ;;
esac

if [ -n "$BIN_DIR" ]; then
  PATH="$BIN_DIR:$PATH"
  export PATH
fi

if ! command -v lcode >/dev/null 2>&1; then
  say "lcode 已安装，但当前 PATH 里找不到它。请新开终端，或把这个目录加入 PATH：$BIN_DIR"
fi

# 首次运行引导：写一份配置模板，用户只需填 api_key
say ""
lcode --init || true

say ""
say "安装完成："
say "  1) 编辑 ~/.lcode/config.yaml，把 providers[0].api_key 换成真实密钥"
say "  2) 在任意项目目录运行：lcode"
say "  3) 查看版本：lcode --version"
