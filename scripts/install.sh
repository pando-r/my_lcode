#!/bin/sh
# mewcode 一行安装脚本（macOS / Linux）
#
#   curl -LsSf https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.sh | sh
#
# 可选环境变量：
#   MEWCODE_REPO     源码仓库地址（默认 https://github.com/pando-r/my_mecode）
#   MEWCODE_CHANNEL  git（默认，从仓库装）| pypi（发布后从 PyPI 装）
set -eu

REPO="${MEWCODE_REPO:-https://github.com/pando-r/my_mecode}"
CHANNEL="${MEWCODE_CHANNEL:-git}"

say() { printf '%s\n' "$*"; }

# 1) 确保有 uv：它负责拉一个隔离的 Python 环境，不需要用户自己装 Python
if ! command -v uv >/dev/null 2>&1; then
  say "==> 未检测到 uv，先安装 uv"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  else
    wget -qO- https://astral.sh/uv/install.sh | sh
  fi
  for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    [ -d "$d" ] && PATH="$d:$PATH"
  done
  export PATH
fi

if ! command -v uv >/dev/null 2>&1; then
  say "uv 安装失败。请手动安装后重试：https://docs.astral.sh/uv/"
  exit 1
fi

# 2) 安装 mewcode 到隔离环境，并生成可执行入口
if [ "$CHANNEL" = "pypi" ]; then
  SRC="mewcode"
else
  SRC="git+${REPO}.git"
fi
say "==> 安装 mewcode（来源：$SRC）"
uv tool install --force "$SRC"

# 3) 让 mewcode 进入 PATH，新开终端也生效
uv tool update-shell >/dev/null 2>&1 || true
BIN_DIR="$(uv tool dir --bin 2>/dev/null || true)"
if [ -n "$BIN_DIR" ]; then
  PATH="$BIN_DIR:$PATH"
  export PATH
fi

# 4) 首次运行引导：写一份配置模板，用户只需填 api_key
say ""
mewcode --init || true

say ""
say "安装完成："
say "  1) 编辑 ~/.mewcode/config.yaml，把 providers[0].api_key 换成真实密钥"
say "  2) 在任意项目目录运行：mewcode"
say "  3) 查看版本：mewcode --version"
