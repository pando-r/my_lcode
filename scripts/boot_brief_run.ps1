# Helper invoked by the lcode-boot-brief scheduled task (see boot_brief.ps1).
#
# Kept separate from boot_brief.ps1 so the one-line Task Scheduler action stays
# short and the multi-step logic remains readable. Runs in a hidden window:
#
#   1. sleep DelaySeconds          (let the desktop settle after logon)
#   2. wait for DNS/TCP           (a fresh boot has no network yet)
#   3. enqueue the prompt file    (UTF-8 file, not argv -> no mojibake)
#   4. one resident round         (result lands as a desktop toast)
#
# There is deliberately NO dedupe here: the requirement is "greet me each time
# the machine starts", so every logon gets a brief. Two earlier attempts at
# de-duping both went wrong on this machine:
#   - a date stamp meant a 00:21 reboot consumed the whole day, so the real
#     11:20 logon got nothing;
#   - the boot time (LastBootUpTime) looks right but Windows Fast Startup
#     (HiberbootEnabled=1 here) restores the kernel session on "shut down ->
#     power on", leaving LastBootUpTime unchanged -- so that brief would
#     silently never arrive.
# The only guard left is "this exact prompt is already pending", which stops a
# failed run from piling up duplicate queue entries. It says nothing about time.
#
# NOTE: keep this file ASCII-only (see boot_brief.ps1 for the reason).

param(
    [Parameter(Mandatory = $true)][string]$WorkDir,
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$PromptFile,
    [int]$DelaySeconds = 60,
    [int]$NetworkWaitSeconds = 180,
    [string]$Mode = "default"
)

$ErrorActionPreference = "Stop"
$stampDir = Join-Path $WorkDir ".lcode\resident"
$logFile = Join-Path $stampDir "boot-brief.log"
$queueFile = Join-Path $stampDir "queue.json"

New-Item -ItemType Directory -Force -Path $stampDir | Out-Null

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Write-Log "boot brief: start (delay ${DelaySeconds}s, network wait ${NetworkWaitSeconds}s)"

# 1. Let the logon storm settle: shell, antivirus, cloud sync all compete early.
if ($DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }

# 2. Wait for the network. Without this the resident run fails fast on DNS and
#    the day simply has no brief -- the failure is silent, which is worse than
#    a slow start.
$deadline = (Get-Date).AddSeconds($NetworkWaitSeconds)
$started = Get-Date
$online = $false
while ((Get-Date) -lt $deadline) {
    try {
        if (Test-NetConnection -ComputerName "www.baidu.com" -Port 443 `
                -InformationLevel Quiet -WarningAction SilentlyContinue) {
            $online = $true
            break
        }
    } catch { }
    Start-Sleep -Seconds 5
}
if ($online) {
    $waited = [int]((Get-Date) - $started).TotalSeconds
    Write-Log "network ready after ${waited}s"
} else {
    Write-Log "network NOT ready after ${NetworkWaitSeconds}s -- trying anyway"
}

if (-not (Test-Path $PromptFile)) {
    Write-Log "prompt file missing: $PromptFile"
    exit 1
}

# 3a. If a previous logon enqueued the brief but the run never finished, the
#     task is still pending on disk. Enqueueing again would produce two briefs.
#
#     Compare after dropping CR: Python's read_text() translates CRLF to LF
#     before enqueueing, while Get-Content -Raw keeps whatever the file uses.
#     Comparing the raw strings therefore fails on a CRLF checkout (measured:
#     same content, 624 vs 597 chars).
function Normalize-Text([string]$Text) {
    if ($null -eq $Text) { return "" }
    return ($Text -replace "`r", "").Trim()
}

$prompt = Normalize-Text (Get-Content $PromptFile -Raw -Encoding UTF8)
$alreadyPending = $false
if (Test-Path $queueFile) {
    try {
        $queue = Get-Content $queueFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($task in $queue) {
            if ($task.status -eq "pending" -and (Normalize-Text $task.prompt) -eq $prompt) {
                Write-Log "brief already pending as $($task.id) -- skip enqueue"
                $alreadyPending = $true
                break
            }
        }
    } catch {
        Write-Log "queue read failed (continue): $($_.Exception.Message)"
    }
}

# 3b. Enqueue through a UTF-8 file so the Chinese prompt never passes through
#     the command line. Output goes to a file: PowerShell 5.1 turns a native
#     command's stderr into a terminating error under $ErrorActionPreference=Stop.
$outFile = Join-Path $stampDir "boot-brief-run.out"
Push-Location $WorkDir
try {
    if ($alreadyPending) {
        Write-Log "using the already-pending brief task"
    } else {
        $enq = "`"$Python`" -m lcode --enqueue-file `"$PromptFile`""
        cmd /c "$enq > `"$outFile`" 2>&1"
        Write-Log ("enqueue exit={0}: {1}" -f $LASTEXITCODE, ((Get-Content $outFile -Encoding UTF8 -ErrorAction SilentlyContinue) -join " "))
    }

    # 4. One resident round. Default mode is read-only, which is all the brief
    #    needs (search_web is read-only).
    #
    #    Retry on lock contention: --resident-once refuses to start while another
    #    daemon holds .lcode/resident/daemon.lock. That is the right default
    #    (two daemons on one queue would double-run tasks), but at logon it can
    #    collide with the mail-watch task firing at the same moment. Without the
    #    retry the brief just silently never arrives, which is the worst outcome.
    $attempts = 2
    $code = 1
    for ($i = 1; $i -le $attempts; $i++) {
        $run = "`"$Python`" -m lcode --resident-once --resident-mode $Mode"
        cmd /c "$run > `"$outFile`" 2>&1"
        $code = $LASTEXITCODE
        $output = (Get-Content $outFile -Encoding UTF8 -ErrorAction SilentlyContinue) -join " "
        Write-Log ("resident attempt {0}/{1} exit={2}: {3}" -f $i, $attempts, $code, $output)
        if ($code -eq 0) { break }
        if ($output -notmatch "pid=") { break }   # not a lock problem, do not retry
        if ($i -lt $attempts) {
            Write-Log "another daemon holds the lock; retrying in 90s"
            Start-Sleep -Seconds 90
        }
    }
} finally {
    Pop-Location
}

if ($code -eq 0) {
    Write-Log "boot brief: done"
} else {
    # The pending-task check above keeps the next logon's retry from piling up
    # duplicate queue entries.
    Write-Log "boot brief: FAILED (exit $code), will retry on next logon"
    exit $code
}
