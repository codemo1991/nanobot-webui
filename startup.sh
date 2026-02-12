#!/bin/bash
set -e

# 切换到脚本所在目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  nanobot-webui 快速启动"
echo "========================================"
echo

# 1. 检查 Python 环境
echo "[1/4] 检查 Python 环境..."
if command -v python3 &>/dev/null; then
    PYTHON_CMD=python3
    PIP_CMD=pip3
elif command -v python &>/dev/null; then
    PYTHON_CMD=python
    PIP_CMD=pip
else
    echo "[错误] 未找到 Python，请先安装 Python 3.11 或更高版本"
    exit 1
fi
$PYTHON_CMD --version
echo

# 2. 加载 Python 依赖
echo "[2/4] 安装/更新 Python 依赖..."
$PIP_CMD install -e .
echo

# 3. 构建 web-ui 前端
echo "[3/4] 构建 web-ui 前端..."
if ! command -v node &>/dev/null || ! command -v npm &>/dev/null; then
    echo "[错误] 未找到 Node.js 或 npm，请先安装 Node.js"
    exit 1
fi
cd web-ui
npm install
npm run build
cd ..
echo

# 4. 启动 nanobot web-ui
echo "[4/4] 启动 nanobot web-ui..."
echo
echo "服务地址: http://127.0.0.1:6788"
echo "按 Ctrl+C 可停止服务"
echo "========================================"
$PYTHON_CMD -m nanobot web-ui
