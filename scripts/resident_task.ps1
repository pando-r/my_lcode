# Register a Windows scheduled task that runs one resident round per day.
#
# Why a scheduled task instead of a long-lived process:
#   A daemon dies when Windows shuts down / sleeps. The queue lives on disk
#   (.lcode/resident/queue.json), so a periodic `--resident-once` picks up
#   every pending task without anyone keeping a process alive.
#
# Usage (no admin rights needed):
#   powershell -ExecutionPolicy Bypass -File scripts\resident_task.ps1 -Time 09:00
#   powershell -ExecutionPolicy Bypass -File scripts\resident_task.ps1 -Remove
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
# with the OEM codepage, so non-ASCII text turns into mojibake and fails to parse.

param(
    [string]$Time = "09:00",
    [string]$Name = "lcode-resident",
    [string]$WorkDir = "",
    [string]$Python = "",
    [string]$Mode = "default",
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

# pythonw.exe avoids a console window flashing every day; fall back if absent.
$windowless = $Python -replace 'python\.exe$', 'pythonw.exe'
if (Test-Path $windowless) { $Python = $windowless }

$taskArgs = "-m lcode --resident-once --resident-mode $Mode"
$action = New-ScheduledTaskAction -Execute $Python -Argument $taskArgs -WorkingDirectory $WorkDir
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# StartWhenAvailable: catch up on a missed run (machine was off at that time).
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $Name `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Force | Out-Null

Write-Host "Registered scheduled task: $Name"
Write-Host "  time      : daily at $Time"
Write-Host "  workdir   : $WorkDir"
Write-Host "  python    : $Python"
Write-Host "  command   : $taskArgs"
Write-Host ""
Write-Host "Inspect : Get-ScheduledTaskInfo -TaskName $Name"
Write-Host "Run now : Start-ScheduledTask -TaskName $Name"
Write-Host "Remove  : powershell -ExecutionPolicy Bypass -File scripts\resident_task.ps1 -Remove"
