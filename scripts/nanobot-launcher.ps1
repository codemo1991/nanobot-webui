#Requires -Version 5.1
<#
.SYNOPSIS
    nanobot 守护启动器 — 支持自更新后自动重启。

.DESCRIPTION
    以守护模式运行 nanobot web-ui。当 nanobot 通过 self_update 工具触发重启时
    （退出码 42），本脚本会自动执行 pip install 并重新启动服务。

.PARAMETER ListenHost
    Web UI 监听地址，默认 127.0.0.1

.PARAMETER Port
    Web UI 监听端口，默认 6788

.EXAMPLE
    .\nanobot-launcher.ps1
    .\nanobot-launcher.ps1 -ListenHost 0.0.0.0 -Port 8080
#>

param(
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 6788,
    [switch]$Verbose,
    [switch]$Debug
)

$RESTART_EXIT_CODE = 42
$MAX_RAPID_RESTARTS = 5
$RAPID_RESTART_WINDOW = 60  # seconds

$restartTimestamps = @()

# 设置 UTF-8 编码，避免 emoji/中文导致 UnicodeEncodeError
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

function Write-Banner {
    Write-Host ""
    Write-Host "  ================================" -ForegroundColor Cyan
    Write-Host "   nanobot launcher (guardian mode)" -ForegroundColor Cyan
    Write-Host "  ================================" -ForegroundColor Cyan
    Write-Host ""
}

function Get-NanobotArgs {
    $args_list = @("web-ui", "--host", $ListenHost, "--port", $Port)
    if ($Verbose) { $args_list += "--verbose" }
    if ($Debug) { $args_list += "--debug" }
    return $args_list
}

Write-Banner

while ($true) {
    $nanobotArgs = Get-NanobotArgs
    Write-Host "[launcher] Starting: nanobot $($nanobotArgs -join ' ')" -ForegroundColor Green
    Write-Host "[launcher] Restart exit code: $RESTART_EXIT_CODE | Ctrl+C to stop" -ForegroundColor DarkGray
    Write-Host ""

    # 直接调用而非 Start-Process，$LASTEXITCODE 可可靠捕获 os._exit() 的退出码
    & nanobot @nanobotArgs
    $exitCode = $LASTEXITCODE

    Write-Host ""
    Write-Host "[launcher] nanobot exited with code: $exitCode" -ForegroundColor Yellow

    if ($exitCode -eq $RESTART_EXIT_CODE) {
        # Rapid restart protection
        $now = Get-Date
        $restartTimestamps = @($restartTimestamps | Where-Object { ($now - $_).TotalSeconds -lt $RAPID_RESTART_WINDOW })
        $restartTimestamps += $now

        if ($restartTimestamps.Count -ge $MAX_RAPID_RESTARTS) {
            Write-Host "[launcher] Too many rapid restarts ($MAX_RAPID_RESTARTS in ${RAPID_RESTART_WINDOW}s). Exiting." -ForegroundColor Red
            exit 1
        }

        Write-Host "[launcher] Self-update restart requested. Pulling & reinstalling..." -ForegroundColor Cyan

        # Find repo directory (same directory as this script's parent)
        $repoDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
        if (Test-Path (Join-Path $repoDir "pyproject.toml")) {
            Push-Location $repoDir

            # 拉取远端最新代码（如已在 nanobot 内部 pull 则为 no-op，无害）
            Write-Host "[launcher] Running: git pull (in $repoDir)" -ForegroundColor DarkGray
            $gitOut = & git pull 2>&1
            $gitOut | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[launcher] Warning: git pull failed (exit $LASTEXITCODE), continuing anyway..." -ForegroundColor Yellow
            }

            # 重新安装依赖（处理新增包）
            Write-Host "[launcher] Running: pip install -e . (in $repoDir)" -ForegroundColor DarkGray
            & pip install -e . --quiet 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

            Pop-Location
        }

        Write-Host "[launcher] Restarting in 2 seconds..." -ForegroundColor Cyan
        Start-Sleep -Seconds 2
        continue
    }
    else {
        Write-Host "[launcher] Normal exit. Goodbye." -ForegroundColor Green
        exit $exitCode
    }
}
