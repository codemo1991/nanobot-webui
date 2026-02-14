"""Claude Code process manager."""

import asyncio
import json
import os
import platform
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.queue import MessageBus


class ClaudeCodeManager:
    """
    Manages Claude Code CLI processes.
    
    Responsibilities:
    - Start Claude Code with proper arguments
    - Track running tasks
    - Handle timeouts and cancellation
    - Generate Hook configuration
    """
    
    def __init__(
        self,
        workspace: Path,
        bus: MessageBus,
        result_dir: Path | None = None,
        default_timeout: int = 600,
        max_concurrent_tasks: int = 3,
    ):
        self.workspace = Path(workspace)
        self.bus = bus
        self.result_dir = result_dir or self.workspace / ".claude-results"
        self.default_timeout = default_timeout
        self.max_concurrent_tasks = max_concurrent_tasks
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_origins: dict[str, dict[str, str]] = {}
        self._watcher: "ResultWatcher | None" = None
        self._started = False
    
    def start_watcher(self) -> None:
        """Start the result file watcher."""
        if self._started:
            return
        
        from nanobot.claude_code.watcher import ResultWatcher
        
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self._watcher = ResultWatcher(
            result_dir=self.result_dir,
            bus=self.bus,
            on_result=self._handle_result,
        )
        self._watcher.start()
        self._started = True
        logger.info(f"Claude Code watcher started, watching {self.result_dir}")
    
    def stop_watcher(self) -> None:
        """Stop the result file watcher."""
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
        self._started = False
    
    async def start_task(
        self,
        prompt: str,
        workdir: str | None = None,
        permission_mode: str = "auto",
        agent_teams: bool = False,
        teammate_mode: str = "auto",
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        timeout: int | None = None,
    ) -> str:
        """
        Start a Claude Code task and return task ID.
        
        Args:
            prompt: The task prompt for Claude Code.
            workdir: Working directory (defaults to workspace). Must be within workspace.
            permission_mode: Permission mode (default/plan/auto/bypassPermissions).
            agent_teams: Enable Agent Teams mode.
            teammate_mode: Teammate mode (auto/in-process/tmux).
            origin_channel: Origin channel for result notification.
            origin_chat_id: Origin chat ID for result notification.
            timeout: Task timeout in seconds.
        
        Returns:
            Task ID for tracking.
        
        Raises:
            ValueError: If workdir is outside the workspace.
        """
        if len(self._running_tasks) >= self.max_concurrent_tasks:
            raise RuntimeError(f"Maximum concurrent tasks ({self.max_concurrent_tasks}) reached")
        
        if not self._started:
            self.start_watcher()
        
        task_id = str(uuid.uuid4())[:8]
        
        # Resolve workdir with workspace restriction for relative paths
        workspace_resolved = self.workspace.resolve()
        if workdir:
            workdir_path = Path(workdir)
            if workdir_path.is_absolute():
                # Absolute path: allow any location
                cwd = workdir_path.resolve()
            else:
                # Relative path: must be within workspace
                cwd = (workspace_resolved / workdir).resolve()
                
                # Security check: ensure relative path doesn't escape workspace
                try:
                    cwd.relative_to(workspace_resolved)
                except ValueError:
                    raise ValueError(
                        f"Relative path '{workdir}' resolves outside the workspace '{self.workspace}'. "
                        f"Use an absolute path if you need to access directories outside the workspace."
                    )
        else:
            cwd = workspace_resolved
        
        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
        }
        self._task_origins[task_id] = origin
        
        task_meta = {
            "task_id": task_id,
            "prompt": prompt[:200] + "..." if len(prompt) > 200 else prompt,
            "workdir": str(cwd),
            "origin": origin,
            "result_dir": str(self.result_dir),
            "timestamp": datetime.now().isoformat(),
        }
        task_meta_path = self.result_dir / f"{task_id}.meta.json"
        task_meta_path.write_text(json.dumps(task_meta, ensure_ascii=False), encoding="utf-8")
        
        bg_task = asyncio.create_task(
            self._run_claude_code(
                task_id=task_id,
                prompt=prompt,
                workdir=cwd,
                permission_mode=permission_mode,
                agent_teams=agent_teams,
                teammate_mode=teammate_mode,
                timeout=timeout or self.default_timeout,
                task_meta_path=task_meta_path,
            )
        )
        self._running_tasks[task_id] = bg_task
        bg_task.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))
        
        logger.info(f"Claude Code task [{task_id}] started: {task_meta['prompt']}")
        return task_id
    
    async def _run_claude_code(
        self,
        task_id: str,
        prompt: str,
        workdir: Path,
        permission_mode: str,
        agent_teams: bool,
        teammate_mode: str,
        timeout: int,
        task_meta_path: Path,
    ) -> None:
        """Execute Claude Code process."""
        try:
            hook_script = self._get_hook_script_path()
            hook_script = hook_script.resolve()
            
            cmd = self._build_command(
                prompt=prompt,
                workdir=workdir,
                task_id=task_id,
                permission_mode=permission_mode,
                agent_teams=agent_teams,
                teammate_mode=teammate_mode,
                hook_script=hook_script,
                task_meta_path=task_meta_path,
            )
            
            logger.debug(f"Claude Code [{task_id}] command: {' '.join(cmd)}")
            
            env = os.environ.copy()
            env["NANOBOT_TASK_ID"] = task_id
            env["NANOBOT_TASK_META"] = str(task_meta_path)
            env["NANOBOT_RESULT_DIR"] = str(self.result_dir)
            
            # On Windows, npm-installed commands are .cmd files
            # We need to use shell=True or resolve the actual path
            if platform.system() == "Windows":
                process = await asyncio.create_subprocess_shell(
                    " ".join(self._quote_args(cmd)),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workdir),
                    env=env,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workdir),
                    env=env,
                )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.warning(f"Claude Code [{task_id}] timed out after {timeout}s")
                await self._write_timeout_result(task_id, timeout)
                return
            
            if process.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
                logger.error(f"Claude Code [{task_id}] failed with code {process.returncode}: {stderr_text}")
                await self._write_error_result(task_id, process.returncode, stderr_text)
            else:
                logger.info(f"Claude Code [{task_id}] process completed successfully")
                
        except Exception as e:
            logger.exception(f"Claude Code [{task_id}] error: {e}")
            await self._write_error_result(task_id, -1, str(e))
    
    def _build_command(
        self,
        prompt: str,
        workdir: Path,
        task_id: str,
        permission_mode: str,
        agent_teams: bool,
        teammate_mode: str,
        hook_script: Path,
        task_meta_path: Path,
    ) -> list[str]:
        """Build Claude Code CLI command."""
        cmd = ["claude"]
        
        if permission_mode == "bypassPermissions":
            cmd.append("--dangerously-skip-permissions")
        elif permission_mode == "auto":
            cmd.extend(["--permission-mode", "auto"])
        elif permission_mode == "plan":
            cmd.extend(["--permission-mode", "plan"])
        
        if agent_teams:
            cmd.append("--agent-teams")
            cmd.extend(["--teammate-mode", teammate_mode])
        
        hook_settings = {
            "hooks": {
                "Stop": [{
                    "hooks": [{
                        "type": "command",
                        "command": f'python "{hook_script}"',
                        "timeout": 10
                    }]
                }],
                "SessionEnd": [{
                    "hooks": [{
                        "type": "command",
                        "command": f'python "{hook_script}"',
                        "timeout": 10
                    }]
                }]
            }
        }
        hook_settings_path = self.result_dir / f"{task_id}.hook.json"
        hook_settings_path.write_text(json.dumps(hook_settings), encoding="utf-8")
        cmd.extend(["--settings", str(hook_settings_path)])
        
        cmd.append("-p")
        cmd.append(prompt)
        
        return cmd
    
    def _get_hook_script_path(self) -> Path:
        """Get path to the hook script, creating it if necessary."""
        hook_dir = Path(__file__).parent.parent / "hooks"
        hook_script = hook_dir / "nanobot-claude-hook.py"
        
        if not hook_script.exists():
            hook_script = self._create_hook_script(hook_script)
        
        return hook_script
    
    def _create_hook_script(self, path: Path) -> Path:
        """Create the hook script file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        
        script_content = '''#!/usr/bin/env python3
"""Claude Code Stop Hook for nanobot integration.

This script is called by Claude Code when a task completes.
It writes the result to a JSON file for nanobot to pick up.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def main():
    task_meta_path = os.environ.get("NANOBOT_TASK_META", "")
    if not task_meta_path:
        sys.exit(0)
    
    task_meta_path = Path(task_meta_path)
    if not task_meta_path.exists():
        sys.exit(0)
    
    try:
        with open(task_meta_path, encoding="utf-8") as f:
            task_meta = json.load(f)
    except (json.JSONDecodeError, IOError):
        sys.exit(0)
    
    task_id = task_meta.get("task_id", "unknown")
    result_dir = Path(task_meta.get("result_dir", "."))
    origin = task_meta.get("origin", {})
    
    output = ""
    for env_var in ["CLAUDE_OUTPUT", "CLAUDE_CODE_OUTPUT"]:
        if env_var in os.environ:
            output = os.environ[env_var]
            break
    
    lock_file = result_dir / f".{task_id}.lock"
    if lock_file.exists():
        sys.exit(0)
    
    try:
        lock_file.touch()
    except IOError:
        sys.exit(0)
    
    try:
        result = {
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "output": output,
            "status": "done",
            "origin": origin,
        }
        
        result_path = result_dir / f"{task_id}.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    finally:
        try:
            lock_file.unlink()
        except IOError:
            pass


if __name__ == "__main__":
    main()
'''
        path.write_text(script_content, encoding="utf-8")
        logger.info(f"Created hook script at {path}")
        return path
    
    async def _write_timeout_result(self, task_id: str, timeout: int) -> None:
        """Write a timeout result file."""
        origin = self._task_origins.pop(task_id, {})
        result = {
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "output": f"Task timed out after {timeout} seconds",
            "status": "timeout",
            "origin": origin,
        }
        result_path = self.result_dir / f"{task_id}.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    
    async def _write_error_result(self, task_id: str, code: int, error: str) -> None:
        """Write an error result file."""
        origin = self._task_origins.pop(task_id, {})
        result = {
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "output": f"Error (code {code}): {error}",
            "status": "error",
            "origin": origin,
        }
        result_path = self.result_dir / f"{task_id}.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    
    def _handle_result(self, result: dict[str, Any]) -> None:
        """Handle a result from the watcher."""
        task_id = result.get("task_id", "")
        self._task_origins.pop(task_id, None)
    
    def get_running_count(self) -> int:
        """Return the number of currently running tasks."""
        return len(self._running_tasks)
    
    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        task = self._running_tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            logger.info(f"Claude Code task [{task_id}] cancelled")
            return True
        return False
    
    def check_claude_available(self) -> bool:
        """Check if Claude Code CLI is available."""
        return shutil.which("claude") is not None
    
    def _quote_args(self, args: list[str]) -> list[str]:
        """Quote command arguments for shell execution on Windows."""
        quoted = []
        for arg in args:
            if " " in arg or '"' in arg or "'" in arg:
                quoted.append(f'"{arg.replace(chr(34), chr(92) + chr(34))}"')
            else:
                quoted.append(arg)
        return quoted
