#!/usr/bin/env bash
#
# nanobot 守护启动器 — 支持自更新后自动重启，并能自动修复损坏的安装。
#
# 用法:
#   ./nanobot-launcher.sh [--host HOST] [--port PORT] [--verbose] [--debug]
#
# 当 nanobot 以退出码 42 退出时（self_update 触发），本脚本会自动
# 执行 git pull 及 pip install -e . 并重新启动服务。
# 启动时会检测 nanobot 安装是否可用；若检测到损坏（如 ModuleNotFoundError），
# 会自动清除残留并重新执行 pip install -e . 进行修复。
#
# 默认启用 --verbose 以输出详细日志

set -euo pipefail

RESTART_EXIT_CODE=42
MAX_RAPID_RESTARTS=5
RAPID_RESTART_WINDOW=60

HOST="127.0.0.1"
PORT=6788
# 默认启用 verbose 模式
VERBOSE=true
DEBUG_MODE=false
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            if [[ "${2:-}" == -* ]] || [[ -z "${2:-}" ]]; then
                echo "[launcher] Warning: --host 需要传值，已恢复默认 127.0.0.1，并启用 debug"
                HOST="127.0.0.1"
                DEBUG_MODE=true
                EXTRA_ARGS+=("--debug")
                shift 1
            else
                HOST="$2"
                shift 2
            fi
            ;;
        --port|-p) PORT="$2"; shift 2 ;;
        --verbose|-v) VERBOSE=true; shift ;;
        --no-verbose|-q) VERBOSE=false; shift ;;
        --debug|-d) EXTRA_ARGS+=("--debug"); VERBOSE=true; DEBUG_MODE=true; shift ;;
        *) EXTRA_ARGS+=("$1"); shift ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# 路径常量：venv 建在仓库根目录下
VENV_DIR="$REPO_DIR/.venv"
NANOBOT_EXE="$VENV_DIR/bin/nanobot"
VENV_PIP="$VENV_DIR/bin/pip"
VENV_PYTHON="$VENV_DIR/bin/python"
WEB_UI_DIR="$REPO_DIR/web-ui"

# 检测并终止已运行的 nanobot 进程
kill_existing_nanobot() {
    # 查找正在运行的 nanobot 进程（排除当前脚本进程）
    # 使用 pgrep 或 ps 来查找进程
    local pid=""
    if command -v pgrep &>/dev/null; then
        # pgrep 返回匹配的 PID 列表，使用 || true 确保即使没找到进程也不会失败
        pid=$(pgrep -f "nanobot.*web-ui" 2>/dev/null | head -1) || true
    elif command -v ps &>/dev/null; then
        # 使用 ps 查找
        pid=$(ps aux 2>/dev/null | grep -E "nanobot.*web-ui" | grep -v grep | awk '{print $2}' | head -1)
    fi

    if [ -n "$pid" ]; then
        echo "[launcher] 检测到已有 nanobot 进程运行中 (PID: $pid)，正在终止..."
        kill "$pid" 2>/dev/null || true
        # 等待进程终止
        local count=0
        while [ $count -lt 10 ]; do
            if ! kill -0 "$pid" 2>/dev/null; then
                echo "[launcher] 已终止旧进程 (PID: $pid)"
                return 0
            fi
            sleep 0.5
            count=$((count + 1))
        done
        # 如果进程仍未终止，强制杀死
        if kill -0 "$pid" 2>/dev/null; then
            echo "[launcher] 进程未响应，强制杀死 (PID: $pid)"
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi
    return 0
}

restart_times=()

# 打印分隔线
print_separator() {
    echo "============================================================================"
}

print_separator
echo "  🐈 Nanobot Launcher (Guardian Mode)"
print_separator
echo ""
echo "  📋 Configuration:"
echo "     Host:     $HOST"
echo "     Port:     $PORT"
echo "     Verbose:  $VERBOSE"
echo "     Debug:    $DEBUG_MODE"
echo "     Repo:     $REPO_DIR"
echo "     Venv:     $VENV_DIR"
echo "     Python:   $(which python3 2>/dev/null || which python)"
echo "     Python Version: $(python3 --version 2>/dev/null || python --version 2>&1)"
echo ""

# 打印当前 git 状态
if [ -d "$REPO_DIR/.git" ]; then
    echo "  � Git Status:"
    cd "$REPO_DIR"
    GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    echo "     Branch:   $GIT_BRANCH"
    echo "     Commit:   $GIT_COMMIT"
    echo ""
fi

print_separator
echo ""

# 终止已存在的 nanobot 进程
kill_existing_nanobot

# 构建前端（npm install + npm run build）
ensure_frontend_built() {
    if [ ! -d "$WEB_UI_DIR" ]; then
        echo "[launcher] 未找到 web-ui 目录，跳过前端构建。"
        return 0
    fi
    if ! command -v npm &>/dev/null; then
        echo "[launcher] 未找到 npm，跳过前端构建（请安装 Node.js）。"
        return 0
    fi

    # npm install —— 每次构建前先执行，确保依赖完整（避免 node_modules 不完整导致 build 失败）
    echo "[launcher] 正在安装前端依赖 (npm install)..."
    set +e
    (cd "$WEB_UI_DIR" && npm install 2>&1 | sed 's/^/  /')
    npm_exit=$?
    set -e
    if [ "$npm_exit" -ne 0 ]; then
        echo "[launcher] npm install 失败（exit $npm_exit），请检查 Node.js 是否已安装。"
        exit 1
    fi
    echo "[launcher] npm install 完成。"

    # npm run build —— 每次都执行，确保源码改动即时生效
    echo "[launcher] 正在构建前端 (npm run build)..."
    set +e
    (cd "$WEB_UI_DIR" && npm run build 2>&1 | sed 's/^/  /')
    build_exit=$?
    set -e
    if [ "$build_exit" -ne 0 ]; then
        echo "[launcher] npm run build 失败（exit $build_exit）。"
        exit 1
    fi
    echo "[launcher] 前端构建完成。"
    echo ""
}

# 检测 nanobot 安装是否可用（可执行且能正常导入模块）
test_nanobot_health() {
    "$NANOBOT_EXE" --help &>/dev/null
}

# Web UI 必需依赖（与 pyproject.toml 一致）；仅 --help 通过时仍可能缺包
test_web_stack_imports() {
    "$VENV_PYTHON" -c "import fastapi, uvicorn" &>/dev/null
}

sync_project_editable_install() {
    if [ ! -f "$REPO_DIR/pyproject.toml" ]; then
        echo "[launcher] 未找到 pyproject.toml，无法同步依赖。"
        exit 1
    fi
    echo "[launcher] 正在根据 pyproject.toml 同步依赖: pip install -e ."
    if (cd "$REPO_DIR" && "$VENV_PIP" install -e . 2>&1 | sed 's/^/  /'); then
        if ! test_web_stack_imports; then
            echo "[launcher] 同步后仍无法 import fastapi/uvicorn，请检查 pyproject.toml 与网络。"
            exit 1
        fi
        echo "[launcher] Python 依赖已与项目声明对齐。"
        echo ""
    fi
}

# 修复损坏的 nanobot 安装：清除残留后重新安装
repair_nanobot_install() {
    echo "[launcher] 正在修复 nanobot 安装..."

    # 1. 尝试卸载（可能因损坏而失败，忽略）
    "$VENV_PIP" uninstall nanobot-ai -y &>/dev/null || true
    "$VENV_PIP" uninstall nanobot_ai -y &>/dev/null || true

    # 2. 清除 site-packages 中可能残留的损坏文件
    SITE_PACKAGES=$("$VENV_PYTHON" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null) || true
    if [ -n "$SITE_PACKAGES" ] && [ -d "$SITE_PACKAGES" ]; then
        for d in "$SITE_PACKAGES"/nanobot "$SITE_PACKAGES"/nanobot_ai* "$SITE_PACKAGES"/~anobot*; do
            [ -e "$d" ] || [ -d "$d" ] 2>/dev/null || continue
            rm -rf "$d" 2>/dev/null || true
            echo "[launcher] 已清除: $(basename "$d" 2>/dev/null || echo "$d")"
        done
    fi

    # 3. 移除可能损坏的可执行文件
    rm -f "$NANOBOT_EXE" 2>/dev/null || true

    # 4. 重新安装
    if [ ! -f "$REPO_DIR/pyproject.toml" ]; then
        echo "[launcher] 未找到 pyproject.toml，无法修复。"
        exit 1
    fi
    echo "[launcher] Running: pip install -e . (in $REPO_DIR)"
    if (cd "$REPO_DIR" && "$VENV_PIP" install -e . 2>&1 | sed 's/^/  /'); then
        if [ ! -f "$NANOBOT_EXE" ]; then
            echo "[launcher] 修复完成但未找到可执行文件: $NANOBOT_EXE"
            exit 1
        fi
        echo "[launcher] nanobot 安装已修复。"
        echo ""
    else
        echo "[launcher] 修复失败。"
        exit 1
    fi
}

# 确保虚拟环境存在且 nanobot 已安装
ensure_venv_ready() {
    # 1. 如果 venv 不存在则创建
    if [ ! -d "$VENV_DIR" ]; then
        echo "[launcher] 正在创建虚拟环境: $VENV_DIR"
        PYTHON_BIN=$(which python3 2>/dev/null || which python)
        "$PYTHON_BIN" -m venv "$VENV_DIR"
        if [ $? -ne 0 ]; then
            echo "[launcher] 虚拟环境创建失败，请确认 Python 已正确安装。"
            exit 1
        fi
        echo "[launcher] 虚拟环境创建成功。"
    fi

    # 2. 构建前端（npm install + npm run build）
    ensure_frontend_built

    # 3. 检查 nanobot 是否已安装且可用
    if [ ! -f "$NANOBOT_EXE" ]; then
        # 未安装 -> 执行安装
        if [ ! -f "$REPO_DIR/pyproject.toml" ]; then
            echo "[launcher] 未找到 pyproject.toml（路径：$REPO_DIR），无法自动安装。"
            exit 1
        fi
        echo "[launcher] 正在安装 nanobot 到虚拟环境..."
        echo "[launcher] Running: pip install -e . (in $REPO_DIR)"
        if (cd "$REPO_DIR" && "$VENV_PIP" install -e . 2>&1 | sed 's/^/  /'); then
            if [ ! -f "$NANOBOT_EXE" ]; then
                echo "[launcher] 安装完成但未找到可执行文件: $NANOBOT_EXE"
                exit 1
            fi
            echo "[launcher] nanobot 安装成功。"
            echo ""
        else
            echo "[launcher] 安装失败，请手动执行: cd $REPO_DIR && $VENV_PIP install -e ."
            exit 1
        fi
    elif ! test_nanobot_health; then
        # 已安装但运行失败（如 ModuleNotFoundError）-> 执行修复
        echo "[launcher] 检测到 nanobot 安装损坏，正在修复..."
        repair_nanobot_install
    elif ! test_web_stack_imports; then
        echo "[launcher] 检测到 Web UI 依赖缺失（如 fastapi），与 pyproject 不同步。"
        sync_project_editable_install
    fi
}

ensure_venv_ready

# 显示额外参数（如果有）
EXTRA_DISPLAY=""
if [ ${#EXTRA_ARGS[@]} -gt 0 ]; then
    EXTRA_DISPLAY=" ${EXTRA_ARGS[*]:-}"
fi

while true; do
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[launcher] [$TIMESTAMP] Starting: $NANOBOT_EXE web-ui --host $HOST --port $PORT$EXTRA_DISPLAY"
    echo "[launcher] [$TIMESTAMP] Restart exit code: $RESTART_EXIT_CODE | Ctrl+C to stop"
    echo ""

    # 根据 VERBOSE 决定是否添加 --verbose
    set +e
    ARGS=("web-ui" "--host" "$HOST" "--port" "$PORT")
    if [ "$VERBOSE" = true ]; then
        ARGS+=("--verbose")
    fi
    if [ ${#EXTRA_ARGS[@]} -gt 0 ]; then
        ARGS+=("${EXTRA_ARGS[@]}")
    fi

    "$NANOBOT_EXE" "${ARGS[@]}"
    exit_code=$?
    set -e

    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo ""
    echo "[launcher] [$TIMESTAMP] nanobot exited with code: $exit_code"

    if [ "$exit_code" -eq "$RESTART_EXIT_CODE" ]; then
        now=$(date +%s)
        # Filter timestamps within the window
        filtered=()
        for ts in "${restart_times[@]:-}"; do
            if [ -n "$ts" ] && [ $((now - ts)) -lt $RAPID_RESTART_WINDOW ]; then
                filtered+=("$ts")
            fi
        done
        filtered+=("$now")
        restart_times=("${filtered[@]}")

        if [ ${#restart_times[@]} -ge $MAX_RAPID_RESTARTS ]; then
            echo "[launcher] Too many rapid restarts ($MAX_RAPID_RESTARTS in ${RAPID_RESTART_WINDOW}s). Exiting."
            exit 1
        fi

        echo "[launcher] Self-update restart requested. Pulling & reinstalling..."
        print_separator

        if [ -f "$REPO_DIR/pyproject.toml" ]; then
            echo "[launcher] Running: git pull (in $REPO_DIR)"
            set +e
            (cd "$REPO_DIR" && git pull 2>&1 | sed 's/^/  /')
            git_exit=$?
            set -e
            if [ "$git_exit" -ne 0 ]; then
                echo "[launcher] Warning: git pull failed (exit $git_exit), continuing anyway..."
            fi

            echo "[launcher] Running: npm install + npm run build (in $WEB_UI_DIR)"
            ensure_frontend_built

            echo "[launcher] Running: pip install -e . (in $REPO_DIR, venv)"
            # 完整 editable 安装，确保 pyproject 新增依赖（如 fastapi）被装上；勿默认 --no-deps
            if (cd "$REPO_DIR" && "$VENV_PIP" install -e . --quiet 2>&1 | sed 's/^/  /'); then
                echo "[launcher] pip install done (ok)"
            else
                echo "[launcher] pip install failed"
                exit 1
            fi
        fi

        print_separator
        echo "[launcher] Restarting in 2 seconds..."
        sleep 2
        continue
    else
        echo "[launcher] Normal exit. Goodbye."
        exit "$exit_code"
    fi
done
