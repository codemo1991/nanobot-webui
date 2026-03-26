"""
Structured trace spans with async-safe parent/child hierarchy.

Each span represents a unit of work (LLM call, tool execution, message processing).
Spans form a tree rooted at the agent.turn span.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from loguru import logger


def timestamp_ms() -> int:
    """Return current UTC timestamp in milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


_SEQUENCE: int = 0
_SEQUENCE_LOCK = asyncio.Lock()


async def _next_seq() -> int:
    global _SEQUENCE
    async with _SEQUENCE_LOCK:
        _SEQUENCE += 1
        return _SEQUENCE


@dataclass
class Span:
    """
    A single trace span representing a unit of work.

    Attributes:
        trace_id: Unique identifier for the entire request/turn.
        name: Human-readable span name (e.g., "llm.inference", "tool.execute").
        span_id: Unique identifier for this span.
        parent_id: span_id of the parent span (None for root span).
        start_ms: Start timestamp in milliseconds (UTC).
        end_ms: End timestamp in milliseconds (UTC), None if still running.
        duration_ms: Total elapsed time, None if still running.
        status: "running" | "ok" | "error".
        attrs: Arbitrary key-value attributes (session_key, model, tool_name, etc.).
        events: Inner events (log lines within the span).
    """

    trace_id: str
    name: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: str | None = None
    start_ms: int = field(default_factory=timestamp_ms)
    end_ms: int | None = None
    duration_ms: int | None = None
    status: str = "running"
    attrs: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    seq: int = 0

    def set_attr(self, key: str, value: Any) -> None:
        """Set or update a span attribute."""
        self.attrs[key] = value

    def add_event(self, name: str, attrs: dict | None = None) -> None:
        """Record an inner event within this span."""
        self.events.append({
            "name": name,
            "ts_ms": timestamp_ms(),
            "attrs": attrs or {},
        })

    def end(self, status: str | None = None) -> None:
        """Mark the span as ended and compute duration."""
        self.end_ms = timestamp_ms()
        self.duration_ms = self.end_ms - self.start_ms
        if status:
            self.status = status
        elif self.status == "running":
            self.status = "ok"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSONL emission."""
        return {
            "type": "span",
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "service": "nanobot",
            "status": self.status,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "duration_ms": self.duration_ms,
            "attrs": self.attrs,
            "events": self.events,
            "seq": self.seq,
        }


# Lazy emitter reference (avoids circular import)
_emitter = None


def set_emitter(emitter) -> None:
    global _emitter
    global _emitter_ref
    _emitter_ref = emitter


_emitter_ref: Any = None


def _get_emitter():
    global _emitter_ref
    return _emitter_ref


@asynccontextmanager
async def span(
    name: str,
    trace_id: str | None = None,
    attrs: dict | None = None,
    parent_id: str | None = None,
):
    """
    Async context manager that creates and emits a span.

    Usage::

        async with span("llm.inference", attrs={"model": "claude-3"}) as s:
            result = await llm.chat(messages)
            s.set_attr("finish_reason", result.finish_reason)

    The span is automatically:
    - Created with correct parent_id (from context stack if not provided)
    - Ended when the context exits
    - Emitted to the configured TraceEmitter
    - Removed from the active span stack
    """
    from nanobot.tracing.context import (
        get_current_span_id,
        get_current_trace_id,
        pop_span,
        push_span,
    )

    # Resolve trace_id
    _trace_id = trace_id or get_current_trace_id()
    if not _trace_id:
        import uuid as _uuid
        _trace_id = _uuid.uuid4().hex[:12]

    # Resolve parent_id
    _parent_id = parent_id or get_current_span_id()

    # Get sequence number
    _seq = await _next_seq()

    s = Span(
        trace_id=_trace_id,
        name=name,
        parent_id=_parent_id,
        attrs=dict(attrs) if attrs else {},
        seq=_seq,
    )

    # Push onto stack so nested spans inherit parent
    push_span(s.span_id)

    try:
        yield s
    except BaseException as exc:
        s.end(status="error")
        s.set_attr("error_type", type(exc).__name__)
        s.set_attr("error_msg", str(exc)[:500])
        raise
    finally:
        if s.status == "running":
            s.end()
        pop_span()

        emitter = _get_emitter()
        if emitter is not None:
            try:
                emitter.emit(s)
            except Exception as e:
                logger.warning(f"[Tracing] Failed to emit span {s.name}: {e}")
