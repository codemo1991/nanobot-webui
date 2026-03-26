"""
nanobot.tracing — Structured trace spans for request-level observability.

Design principles:
- Zero external dependencies (no OpenTelemetry required by default).
- `contextvars` for async-safe propagation of trace_id across coroutines.
- Structured JSONL export to ~/.nanobot/traces/ with rotation and retention.
- Seamless integration with existing loguru logger via `extra` context.

Quick start::

    from nanobot.tracing import span, get_current_trace_id, init_tracing

    # Initialize once at startup (e.g., in CLI main)
    init_tracing()

    # In an async handler:
    async with span("my.operation", attrs={"key": "value"}) as s:
        s.set_attr("result", 42)

    # All loguru calls within the span automatically carry trace_id:
    logger.info("processing...")  # logged as [abc123] processing...
"""

from nanobot.tracing.context import (
    get_current_trace_id,
    get_current_span_id,
    get_parent_span_id,
    trace_context,
)
from nanobot.tracing.emitter import TraceEmitter
from nanobot.tracing.spans import Span, span

__all__ = [
    "span",
    "Span",
    "trace_context",
    "get_current_trace_id",
    "get_current_span_id",
    "get_parent_span_id",
    "init_tracing",
    "get_emitter",
    "set_emitter",
    "TraceEmitter",
]

# Module-level emitter instance (lazy, set by init_tracing)
_emitter: TraceEmitter | None = None


def init_tracing(
    trace_dir: str | None = None,
    rotation: str = "50 MB",
    retention_days: int = 7,
    buffer_size: int = 50,
    enabled: bool = True,
) -> TraceEmitter:
    """
    Initialize the global trace emitter.

    Call this once at application startup (before any spans are created).

    Args:
        trace_dir: Directory for trace files. Default: ~/.nanobot/traces/
        rotation: Max file size before rotation. Default: "50 MB"
        retention_days: Delete files older than this. Default: 7
        buffer_size: Spans to buffer before flushing. Default: 50
        enabled: If False, tracing is a no-op. Default: True
    """
    global _emitter
    from nanobot.tracing.spans import set_emitter

    _emitter = TraceEmitter(
        trace_dir=trace_dir,
        rotation=rotation,
        retention_days=retention_days,
        buffer_size=buffer_size,
        enabled=enabled,
    )
    set_emitter(_emitter)
    return _emitter


def get_emitter() -> TraceEmitter | None:
    """Return the global trace emitter instance."""
    return _emitter


def set_emitter(emitter: TraceEmitter | None) -> None:
    """Set the global trace emitter (used internally and by init_tracing)."""
    global _emitter
    _emitter = emitter
    from nanobot.tracing.spans import set_emitter as _set

    _set(emitter)
