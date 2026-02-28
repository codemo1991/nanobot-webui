"""Spawn tool for creating background subagents."""

import mimetypes
from pathlib import Path
from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.agent.tools.base import Tool


def _media_has_only_images(media: list[str]) -> bool:
    """Check if media contains only image files (no audio)."""
    if not media:
        return False
    for path in media:
        p = Path(path)
        mime, _ = mimetypes.guess_type(path)
        is_image = mime and mime.startswith("image/")
        is_audio = mime and mime.startswith("audio/")
        if not mime:
            ext = p.suffix.lower()
            is_image = ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            is_audio = ext in (".mp3", ".wav", ".ogg", ".m4a", ".opus", ".webm", ".aac")
        if is_audio:
            return False  # 含音频则非纯图片
    return True

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
        self._batch_id: str | None = None

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id

    def set_batch_id(self, batch_id: str) -> None:
        """Set the batch ID for this turn; all spawns in this turn share it for aggregation."""
        self._batch_id = batch_id

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
            "The subagent will complete the task and report back when done. "
            "IMPORTANT: Do not spawn the same or equivalent task more than once per user request; "
            "if you already called spawn for a task, wait for its result instead of spawning again.\n\n"
            "For coding tasks, use template='coder'. With backend='auto' (default), the system "
            "automatically selects the best available backend: Claude Code CLI (if installed) or "
            "the native LLM coder. You can also force a specific backend with backend='claude_code' "
            "or backend='native'.\n\n"
            "Template selection by media type: use template='vision' for images (analysis/recognition); use template='voice' ONLY for audio files (transcription). Never use voice for images. Set attach_media=true to forward media to the subagent."
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
                    "enum": ["minimal", "coder", "researcher", "analyst", "claude-coder", "vision", "voice"],
                    "description": "The subagent template: minimal (simple), coder (code), claude-coder (Claude Code), vision (image analysis), voice (audio transcription), researcher (info), analyst (data)",
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
        media = self._current_media if attach_media and self._current_media else None
        # 防止图片被错误路由到 voice：纯图片时强制 vision
        if media and template == "voice" and _media_has_only_images(media):
            logger.info("[SpawnTool] Media contains only images, overriding template voice->vision")
            template = "vision"
        logger.info(f"[SpawnTool] Spawning subagent with manager id: {id(self._manager)}, origin: {self._origin_channel}:{self._origin_chat_id}")
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
            batch_id=self._batch_id,
        )
