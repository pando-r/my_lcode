# mewcode one-line installer (Windows PowerShell)
#
#   irm https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1 | iex
#
# Optional environment variables:
#   MEWCODE_REPO       source repository (default: https://github.com/pando-r/my_mewcode)
#   MEWCODE_CHANNEL    git (default, install from repo) | pypi (install from PyPI)
#   MEWCODE_INSTALLER  auto (default) | uv | pipx | pip
#
# Installer priority: uv > pipx > pip. If uv is absent but pipx or Python 3.12+
# exists, we use those instead of downloading uv.
$ErrorActionPreference = "Stop"

$repo = if ($env:MEWCODE_REPO) { $env:MEWCODE_REPO } else { "https://github.com/pando-r/my_mewcode" }
$channel = if ($env:MEWCODE_CHANNEL) { $env:MEWCODE_CHANNEL } else { "git" }

# Run a native command without letting its stderr progress output abort the script.
# $ErrorActionPreference="Stop" would otherwise turn uv's progress lines into
# terminating NativeCommandError records.
function Invoke-Native {
    param([string[]]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command[0] @($Command[1..($Command.Length - 1)]) 2>&1 | ForEach-Object { Write-Host $_ }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-NativeOutput {
    param([string[]]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $Command[0] @($Command[1..($Command.Length - 1)]) 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($out | Out-String).Trim()
    } finally {
        $ErrorActionPreference = $previous
    }
}

# Find a Python 3.12+ launcher; returns the command array (e.g. @("py","-3.12")) or $null.
function Find-Python {
    foreach ($candidate in @(@("py", "-3.12"), @("py", "-3"), @("python"), @("python3"))) {
        if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
        $check = $candidate + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)")
        if ((Invoke-Native $check) -eq 0) { return , $candidate }
    }
    return $null
}

# 1) Choose an installer: uv > pipx > pip.
$installer = $env:MEWCODE_INSTALLER
if (-not $installer) { $installer = "auto" }
$python = $null
if ($installer -eq "auto") {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $installer = "uv"
    } elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
        $installer = "pipx"
    } else {
        $python = Find-Python
        $installer = if ($python) { "pip" } else { "uv" }
    }
}

# 2) Resolve install source; repo channel needs git, otherwise fall back to the zip archive.
$src = $null
if ($channel -eq "pypi") {
    $src = "mewcode"
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
    $src = "git+$repo.git"
} else {
    $src = "$repo/archive/refs/heads/main.zip"
}

Write-Host "==> Installing mewcode (installer: $installer, source: $src)"

$binDir = $null
switch ($installer) {
    "uv" {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-Host "==> uv / pipx / Python 3.12+ not found, installing uv (it brings its own Python)"
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
            $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
        }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-Host "Failed to install uv. Set MEWCODE_INSTALLER=pip and rerun, or see https://docs.astral.sh/uv/"
            exit 1
        }
        Write-Host "==> Installing mewcode into an isolated environment"
        $code = Invoke-Native @("uv", "tool", "install", "--force", $src)
        if ($code -ne 0) {
            Write-Host "uv tool install failed (exit $code)."
            exit 1
        }
        [void](Invoke-Native @("uv", "tool", "update-shell"))
        $binDir = Get-NativeOutput @("uv", "tool", "dir", "--bin")
    }
    "pipx" {
        Write-Host "==> Using installed pipx (isolated environment, no uv needed)"
        $code = Invoke-Native @("pipx", "install", "--force", $src)
        if ($code -ne 0) {
            Write-Host "pipx install failed (exit $code). Retry with MEWCODE_INSTALLER=pip."
            exit 1
        }
        $binDir = Get-NativeOutput @("pipx", "environment", "--value", "PIPX_BIN_DIR")
        if (-not $binDir) { $binDir = "$env:USERPROFILE\.local\bin" }
    }
    "pip" {
        if (-not $python) { $python = Find-Python }
        if (-not $python) {
            Write-Host "Python 3.12+ not found. Install it from https://www.python.org/downloads/ and rerun."
            exit 1
        }
        Write-Host "==> Using pip from: $($python -join ' ') (no uv needed)"
        $pipArgs = $python + @("-m", "pip", "install", "--user", "--upgrade", $src)
        $code = Invoke-Native $pipArgs
        if ($code -ne 0) {
            Write-Host "pip install failed (exit $code). Try pipx instead: pipx install `"$src`""
            exit 1
        }
        $binDir = Get-NativeOutput ($python + @("-c", "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"))
    }
    default {
        Write-Host "Unknown MEWCODE_INSTALLER: $installer (expected auto | uv | pipx | pip)"
        exit 1
    }
}

# 3) Put the executable on PATH for this session.
if ($binDir) { $env:Path = "$binDir;$env:Path" }

if (-not (Get-Command mewcode -ErrorAction SilentlyContinue)) {
    Write-Host "mewcode installed but not on PATH. Open a new terminal, or add this directory: $binDir"
}

# 4) First-run bootstrap: write a config template, the user only fills in api_key.
Write-Host ""
[void](Invoke-Native @("mewcode", "--init"))

Write-Host ""
Write-Host "Installed. Next steps:"
Write-Host "  1) Edit $env:USERPROFILE\.mewcode\config.yaml and set providers[0].api_key"
Write-Host "  2) Run 'mewcode' in any project directory"
Write-Host "  3) Check version: mewcode --version"
