"""
Type constants and enumerations for the nanobot tracing system.

This module provides type-safe enums and constants used across the enhanced
tracing system to categorize spans, track status, and define limits.
"""

from __future__ import annotations

from enum import Enum


class SpanType(str, Enum):
    """
    Enumeration of span types in the nanobot tracing system.

    Each span type represents a distinct unit of work in the agent lifecycle.

    Attributes:
        AGENT_TURN: Root span for an entire agent turn/conversation turn.
        LLM_CALL: Span for a single LLM API call (prompt → response).
        TOOL_EXECUTE: Span for executing a single tool/function.
        SUBAGENT_SPAWN: Span for spawning a sub-agent to handle a subtask.
        SUBAGENT_RESULT: Span capturing the result returned by a sub-agent.
    """

    AGENT_TURN = "agent.turn"
    LLM_CALL = "llm.call"
    TOOL_EXECUTE = "tool.execute"
    SUBAGENT_SPAWN = "subagent.spawn"
    SUBAGENT_RESULT = "subagent.result"


class SpanStatus(str, Enum):
    """
    Enumeration of span statuses.

    Represents the lifecycle state of a span.

    Attributes:
        RUNNING: Span has started but not yet completed.
        OK: Span completed successfully.
        ERROR: Span completed with an error.
    """

    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


class ToolResultStatus(str, Enum):
    """
    Enumeration of tool execution result statuses.

    Represents the outcome of a tool/function execution.

    Attributes:
        SUCCESS: Tool executed successfully.
        ERROR: Tool execution failed.
        TIMEOUT: Tool execution timed out.
    """

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


# ---------------------------------------------------------------------------
# Trace format version
# ---------------------------------------------------------------------------

TRACE_VERSION: str = "2.0"
"""
Version string for the trace format schema.

This version is included in trace output for forward/backward compatibility.
"""


# ---------------------------------------------------------------------------
# Preview length limits
# ---------------------------------------------------------------------------

ARGS_PREVIEW_MAX_LEN: int = 500
"""
Maximum length for argument previews in spans.

Arguments longer than this will be truncated when stored in span attributes.
"""

RESULT_PREVIEW_MAX_LEN: int = 1000
"""
Maximum length for result previews in spans.

Results longer than this will be truncated when stored in span attributes.
"""
