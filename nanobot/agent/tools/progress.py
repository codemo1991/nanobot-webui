"""Tool progress throttling utilities."""

from __future__ import annotations

import time
from typing import Protocol


class ToolProgressCallback(Protocol):
    """Protocol for tool progress callback functions."""

    def __call__(self, tool_id: str, message: str, progress: float | None = None) -> None:
        """
        Callback for tool progress updates.

        Args:
            tool_id: Unique identifier for the tool instance.
            message: Human-readable progress message.
            progress: Optional progress value between 0.0 and 1.0.
        """
        ...


class ToolProgressThrottler:
    """
    Throttles progress updates to prevent flooding.

    Each tool instance is tracked independently based on its tool_id.
    Progress pushes are rate-limited by min_interval seconds.

    Example:
        throttler = ToolProgressThrottler(min_interval=1.0)
        if throttler.should_push("tool-1"):
            send_progress_update()
    """

    def __init__(self, min_interval: float = 1.0) -> None:
        """
        Initialize the throttler.

        Args:
            min_interval: Minimum seconds between progress pushes for each tool.
        """
        self._min_interval = min_interval
        self._last_push: dict[str, float] = {}

    def should_push(self, tool_id: str) -> bool:
        """
        Check if a progress push should be allowed for the given tool.

        First push for a tool always succeeds. Subsequent pushes are
        throttled based on min_interval.

        Args:
            tool_id: Unique identifier for the tool instance.

        Returns:
            True if push is allowed, False if it should be throttled.
        """
        now = time.monotonic()
        last = self._last_push.get(tool_id)

        if last is None or (now - last) >= self._min_interval:
            self._last_push[tool_id] = now
            return True

        return False

    def reset(self, tool_id: str | None = None) -> None:
        """
        Reset the throttle state.

        Args:
            tool_id: If provided, reset only this tool's state.
                    If None, reset all tools.
        """
        if tool_id is not None:
            self._last_push.pop(tool_id, None)
        else:
            self._last_push.clear()
