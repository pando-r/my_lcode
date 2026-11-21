#!/bin/sh
# lcode 一行安装脚本（macOS / Linux）
#
#   curl -LsSf --max-time 30 https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.sh | sh
#
# 国内网络下 raw.githubusercontent.com / github.com 经常不通，可换镜像（任选一条）：
#   curl -LsSf --max-time 30 https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.sh | sh
#   curl -LsSf --max-time 30 https://ghfast.top/https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.sh | sh
#
# 可选环境变量：
#   LCODE_REPO       源码仓库（默认 https://github.com/pando-r/my_mecode）
#   LCODE_CHANNEL    git（默认，从仓库装）| pypi（仅在 PyPI 上有本项目包时可用）
#   LCODE_INSTALLER  auto（默认）| uv | pipx | pip
#   LCODE_PIP_INDEX  自定义 PyPI 镜像，如 https://mirrors.aliyun.com/pypi/simple/
#   LCODE_PYPI_NAME  pypi 渠道使用的发行包名（默认 lcode）
#   LCODE_GH_PROXY   GitHub 代理前缀（默认 ghfast.top / gh-proxy.com / ghproxy.net）；
#                    设为 none 则只用直连
#   LCODE_SOURCE_URL 直接指定安装源，跳过所有探测（排查用）
#   LCODE_DRY_RUN    设为 1 只打印命令不执行
#   LCODE_DOCTOR     设为 1 只做环境体检（git/python/uv 与各站点连通性），不安装
#
# 安装器优先级：uv > pipx > pip。没有 uv 也不会被强制下载 uv——
# 机器上已有 pipx 或 Python 3.12+ 时直接走它们。
set -eu

REPO="${LCODE_REPO:-https://github.com/pando-r/my_mecode}"
CHANNEL="${LCODE_CHANNEL:-git}"
INSTALLER="${LCODE_INSTALLER:-auto}"
PYPI_NAME="${LCODE_PYPI_NAME:-lcode}"
DRY="${LCODE_DRY_RUN:-0}"
DOCTOR="${LCODE_DOCTOR:-0}"

REPO="${REPO%/}"
REPO="${REPO%.git}"

# 探测单个地址的最长等待秒数（域名被黑洞时 TCP 会一直重试，必须自己限时）。
PROBE_TIMEOUT=8

# ── 防挂死：让所有子进程在网络异常时自己失败，而不是无限期等待 ──────────────
# git 遇到私有库或需要凭据时会在终端里等输入，管道执行时表现为「脚本卡死」。
GIT_TERMINAL_PROMPT=0
GIT_HTTP_LOW_SPEED_LIMIT=1000
GIT_HTTP_LOW_SPEED_TIME=30
PIP_NO_INPUT=1
PIP_DISABLE_PIP_VERSION_CHECK=1
UV_HTTP_TIMEOUT=60
export GIT_TERMINAL_PROMPT GIT_HTTP_LOW_SPEED_LIMIT GIT_HTTP_LOW_SPEED_TIME
export PIP_NO_INPUT PIP_DISABLE_PIP_VERSION_CHECK UV_HTTP_TIMEOUT

say() { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

run() {
  if [ "$DRY" = "1" ]; then
    say "    [dry-run] $*"
    return 0
  fi
  "$@"
}

# 自定义镜像同时喂给 pip（PIP_INDEX_URL）和 uv（UV_DEFAULT_INDEX）。
if [ -n "${LCODE_PIP_INDEX:-}" ]; then
  PIP_INDEX_URL="$LCODE_PIP_INDEX"
  UV_DEFAULT_INDEX="$LCODE_PIP_INDEX"
  export PIP_INDEX_URL UV_DEFAULT_INDEX
  say "==> 使用自定义包索引：$LCODE_PIP_INDEX"
fi

find_python() {
  for c in python3.14 python3.13 python3.12 \
    /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 python3 python; do
    if have "$c" &&
      "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      printf '%s' "$c"
      return 0
    fi
  done
  return 1
}

python_version_text() {
  for c in python3 python; do
    if have "$c"; then
      v="$("$c" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || true)"
      if [ -n "$v" ]; then printf '%s = %s' "$c" "$v"; return 0; fi
    fi
  done
  return 1
}

# GitHub 代理前缀：直连不通时用来换路。
gh_proxies() {
  if [ -n "${LCODE_GH_PROXY:-}" ]; then
    case "$LCODE_GH_PROXY" in
      none|off|-) return 0 ;;
      *) printf '%s\n' "${LCODE_GH_PROXY%/}/" ;;
    esac
    return 0
  fi
  printf '%s\n' \
    "https://ghfast.top/" \
    "https://gh-proxy.com/" \
    "https://ghproxy.net/"
}

# 带硬超时的可达性探测。只看「能不能连上」，不校验状态码。
test_url() {
  [ -n "${1:-}" ] || return 1
  if have curl; then
    curl -sS -o /dev/null -I --max-time "$PROBE_TIMEOUT" "$1" >/dev/null 2>&1
  elif have wget; then
    wget -q -S --spider --timeout="$PROBE_TIMEOUT" --tries=1 "$1" >/dev/null 2>&1
  else
    return 1
  fi
}

# 依次探测候选地址，输出第一个可达的；全都不通返回非 0。
pick_url() {
  i=0
  total=$#
  for candidate in "$@"; do
    i=$((i + 1))
    if [ "$DRY" = "1" ]; then
      say "    [dry-run] 跳过连通性探测，直接使用：$candidate" >&2
      printf '%s' "$candidate"
      return 0
    fi
    say "    探测 $i/$total：$candidate" >&2
    if test_url "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
    say "      → 不通（${PROBE_TIMEOUT}s 超时）" >&2
  done
  return 1
}

# 查询 PyPI 上某个名字归谁：own | other | none | unknown
pypi_claim() {
  tmp="$(mktemp 2>/dev/null || printf '%s/lcode-pypi-check.%s' "${TMPDIR:-/tmp}" "$$")"
  # 设了自定义镜像就查镜像，否则国内直连 pypi.org 不通会误判成 unknown。
  base="https://pypi.org"
  if [ -n "${LCODE_PIP_INDEX:-}" ]; then
    base="${LCODE_PIP_INDEX%/}"
    base="${base%/simple}"
  fi
  if have curl; then
    code="$(curl -sSL --max-time 20 -o "$tmp" -w '%{http_code}' "$base/pypi/$1/json" 2>/dev/null || echo 000)"
  else
    code=000
  fi
  body="$(cat "$tmp" 2>/dev/null || true)"
  rm -f "$tmp"
  case "$code" in
    200)
      if printf '%s' "$body" | grep -q 'pando-r/my_mecode'; then printf 'own'; else printf 'other'; fi
      ;;
    404) printf 'none' ;;
    *) printf 'unknown' ;;
  esac
}

# ── 环境体检 ────────────────────────────────────────────────────────────────
if [ "$DOCTOR" = "1" ]; then
  say "==> Lcode 安装环境体检"
  say ""
  say "系统       : $(uname -srm 2>/dev/null || printf 'unknown')"
  for tool in git uv pipx lcode; do
    if have "$tool"; then
      say "$(printf '%-11s' "$tool"): $(command -v "$tool")"
    else
      say "$(printf '%-11s' "$tool"): 未安装"
    fi
  done
  if PY="$(find_python)"; then
    say "$(printf '%-11s' python): $PY （Python $("$PY" -c 'import sys; print(sys.version.split()[0])')）"
  elif found="$(python_version_text)"; then
    say "$(printf '%-11s' python): $found（版本低于 3.12，本项目需要 3.12+）"
  else
    say "$(printf '%-11s' python): 未安装"
  fi
  say ""
  say "网络连通性（每个地址最长 ${PROBE_TIMEOUT} 秒）："
  for t in https://github.com https://raw.githubusercontent.com https://pypi.org \
    https://astral.sh https://cdn.jsdelivr.net https://ghfast.top; do
    if test_url "$t"; then say "  [通]   $t"; else say "  [不通] $t"; fi
  done
  say ""
  say "判断建议："
  say "  · github.com 不通 → 用 LCODE_GH_PROXY 指定代理，或走 PyPI 镜像"
  say "  · 只有 pypi.org 不通 → 设 LCODE_PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/"
  exit 0
fi

# 1) 选安装器：uv > pipx > pip（都没有 Python 才退回装 uv，因为 uv 会自带 Python）
PY=""
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

# 2) 解析安装源（带镜像回退）
USE_PROXY=0
if [ -n "${LCODE_SOURCE_URL:-}" ]; then
  SRC="$LCODE_SOURCE_URL"
  say "==> 使用手工指定的安装源：$SRC"
  say ""
elif [ "$CHANNEL" = "pypi" ]; then
  claim="$(pypi_claim "$PYPI_NAME")"
  case "$claim" in
    other)
      say "错误：PyPI 上的「$PYPI_NAME」不是本项目。"
      say "  该名字已被其他人占用（例如 lcodePy-team 的等离子体仿真包），"
      say "  pip install lcode 会静默装成一个完全无关的程序。"
      say "  请改用源码渠道（去掉 LCODE_CHANNEL=pypi），或用 LCODE_PYPI_NAME 指定本项目的发布名。"
      exit 1
      ;;
    none)
      say "错误：PyPI 上还没有发布「$PYPI_NAME」。"
      say "  请改用源码渠道（去掉 LCODE_CHANNEL=pypi 即可从 GitHub 安装）。"
      exit 1
      ;;
    unknown)
      say "警告：无法访问 pypi.org 确认「$PYPI_NAME」归属，安装后会自动校验版本。"
      ;;
  esac
  SRC="$PYPI_NAME"
else
  # 直连优先，不通再依次换 GitHub 代理；只测仓库主页，避免逐个源包试错。
  set -- "$REPO"
  for proxy in $(gh_proxies); do
    set -- "$@" "$proxy$REPO"
  done
  say "==> 探测源码仓库可达性"
  if BASE="$(pick_url "$@")"; then
    if [ "$BASE" != "$REPO" ]; then
      USE_PROXY=1
      say "==> 直连不通，改用代理：$BASE"
    fi
  else
    say "警告：所有候选地址都不通，仍按直连尝试一次（可能是 HEAD 被拦而 GET 可用）。"
    BASE="$REPO"
  fi
  say ""

  if have git; then
    SRC="git+${BASE}.git"
  else
    SRC="${BASE}/archive/refs/heads/main.zip"
  fi
fi

say "==> 安装 Lcode（方式：$INSTALLER，来源：$SRC）"

BIN_DIR=""
case "$INSTALLER" in
  uv)
    if ! have uv; then
      # 优先 pip 装 uv：走 PyPI 镜像，国内基本可达；astral.sh 则经常被墙。
      if [ -z "${PY:-}" ]; then PY="$(find_python || true)"; fi
      if [ -n "${PY:-}" ]; then
        say "==> 用 pip 安装 uv（走包索引，不依赖 astral.sh）"
        if [ -n "${LCODE_PIP_INDEX:-}" ]; then
          run "$PY" -m pip install --user --upgrade uv \
            --index-url "$LCODE_PIP_INDEX" --timeout 30 --retries 2 || true
        else
          run "$PY" -m pip install --user --upgrade uv --timeout 30 --retries 2 || true
        fi
        for d in "$("$PY" -c 'import sysconfig; print(sysconfig.get_path("scripts", "posix_user"))' 2>/dev/null || true)" \
          "$HOME/.local/bin"; do
          if [ -n "$d" ] && [ -d "$d" ]; then PATH="$d:$PATH"; fi
        done
        export PATH
      fi
    fi
    if ! have uv; then
      say "==> 从 astral.sh 下载 uv（60 秒超时）"
      if [ "$DRY" = "1" ]; then
        say "    [dry-run] curl -LsSf --max-time 60 https://astral.sh/uv/install.sh | sh"
      elif have curl; then
        curl -LsSf --max-time 60 https://astral.sh/uv/install.sh | sh || {
          say "uv 下载失败（astral.sh 不通，常见于公司/校园网或国内网络）。"
          say "  改用 pip：LCODE_INSTALLER=pip 重跑安装命令"
          say "  排查网络：LCODE_DOCTOR=1 重跑安装命令"
          exit 1
        }
      else
        wget -qO- --timeout=60 https://astral.sh/uv/install.sh | sh || {
          say "uv 下载失败。可改用 pip：设置 LCODE_INSTALLER=pip 后重试"
          exit 1
        }
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
    # uv 若挑到过老的 Python 会直接 no solution，这里显式要求 3.12
    if ! find_python >/dev/null 2>&1; then
      say "==> 系统里没有 Python 3.12+，让 uv 装一个自带解释器"
      if [ "$USE_PROXY" = "1" ] && [ -z "${UV_PYTHON_INSTALL_MIRROR:-}" ]; then
        # uv 的 Python 构建产物托管在 GitHub Releases，直连不通时同样走代理。
        UV_PYTHON_INSTALL_MIRROR="https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download"
        export UV_PYTHON_INSTALL_MIRROR
        say "    下载源：$UV_PYTHON_INSTALL_MIRROR"
      fi
      run uv python install 3.12 || true
      run uv tool install --force --python 3.12 "$SRC" || {
        say "uv tool install 失败。国内网络可加镜像重试："
        say "  LCODE_PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/ LCODE_INSTALLER=uv ..."
        exit 1
      }
    else
      run uv tool install --force "$SRC" || {
        say "uv tool install 失败。国内网络可加镜像重试："
        say "  LCODE_PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/ LCODE_INSTALLER=uv ..."
        exit 1
      }
    fi
    # 让 lcode 进入 PATH，新开终端也生效
    run uv tool update-shell >/dev/null 2>&1 || true
    BIN_DIR="$(uv tool dir --bin 2>/dev/null || true)"
    ;;
  pipx)
    say "==> 使用已安装的 pipx（隔离环境，无需 uv）"
    if ! run pipx install --force --pip-args="--timeout 30 --retries 2" "$SRC"; then
      say "pipx 安装失败。可改用 pip：LCODE_INSTALLER=pip 后重试。"
      if [ -z "${LCODE_PIP_INDEX:-}" ]; then
        say "  国内网络可先设镜像：LCODE_PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/"
      fi
      exit 1
    fi
    # ensurepath 幂等；不执行它 pipx 装完往往不在 PATH 里。
    run pipx ensurepath >/dev/null 2>&1 || true
    BIN_DIR="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
    if [ -z "$BIN_DIR" ]; then BIN_DIR="$HOME/.local/bin"; fi
    ;;
  pip)
    if [ -z "${PY:-}" ]; then
      if ! PY="$(find_python)"; then
        say "未找到可用的 Python 3.12+。"
        if found="$(python_version_text)"; then say "  当前检测到：$found（版本过低）"; fi
        say "  方案一：装 Python 3.12+ https://www.python.org/downloads/"
        say "  方案二：装 uv，由它自带解释器 https://docs.astral.sh/uv/"
        exit 1
      fi
    fi
    say "==> 使用 $PY 的 pip（免 uv）"
    # --timeout/--retries 让 pip 自己给网络操作限时，避免无限等待。
    if [ -n "${LCODE_PIP_INDEX:-}" ]; then
      if ! run "$PY" -m pip install --index-url "$LCODE_PIP_INDEX" \
        --user --upgrade --timeout 30 --retries 2 "$SRC"; then
        PIP_FAILED=1
      else
        PIP_FAILED=0
      fi
    else
      if ! run "$PY" -m pip install --user --upgrade --timeout 30 --retries 2 "$SRC"; then
        PIP_FAILED=1
      else
        PIP_FAILED=0
      fi
    fi
    if [ "$PIP_FAILED" = "1" ]; then
      say ""
      say "pip 安装失败。常见原因："
      say "  · 网络超时 / SSL 错误：换国内镜像重试"
      say "      LCODE_PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/ sh install.sh"
      say "  · 需要代理：export HTTPS_PROXY=http://host:port 后重试"
      say "  · externally-managed-environment：系统 Python 受保护，请改用 pipx："
      say "      pipx install \"$SRC\""
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

# 3) 装完自检：确认装的是 Lcode 本体，而不是同名无关包
if have lcode; then
  if [ "$DRY" = "1" ]; then
    say "==> [dry-run] 跳过自检"
  else
    ver="$(lcode --version 2>&1 | head -n 1 || true)"
    case "$ver" in
      *Lcode*) say "==> 自检通过：$ver" ;;
      *)
        say "错误：命令行里的 lcode 不是 Lcode 本体（--version 输出：$ver）。"
        say "  很可能机器上已有另一个同名的 lcode（例如 PyPI 上的 lcodePy 包）。"
        say "  先卸载同名包（pip uninstall lcode），或改用隔离安装："
        say "      LCODE_INSTALLER=pipx 重跑安装命令"
        exit 1
        ;;
    esac
  fi
else
  say "lcode 已安装，但当前 PATH 里找不到它。请新开终端，或把这个目录加入 PATH：$BIN_DIR"
fi

# 4) 首次运行引导：写一份配置模板，用户只需填 api_key
say ""
if have lcode; then
  run lcode --init || true
fi

say ""
say "安装完成："
say "  1) 编辑 ~/.lcode/config.yaml，把 providers[0].api_key 换成真实密钥"
say "  2) 在任意项目目录运行：lcode"
say "  3) 查看版本：lcode --version"
