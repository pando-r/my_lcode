# mewcode one-line installer (Windows PowerShell)
#
#   irm https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1 | iex
#
# Optional environment variables:
#   MEWCODE_REPO     source repository (default: https://github.com/pando-r/my_mecode)
#   MEWCODE_CHANNEL  git (default, install from repo) | pypi (install from PyPI)
$ErrorActionPreference = "Stop"

$repo = if ($env:MEWCODE_REPO) { $env:MEWCODE_REPO } else { "https://github.com/pando-r/my_mecode" }
$channel = if ($env:MEWCODE_CHANNEL) { $env:MEWCODE_CHANNEL } else { "git" }

# 1) Make sure uv exists: it provisions an isolated Python, so users need no Python install.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "==> uv not found, installing uv"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Failed to install uv. Install it manually, then rerun: https://docs.astral.sh/uv/"
    exit 1
}

# 2) Install mewcode into an isolated environment.
$src = if ($channel -eq "pypi") { "mewcode" } else { "git+$repo.git" }
Write-Host "==> Installing mewcode (source: $src)"
uv tool install --force $src

# 3) Put the executable on PATH for this session.
uv tool update-shell 2>$null | Out-Null
$binDir = (uv tool dir --bin)
if ($binDir) { $env:Path = "$binDir;$env:Path" }

# 4) First-run bootstrap: write a config template, the user only fills in api_key.
Write-Host ""
mewcode --init

Write-Host ""
Write-Host "Installed. Next steps:"
Write-Host "  1) Edit $env:USERPROFILE\.mewcode\config.yaml and set providers[0].api_key"
Write-Host "  2) Run 'mewcode' in any project directory"
Write-Host "  3) Check version: mewcode --version"
