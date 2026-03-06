"""Backend registry for subagent execution backends.

This module provides a registry pattern for managing different subagent backends
(e.g., native LLM, Claude Code CLI). New backends can be registered without
modifying the core SubagentManager code.
"""

from typing import Callable, Awaitable, Any, Optional

# Type aliases
Runner = Callable[..., Awaitable[None]]
AvailabilityCheck = Callable[[], bool]


class BackendRegistry:
    """Registry for managing subagent backend runners.

    Backends are registered with a name, a runner function, and an availability check.
    The registry provides lookup by name, filtering by availability.
    """

    _backends: dict[str, tuple[Runner, AvailabilityCheck]] = {}

    @classmethod
    def register(cls, name: str, runner: Runner, available_check: AvailabilityCheck) -> None:
        """Register a backend with its runner and availability check.

        Args:
            name: Backend identifier (e.g., "native", "claude_code")
            runner: Async callable that executes the subagent task
            available_check: Function that returns True if the backend is available
        """
        cls._backends[name] = (runner, available_check)

    @classmethod
    def get(cls, name: str) -> Optional[Runner]:
        """Get a runner for the specified backend if available.

        Args:
            name: Backend identifier

        Returns:
            Runner function if registered and available, None otherwise
        """
        entry = cls._backends.get(name)
        if not entry:
            return None
        runner, check = entry
        if not check():
            return None
        return runner

    @classmethod
    def list_available(cls) -> list[str]:
        """List all registered backends that are currently available.

        Returns:
            List of available backend names
        """
        return [n for n, (_, c) in cls._backends.items() if c()]

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """Check if a backend is registered (regardless of availability).

        Args:
            name: Backend identifier

        Returns:
            True if registered, False otherwise
        """
        return name in cls._backends

    @classmethod
    def clear(cls) -> None:
        """Clear all registered backends. Mainly for testing."""
        cls._backends.clear()
