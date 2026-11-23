# Install (or remove) the "voice assistant starts at logon" shortcut.
#
# Why a Startup-folder shortcut instead of a scheduled task:
#   scripts\voice_task.ps1 needs Register-ScheduledTask, which on this machine
#   returns 0x80070005 (access denied) for a non-elevated shell. A .lnk costs
#   no privileges and does the same job: run a windowless pythonw at logon.
#   The audio-service readiness problem is handled inside open_input()
#   (8 retries x 5s), so no artificial delay is needed here.
#
# The old shortcut pointed at a *copy* of wake.py under ~\.lcode\voice, which
# drifted away from src\lcode\voice. This script deletes any shortcut that
# launches wake.py so only one listener can ever start.
#
# Usage (no admin rights needed):
#   powershell -ExecutionPolicy Bypass -File scripts\voice_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\voice_autostart.ps1 -Remove
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
# with the OEM codepage, so non-ASCII text turns into mojibake and fails to parse.

param(
    [string]$WorkDir = "",
    [string]$Python = "",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

if (-not $WorkDir) {
    # scripts\ lives inside the project root
    $WorkDir = Split-Path -Parent $PSScriptRoot
}
$WorkDir = (Resolve-Path $WorkDir).Path

$startup = [Environment]::GetFolderPath("Startup")
$shell = New-Object -ComObject WScript.Shell
$lnkName = "Lcode-voice.lnk"
$lnkPath = Join-Path $startup $lnkName

# Drop stale shortcuts: anything that runs wake.py or lives in the old copy.
$removed = @()
foreach ($item in (Get-ChildItem $startup -Filter *.lnk -ErrorAction SilentlyContinue)) {
    $sc = $shell.CreateShortcut($item.FullName)
    if ($sc.Arguments -match 'wake\.py' -or $sc.TargetPath -match '\\\.lcode\\voice\\') {
        Remove-Item $item.FullName -Force
        $removed += $item.Name
    }
}
foreach ($name in $removed) { Write-Host "Removed stale shortcut: $name" }

if ($Remove) {
    if (Test-Path $lnkPath) {
        Remove-Item $lnkPath -Force
        Write-Host "Removed: $lnkName"
    } else {
        Write-Host "Not installed: $lnkName"
    }
    exit 0
}

if (-not $Python) {
    $candidate = Join-Path $WorkDir ".venv\Scripts\pythonw.exe"
    if (Test-Path $candidate) {
        $Python = $candidate
    } else {
        $fallback = Join-Path $WorkDir ".venv\Scripts\python.exe"
        if (-not (Test-Path $fallback)) { throw "interpreter not found: pass -Python <path>" }
        $Python = $fallback
    }
}
if (-not (Test-Path $Python)) { throw "interpreter not found: $Python" }

$args = '-m lcode --voice --voice-project "{0}"' -f $WorkDir

$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $Python
$lnk.Arguments = $args
$lnk.WorkingDirectory = $WorkDir
$lnk.Description = "Lcode voice assistant (wake word listening)"
$lnk.WindowStyle = 7
$lnk.Save()

Write-Host "Installed: $lnkPath"
Write-Host "  python : $Python"
Write-Host "  args   : $args"
Write-Host "  workdir: $WorkDir"
Write-Host ""
Write-Host "Start now : Start-Process '$Python' -ArgumentList '$args' -WorkingDirectory '$WorkDir'"
Write-Host "Logs      : $env:USERPROFILE\.lcode\voice\wake.log"
Write-Host "Remove    : powershell -ExecutionPolicy Bypass -File scripts\voice_autostart.ps1 -Remove"
