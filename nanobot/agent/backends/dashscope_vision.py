"""DashScope vision backend for subagent execution.

When media contains images and model is DashScope/Qwen, this backend
calls DashScope API directly, bypassing LiteLLM (Bug #16007: LiteLLM
drops image_url content).
"""

import os
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


def _available() -> bool:
    """Check if DashScope API key is configured."""
    if os.environ.get("DASHSCOPE_API_KEY"):
        return True
    try:
        from nanobot.config.loader import load_config
        cfg = load_config()
        key = (cfg.providers.dashscope.api_key or "").strip()
        return bool(key)
    except Exception:
        return False


def register() -> None:
    """Register the DashScope vision backend with BackendRegistry."""
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
        model: str = "",
        **kwargs,
    ) -> None:
        """Runner: delegate to SubagentManager._run_via_dashscope_vision."""
        if subagent_manager is None:
            logger.error(f"Subagent [{task_id}] DashScope vision backend requires SubagentManager reference")
            return
        await subagent_manager._run_via_dashscope_vision(
            task_id, task, label, origin,
            template=template, batch_id=batch_id, media=media or [],
            model=model,
        )

    BackendRegistry.register("dashscope_vision", _run, _available)
    logger.info("DashScope vision backend registered with BackendRegistry")
