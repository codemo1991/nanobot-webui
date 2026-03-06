"""Backend resolver for determining which backend to use for a subagent task.

This module resolves the backend to use based on:
1. Spawn parameter (explicit backend request)
2. Template configuration (preferred backend)
3. Template+media: voice (template=voice + audio), dashscope_vision (images + DashScope model)
4. Backward compatibility (template name inference)
5. Default fallback
"""

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from nanobot.agent.backend_registry import BackendRegistry
    from nanobot.config.agent_templates import AgentTemplateManager


def _media_has_audio(media: list[str] | None) -> bool:
    """Check if media contains audio files."""
    if not media:
        return False
    for path in media:
        p = Path(path)
        mime, _ = mimetypes.guess_type(path)
        ext = p.suffix.lower()
        is_audio = (mime and mime.startswith("audio/")) or ext in (".mp3", ".wav", ".ogg", ".m4a", ".opus", ".webm", ".aac")
        if is_audio:
            return True
    return False


def _media_has_images(media: list[str] | None) -> bool:
    """Check if media contains image files."""
    if not media:
        return False
    for path in media:
        p = Path(path)
        mime, _ = mimetypes.guess_type(path)
        if mime and mime.startswith("image/"):
            return True
        if not mime and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            return True
    return False


def _is_dashscope_model(model: str) -> bool:
    """Check if model is DashScope/Qwen."""
    return any(k in (model or "").lower() for k in ("dashscope", "qwen"))


class BackendResolver:
    """Resolves the backend to use for a subagent task.

    Resolution priority (highest to lowest):
    1. spawn_param is explicitly set (not "auto") and is available
    2. spawn_param is "native" - always use native
    3. template.config.backend is set and available
    4. Backward compatibility: template name contains "coder"/"claude" and "claude_code" is available
    5. Default: "native"
    """

    def __init__(
        self,
        template_manager: Optional["AgentTemplateManager"],
        registry: "BackendRegistry",
    ):
        self._template_manager = template_manager
        self._registry = registry

    def resolve(
        self,
        template: str,
        spawn_param: str,
        media: list[str] | None = None,
        model: str | None = None,
    ) -> str:
        """Resolve the backend to use for a subagent task.

        Args:
            template: Template name
            spawn_param: Backend parameter from spawn call ("auto", "native", "claude_code", etc.)
            media: Optional media file paths (for voice/vision routing)
            model: Optional effective model (for dashscope_vision routing)

        Returns:
            Resolved backend name
        """
        available = set(self._registry.list_available())

        # 1. spawn_param is explicitly specified and available
        if spawn_param != "auto" and spawn_param in available:
            return spawn_param

        # 2. spawn_param is "native" - always use native (even if not registered)
        if spawn_param == "native":
            return "native"

        # 3. Template+media: voice (template=voice + audio only)
        if template == "voice" and media and _media_has_audio(media) and not _media_has_images(media):
            if "voice" in available:
                return "voice"

        # 4. Template+media+model: dashscope_vision (images + DashScope model)
        if media and _media_has_images(media) and model and _is_dashscope_model(model):
            if "dashscope_vision" in available:
                return "dashscope_vision"

        # 5. Check template config for backend preference
        if self._template_manager:
            cfg = self._template_manager.get_template(template)
            if cfg and hasattr(cfg, "backend") and cfg.backend and cfg.backend in available:
                return cfg.backend

        # 6. Backward compatibility: old templates without backend field
        if ("coder" in template.lower() or "claude" in template.lower()) and "claude_code" in available:
            return "claude_code"

        # 7. Default fallback
        return "native"
