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

.PARAMETER EnableVerbose
    启用 verbose 日志（DEBUG 级别），可简写为 -v

.PARAMETER EnableDebug
    启用 debug 模式，输出最详细的 TRACE 级别日志，可简写为 -d

.EXAMPLE
    .\nanobot-launcher.ps1
    .\nanobot-launcher.ps1 -ListenHost 0.0.0.0 -Port 8080
    .\nanobot-launcher.ps1 -EnableDebug
    .\nanobot-launcher.ps1 -d
    .\nanobot-launcher.ps1 --debug
#>

param(
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 6788,
    [Alias("v")][switch]$EnableVerbose,
    [Alias("d")][switch]$EnableDebug,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Remaining
)

# 支持 --debug 传参（Linux 风格）
if (-not $EnableDebug -and $Remaining -contains "--debug") { $EnableDebug = $true }

# 防止 --debug 被误解析为 --host 的值（如 -ListenHost --debug）
if ($ListenHost -match '^-') {
    Write-Host "[launcher] Warning: ListenHost 不能以 - 开头，已恢复默认 127.0.0.1" -ForegroundColor Yellow
    $ListenHost = "127.0.0.1"
    $EnableDebug = $true
}

$RESTART_EXIT_CODE = 42
$MAX_RAPID_RESTARTS = 5
$RAPID_RESTART_WINDOW = 60  # seconds

$restartTimestamps = @()

# 路径常量：脚本所在目录的上一级即仓库根目录，venv 建在根目录下
$REPO_DIR  = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$VENV_DIR  = Join-Path $REPO_DIR ".venv"
$NANOBOT_EXE = Join-Path $VENV_DIR "Scripts\nanobot.exe"
$VENV_PIP    = Join-Path $VENV_DIR "Scripts\pip.exe"
$WEB_UI_DIR  = Join-Path $REPO_DIR "web-ui"

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
    if ($EnableVerbose) { $args_list += "--verbose" }
    if ($EnableDebug) { $args_list += "--debug" }
    return $args_list
}

function Ensure-FrontendBuilt {
    param([switch]$Force)

    if (-not (Test-Path $WEB_UI_DIR)) {
        Write-Host "[launcher] 未找到 web-ui 目录，跳过前端构建。" -ForegroundColor Yellow
        return
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host "[launcher] 未找到 npm，跳过前端构建（请安装 Node.js）。" -ForegroundColor Yellow
        return
    }

    # npm install —— 仅在 node_modules 缺失时执行（依赖安装耗时，package.json 不常变）
    $nodeModules = Join-Path $WEB_UI_DIR "node_modules"
    if ($Force -or -not (Test-Path $nodeModules)) {
        Write-Host "[launcher] 正在安装前端依赖 (npm install)..." -ForegroundColor Yellow
        Push-Location $WEB_UI_DIR
        & npm install 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        $npmExit = $LASTEXITCODE
        Pop-Location
        if ($npmExit -ne 0) {
            Write-Host "[launcher] npm install 失败（exit $npmExit）。" -ForegroundColor Red
            exit 1
        }
        Write-Host "[launcher] npm install 完成。" -ForegroundColor Green
    }

    # npm run build —— 每次都执行，确保源码改动即时生效
    Write-Host "[launcher] 正在构建前端 (npm run build)..." -ForegroundColor Yellow
    Push-Location $WEB_UI_DIR
    & npm run build 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    $buildExit = $LASTEXITCODE
    Pop-Location
    if ($buildExit -ne 0) {
        Write-Host "[launcher] npm run build 失败（exit $buildExit）。" -ForegroundColor Red
        exit 1
    }
    Write-Host "[launcher] 前端构建完成。" -ForegroundColor Green
    Write-Host ""
}

function Ensure-VenvReady {
    # 1. 如果 venv 不存在则创建
    if (-not (Test-Path $VENV_DIR)) {
        Write-Host "[launcher] 正在创建虚拟环境: $VENV_DIR" -ForegroundColor Yellow
        & python -m venv $VENV_DIR
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[launcher] 虚拟环境创建失败，请确认 Python 已正确安装。" -ForegroundColor Red
            exit 1
        }
        Write-Host "[launcher] 虚拟环境创建成功。" -ForegroundColor Green
    }

    # 2. 构建前端（npm install + npm run build）
    Ensure-FrontendBuilt

    # 3. 如果 nanobot 尚未安装到 venv，则安装
    if (-not (Test-Path $NANOBOT_EXE)) {
        if (-not (Test-Path (Join-Path $REPO_DIR "pyproject.toml"))) {
            Write-Host "[launcher] 未找到 pyproject.toml（路径：$REPO_DIR），无法自动安装。" -ForegroundColor Red
            exit 1
        }
        Write-Host "[launcher] 正在安装 nanobot 到虚拟环境..." -ForegroundColor Yellow
        Write-Host "[launcher] Running: pip install -e . (in $REPO_DIR)" -ForegroundColor DarkGray
        Push-Location $REPO_DIR
        & $VENV_PIP install -e . 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        $pipExit = $LASTEXITCODE
        Pop-Location
        if ($pipExit -ne 0) {
            Write-Host "[launcher] 安装失败（exit $pipExit）。" -ForegroundColor Red
            exit 1
        }
        if (-not (Test-Path $NANOBOT_EXE)) {
            Write-Host "[launcher] 安装完成但未找到可执行文件: $NANOBOT_EXE" -ForegroundColor Red
            exit 1
        }
        Write-Host "[launcher] nanobot 安装成功。" -ForegroundColor Green
        Write-Host ""
    }
}

Write-Banner
Write-Host "[launcher] Repo:   $REPO_DIR" -ForegroundColor DarkGray
Write-Host "[launcher] Venv:   $VENV_DIR" -ForegroundColor DarkGray
Write-Host "[launcher] Debug:  $($EnableDebug.IsPresent)" -ForegroundColor DarkGray
Write-Host ""
Ensure-VenvReady

while ($true) {
    $nanobotArgs = Get-NanobotArgs
    Write-Host "[launcher] Starting: $NANOBOT_EXE $($nanobotArgs -join ' ')" -ForegroundColor Green
    Write-Host "[launcher] Restart exit code: $RESTART_EXIT_CODE | Ctrl+C to stop" -ForegroundColor DarkGray
    Write-Host ""

    # 直接调用而非 Start-Process，$LASTEXITCODE 可可靠捕获 os._exit() 的退出码
    & $NANOBOT_EXE @nanobotArgs
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

        if (Test-Path (Join-Path $REPO_DIR "pyproject.toml")) {
            Push-Location $REPO_DIR

            # 拉取远端最新代码（如已在 nanobot 内部 pull 则为 no-op，无害）
            Write-Host "[launcher] Running: git pull (in $REPO_DIR)" -ForegroundColor DarkGray
            $gitOut = & git pull 2>&1
            $gitOut | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[launcher] Warning: git pull failed (exit $LASTEXITCODE), continuing anyway..." -ForegroundColor Yellow
            }

            # 重建前端
            Write-Host "[launcher] Running: npm install + npm run build (in $WEB_UI_DIR)" -ForegroundColor DarkGray
            Ensure-FrontendBuilt -Force

            # 重新安装依赖（使用 venv 的 pip）
            Write-Host "[launcher] Running: pip install -e . (in $REPO_DIR, venv)" -ForegroundColor DarkGray
            & $VENV_PIP install -e . --quiet 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

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
