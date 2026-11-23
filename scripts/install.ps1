# lcode one-line installer (Windows)
#
# ⚠ 两条硬性约束，改这个文件前先读：
#
#   1) 必须保存为**带 BOM 的 UTF-8**。
#      没有 BOM 时，PowerShell 5.1 的 `-File` 会按系统代码页（中文 Windows = GBK）
#      读文件，中文字符串被拆坏、把引号吃掉，直接报几十条
#      `Missing closing '}'` ——脚本根本跑不起来。
#      另存为时请确认文件头三字节是 EF BB BF。
#
#   2) 所以**不要**用 `irm ... | iex` 分发本脚本。
#      带 BOM 的文本传给 iex 时，首字符 U+FEFF 会被当成标识符的一部分，
#      首行 `#` 注释失效，报「无法将 Windows 项识别为 cmdlet」。
#      正确用法是下载成文件再执行（见 README）：
#         irm <url> -TimeoutSec 30 -OutFile "$env:TEMP\lcode-install.ps1"
#         powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\lcode-install.ps1"
#
# 推荐用法（带超时：网不通时会报错退出，不会一直卡着）：
#   $ProgressPreference='SilentlyContinue'
#   irm https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1 -TimeoutSec 30 -OutFile "$env:TEMP\lcode-install.ps1"
#   powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\lcode-install.ps1"
#
# 国内网络下 raw.githubusercontent.com 经常直接不通，换镜像（只改 URL）：
#   irm 'https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.ps1' -TimeoutSec 30 -OutFile "$env:TEMP\lcode-install.ps1"
#   irm 'https://ghfast.top/https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1' -TimeoutSec 30 -OutFile "$env:TEMP\lcode-install.ps1"
#
# 一条都不通时，用这段自带回退的引导（每步都有超时）：
#   $ProgressPreference='SilentlyContinue'
#   $f="$env:TEMP\lcode-install.ps1"
#   $u=@('https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.ps1',
#        'https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1',
#        'https://ghfast.top/https://raw.githubusercontent.com/pando-r/my_mecode/main/scripts/install.ps1')
#   $ok=$false
#   foreach($x in $u){ try{
#       irm $x -UseBasicParsing -TimeoutSec 25 -OutFile $f
#       if((Get-Item $f).Length -gt 5000 -and (Select-String -Path $f -Pattern 'LCODE_' -Quiet -Encoding UTF8)){ $ok=$true; break }
#     }catch{ Write-Host "失败: $x" } }
#   if(-not $ok){ throw '三个地址都下载失败' }
#   powershell -NoProfile -ExecutionPolicy Bypass -File $f
#
# 必须在 PowerShell 里运行（Windows PowerShell 5.1 或 PowerShell 7+）。
# 在 cmd.exe 里请改用：
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm 'https://cdn.jsdelivr.net/gh/pando-r/my_mecode@main/scripts/install.ps1' -TimeoutSec 30 -OutFile \"$env:TEMP\lcode-install.ps1\"; powershell -NoProfile -ExecutionPolicy Bypass -File \"$env:TEMP\lcode-install.ps1\""
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
#   LCODE_NO_PATH    设为 1 则不修改用户 PATH
#   LCODE_DRY_RUN    设为 1 只打印命令不执行
#   LCODE_DOCTOR     设为 1 只做环境体检（git/python/uv 与各站点连通性），不安装
#
# 安装器优先级：uv > pipx > pip。没有 uv 也不会被强制下载 uv——
# 机器上已有 pipx 或 Python 3.12+ 时直接走它们。
$ErrorActionPreference = "Stop"

# PowerShell 5.1 的进度条渲染极慢（每个字节一次重绘），大包下载时看起来完全像卡死。
$ProgressPreference = "SilentlyContinue"

# Windows PowerShell 5.1 默认只启用 SSL3/TLS1.0，访问 pypi.org / github.com 会直接失败。
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch { }
try { [Net.ServicePointManager]::Expect100Continue = $false } catch { }

# ── 防挂死：让所有子进程在网络异常时自己失败，而不是无限期等待 ──────────────
# git 遇到私有库或需要凭据时会在终端里等输入，表现为「脚本卡死」。
$env:GIT_TERMINAL_PROMPT = "0"
$env:GIT_HTTP_LOW_SPEED_LIMIT = "1000"
$env:GIT_HTTP_LOW_SPEED_TIME = "30"
$env:PIP_NO_INPUT = "1"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:UV_HTTP_TIMEOUT = "60"
# 探测单个地址的最长等待秒数（DNS 被污染时 TCP 会一直重试，必须自己限时）。
$script:ProbeTimeout = 8

$repo = if ($env:LCODE_REPO) { $env:LCODE_REPO } else { "https://github.com/pando-r/my_mecode" }
$channel = if ($env:LCODE_CHANNEL) { $env:LCODE_CHANNEL } else { "git" }
$dryRun = ($env:LCODE_DRY_RUN -eq "1")
$doctor = ($env:LCODE_DOCTOR -eq "1")

$repo = $repo.TrimEnd("/")
if ($repo.EndsWith(".git")) { $repo = $repo.Substring(0, $repo.Length - 4) }

# 自定义镜像同时喂给 pip（PIP_INDEX_URL）和 uv（UV_DEFAULT_INDEX）。
if ($env:LCODE_PIP_INDEX) {
    $env:PIP_INDEX_URL = $env:LCODE_PIP_INDEX
    $env:UV_DEFAULT_INDEX = $env:LCODE_PIP_INDEX
    Write-Host "==> 使用自定义包索引：$($env:LCODE_PIP_INDEX)"
}

# 把命令行数组切成「程序 + 参数」，兼容只有一个元素的调用。
# 注意 return 前的逗号：PowerShell 会把「单元素数组」自动解包成标量，
# 于是 @rest 对字符串做 splat 时会按字符展开（"--init" 变成 - - i n i t）。
function Get-ProgramArgs {
    param([string[]]$Command)
    if ($Command.Length -le 1) { return , @() }
    return , @($Command[1..($Command.Length - 1)])
}

# 运行原生命令，不因为它的 stderr 进度输出而中止脚本。
# $ErrorActionPreference="Stop" 否则会把 uv / pip 的进度行变成终止性 NativeCommandError。
function Invoke-Native {
    param([string[]]$Command)
    $rest = Get-ProgramArgs $Command
    if ($dryRun) {
        Write-Host "    [dry-run] $($Command -join ' ')"
        return 0
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($rest.Length -eq 0) {
            & $Command[0] 2>&1 | ForEach-Object { Write-Host $_ }
        } else {
            & $Command[0] @rest 2>&1 | ForEach-Object { Write-Host $_ }
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

# 抓取原生命令的 stdout（失败时返回空串）。
function Get-NativeOutput {
    param([string[]]$Command)
    $rest = Get-ProgramArgs $Command
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = if ($rest.Length -eq 0) { & $Command[0] 2>$null } else { & $Command[0] @rest 2>$null }
        if ($LASTEXITCODE -ne 0) { return "" }
        return ($out | Out-String).Trim()
    } catch {
        return ""
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-NativeExit {
    param([string[]]$Command)
    $rest = Get-ProgramArgs $Command
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($rest.Length -eq 0) { & $Command[0] *> $null } else { & $Command[0] @rest *> $null }
        return $LASTEXITCODE
    } catch {
        return 1
    } finally {
        $ErrorActionPreference = $previous
    }
}

# 一个 python.exe 是否「不可执行」。
# Windows 自带的「应用商店别名」python.exe / python3.exe 是 0 字节的 reparse point：
# 执行它不报错，而是把 Microsoft Store 顶到前台并一直阻塞——
# 在安装脚本看来就是「永远卡住」。必须提前识别并跳过。
function Test-UnusableExe {
    param([string]$Path)
    if (-not $Path) { return $true }
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    if ($Path -like "*\WindowsApps\*") { return $true }
    try {
        if ((Get-Item -LiteralPath $Path -Force).Length -eq 0) { return $true }
    } catch { }
    return $false
}

# 同名命令可能有多条（PATH 上靠前的往往是商店占位符），逐条筛出真正可执行的。
function Get-UsableCommands {
    param([string]$Name)
    $paths = @()
    foreach ($cmd in @(Get-Command $Name -All -ErrorAction SilentlyContinue)) {
        $src = [string]$cmd.Source
        if (Test-UnusableExe $src) { continue }
        if ($paths -notcontains $src) { $paths += $src }
    }
    return $paths
}

$script:PyVersionCheck = "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"

# 找一个 Python 3.12+ 启动器；返回命令数组（如 @("py","-3.12") 或 @("C:\...\python.exe")）或 $null。
function Find-Python {
    # py 启动器最省事，但它自己也可能不存在，先确认命令可解析。
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @("-3.12", "-3.13", "-3.14", "-3")) {
            $candidate = @("py", $version)
            if ((Get-NativeExit ($candidate + @("-c", $script:PyVersionCheck))) -eq 0) {
                return , $candidate
            }
        }
    }
    # 退化到 PATH 上的真实 python.exe / python3.exe，跳过商店占位符。
    foreach ($name in @("python", "python3")) {
        foreach ($exe in Get-UsableCommands $name) {
            $candidate = @($exe)
            if ((Get-NativeExit ($candidate + @("-c", $script:PyVersionCheck))) -eq 0) {
                return , $candidate
            }
        }
    }
    return $null
}

# 报告本机 Python 版本，方便用户判断为什么不满足 3.12+。
function Get-PythonVersionText {
    foreach ($name in @("python", "python3")) {
        foreach ($exe in Get-UsableCommands $name) {
            $v = Get-NativeOutput @(
                $exe, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))")
            if ($v) { return "$exe = $v" }
        }
    }
    return $null
}

# 是否只存在「商店占位符」而没有真正的 Python（用于给出精准提示）。
function Test-StoreAliasOnly {
    $anyStore = $false
    foreach ($name in @("python", "python3")) {
        foreach ($cmd in @(Get-Command $name -All -ErrorAction SilentlyContinue)) {
            $src = [string]$cmd.Source
            if (-not $src) { continue }
            if (Test-UnusableExe $src) { $anyStore = $true }
            else { return $false }
        }
    }
    return $anyStore
}

# 查询 PyPI 上某个名字归谁：
#   "own"     → 是本项目（project_urls 指向 pando-r/my_mecode）
#   "other"   → 被别人占用，装下去就是装错包
#   "none"    → 名字还没发布
#   "unknown" → 网络不可达，无法判断
function Test-PyPIClaim {
    param([string]$Name)
    # 用自定义镜像时要查镜像，否则在国内 pypi.org 直连不通会误判成 unknown。
    $bases = @("https://pypi.org")
    if ($env:LCODE_PIP_INDEX) {
        $root = $env:LCODE_PIP_INDEX.TrimEnd("/")
        if ($root.EndsWith("/simple")) { $root = $root.Substring(0, $root.Length - 7) }
        if ($root -ne "https://pypi.org") { $bases = @($root) + $bases }
    }
    foreach ($base in $bases) {
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 "$base/pypi/$Name/json"
        } catch {
            $status = 0
            try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = 0 }
            if ($status -eq 404) { return "none" }
            continue
        }
        if ([string]$resp.Content -match "pando-r/my_mecode") { return "own" }
        return "other"
    }
    return "unknown"
}

# 把 binDir 追加到「用户级」PATH（幂等，只动 User 作用域）。
function Add-ToUserPath {
    param([string]$Dir)
    if (-not $Dir) { return $false }
    if ($env:LCODE_NO_PATH -eq "1") { return $false }
    if ($dryRun) { Write-Host "    [dry-run] 追加用户 PATH：$Dir"; return $false }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { $userPath = "" }
    $parts = @($userPath -split ';' | Where-Object { $_ -ne "" })
    if ($parts -contains $Dir) { return $false }
    try {
        $newPath = if ($userPath.Trim()) { "$($userPath.TrimEnd(';'));$Dir" } else { $Dir }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        return $true
    } catch {
        return $false
    }
}

# 装完自检：确认 `lcode --version` 打出来的确实是本项目，而不是 PyPI 上的同名包。
function Test-InstalledLcode {
    if (-not (Get-Command lcode -ErrorAction SilentlyContinue)) { return "missing" }
    if ($dryRun) { return "skipped" }
    $out = Get-NativeOutput @("lcode", "--version")
    if (-not $out) { return "broken" }
    if ($out -match "Lcode") { return "ok" }
    return "wrong-package"
}

# GitHub 代理前缀：直连不通时用来换路。
function Get-GitHubProxies {
    if ($env:LCODE_GH_PROXY) {
        if ($env:LCODE_GH_PROXY -match '^(none|off|-)$') { return @() }
        return @($env:LCODE_GH_PROXY.TrimEnd("/") + "/")
    }
    return @("https://ghfast.top/", "https://gh-proxy.com/", "https://ghproxy.net/")
}

# 带硬超时的可达性探测。用 HttpWebRequest 而不是 Invoke-WebRequest：
# 前者能对 DNS 解析与 TCP 连接一并限时，后者在域名被黑洞时会卡很久。
function Test-Url {
    param([string]$Url, [int]$TimeoutSec = 8)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Method = "HEAD"
        $req.Timeout = $TimeoutSec * 1000
        $req.ReadWriteTimeout = $TimeoutSec * 1000
        $req.UserAgent = "lcode-installer"
        $req.AllowAutoRedirect = $true
        $resp = $req.GetResponse()
        $resp.Close()
        return $true
    } catch {
        # 403 / 405 / 400 说明主机可达，只是不接受 HEAD 请求。
        try {
            $status = [int]$_.Exception.Response.StatusCode
            if ($status -eq 403 -or $status -eq 405 -or $status -eq 400) { return $true }
        } catch { }
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

# 依次探测候选地址，返回第一个可达的；全都不通返回 $null。
function Select-ReachableUrl {
    param([string[]]$Candidates)
    $index = 0
    foreach ($candidate in $Candidates) {
        $index++
        if ($dryRun) {
            Write-Host "    [dry-run] 跳过连通性探测，直接使用：$candidate"
            return $candidate
        }
        Write-Host "    探测 $index/$($Candidates.Count)：$candidate"
        if (Test-Url $candidate $script:ProbeTimeout) { return $candidate }
        Write-Host "      → 不通（${script:ProbeTimeout}s 超时）"
    }
    return $null
}

# 失败时不要把用户丢在「窗口直接关掉、什么都没看见」的处境里：
# 用 -File 直接跑时 exit 会立刻关掉窗口；这里改用 throw，
# 报错留在屏幕上，窗口不会消失。
function Write-Fail {
    param([string[]]$Lines)
    Write-Host ""
    foreach ($line in $Lines) { Write-Host $line }
    Write-Host ""
    throw ($Lines | Select-Object -First 1)
}

# ── 环境体检 ────────────────────────────────────────────────────────────────
if ($doctor) {
    Write-Host "==> Lcode 安装环境体检"
    Write-Host ""
    Write-Host "PowerShell : $($PSVersionTable.PSVersion)"
    Write-Host "系统       : $([System.Environment]::OSVersion.VersionString)"
    Write-Host ""

    foreach ($tool in @("git", "uv", "pipx", "lcode")) {
        $cmd = Get-Command $tool -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd) { Write-Host ("{0,-11}: {1}" -f $tool, $cmd.Source) }
        else { Write-Host ("{0,-11}: 未安装" -f $tool) }
    }

    $py = Find-Python
    if ($py) {
        $ver = Get-NativeOutput ($py + @("-c", "import sys; print(sys.version.split()[0])"))
        Write-Host ("{0,-11}: {1} （Python {2}）" -f "python", ($py -join " "), $ver)
    } elseif (Test-StoreAliasOnly) {
        # 有 python 命令，但它只是应用商店占位符：跑起来会拉起商店而不是解释器。
        Write-Host "python     : 只有 Windows 应用商店的 0 字节占位符（WindowsApps\python.exe）"
        Write-Host "             它不会执行 Python，只会打开商店。请安装真正的 Python 3.12+，"
        Write-Host "             或让安装脚本用 uv 自带的解释器（`$env:LCODE_INSTALLER='uv'）。"
    } elseif (Get-PythonVersionText) {
        $found = Get-PythonVersionText
        Write-Host ("{0,-11}: {1}（版本低于 3.12，本项目需要 3.12+）" -f "python", $found)
    } else {
        Write-Host "python     : 未安装"
    }
    Write-Host ""

    Write-Host "网络连通性（每个地址最长 ${script:ProbeTimeout} 秒）："
    $targets = @(
        "https://github.com",
        "https://raw.githubusercontent.com",
        "https://pypi.org",
        "https://astral.sh",
        "https://cdn.jsdelivr.net",
        "https://ghfast.top"
    )
    foreach ($t in $targets) {
        if (Test-Url $t $script:ProbeTimeout) { Write-Host "  [通]   $t" }
        else { Write-Host "  [不通] $t" }
    }
    Write-Host ""
    Write-Host "判断建议："
    Write-Host "  · github.com 不通 → 用 LCODE_GH_PROXY 指定代理，或走 PyPI 镜像"
    Write-Host "  · 只有 pypi.org 不通 → 设 `$env:LCODE_PIP_INDEX='https://mirrors.aliyun.com/pypi/simple/'"
    Write-Host "  · python 那里提示商店占位符 → 先装 Python 3.12+，或让它用 uv 自带解释器"
    return
}

# 1) 选安装器：uv > pipx > pip
$installer = $env:LCODE_INSTALLER
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

# 2) 解析安装源（带镜像回退）
$useProxy = $false
if ($env:LCODE_SOURCE_URL) {
    $src = $env:LCODE_SOURCE_URL
    Write-Host "==> 使用手工指定的安装源：$src"
    Write-Host ""
} else {
    if ($channel -eq "pypi") {
        $pypiName = if ($env:LCODE_PYPI_NAME) { $env:LCODE_PYPI_NAME } else { "lcode" }
        $claim = Test-PyPIClaim $pypiName
        switch ($claim) {
            "other" {
                Write-Fail @(
                    "错误：PyPI 上的「$pypiName」不是本项目。",
                    "  该名字已被其他人占用（例如 lcodePy-team 的等离子体仿真包），",
                    "  pip install lcode 会静默装成一个完全无关的程序。",
                    "  请改用源码渠道（去掉 LCODE_CHANNEL=pypi），或用 LCODE_PYPI_NAME 指定本项目的发布名。"
                )
            }
            "none" {
                Write-Fail @(
                    "错误：PyPI 上还没有发布「$pypiName」。",
                    "  请改用源码渠道（去掉 LCODE_CHANNEL=pypi 即可从 GitHub 安装）。"
                )
            }
            "unknown" {
                Write-Host "警告：无法访问 pypi.org 确认「$pypiName」归属，安装后会自动校验版本。"
            }
        }
        $src = $pypiName
        Write-Host "==> 安装 Lcode（方式：$installer，来源：$src）"
        Write-Host ""
    } else {
        # 直连优先，不通再依次换 GitHub 代理；只测仓库主页，避免逐个源包试错。
        $candidates = @($repo)
        foreach ($proxy in Get-GitHubProxies) { $candidates += "$proxy$repo" }
        Write-Host "==> 探测源码仓库可达性"
        $base = Select-ReachableUrl $candidates
        if (-not $base) {
            Write-Host "警告：所有候选地址都不通，仍按直连尝试一次（可能是 HEAD 被拦而 GET 可用）。"
            $base = $repo
        } elseif ($base -ne $repo) {
            $useProxy = $true
            Write-Host "==> 直连不通，改用代理：$base"
        }
        Write-Host ""

        if (Get-Command git -ErrorAction SilentlyContinue) {
            $src = "git+$base.git"
        } else {
            # 没有 git 就退回 GitHub 的 zip 源码包
            $src = "$base/archive/refs/heads/main.zip"
        }
        Write-Host "==> 安装 Lcode（方式：$installer，来源：$src）"
        Write-Host ""
    }
}

$binDir = $null
switch ($installer) {
    "uv" {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            # 优先 pip 装 uv：走 PyPI 镜像，国内基本可达；astral.sh 则经常被墙。
            $pythonForUv = Find-Python
            if ($pythonForUv -and -not $dryRun) {
                Write-Host "==> 用 pip 安装 uv（走包索引，不依赖 astral.sh）"
                $uvArgs = $pythonForUv + @("-m", "pip", "install", "--user", "--upgrade", "uv")
                if ($env:LCODE_PIP_INDEX) { $uvArgs += @("--index-url", $env:LCODE_PIP_INDEX) }
                [void](Invoke-Native $uvArgs)
                $scriptDir = Get-NativeOutput ($pythonForUv + @(
                    "-c", "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"))
                if ($scriptDir -and (Test-Path $scriptDir)) { $env:Path = "$scriptDir;$env:Path" }
            }
        }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-Host "==> 从 astral.sh 下载 uv（30 秒超时）"
            if ($dryRun) {
                Write-Host "    [dry-run] irm https://astral.sh/uv/install.ps1 -TimeoutSec 30 | iex"
            } else {
                try {
                    $uvInstaller = Invoke-RestMethod -UseBasicParsing -TimeoutSec 30 https://astral.sh/uv/install.ps1
                    $uvInstaller | Invoke-Expression
                } catch {
                    Write-Fail @(
                        "uv 下载失败（astral.sh 不通，常见于公司/校园网或国内网络）。",
                        "  改用 pip：先执行 `$env:LCODE_INSTALLER='pip'，再重跑安装命令",
                        "  或手动安装 uv 后重试：https://docs.astral.sh/uv/getting-started/installation/",
                        "  排查网络：`$env:LCODE_DOCTOR='1'; 重跑安装命令"
                    )
                }
                $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
            }
        }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue) -and -not $dryRun) {
            Write-Fail @(
                "uv 安装失败。可改用 pip：先执行 `$env:LCODE_INSTALLER='pip'，再重跑安装命令。"
            )
        }

        $toolArgs = @("uv", "tool", "install", "--force")
        if (-not (Find-Python)) {
            Write-Host "==> 系统里没有可用的 Python 3.12+，让 uv 装一个自带解释器"
            if ($useProxy -and -not $env:UV_PYTHON_INSTALL_MIRROR) {
                # uv 的 Python 构建产物托管在 GitHub Releases，直连不通时同样走代理。
                $env:UV_PYTHON_INSTALL_MIRROR =
                    "https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download"
                Write-Host "    下载源：$($env:UV_PYTHON_INSTALL_MIRROR)"
            }
            [void](Invoke-Native @("uv", "python", "install", "3.12"))
            $toolArgs += @("--python", "3.12")
        }
        Write-Host "==> 把 Lcode 装进隔离环境"
        $toolArgs += $src
        $code = Invoke-Native $toolArgs
        if ($code -ne 0) {
            Write-Fail @(
                "uv tool install 失败（退出码 $code）。",
                $(if ($env:LCODE_PIP_INDEX) {
                    "  已指定镜像 $($env:LCODE_PIP_INDEX)，请确认它可访问。"
                } else {
                    "  网络慢/超时可换镜像重试：`$env:LCODE_PIP_INDEX='https://mirrors.aliyun.com/pypi/simple/'"
                })
            )
        }
        [void](Invoke-Native @("uv", "tool", "update-shell"))
        $binDir = Get-NativeOutput @("uv", "tool", "dir", "--bin")
    }
    "pipx" {
        Write-Host "==> 使用已安装的 pipx（隔离环境，无需 uv）"
        $pipxArgs = @("pipx", "install", "--force", "--pip-args=--timeout 30 --retries 2", $src)
        $code = Invoke-Native $pipxArgs
        if ($code -ne 0) {
            Write-Fail @(
                "pipx 安装失败（退出码 $code）。重试：先执行 `$env:LCODE_INSTALLER='pip'，再重跑安装命令。",
                $(if (-not $env:LCODE_PIP_INDEX) {
                    "  国内网络可先设镜像：`$env:LCODE_PIP_INDEX='https://mirrors.aliyun.com/pypi/simple/'"
                } else { "" })
            )
        }
        # ensurepath 幂等；不执行它 pipx 装完往往不在 PATH 里。
        [void](Invoke-Native @("pipx", "ensurepath"))
        $binDir = Get-NativeOutput @("pipx", "environment", "--value", "PIPX_BIN_DIR")
        if (-not $binDir) { $binDir = "$env:USERPROFILE\.local\bin" }
    }
    "pip" {
        if (-not $python) { $python = Find-Python }
        if (-not $python) {
            $found = Get-PythonVersionText
            $lines = @("未找到可用的 Python 3.12+。")
            if ($found) {
                $lines += "  当前检测到：$found（版本过低）"
            } elseif (Test-StoreAliasOnly) {
                $lines += "  检测到的 python 命令是 Windows 应用商店的 0 字节占位符"
                $lines += "  （WindowsApps\python.exe）。执行它只会打开商店，不会运行 Python。"
            } else {
                $lines += "  当前机器上没有任何 Python。"
            }
            $lines += "  方案一：装 Python 3.12+ https://www.python.org/downloads/ （安装时勾选 Add to PATH）"
            $lines += "  方案二：装 uv，由它自带解释器 https://docs.astral.sh/uv/"
            Write-Fail $lines
        }
        $pyText = Get-NativeOutput ($python + @("-c", "import sys; print(sys.version.split()[0])"))
        Write-Host "==> 使用 $($python -join ' ')（Python $pyText，免 uv）"
        # --timeout/--retries 让 pip 自己给网络操作限时，避免无限等待。
        $pipArgs = $python + @(
            "-m", "pip", "install", "--user", "--upgrade",
            "--timeout", "30", "--retries", "2"
        )
        if ($env:LCODE_PIP_INDEX) { $pipArgs += @("--index-url", $env:LCODE_PIP_INDEX) }
        $pipArgs += $src
        $code = Invoke-Native $pipArgs
        if ($code -ne 0) {
            Write-Fail @(
                "pip 安装失败（退出码 $code）。常见原因：",
                "  · 网络超时 / SSL 错误：换国内镜像",
                "      `$env:LCODE_PIP_INDEX='https://mirrors.aliyun.com/pypi/simple/'",
                "  · 公司网络需要代理：先设 `$env:HTTPS_PROXY='http://host:port'",
                "  · 想装成隔离的全局命令：pipx install `"$src`"",
                "  · git 拉取源码失败：确认 git 已安装且能访问仓库"
            )
        }
        $binDir = Get-NativeOutput ($python + @(
            "-c", "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"))
    }
    default {
        Write-Fail @("未知 LCODE_INSTALLER: $installer（期望 auto | uv | pipx | pip）")
    }
}

# 3) 让当前会话能找到 lcode，并尽力写进用户 PATH（新终端也能用）
if ($binDir) { $env:Path = "$binDir;$env:Path" }
$pathAdded = $false
if ($binDir) { $pathAdded = Add-ToUserPath -Dir $binDir }

# 4) 装完自检：确认装的确实是 Lcode，而不是同名无关包
$state = Test-InstalledLcode
switch ($state) {
    "ok" {
        $ver = Get-NativeOutput @("lcode", "--version")
        Write-Host "==> 自检通过：$ver"
    }
    "wrong-package" {
        Write-Fail @(
            "错误：命令行里的 lcode 不是 Lcode 本体（--version 输出异常）。",
            "  很可能机器上已有另一个同名的 lcode（例如 PyPI 上的 lcodePy 包）。",
            "  执行 pip uninstall lcode 后重试，或改用 pipx/uv 隔离安装：",
            "    `$env:LCODE_INSTALLER='pipx'"
        )
    }
    "broken" {
        Write-Host "警告：lcode 已安装但 --version 无法运行，可能缺少依赖。"
        Write-Host "  可重试：`$env:LCODE_INSTALLER='uv'（隔离安装，依赖最干净）"
    }
    "missing" {
        Write-Host "lcode 已安装但当前 PATH 里找不到。"
        if ($binDir) { Write-Host "  请新开一个终端，或手动加入 PATH：$binDir" }
    }
}

# 5) 首次运行引导：写一份配置模板，用户只需填 api_key
Write-Host ""
if (Get-Command lcode -ErrorAction SilentlyContinue) {
    [void](Invoke-Native @("lcode", "--init"))
} elseif ($binDir -and (Test-Path (Join-Path $binDir "lcode.cmd"))) {
    [void](Invoke-Native @((Join-Path $binDir "lcode.cmd"), "--init"))
}

Write-Host ""
Write-Host "安装完成："
Write-Host "  1) 编辑 $env:USERPROFILE\.lcode\config.yaml，把 providers[0].api_key 换成真实密钥"
Write-Host "  2) 在任意项目目录运行：lcode"
Write-Host "  3) 查看版本：lcode --version"
if ($pathAdded) {
    Write-Host ""
    Write-Host "（已把 $binDir 追加到用户 PATH，新开的终端直接可用；不想改 PATH 下次设 LCODE_NO_PATH=1）"
}
