"""Voice transcription backend for subagent execution.

When template=voice and media contains audio (no images), this backend
directly calls voice_transcribe tool, bypassing the LLM loop.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import mimetypes
from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


def _available() -> bool:
    """Check if voice backend is available (voice_transcribe tool can be used)."""
    try:
        from nanobot.agent.tools.voice_transcribe import VoiceTranscribeTool
        return True
    except ImportError:
        return False


def register() -> None:
    """Register the voice backend with BackendRegistry."""
    from nanobot.agent.backend_registry import BackendRegistry

    async def _run(
        task_id: str,
        task: str,
        label: str,
        origin: dict,
        template: str = "",
        batch_id: str | None = None,
        subagent_manager: Optional["SubagentManager"] = None,
        media: list[str] | None = None,
        **kwargs,
    ) -> None:
        """Runner: delegate to SubagentManager._run_via_voice."""
        if subagent_manager is None:
            logger.error(f"Subagent [{task_id}] Voice backend requires SubagentManager reference")
            return
        await subagent_manager._run_via_voice(
            task_id, task, label, origin,
            template=template, batch_id=batch_id, media=media or [],
        )

    BackendRegistry.register("voice", _run, _available)
    logger.info("Voice backend registered with BackendRegistry")
