#Requires -Version 5.1
<#
.SYNOPSIS
    nanobot 守护启动器 — 支持自更新后自动重启，并能自动修复损坏的安装。

.DESCRIPTION
    以守护模式运行 nanobot web-ui。当 nanobot 通过 self_update 工具触发重启时
    （退出码 42），本脚本会自动执行 pip install 并重新启动服务。
    启动时会检测 nanobot 安装是否可用；若检测到损坏（如 ModuleNotFoundError），
    会自动清除残留并重新执行 pip install -e . 进行修复。

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

$LAUNCHER_TAG = '[launcher]'

# 支持 --debug 传参（Linux 风格）
if (-not $EnableDebug -and $Remaining -contains "--debug") { $EnableDebug = $true }

# 防止 --debug 被误解析为 --host 的值（如 -ListenHost --debug）
if ($ListenHost -match '^-') {
    Write-Host "$LAUNCHER_TAG Warning: ListenHost 不能以 - 开头，已恢复默认 127.0.0.1" -ForegroundColor Yellow
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
$VENV_PYTHON = Join-Path $VENV_DIR "Scripts\python.exe"
$SITE_PACKAGES = Join-Path $VENV_DIR "Lib\site-packages"
$WEB_UI_DIR  = Join-Path $REPO_DIR "web-ui"

# 设置 UTF-8 编码，避免 emoji/中文导致 UnicodeEncodeError
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
# 设置控制台代码页为 UTF-8
if (Get-Command chcp -ErrorAction SilentlyContinue) {
    chcp 65001 > $null
}

function Write-Banner {
    Write-Host ""
    Write-Host "  ================================" -ForegroundColor Cyan
    Write-Host "   nanobot launcher (guardian mode)" -ForegroundColor Cyan
    Write-Host "  ================================" -ForegroundColor Cyan
    Write-Host ""
}

# 检测并终止已运行的 nanobot 进程
function Stop-ExistingNanobot {
    # 查找正在运行的 nanobot 进程
    $nanobotProcesses = Get-Process -Name "nanobot" -ErrorAction SilentlyContinue
    if (-not $nanobotProcesses) {
        # 尝试通过命令行查找
        $nanobotProcesses = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match "nanobot.*web-ui" }
    }

    if ($nanobotProcesses) {
        Write-Host "$LAUNCHER_TAG 检测到已有 nanobot 进程运行中，正在终止..." -ForegroundColor Yellow
        if ($nanobotProcesses -is [System.Diagnostics.Process]) {
            $nanobotProcesses | ForEach-Object {
                Write-Host "$LAUNCHER_TAG 终止进程: $($_.Id) ($($_.ProcessName))" -ForegroundColor DarkGray
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        } else {
            # CIMInstance 返回的是进程对象
            $nanobotProcesses | ForEach-Object {
                Write-Host "$LAUNCHER_TAG 终止进程: $($_.ProcessId) ($($_.Name))" -ForegroundColor DarkGray
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        # 等待进程终止
        Start-Sleep -Milliseconds 500
        Write-Host "$LAUNCHER_TAG 已终止旧进程" -ForegroundColor Green
    }
}

function Get-NanobotArgs {
    $args_list = @("web-ui", "--host", $ListenHost, "--port", $Port)
    if ($EnableVerbose) { $args_list += "--verbose" }
    if ($EnableDebug) { $args_list += "--debug" }
    return $args_list
}

# 检测 nanobot 安装是否可用（可执行且能正常导入模块）
function Test-NanobotHealth {
    $null = & $NANOBOT_EXE --help 2>&1
    return $LASTEXITCODE -eq 0
}

# 修复损坏的 nanobot 安装：清除残留后重新安装
function Repair-NanobotInstall {
    Write-Host "$LAUNCHER_TAG 正在修复 nanobot 安装..." -ForegroundColor Yellow

    # 1. 尝试卸载（可能因损坏而失败，忽略）
    & $VENV_PIP uninstall nanobot-ai -y 2>$null | Out-Null
    & $VENV_PIP uninstall nanobot_ai -y 2>$null | Out-Null

    # 2. 清除 site-packages 中可能残留的损坏文件
    if (Test-Path $SITE_PACKAGES) {
        Get-ChildItem -Path $SITE_PACKAGES -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "nanobot" -or $_.Name -like "nanobot_ai*" -or $_.Name -like "~anobot*" } |
            ForEach-Object {
                Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
                Write-Host "$LAUNCHER_TAG 已清除: $($_.Name)" -ForegroundColor DarkGray
            }
    }

    # 3. 移除可能损坏的可执行文件
    if (Test-Path $NANOBOT_EXE) {
        Remove-Item -Path $NANOBOT_EXE -Force -ErrorAction SilentlyContinue
    }

    # 4. 重新安装
    if (-not (Test-Path (Join-Path $REPO_DIR "pyproject.toml"))) {
        Write-Host "$LAUNCHER_TAG 未找到 pyproject.toml，无法修复。" -ForegroundColor Red
        exit 1
    }
    Write-Host "$LAUNCHER_TAG Running: pip install -e . (in $REPO_DIR)" -ForegroundColor DarkGray
    Push-Location $REPO_DIR
    & $VENV_PIP install -e . 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    $pipExit = $LASTEXITCODE
    Pop-Location
    if ($pipExit -ne 0) {
        Write-Host "$LAUNCHER_TAG 修复失败（pip exit $pipExit）。" -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path $NANOBOT_EXE)) {
        Write-Host "$LAUNCHER_TAG 修复完成但未找到可执行文件: $NANOBOT_EXE" -ForegroundColor Red
        exit 1
    }
    Write-Host "$LAUNCHER_TAG nanobot 安装已修复。" -ForegroundColor Green
    Write-Host ""
}

function Ensure-FrontendBuilt {
    if (-not (Test-Path $WEB_UI_DIR)) {
        Write-Host "$LAUNCHER_TAG 未找到 web-ui 目录，跳过前端构建。" -ForegroundColor Yellow
        return
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host "$LAUNCHER_TAG 未找到 npm，跳过前端构建（请安装 Node.js）。" -ForegroundColor Yellow
        return
    }

    # npm install —— 每次构建前先执行，确保依赖完整（避免 node_modules 不完整导致 build 失败）
    Write-Host "$LAUNCHER_TAG 正在安装前端依赖 (npm install)..." -ForegroundColor Yellow
    Push-Location $WEB_UI_DIR
    & npm install 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    $npmExit = $LASTEXITCODE
    Pop-Location
    if ($npmExit -ne 0) {
        Write-Host "$LAUNCHER_TAG npm install 失败（exit $npmExit）。" -ForegroundColor Red
        exit 1
    }
    Write-Host "$LAUNCHER_TAG npm install 完成。" -ForegroundColor Green

    # npm run build —— 每次都执行，确保源码改动即时生效
    Write-Host "$LAUNCHER_TAG 正在构建前端 (npm run build)..." -ForegroundColor Yellow
    Push-Location $WEB_UI_DIR
    & npm run build 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    $buildExit = $LASTEXITCODE
    Pop-Location
    if ($buildExit -ne 0) {
        Write-Host "$LAUNCHER_TAG npm run build 失败（exit $buildExit）。" -ForegroundColor Red
        exit 1
    }
    Write-Host "$LAUNCHER_TAG 前端构建完成。" -ForegroundColor Green
    Write-Host ""
}

function Ensure-VenvReady {
    # 1. 如果 venv 不存在则创建
    if (-not (Test-Path $VENV_DIR)) {
        Write-Host "$LAUNCHER_TAG 正在创建虚拟环境: $VENV_DIR" -ForegroundColor Yellow
        & python -m venv $VENV_DIR
        if ($LASTEXITCODE -ne 0) {
            Write-Host "$LAUNCHER_TAG 虚拟环境创建失败，请确认 Python 已正确安装。" -ForegroundColor Red
            exit 1
        }
        Write-Host "$LAUNCHER_TAG 虚拟环境创建成功。" -ForegroundColor Green
    }

    # 2. 构建前端（npm install + npm run build）
    Ensure-FrontendBuilt

    # 3. 检查 nanobot 是否已安装且可用
    if (-not (Test-Path $NANOBOT_EXE)) {
        # 未安装 -> 执行安装
        if (-not (Test-Path (Join-Path $REPO_DIR "pyproject.toml"))) {
            Write-Host "$LAUNCHER_TAG 未找到 pyproject.toml（路径：$REPO_DIR），无法自动安装。" -ForegroundColor Red
            exit 1
        }
        Write-Host "$LAUNCHER_TAG 正在安装 nanobot 到虚拟环境..." -ForegroundColor Yellow
        Write-Host "$LAUNCHER_TAG Running: pip install -e . (in $REPO_DIR)" -ForegroundColor DarkGray
        Push-Location $REPO_DIR
        & $VENV_PIP install -e . 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        $pipExit = $LASTEXITCODE
        Pop-Location
        if ($pipExit -ne 0) {
            Write-Host "$LAUNCHER_TAG 安装失败（exit $pipExit）。" -ForegroundColor Red
            exit 1
        }
        if (-not (Test-Path $NANOBOT_EXE)) {
            Write-Host "$LAUNCHER_TAG 安装完成但未找到可执行文件: $NANOBOT_EXE" -ForegroundColor Red
            exit 1
        }
        Write-Host "$LAUNCHER_TAG nanobot 安装成功。" -ForegroundColor Green
        Write-Host ""
    }
    elseif (-not (Test-NanobotHealth)) {
        # 已安装但运行失败（如 ModuleNotFoundError）-> 执行修复
        Write-Host "$LAUNCHER_TAG 检测到 nanobot 安装损坏，正在修复..." -ForegroundColor Yellow
        Repair-NanobotInstall
    }
}

Write-Banner
Write-Host "$LAUNCHER_TAG Repo:   $REPO_DIR" -ForegroundColor DarkGray
Write-Host "$LAUNCHER_TAG Venv:   $VENV_DIR" -ForegroundColor DarkGray
Write-Host "$LAUNCHER_TAG Debug:  $($EnableDebug.IsPresent)" -ForegroundColor DarkGray
Write-Host ""

# 终止已存在的 nanobot 进程
Stop-ExistingNanobot

Ensure-VenvReady

while ($true) {
    $nanobotArgs = Get-NanobotArgs
    Write-Host "$LAUNCHER_TAG Starting: $NANOBOT_EXE $($nanobotArgs -join ' ')" -ForegroundColor Green
    Write-Host "$LAUNCHER_TAG Restart exit code: $RESTART_EXIT_CODE | Ctrl+C to stop" -ForegroundColor DarkGray
    Write-Host ""

    # 直接调用而非 Start-Process，$LASTEXITCODE 可可靠捕获 os._exit() 的退出码
    & $NANOBOT_EXE @nanobotArgs
    $exitCode = $LASTEXITCODE

    Write-Host ""
    Write-Host "$LAUNCHER_TAG nanobot exited with code: $exitCode" -ForegroundColor Yellow

    if ($exitCode -eq $RESTART_EXIT_CODE) {
        # Rapid restart protection
        $now = Get-Date
        $restartTimestamps = @($restartTimestamps | Where-Object { ($now - $_).TotalSeconds -lt $RAPID_RESTART_WINDOW })
        $restartTimestamps += $now

        if ($restartTimestamps.Count -ge $MAX_RAPID_RESTARTS) {
            Write-Host "$LAUNCHER_TAG Too many rapid restarts ($MAX_RAPID_RESTARTS in ${RAPID_RESTART_WINDOW}s). Exiting." -ForegroundColor Red
            exit 1
        }

        Write-Host "$LAUNCHER_TAG Self-update restart requested. Pulling & reinstalling..." -ForegroundColor Cyan

        if (Test-Path (Join-Path $REPO_DIR "pyproject.toml")) {
            Push-Location $REPO_DIR

            # 拉取远端最新代码（如已在 nanobot 内部 pull 则为 no-op，无害）
            Write-Host "$LAUNCHER_TAG Running: git pull (in $REPO_DIR)" -ForegroundColor DarkGray
            $gitOut = & git pull 2>&1
            $gitOut | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
            if ($LASTEXITCODE -ne 0) {
                Write-Host "$LAUNCHER_TAG Warning: git pull failed (exit $LASTEXITCODE), continuing anyway..." -ForegroundColor Yellow
            }

            # 重建前端
            Write-Host "$LAUNCHER_TAG Running: npm install + npm run build (in $WEB_UI_DIR)" -ForegroundColor DarkGray
            Ensure-FrontendBuilt

            # 重新安装依赖（使用 venv 的 pip）
            Write-Host "$LAUNCHER_TAG Running: pip install -e . (in $REPO_DIR, venv)" -ForegroundColor DarkGray
            & $VENV_PIP install -e . --quiet 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

            Pop-Location
        }

        Write-Host "$LAUNCHER_TAG Restarting in 2 seconds..." -ForegroundColor Cyan
        Start-Sleep -Seconds 2
        continue
    }
    else {
        Write-Host "$LAUNCHER_TAG Normal exit. Goodbye." -ForegroundColor Green
        exit $exitCode
    }
}
