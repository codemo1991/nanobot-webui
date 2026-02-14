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
    
    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the origin context for result notifications."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id
    
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

The task runs independently with its own token budget.
You will be notified when it completes via system message.

IMPORTANT: Only use this for substantial coding tasks that benefit from
Claude Code's specialized capabilities. For simple file operations,
use read_file/write_file/edit_file tools instead."""
    
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
                    "description": "Enable Agent Teams mode for parallel work. Useful for large tasks that can be split."
                },
                "teammate_mode": {
                    "type": "string",
                    "enum": ["auto", "in-process", "tmux"],
                    "default": "auto",
                    "description": "Teammate mode for Agent Teams: 'auto' (let Claude decide), 'in-process' (same process), 'tmux' (separate tmux sessions)"
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
        teammate_mode: str = "auto",
        timeout: int = 600,
        **kwargs: Any,
    ) -> str:
        """Start Claude Code and return task ID for tracking."""
        if not self._manager.check_claude_available():
            return "Error: Claude Code CLI is not available. Please install it first: npm install -g @anthropic-ai/claude-code"
        
        try:
            task_id = await self._manager.start_task(
                prompt=prompt,
                workdir=workdir,
                permission_mode=permission_mode,
                agent_teams=agent_teams,
                teammate_mode=teammate_mode,
                origin_channel=self._origin_channel,
                origin_chat_id=self._origin_chat_id,
                timeout=timeout,
            )
            
            running_count = self._manager.get_running_count()
            
            return (
                f"Claude Code task [{task_id}] started successfully.\n"
                f"I'll notify you when it completes.\n"
                f"Currently running {running_count} Claude Code task(s)."
            )
        except ValueError as e:
            return f"Error: {str(e)}"
        except RuntimeError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error starting Claude Code: {str(e)}"
