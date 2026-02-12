@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: 切换到脚本所在目录（项目根目录）
cd /d "%~dp0"

echo ========================================
echo   nanobot-webui 快速启动
echo ========================================
echo.

:: 1. 检查 Python 环境
echo [1/4] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.11 或更高版本
    pause
    exit /b 1
)
python --version
echo.

:: 2. 加载 Python 依赖
echo [2/4] 安装/更新 Python 依赖...
pip install -e .
if errorlevel 1 (
    echo [错误] Python 依赖安装失败
    pause
    exit /b 1
)
echo.

:: 3. 构建 web-ui 前端
echo [3/4] 构建 web-ui 前端...
where node >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Node.js，请先安装 Node.js 和 npm
    pause
    exit /b 1
)
cd web-ui
call npm install
if errorlevel 1 (
    echo [错误] npm install 失败
    cd ..
    pause
    exit /b 1
)
call npm run build
if errorlevel 1 (
    echo [错误] npm run build 失败
    cd ..
    pause
    exit /b 1
)
cd ..
echo.

:: 4. 启动 nanobot web-ui
echo [4/4] 启动 nanobot web-ui...
echo.
echo 服务地址: http://127.0.0.1:6788
echo 按 Ctrl+C 可停止服务
echo ========================================
python -m nanobot web-ui

pause
