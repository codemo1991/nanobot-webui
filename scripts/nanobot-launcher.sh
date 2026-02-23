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
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --host) HOST="$2"; shift 2 ;;
        --port|-p) PORT="$2"; shift 2 ;;
        --verbose|-v) VERBOSE=true; shift ;;
        --no-verbose|-q) VERBOSE=false; shift ;;
        --debug|-d) EXTRA_ARGS+=("--debug"); VERBOSE=true; shift ;;
        *) EXTRA_ARGS+=("$1"); shift ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

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
echo "     Repo:     $REPO_DIR"
echo "     Python:   $(which python)"
echo "     Python Version: $(python --version 2>&1)"
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

    # 显示额外参数（如果有）
    EXTRA_DISPLAY=""
    if [ ${#EXTRA_ARGS[@]} -gt 0 ]; then
        EXTRA_DISPLAY=" ${EXTRA_ARGS[*]:-}"
    fi

while true; do
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[launcher] [$TIMESTAMP] Starting: nanobot web-ui --host $HOST --port $PORT$EXTRA_DISPLAY"
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

    nanobot "${ARGS[@]}"
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

            echo "[launcher] Running: pip install -e . (in $REPO_DIR)"
            # 使用 --no-deps 加速，主要目的是让 Python 识别代码变更
            (cd "$REPO_DIR" && pip install -e . --no-deps --quiet 2>&1 | sed 's/^/  /') || \
            (cd "$REPO_DIR" && pip install -e . --quiet 2>&1 | sed 's/^/  /')
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
