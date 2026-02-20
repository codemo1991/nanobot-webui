"""Claude Code CLI integration tool."""

from typing import Any, TYPE_CHECKING

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.claude_code.manager import ClaudeCodeManager


class ClaudeCodeTool(Tool):
    """
    Tool to delegate coding tasks to Claude Code CLI.
    
    Claude Code runs as an independent process with its own token budget.
    Results are delivered via Hook mechanism (no polling needed).
    """
    
    def __init__(self, manager: "ClaudeCodeManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._progress_callback: Any = None
    
    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the origin context for result notifications."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id
    
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
                "agent_teams": {
                    "type": "boolean",
                    "default": False,
                    "description": "Enable Agent Teams mode for parallel work. Only supported on newer Claude Code versions."
                },
                "teammate_mode": {
                    "type": "string",
                    "enum": ["auto", "in-process", "tmux"],
                    "default": "in-process",
                    "description": "Teammate mode for Agent Teams: 'in-process' (same process, recommended), 'auto' (let Claude decide), 'tmux' (separate tmux sessions)"
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
        agent_teams: bool = False,
        teammate_mode: str = "in-process",
        timeout: int = 600,
        **kwargs: Any,
    ) -> str:
        """Run Claude Code synchronously and return the result."""
        if not self._manager.check_claude_available():
            return "Error: Claude Code CLI is not available. Please install it first: npm install -g @anthropic-ai/claude-code"
        
        try:
            result = await self._manager.run_task(
                prompt=prompt,
                workdir=workdir,
                permission_mode=permission_mode,
                agent_teams=agent_teams,
                teammate_mode=teammate_mode,
                timeout=timeout,
                progress_callback=self._progress_callback,
            )
            
            status = result.get("status", "unknown")
            output = result.get("output", "")
            task_id = result.get("task_id", "")
            
            if status == "done":
                return f"Claude Code task [{task_id}] completed.\n\nResult:\n{output}"
            elif status == "timeout":
                return f"Claude Code task [{task_id}] timed out.\n\n{output}"
            else:
                return f"Claude Code task [{task_id}] failed.\n\n{output}"
        except ValueError as e:
            return f"Error: {str(e)}"
        except RuntimeError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error running Claude Code: {str(e)}"
