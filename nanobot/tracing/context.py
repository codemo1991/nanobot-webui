"""
Trace context using contextvars for async-safe propagation.

Trace IDs and the current span stack are stored in context-local variables,
automatically propagated across async boundaries without explicit passing.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

# The current trace ID for this async context
_current_trace_id: ContextVar[str] = ContextVar("trace_id", default="")

# Stack of active span IDs (top = current span)
_current_span_stack: ContextVar[list[str]] = ContextVar("span_stack", default=[])


def get_current_trace_id() -> str:
    """Return the current trace ID, or empty string if none set."""
    return _current_trace_id.get()


def get_current_span_id() -> str | None:
    """Return the current (topmost) span ID, or None if no span is active."""
    stack = _current_span_stack.get()
    return stack[-1] if stack else None


def get_parent_span_id() -> str | None:
    """Return the parent span ID (second from top), or None if no parent."""
    stack = _current_span_stack.get()
    return stack[-2] if len(stack) >= 2 else None


def push_span(span_id: str) -> None:
    """Push a span ID onto the current stack."""
    stack = list(_current_span_stack.get())
    stack.append(span_id)
    _current_span_stack.set(stack)


def pop_span() -> str | None:
    """Pop and return the topmost span ID."""
    stack = list(_current_span_stack.get())
    if stack:
        popped = stack.pop()
        _current_span_stack.set(stack)
        return popped
    return None


@asynccontextmanager
async def trace_context(
    trace_id: str,
    span_name: str = "agent.turn",
    attrs: dict | None = None,
):
    """
    Async context manager that activates a trace context and creates the root span.

    While active:
    - ``get_current_trace_id()`` returns the given trace_id.
    - Nested ``span()`` calls automatically inherit this trace_id and set parent_id.
    - loguru ``logger`` calls automatically include the trace_id in ``extra["trace_id"]``.

    Usage::

        async with trace_context(msg.session_key, "agent.turn", attrs) as root:
            logger.info("processing message")   # logs [abc123] processing message
            async with span("llm.inference") as s:
                ...                              # root is returned here

    Note: the root span is returned as the context value and auto-closed on exit.
    """
    # Import inside function to avoid circular import at module load time
    from nanobot.tracing.spans import span as _span

    # Activate contextvars
    _tid_token = _current_trace_id.set(trace_id)
    _stack_token = _current_span_stack.set([])

    # Patch loguru to inject trace_id into every log record
    _logger_patched_id: list[Any] = []  # store patch token

    def _inject_trace_id(record: dict) -> None:
        record["extra"]["trace_id"] = trace_id

    def _eject_trace_id(record: dict) -> None:
        record["extra"].pop("trace_id", None)

    # Import loguru lazily to avoid import-time side effects
    from loguru import logger

    _patch_id = logger.patch(_inject_trace_id)
    _logger_patched_id.append(_patch_id)

    try:
        # Create and enter the root span, yield it to the caller
        async with _span(span_name, trace_id=trace_id, attrs=attrs) as root:
            yield root
    finally:
        # Revert contextvars
        _current_trace_id.reset(_tid_token)
        _current_span_stack.reset(_stack_token)
        # Revert loguru patch
        for _pid in _logger_patched_id:
            logger.patch(_eject_trace_id)

