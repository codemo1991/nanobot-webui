#!/usr/bin/env bash
#
# nanobot 守护启动器 — 支持自更新后自动重启。
#
# 用法:
#   ./nanobot-launcher.sh [--host HOST] [--port PORT] [--verbose] [--debug]
#
# 当 nanobot 以退出码 42 退出时（self_update 触发），本脚本会自动
# 执行 git pull 及 pip install -e . 并重新启动服务。

set -euo pipefail

RESTART_EXIT_CODE=42
MAX_RAPID_RESTARTS=5
RAPID_RESTART_WINDOW=60

HOST="127.0.0.1"
PORT=6788
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --host) HOST="$2"; shift 2 ;;
        --port|-p) PORT="$2"; shift 2 ;;
        --verbose|-v) EXTRA_ARGS+=("--verbose"); shift ;;
        --debug|-d) EXTRA_ARGS+=("--debug"); shift ;;
        *) EXTRA_ARGS+=("$1"); shift ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

restart_times=()

echo ""
echo "  ================================"
echo "   nanobot launcher (guardian mode)"
echo "  ================================"
echo ""

while true; do
    echo "[launcher] Starting: nanobot web-ui --host $HOST --port $PORT ${EXTRA_ARGS[*]:-}"
    echo "[launcher] Restart exit code: $RESTART_EXIT_CODE | Ctrl+C to stop"
    echo ""

    set +e
    if [ ${#EXTRA_ARGS[@]} -eq 0 ]; then
        nanobot web-ui --host "$HOST" --port "$PORT"
    else
        nanobot web-ui --host "$HOST" --port "$PORT" "${EXTRA_ARGS[@]}"
    fi
    exit_code=$?
    set -e

    echo ""
    echo "[launcher] nanobot exited with code: $exit_code"

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

        echo "[launcher] Restarting in 2 seconds..."
        sleep 2
        continue
    else
        echo "[launcher] Normal exit. Goodbye."
        exit "$exit_code"
    fi
done
