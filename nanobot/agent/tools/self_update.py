"""Self-update tool: git operations + graceful restart for self-evolution."""

import asyncio
import os
import sys
import threading
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool

RESTART_EXIT_CODE = 42


class SelfUpdateTool(Tool):
    """
    Tool for nanobot to update its own codebase and restart.

    Supports three actions:
    - commit_and_push: stage, commit, and push changes to GitHub
    - restart: gracefully shutdown and signal the launcher to restart
    - evolve: full pipeline — commit, push, reinstall, restart
    """

    def __init__(self, workspace: str | None = None):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "self_update"

    @property
    def description(self) -> str:
        return """Manage nanobot's own code updates and service restarts.

Actions:
- commit_and_push: Git add, commit, and push changes to the remote repository.
- pull: Git pull latest changes from the remote repository.
- pull_and_push: Git pull then push existing commits to the remote repository (sync).
- restart: Gracefully restart the nanobot service (requires launcher wrapper).
- evolve: Full self-evolution pipeline — commit, push, pip install, then restart.
- pull_and_restart: Pull latest code from remote, pip install, then restart. Use this
  when remote repository has been updated and you want to apply changes.

IMPORTANT:
- Use 'commit_and_push' after Claude Code finishes modifying nanobot's own code.
- Use 'pull_and_push' to sync with remote (pull first to avoid conflicts, then push).
- Use 'pull' to fetch and apply remote updates without restarting.
- Use 'pull_and_restart' to fetch remote updates and restart to apply them.
- Use 'restart' only when code changes require a process restart to take effect.
- Use 'evolve' for the complete self-improvement cycle (local edits → push → restart).
- The restart action will terminate the current process. Make sure to inform the
  user before calling it."""

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["commit_and_push", "pull", "pull_and_push", "restart", "evolve", "pull_and_restart"],
                    "description": "The action to perform."
                },
                "commit_message": {
                    "type": "string",
                    "description": "Git commit message. Required for 'commit_and_push' and 'evolve' actions."
                },
                "branch": {
                    "type": "string",
                    "description": "Git branch to push to. Defaults to current branch."
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        commit_message: str | None = None,
        branch: str | None = None,
        **kwargs: Any,
    ) -> str:
        repo_dir = self._find_repo_dir()
        if not repo_dir:
            return "Error: 无法定位 nanobot 源码仓库目录。self_update 仅在开发模式（pip install -e .）下可用。"

        if action == "commit_and_push":
            return await self._commit_and_push(repo_dir, commit_message, branch)
        elif action == "pull":
            return await self._git_pull(repo_dir)
        elif action == "pull_and_push":
            return await self._pull_and_push(repo_dir, branch)
        elif action == "restart":
            return await self._restart()
        elif action == "evolve":
            return await self._evolve(repo_dir, commit_message, branch)
        elif action == "pull_and_restart":
            return await self._pull_and_restart(repo_dir)
        else:
            return f"Error: 未知 action '{action}'。可选: commit_and_push, pull, pull_and_push, restart, evolve, pull_and_restart"

    def _find_repo_dir(self) -> Path | None:
        """定位 nanobot 源码仓库根目录。"""
        # 方式1: 从当前模块路径向上找 .git
        module_dir = Path(__file__).resolve().parent.parent.parent  # nanobot/agent/tools -> nanobot root
        for candidate in [module_dir, module_dir.parent]:
            if (candidate / ".git").exists() and (candidate / "pyproject.toml").exists():
                return candidate

        # 方式2: 从工作空间路径查找
        if self._workspace:
            ws = Path(self._workspace)
            if (ws / ".git").exists() and (ws / "pyproject.toml").exists():
                return ws

        return None

    async def _run_cmd(self, cmd: str, cwd: Path, timeout: int = 60) -> tuple[int, str]:
        """执行 shell 命令并返回 (exit_code, output)。"""
        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                err_text = stderr.decode("utf-8", errors="replace").strip()
                if err_text:
                    output += f"\n{err_text}"
            return process.returncode or 0, output.strip()
        except asyncio.TimeoutError:
            process.kill()
            return -1, f"命令超时（{timeout}s）: {cmd}"
        except Exception as e:
            return -1, f"执行失败: {e}"

    async def _commit_and_push(
        self, repo_dir: Path, commit_message: str | None, branch: str | None
    ) -> str:
        if not commit_message:
            return "Error: commit_and_push 需要提供 commit_message 参数。"

        results = []

        # 1. git status
        code, status_out = await self._run_cmd("git status --porcelain", repo_dir)
        if code != 0:
            return f"Error: git status 失败: {status_out}"
        if not status_out.strip():
            return "没有需要提交的更改。工作区是干净的。"
        results.append(f"变更文件:\n{status_out}")

        # 2. git add
        code, out = await self._run_cmd("git add -A", repo_dir)
        if code != 0:
            return f"Error: git add 失败: {out}"

        # 3. git commit
        safe_msg = commit_message.replace('"', '\\"')
        code, out = await self._run_cmd(f'git commit -m "{safe_msg}"', repo_dir)
        if code != 0:
            return f"Error: git commit 失败: {out}"
        results.append(f"提交成功: {out}")

        # 4. git push
        branch_arg = branch or ""
        push_cmd = f"git push origin {branch_arg}".strip() if branch_arg else "git push"
        code, out = await self._run_cmd(push_cmd, repo_dir, timeout=120)
        if code != 0:
            return f"Error: git push 失败: {out}"
        results.append(f"推送成功: {out}")

        return "\n\n".join(results)

    async def _git_pull(self, repo_dir: Path) -> str:
        """从远端拉取最新代码。"""
        code, out = await self._run_cmd("git pull", repo_dir, timeout=120)
        if code != 0:
            return f"Error: git pull 失败: {out}"
        return f"git pull 成功:\n{out}"

    async def _pull_and_push(self, repo_dir: Path, branch: str | None = None) -> str:
        """先 git pull，再 git push，同步本地提交到远端。"""
        results = []

        # Step 1: git pull
        pull_result = await self._git_pull(repo_dir)
        if pull_result.startswith("Error:"):
            return pull_result
        results.append(f"[1/2 Git Pull]\n{pull_result}")

        # Step 2: git push
        branch_arg = branch or ""
        push_cmd = f"git push origin {branch_arg}".strip() if branch_arg else "git push"
        code, out = await self._run_cmd(push_cmd, repo_dir, timeout=120)
        if code != 0:
            return "\n\n".join(results) + f"\n\nError: git push 失败: {out}"
        results.append(f"[2/2 Git Push]\n{out}")

        return "\n\n".join(results)

    async def _pull_and_restart(self, repo_dir: Path) -> str:
        """从远端拉取最新代码，重新安装依赖，然后触发重启。"""
        results = []

        # Step 1: git pull
        pull_result = await self._git_pull(repo_dir)
        if pull_result.startswith("Error:"):
            return pull_result
        results.append(f"[1/3 Git Pull]\n{pull_result}")

        # Step 2: pip install
        install_result = await self._pip_install(repo_dir)
        if install_result.startswith("Error:"):
            results.append(f"[2/3 Pip Install] {install_result}")
            return "\n\n".join(results) + "\n\n已拉取代码但依赖安装失败，跳过重启。"
        results.append(f"[2/3 Pip Install]\n{install_result}")

        # Step 3: restart
        restart_result = await self._restart()
        results.append(f"[3/3 Restart]\n{restart_result}")

        return "\n\n".join(results)

    async def _pip_install(self, repo_dir: Path) -> str:
        """在仓库目录执行 pip install -e . 更新安装。"""
        python = sys.executable
        code, out = await self._run_cmd(
            f'"{python}" -m pip install -e . --quiet', repo_dir, timeout=120
        )
        if code != 0:
            return f"Error: pip install 失败: {out}"
        return f"pip install 成功。{out}"

    async def _restart(self) -> str:
        """触发优雅重启：通知用户后以特殊退出码退出进程。"""
        logger.info(f"Self-update: 触发重启，退出码 {RESTART_EXIT_CODE}")

        # 使用 daemon 线程而非 asyncio task：asyncio.run() 结束时会取消所有 task，
        # 导致进程过早退出（响应尚未送达客户端）。线程不受 event loop 生命周期影响，
        # 能确保等到 HTTP 响应完全发送后才退出。
        def _delayed_exit() -> None:
            import time
            time.sleep(5)  # 等待 HTTP 响应完全送达客户端
            logger.info("Self-update: 正在退出进程...")
            os._exit(RESTART_EXIT_CODE)

        threading.Thread(target=_delayed_exit, daemon=True, name="restart-delay").start()
        return (
            "✅ 重启指令已收到，nanobot 服务将在 **5 秒后**重启。\n"
            "- 如果使用 nanobot-launcher 启动，服务将自动重新上线，稍后刷新页面即可继续使用。\n"
            "- 如果直接运行 `nanobot web-ui`，需要手动重新启动。"
        )

    async def _evolve(
        self, repo_dir: Path, commit_message: str | None, branch: str | None
    ) -> str:
        """完整的自进化流程: commit → push → pip install → restart。"""
        results = []

        # Step 1: Commit and push
        commit_result = await self._commit_and_push(repo_dir, commit_message, branch)
        if commit_result.startswith("Error:"):
            return commit_result
        results.append(f"[1/3 Git Push]\n{commit_result}")

        # Step 2: pip install
        install_result = await self._pip_install(repo_dir)
        if install_result.startswith("Error:"):
            results.append(f"[2/3 Pip Install] {install_result}")
            return "\n\n".join(results) + "\n\n已提交推送但安装失败，跳过重启。"
        results.append(f"[2/3 Pip Install]\n{install_result}")

        # Step 3: Restart
        restart_result = await self._restart()
        results.append(f"[3/3 Restart]\n{restart_result}")

        return "\n\n".join(results)
