"""Claude Code CLI integration tool."""

import asyncio
from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import InboundMessage

if TYPE_CHECKING:
    from nanobot.claude_code.manager import ClaudeCodeManager


class ClaudeCodeTool(Tool):
    """
    Tool to delegate coding tasks to Claude Code CLI.

    Claude Code runs as an independent process with its own token budget.
    When execution exceeds the agent timeout, the task is shielded and
    continues running in the background; the result is delivered via a
    system InboundMessage when it completes.
    """

    def __init__(self, manager: "ClaudeCodeManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._progress_callback: Any = None
        # Keep background notification tasks alive (prevent GC)
        self._bg_tasks: set[asyncio.Task] = set()

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the origin context for result notifications and decision relay."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._manager.set_context(channel, chat_id)

    def set_progress_callback(self, callback: Any) -> None:
        """Set callback for progress updates."""
        self._progress_callback = callback

    @property
    def name(self) -> str:
        return "claude_code"

    @property
    def description(self) -> str:
        return """Delegate a coding task to Claude Code CLI.

Claude Code is a specialized coding agent that excels at:
- Implementing new features from scratch
- Large-scale refactoring
- Writing comprehensive tests
- Debugging complex issues
- Code review and improvements

The task runs with its own token budget and you will receive the result directly.
Progress is streamed in real-time during execution.

IMPORTANT: Only use this for substantial coding tasks that benefit from
Claude Code's specialized capabilities. For simple file operations,
use read_file/write_file/edit_file tools instead.

NOTE: For best results, set permission_mode to 'bypassPermissions' to avoid
interactive permission prompts in non-interactive mode."""

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The coding task for Claude Code to execute. Be specific about requirements, file paths, and expected behavior."
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory for the task. Defaults to the current workspace."
                },
                "permission_mode": {
                    "type": "string",
                    "enum": ["default", "plan", "auto", "bypassPermissions"],
                    "default": "auto",
                    "description": "Permission mode: 'default' (ask for permissions), 'plan' (plan first), 'auto' (auto-approve safe operations), 'bypassPermissions' (skip all permissions - use carefully)"
                },
                "enable_subagents": {
                    "type": "boolean",
                    "default": True,
                    "description": "Enable SDK subagents for parallel execution. When true, Claude can spawn specialized subagents (code-explorer, code-implementer, command-runner) to work on subtasks in parallel, significantly speeding up complex tasks. Recommended to keep enabled."
                },
                "timeout": {
                    "type": "integer",
                    "default": 600,
                    "description": "Task timeout in seconds. Default is 600 (10 minutes)."
                }
            },
            "required": ["prompt"]
        }

    async def execute(
        self,
        prompt: str,
        workdir: str | None = None,
        permission_mode: str = "auto",
        enable_subagents: bool = True,
        timeout: int = 600,
        **kwargs: Any,
    ) -> str:
        """
        Run Claude Code and return the result.

        Uses asyncio.shield() to protect the underlying run_task() coroutine
        from outer cancellation (e.g. agent message_timeout). If the outer
        agent times out before Claude Code finishes, the task keeps running
        in background and the result is delivered via the message bus.
        """
        if not self._manager.check_claude_available():
            return "Error: Claude Code CLI is not available. Please install it first: npm install -g @anthropic-ai/claude-code"

        try:
            # Wrap in an explicit Task so asyncio.shield can protect it
            inner_task: asyncio.Task = asyncio.ensure_future(
                self._manager.run_task(
                    prompt=prompt,
                    workdir=workdir,
                    permission_mode=permission_mode,
                    enable_subagents=enable_subagents,
                    timeout=timeout,
                    progress_callback=self._progress_callback,
                )
            )

            try:
                # shield() prevents outer cancellation from reaching inner_task
                result = await asyncio.shield(inner_task)

                status = result.get("status", "unknown")
                output = result.get("output", "")
                task_id = result.get("task_id", "")

                if status == "done":
                    return f"Claude Code task [{task_id}] completed.\n\nResult:\n{output}"
                elif status == "timeout":
                    return f"Claude Code task [{task_id}] timed out.\n\n{output}"
                else:
                    return f"Claude Code task [{task_id}] failed.\n\n{output}"

            except asyncio.CancelledError:
                # The outer agent timeout fired. inner_task is still running
                # because asyncio.shield protected it from cancellation.
                # Schedule a background watcher that will notify the user on completion.
                bg = asyncio.ensure_future(self._background_notify(inner_task))
                self._bg_tasks.add(bg)
                bg.add_done_callback(self._bg_tasks.discard)
                logger.info(
                    f"Claude Code task shielded into background "
                    f"(channel={self._origin_channel}, chat={self._origin_chat_id})"
                )
                raise  # Propagate CancelledError so agent loop records timeout

        except asyncio.CancelledError:
            raise
        except ValueError as e:
            return f"Error: {str(e)}"
        except RuntimeError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error running Claude Code: {str(e)}"

    async def _background_notify(self, inner_task: "asyncio.Task[dict[str, Any]]") -> None:
        """
        Wait for the shielded inner_task to finish, then publish the result
        as a system InboundMessage so the agent can forward it to the user.
        """
        try:
            result = await inner_task
            task_id = result.get("task_id", "unknown")
            output = result.get("output", "")
            status = result.get("status", "done")

            if status == "done":
                content = (
                    f"[Claude Code 后台任务 '{task_id}' 已完成]\n\n"
                    f"Result:\n{output[:3000]}{'...' if len(output) > 3000 else ''}\n\n"
                    f"请将以上 Claude Code 执行结果自然地、完整地告知用户。"
                )
            else:
                content = (
                    f"[Claude Code 后台任务 '{task_id}' 执行失败 (status={status})]\n\n"
                    f"{output[:1000]}\n\n"
                    f"请告知用户任务执行失败及具体原因。"
                )

            msg = InboundMessage(
                channel="system",
                sender_id="claude-code-bg",
                chat_id=f"{self._origin_channel}:{self._origin_chat_id}",
                content=content,
            )
            await self._manager.bus.publish_inbound(msg)
            logger.info(f"Background Claude Code task '{task_id}' result published to bus")

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Background Claude Code notification failed")
