# Register a Windows scheduled task that starts voice wake-word listening at logon.
#
# Why a scheduled task instead of the Startup folder:
#   - a .lnk in Startup dies with the shell and gives no restart policy;
#   - Task Scheduler can delay the start (the audio service needs a few seconds
#     after logon; starting too early means sd.RawInputStream fails, and the
#     built-in retry then eats 8 x 5s before it can listen);
#   - RestartCount keeps it alive if the process is killed.
#
# Usage (no admin rights needed):
#   powershell -ExecutionPolicy Bypass -File scripts\voice_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\voice_task.ps1 -Remove
#
# Prerequisites (run once, by hand):
#   pip install lcode[voice]
#   python -m lcode.voice.download
#   lcode --voice-selftest          # prove the microphone works first
#
# NOTE: on machines where Register-ScheduledTask is denied (0x80070005),
# use scripts\voice_autostart.ps1 instead -- it puts a shortcut in the
# Startup folder, which needs no privileges. Always prefer that one when
# both work: the .lnk and the task would otherwise fight over the mic.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
# with the OEM codepage, so non-ASCII text turns into mojibake and fails to parse.

param(
    [string]$Name = "lcode-voice",
    [string]$WorkDir = "",
    [string]$Python = "",
    [int]$DelaySeconds = 30,
    [string]$Wake = "",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

if (-not $WorkDir) { $WorkDir = (Get-Location).Path }
if (-not $Python) {
    $candidate = Join-Path $WorkDir ".venv\Scripts\python.exe"
    if (Test-Path $candidate) {
        $Python = $candidate
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) { throw "python not found: pass -Python <path>" }
        $Python = $cmd.Source
    }
}

if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
        Write-Host "Removed scheduled task: $Name"
    } else {
        Write-Host "Scheduled task not found: $Name"
    }
    # Stop a copy that is still listening, otherwise it keeps holding the port.
    Get-Process pythonw, python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $Python -replace 'python\.exe$', 'pythonw.exe' } |
        ForEach-Object { Write-Host "  still running: pid=$($_.Id) (kill it manually if needed)" }
    exit 0
}

if (-not (Test-Path $Python)) { throw "interpreter not found: $Python" }

# pythonw.exe avoids a console window at logon; fall back if absent.
$windowless = $Python -replace 'python\.exe$', 'pythonw.exe'
if (Test-Path $windowless) { $Python = $windowless }

# Always bind to an explicit project root. Voice has no concept of "current
# directory" -- inheriting whatever the shell happened to be in would make the
# same question behave differently depending on where Lcode was launched.
$taskArgs = "-m lcode --voice --voice-project `"$WorkDir`""
if ($Wake) { $taskArgs += " --voice-wake `"$Wake`"" }

# A plain delay is enough here: the interpreter starts once and stays resident,
# so there is no per-run cost to amortize.
$action = New-ScheduledTaskAction -Execute $Python -Argument $taskArgs -WorkingDirectory $WorkDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT${DelaySeconds}S"

# ExecutionTimeLimit must be zero (unlimited): this process is meant to live
# for the whole session. MultipleInstances IgnoreNew + the in-process port lock
# (127.0.0.1:47311) both guard against a second listener.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $Name `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Force | Out-Null

Write-Host "Registered scheduled task: $Name"
Write-Host "  trigger   : at logon, delayed ${DelaySeconds}s"
Write-Host "  workdir   : $WorkDir"
Write-Host "  python    : $Python"
Write-Host "  command   : $taskArgs"
Write-Host ""
Write-Host "Inspect : Get-ScheduledTaskInfo -TaskName $Name"
Write-Host "Run now : Start-ScheduledTask -TaskName $Name"
Write-Host "Logs    : $env:USERPROFILE\.lcode\voice\wake.log"
Write-Host "Remove  : powershell -ExecutionPolicy Bypass -File scripts\voice_task.ps1 -Remove"
