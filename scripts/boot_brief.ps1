# Register a Windows scheduled task that greets you on logon with a brief.
#
# What it does on EVERY logon (no daily / per-boot dedupe):
#   1. waits for the network (a fresh boot often has no DNS yet)
#   2. queues .lcode/brief.md as a resident task
#   3. runs one resident round (--resident-once); the result pops a desktop toast
#
# Why no dedupe: the requirement is "one greeting each time the machine starts".
# Every de-dupe scheme tried here failed on this machine:
#   - a calendar date made a 00:21 reboot consume the whole day, so the real
#     11:20 logon got nothing;
#   - LastBootUpTime looks like the right key, but Windows Fast Startup
#     (HiberbootEnabled is on here) restores the kernel session on
#     "shut down -> power on", so the boot time does not change and the brief
#     would silently never arrive.
# Sending on every logon cannot fail that way. The only cost is a second brief
# if you sign out and back in on the same boot.
#
# Why a scheduled task + disk queue instead of a daemon:
#   a long-lived process dies with the machine. The queue lives on disk
#   (.lcode/resident/queue.json), so one --resident-once per logon is enough.
#
# Usage (no admin rights needed):
#   powershell -ExecutionPolicy Bypass -File scripts\boot_brief.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\boot_brief.ps1 -PromptFile .lcode\brief.md
#   powershell -ExecutionPolicy Bypass -File scripts\boot_brief.ps1 -Remove
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
# with the OEM codepage, so non-ASCII text turns into mojibake and fails to parse.
# That is why the prompt itself lives in a separate UTF-8 file (--enqueue-file),
# never in the command line.

param(
    [string]$Name = "lcode-boot-brief",
    [string]$WorkDir = "",
    [string]$Python = "",
    [string]$PromptFile = ".lcode\brief.md",
    [int]$DelaySeconds = 60,
    [int]$NetworkWaitSeconds = 180,
    [string]$Mode = "default",
    [switch]$DryRun,
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

$promptPath = Join-Path $WorkDir $PromptFile
$runner = Join-Path $WorkDir "scripts\boot_brief_run.ps1"
if (-not (Test-Path $runner)) { throw "helper script not found: $runner" }

# -File (not -Command): the helper owns the multi-step flow, and an external
# script file keeps quoting sane for paths that contain spaces.
$taskArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`"" +
    " -WorkDir `"$WorkDir`"" +
    " -Python `"$Python`"" +
    " -PromptFile `"$promptPath`"" +
    " -DelaySeconds $DelaySeconds" +
    " -NetworkWaitSeconds $NetworkWaitSeconds" +
    " -Mode $Mode"

if ($DryRun) {
    Write-Host "[dry-run] would register: $Name"
    Write-Host "  command : powershell.exe $taskArgs"
    Write-Host "  trigger : at logon"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $taskArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

# A fresh boot has no network and a slow disk. StartWhenAvailable also covers
# "machine was off when the trigger fired".
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $Name `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Force | Out-Null

Write-Host "Registered scheduled task: $Name"
Write-Host "  trigger   : at logon of $env:USERDOMAIN\$env:USERNAME"
Write-Host "  prompt    : $promptPath"
Write-Host "  workdir   : $WorkDir"
Write-Host "  python    : $Python"
Write-Host "  dedupe    : none (one brief per logon)"
Write-Host "  delay     : ${DelaySeconds}s, then wait up to ${NetworkWaitSeconds}s for network"
Write-Host ""
Write-Host "Inspect : Get-ScheduledTaskInfo -TaskName $Name"
Write-Host "Run now : Start-ScheduledTask -TaskName $Name"
Write-Host "Logs    : $WorkDir\.lcode\resident\daemon.log"
Write-Host "Remove  : powershell -ExecutionPolicy Bypass -File scripts\boot_brief.ps1 -Remove"
