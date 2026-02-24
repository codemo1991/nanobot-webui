#!/usr/bin/env bash
#
# nanobot 守护启动器 — 支持自更新后自动重启。
#
# 用法:
#   ./nanobot-launcher.sh [--host HOST] [--port PORT] [--verbose] [--debug]
#
# 当 nanobot 以退出码 42 退出时（self_update 触发），本脚本会自动
# 执行 git pull 及 pip install -e . 并重新启动服务。
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
WEB_UI_DIR="$REPO_DIR/web-ui"

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

# 构建前端（npm install + npm run build）
# 参数: force=true 时强制重新安装/构建，不论目录是否存在
ensure_frontend_built() {
    local force="${1:-false}"

    if [ ! -d "$WEB_UI_DIR" ]; then
        echo "[launcher] 未找到 web-ui 目录，跳过前端构建。"
        return 0
    fi
    if ! command -v npm &>/dev/null; then
        echo "[launcher] 未找到 npm，跳过前端构建（请安装 Node.js）。"
        return 0
    fi

    # npm install —— 仅在 node_modules 缺失时执行（依赖安装耗时，package.json 不常变）
    if [ "$force" = "true" ] || [ ! -d "$WEB_UI_DIR/node_modules" ]; then
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
    fi

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

    # 3. 如果 nanobot 尚未安装到 venv，则安装
    if [ ! -f "$NANOBOT_EXE" ]; then
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
            ensure_frontend_built "true"

            echo "[launcher] Running: pip install -e . (in $REPO_DIR, venv)"
            # 使用 --no-deps 加速，主要目的是让 Python 识别代码变更
            (cd "$REPO_DIR" && "$VENV_PIP" install -e . --no-deps --quiet 2>&1 | sed 's/^/  /') || \
            (cd "$REPO_DIR" && "$VENV_PIP" install -e . --quiet 2>&1 | sed 's/^/  /')
            echo "[launcher] pip install done (exit: $?)"
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
