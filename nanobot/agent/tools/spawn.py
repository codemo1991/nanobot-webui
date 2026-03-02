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
        self._user_original_message: str = ""  # 存储用户的原始消息

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

    def set_user_message(self, message: str) -> None:
        """Set the user's original message for forced task propagation."""
        self._user_original_message = message
    
    @property
    def name(self) -> str:
        return "spawn"
    
    @property
    def _template_manager(self):
        """获取模板管理器，用于动态构建模板列表"""
        return getattr(self._manager, '_agent_template_manager', None)

    def _get_available_templates(self) -> list[str]:
        """动态获取可用的模板列表"""
        tm = self._template_manager
        if tm:
            try:
                templates = tm.list_templates()
                return [t.name for t in templates if t.enabled]
            except Exception:
                pass
        # 回退到默认列表
        return ["minimal", "coder", "researcher", "analyst", "claude-coder", "vision", "voice"]

    def _build_template_enum_description(self) -> str:
        """动态构建模板列表描述"""
        tm = self._template_manager
        templates = []
        if tm:
            try:
                for t in tm.list_templates():
                    if t.enabled:
                        desc = t.description or "无描述"
                        templates.append(f"{t.name}: {desc}")
            except Exception:
                pass
        if not templates:
            templates = [
                "minimal: 简单任务",
                "coder: 代码编写",
                "researcher: 信息检索",
                "analyst: 数据分析",
                "claude-coder: Claude Code",
                "vision: 图片分析",
                "voice: 语音转写"
            ]
        return "\n".join(templates)

    @property
    def description(self) -> str:
        # 动态获取模板描述
        template_list = self._build_template_enum_description()
        return (
            "Spawn a subagent to handle a task in the background. "
            "The subagent will complete the task and report back when done. "
            "IMPORTANT: Do not spawn the same or equivalent task more than once per user request; "
            "if you already called spawn for a task, wait for its result instead of spawning again.\n\n"
            "Available subagent templates:\n"
            f"{template_list}\n\n"
            "Task description rules (CRITICAL - MUST FOLLOW):\n"
            "- The 'task' parameter MUST be the EXACT, WORD-FOR-WORD request from the user\n"
            "- DO NOT add, interpret, rephrase, or elaborate on the user's request\n"
            "- DO NOT include phrases like '分析这张图片的内容' when user said '描述图片内容，用mermaid代码表示出来'\n"
            "- WRONG: task: '分析这张图片的内容' (这是你的解释，不是用户的原话)\n"
            "- CORRECT: task: '描述图片内容，用mermaid代码表示出来' (用户原话)\n"
            "- Examples of CORRECT usage:\n"
            "  * User said: '描述图片内容，用mermaid代码表示出来' → task: '描述图片内容，用mermaid代码表示出来'\n"
            "  * User said: '分析这张图片' → task: '分析这张图片'\n"
            "  * User said: '把流程图画成mermaid' → task: '把流程图画成mermaid'\n"
            "- The subagent will receive the actual image file, no need to include URLs or describe the image yourself\n\n"
            "Select the appropriate template based on the task requirements. "
            "For media (images/audio), select the template that best matches the media type. "
            "Set attach_media=true to forward media files to the subagent."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        # 动态获取模板列表用于 enum
        available_templates = self._get_available_templates()
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
                    "enum": available_templates,
                    "description": f"Subagent template to use. Available: {', '.join(available_templates)}",
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
                    "description": "Whether to forward the current message's media (images/audio) to the subagent",
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
        # 对于 vision/voice 模板，自动强制传递媒体文件（不管 attach_media 是否显式设置）
        # 这样可以确保图片/音频正确传递给子 agent
        template_lower = template.lower()
        is_vision_template = template_lower in ("vision", "visionary")
        is_voice_template = template_lower == "voice"

        if is_vision_template or is_voice_template:
            # 强制传递媒体
            media = self._current_media if self._current_media else None
            logger.info(f"[SpawnTool] Vision/Voice template detected, forcing media transfer: {len(media) if media else 0} files")
        else:
            media = self._current_media if attach_media and self._current_media else None

        # 防止图片被错误路由到 voice：纯图片时强制 vision
        if media and template == "voice" and _media_has_only_images(media):
            logger.info("[SpawnTool] Media contains only images, overriding template voice->vision")
            template = "vision"

        # 强制使用用户的原始消息，而不是主 Agent 解释后的版本
        # 这样可以确保子 agent 收到用户真正的意图
        # 但是要排除占位符如 "[图片]" 等
        if self._user_original_message and self._user_original_message.strip() not in ("[图片]", "[语音]", "[文件]", "[空消息]", ""):
            task = self._user_original_message
            logger.info(f"[SpawnTool] Using user original message as task (forced): {task[:100]}...")
        else:
            # 用户只发送了媒体文件（图片/语音）而没有文字，使用传入的 task 参数
            # 这是正常情况，比如用户只发了张图片然后说"分析一下"
            logger.info(f"[SpawnTool] Using provided task (user message is placeholder): {task[:100]}...")

        # 详细日志：spawn 参数
        logger.info(f"[SpawnTool] === SPAWN CALLED ===")
        logger.info(f"[SpawnTool] task: {task[:100]}..." if len(task) > 100 else f"[SpawnTool] task: {task}")
        logger.info(f"[SpawnTool] label: {label}, template: {template}, backend: {backend}")
        logger.info(f"[SpawnTool] enable_memory: {enable_memory}, attach_media: {bool(media)}, media_count: {len(media) if media else 0}")
        logger.info(f"[SpawnTool] origin: {self._origin_channel}:{self._origin_chat_id}, batch_id: {self._batch_id}")
        logger.info(f"[SpawnTool] manager id: {id(self._manager)}")

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
