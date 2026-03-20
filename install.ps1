<#
.SYNOPSIS
    Session Picker - One-click installer / 一键安装脚本
.DESCRIPTION
    Install cs (Claude Code) and codexs (Codex) session picker commands.
    安装 cs (Claude Code) 和 codexs (Codex) 会话选择器命令。
.NOTES
    Requirements / 要求: Python 3.10+
    Usage / 用法: powershell -ExecutionPolicy Bypass -File install.ps1
#>

$ErrorActionPreference = "Stop"

# ── Banner ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  Session Picker - Installer" -ForegroundColor Cyan
Write-Host "  ==========================" -ForegroundColor DarkGray
Write-Host "  Claude Code (cs) + Codex (codexs)" -ForegroundColor DarkGray
Write-Host ""

# ── Locate script files / 定位脚本文件 ────────────────────────────────────────

$installerDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$claudeScript = Join-Path $installerDir "claude-sessions.py"
$codexScript  = Join-Path $installerDir "codex-sessions.py"

$hasClaudeScript = Test-Path $claudeScript
$hasCodexScript  = Test-Path $codexScript

if (-not $hasClaudeScript -and -not $hasCodexScript) {
    Write-Host "  [x] claude-sessions.py and codex-sessions.py not found next to install.ps1" -ForegroundColor Red
    Write-Host "      Please place install.ps1 alongside the .py files." -ForegroundColor Red
    exit 1
}

# ── Check Python 3.10+ / 检查 Python 版本 ────────────────────────────────────

function Test-Python {
    param([string]$PythonCmd)
    try {
        $ver = & $PythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $ver) { return $null }
        $parts = $ver.Split(".")
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        if ($major -ge 3 -and $minor -ge 10) {
            return @{ Version = $ver; Command = $PythonCmd }
        }
        Write-Host "  [x] $PythonCmd version $ver < 3.10" -ForegroundColor Red
        return $null
    } catch {
        return $null
    }
}

$pythonExe = $null
foreach ($candidate in @("python", "python3", "py")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        $result = if ($candidate -eq "py") { Test-Python "py -3" } else { Test-Python $candidate }
        if ($result) {
            $pythonExe = $result.Command
            Write-Host "  [ok] Python $($result.Version) ($pythonExe)" -ForegroundColor Green
            break
        }
    }
}

while (-not $pythonExe) {
    Write-Host ""
    Write-Host "  Python 3.10+ not found in PATH." -ForegroundColor Yellow
    $customPath = Read-Host "  Enter Python install directory or exe path ('q' to quit)"
    if ($customPath -eq 'q' -or $customPath -eq 'Q') { exit 1 }

    $customPath = $customPath.Trim('"', "'", ' ')
    if (Test-Path (Join-Path $customPath "python.exe")) {
        $candidate = Join-Path $customPath "python.exe"
    } elseif (Test-Path $customPath -PathType Leaf) {
        $candidate = $customPath
    } else {
        Write-Host "  [x] python.exe not found in: $customPath" -ForegroundColor Red
        continue
    }

    $result = Test-Python $candidate
    if ($result) {
        $pythonExe = $result.Command
        Write-Host "  [ok] Python $($result.Version) ($pythonExe)" -ForegroundColor Green
    }
}

# ── Check CLIs / 检查 CLI 工具 ────────────────────────────────────────────────

$hasClaude = [bool](Get-Command claude -ErrorAction SilentlyContinue)
$hasCodex  = [bool](Get-Command codex -ErrorAction SilentlyContinue)

if ($hasClaude) { Write-Host "  [ok] Claude Code CLI" -ForegroundColor Green }
else            { Write-Host "  [--] Claude Code CLI not found (skip cs)" -ForegroundColor DarkGray }

if ($hasCodex)  { Write-Host "  [ok] Codex CLI" -ForegroundColor Green }
else            { Write-Host "  [--] Codex CLI not found (skip codexs)" -ForegroundColor DarkGray }

if (-not $hasClaude -and -not $hasCodex) {
    Write-Host "  [!!] Neither claude nor codex found. Commands will install but resume won't work." -ForegroundColor Yellow
}

# ── Install scripts / 安装脚本文件 ────────────────────────────────────────────

$installed = @()

if ($hasClaudeScript) {
    $destDir = Join-Path $HOME ".claude" "scripts"
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    Copy-Item -Path $claudeScript -Destination (Join-Path $destDir "claude-sessions.py") -Force
    Write-Host "  [ok] Installed: ~/.claude/scripts/claude-sessions.py" -ForegroundColor Green
    $installed += "claude"
}

if ($hasCodexScript) {
    $destDir = Join-Path $HOME ".codex" "scripts"
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    Copy-Item -Path $codexScript -Destination (Join-Path $destDir "codex-sessions.py") -Force
    Write-Host "  [ok] Installed: ~/.codex/scripts/codex-sessions.py" -ForegroundColor Green
    $installed += "codex"
}

# ── Update PowerShell Profile / 更新 PowerShell 配置 ─────────────────────────

$profileDir = Split-Path $PROFILE -Parent
if (-not (Test-Path $profileDir)) {
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
}
if (-not (Test-Path $PROFILE)) {
    New-Item -ItemType File -Force -Path $PROFILE | Out-Null
}

$profileContent = Get-Content $PROFILE -Raw -ErrorAction SilentlyContinue
if (-not $profileContent) { $profileContent = "" }

# Build profile block
$profileLines = @()
$profileLines += ""
$profileLines += "# ── Session Picker ──"

if ($installed -contains "claude") {
    $profileLines += @"
function cs {
    `$result = & "$pythonExe" "`$HOME\.claude\scripts\claude-sessions.py" --select @args
    if (`$result) {
        `$lines = `$result -split "``n"
        `$sid = `$lines[0].Trim()
        `$proj = if (`$lines.Count -gt 1) { `$lines[1].Trim() } else { "" }
        if (`$sid) {
            if (`$proj -and (Test-Path `$proj)) { Set-Location `$proj }
            claude --resume `$sid
        }
    }
}
function csl { & "$pythonExe" "`$HOME\.claude\scripts\claude-sessions.py" --list @args }
"@
}

if ($installed -contains "codex") {
    $profileLines += @"
function codexs {
    `$result = & "$pythonExe" "`$HOME\.codex\scripts\codex-sessions.py" --select @args
    if (`$result) {
        `$lines = `$result -split "``n"
        `$sid = `$lines[0].Trim()
        `$proj = if (`$lines.Count -gt 1) { `$lines[1].Trim() } else { "" }
        if (`$sid) {
            if (`$proj -and (Test-Path `$proj)) { Set-Location `$proj }
            codex resume `$sid
        }
    }
}
function codexsl { & "$pythonExe" "`$HOME\.codex\scripts\codex-sessions.py" --list @args }
"@
}

$profileBlock = $profileLines -join "`n"

# Remove old version if exists, then append new
if ($profileContent.Contains("# ── Session Picker ──")) {
    $pattern = '(?s)# ── Session Picker ──.*?(?=\n# ── (?!Session Picker)|\z)'
    $newContent = [regex]::Replace($profileContent, $pattern, $profileBlock.TrimStart())
    Set-Content -Path $PROFILE -Value $newContent -Encoding UTF8
    Write-Host "  [ok] PowerShell profile updated (replaced)" -ForegroundColor Green
} elseif ($profileContent.Contains("Claude Code Session Manager (cs)")) {
    # Migrate from old installer format
    $pattern = '(?s)# ── Claude Code Session Manager \(cs\) ──.*?function csl \{[^\}]+\}'
    $newContent = [regex]::Replace($profileContent, $pattern, $profileBlock.TrimStart())
    Set-Content -Path $PROFILE -Value $newContent -Encoding UTF8
    Write-Host "  [ok] PowerShell profile migrated from old version" -ForegroundColor Green
} else {
    Add-Content -Path $PROFILE -Value $profileBlock -Encoding UTF8
    Write-Host "  [ok] PowerShell profile updated: $PROFILE" -ForegroundColor Green
}

# ── Done / 完成 ──────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  Installation complete!" -ForegroundColor Cyan
Write-Host "  ──────────────────────────────────────" -ForegroundColor DarkGray

if ($installed -contains "claude") {
    Write-Host "    cs      - Claude Code session picker (TUI)" -ForegroundColor White
    Write-Host "    csl     - Claude Code session list" -ForegroundColor White
}
if ($installed -contains "codex") {
    Write-Host "    codexs  - Codex session picker (TUI)" -ForegroundColor White
    Write-Host "    codexsl - Codex session list" -ForegroundColor White
}

Write-Host ""
Write-Host "  Reload now:  . `$PROFILE" -ForegroundColor DarkGray
Write-Host ""
