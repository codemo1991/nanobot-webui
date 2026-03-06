"""Claude Code CLI backend for subagent execution.

This backend uses Claude Code Agent SDK to execute coding tasks.
Registration must be done via register() before the backend can be used.
"""

from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from loguru import logger

if TYPE_CHECKING:
    from nanobot.claude_code.manager import ClaudeCodeManager
    from nanobot.agent.backend_registry import Runner

# Module-level reference to ClaudeCodeManager
_claude_code_manager: Optional["ClaudeCodeManager"] = None


def _set_manager(mgr: "ClaudeCodeManager") -> None:
    """Set the ClaudeCodeManager instance."""
    global _claude_code_manager
    _claude_code_manager = mgr


def _available() -> bool:
    """Check if Claude Code backend is available."""
    if _claude_code_manager is None:
        return False
    return _claude_code_manager.check_claude_available()


def register(claude_code_manager: "ClaudeCodeManager") -> None:
    """Register the Claude Code backend with BackendRegistry.

    This must be called after creating ClaudeCodeManager and before
    creating SubagentManager.

    Args:
        claude_code_manager: The ClaudeCodeManager instance to use
    """
    global _claude_code_manager
    _claude_code_manager = claude_code_manager

    from nanobot.agent.backend_registry import BackendRegistry

    # Create a runner that captures the manager reference
    async def _run(
        task_id: str,
        task: str,
        label: str,
        origin: dict,
        template: str = "",
        batch_id: str | None = None,
        subagent_manager: Optional["SubagentManager"] = None,
        media: list[str] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> None:
        """Runner function that delegates to SubagentManager's _run_via_claude_code."""
        if subagent_manager is None:
            logger.error(f"Subagent [{task_id}] Claude Code backend requires SubagentManager reference")
            return
        await subagent_manager._run_via_claude_code(
            task_id, task, label, origin, template=template, batch_id=batch_id
        )

    BackendRegistry.register("claude_code", _run, _available)
    logger.info("Claude Code backend registered with BackendRegistry")


# Import SubagentManager for type hints (avoid circular import at runtime)
if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager
