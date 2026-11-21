# Register a Windows scheduled task that polls the mailbox and answers by email.
#
# Why a scheduled task instead of a long-lived process:
#   A daemon dies when Windows shuts down / sleeps. The queue lives on disk,
#   so a periodic `--resident-once --mail-watch` picks up every pending mail
#   without anyone keeping a process alive.
#
# Usage (no admin rights needed):
#   powershell -ExecutionPolicy Bypass -File scripts\mail_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\mail_task.ps1 -IntervalMinutes 1
#   powershell -ExecutionPolicy Bypass -File scripts\mail_task.ps1 -Remove
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
# with the OEM codepage, so non-ASCII text turns into mojibake and fails to parse.

param(
    [int]$IntervalMinutes = 3,
    [string]$Name = "lcode-mail-watch",
    [string]$WorkDir = "",
    [string]$Python = "",
    [string]$Mode = "default",
    [switch]$NoNotify,
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
    exit 0
}

if (-not (Test-Path $Python)) { throw "interpreter not found: $Python" }

if ($IntervalMinutes -lt 1) { throw "IntervalMinutes must be >= 1" }

# pythonw.exe avoids a console window flashing on every poll.
$windowless = $Python -replace 'python\.exe$', 'pythonw.exe'
if (Test-Path $windowless) { $Python = $windowless }

$taskArgs = "-m lcode --resident-once --mail-watch --resident-mode $Mode"
if ($NoNotify) { $taskArgs += " --no-notify" }

$action = New-ScheduledTaskAction -Execute $Python -Argument $taskArgs -WorkingDirectory $WorkDir

# -Once + -RepetitionInterval: repeat every N minutes, for as long as possible.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $Name `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Force | Out-Null

Write-Host "Registered scheduled task: $Name"
Write-Host "  every     : $IntervalMinutes minute(s)"
Write-Host "  workdir   : $WorkDir"
Write-Host "  python    : $Python"
Write-Host "  command   : $taskArgs"
Write-Host ""
Write-Host "Inspect : Get-ScheduledTaskInfo -TaskName $Name"
Write-Host "Run now : Start-ScheduledTask -TaskName $Name"
Write-Host "Remove  : powershell -ExecutionPolicy Bypass -File scripts\mail_task.ps1 -Remove"
Write-Host ""
Write-Host "Logs    : $WorkDir\.lcode\resident\daemon.log"
