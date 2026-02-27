"""Spawn tool for creating background subagents."""

from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


class SpawnTool(Tool):
    """
    Tool to spawn a subagent for background task execution.
    
    The subagent runs asynchronously and announces its result back
    to the main agent when complete.
    """
    
    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._current_media: list[str] = []
    
    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id

    def set_media(self, media: list[str]) -> None:
        """Set the current message's media paths for optional forwarding."""
        self._current_media = list(media) if media else []
    
    @property
    def name(self) -> str:
        return "spawn"
    
    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done.\n\n"
            "For coding tasks, use template='coder'. With backend='auto' (default), the system "
            "automatically selects the best available backend: Claude Code CLI (if installed) or "
            "the native LLM coder. You can also force a specific backend with backend='claude_code' "
            "or backend='native'.\n\n"
            "Set attach_media=true to forward the current message's images to the subagent."
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to complete",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the task (for display)",
                },
                "template": {
                    "type": "string",
                    "enum": ["minimal", "coder", "researcher", "analyst", "claude-coder", "vision"],
                    "description": "The subagent template: minimal (simple), coder (code), claude-coder (Claude Code), vision (image analysis), researcher (info), analyst (data)",
                    "default": "minimal",
                },
                "backend": {
                    "type": "string",
                    "enum": ["auto", "native", "claude_code"],
                    "description": "Execution backend. 'auto': auto-select. 'claude_code': Claude Code CLI. 'native': native LLM.",
                    "default": "auto",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session ID to continue an existing subagent conversation",
                },
                "enable_memory": {
                    "type": "boolean",
                    "description": "Enable agent-specific memory for this subagent",
                    "default": False,
                },
                "attach_media": {
                    "type": "boolean",
                    "description": "Whether to forward the current message's images to the subagent for visual analysis",
                    "default": False,
                },
            },
            "required": ["task"],
        }

    async def execute(
        self,
        task: str,
        label: str | None = None,
        template: str = "minimal",
        backend: str = "auto",
        session_id: str | None = None,
        enable_memory: bool = False,
        attach_media: bool = False,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        logger.info(f"[SpawnTool] Spawning subagent with manager id: {id(self._manager)}, origin: {self._origin_channel}:{self._origin_chat_id}")
        media = self._current_media if attach_media and self._current_media else None
        return await self._manager.spawn(
            task=task,
            label=label,
            template=template,
            backend=backend,
            session_id=session_id,
            enable_memory=enable_memory,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            media=media,
        )
