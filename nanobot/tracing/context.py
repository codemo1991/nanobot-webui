"""
Trace context using contextvars for async-safe propagation.

Trace IDs and the current span stack are stored in context-local variables,
automatically propagated across async boundaries without explicit passing.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar

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
    - 经 ``logging_config`` 中 sink 的 filter，全局 ``logger`` 输出会带上当前 ``trace_id`` 前缀。

    Usage::

        async with trace_context(msg.session_key, "agent.turn", attrs) as root:
            logger.info("processing message")   # logs [abc123] processing message
            async with span("llm.inference") as s:
                ...                              # root is returned here

    Note: the root span is returned as the context value and auto-closed on exit.
    """
    # Import inside function to avoid circular import at module load time
    from nanobot.tracing.spans import span as _span

    # Activate contextvars（日志中的 trace_id 由 logging_config._ensure_trace_id 从本处读取）
    _tid_token = _current_trace_id.set(trace_id)
    _stack_token = _current_span_stack.set([])

    try:
        async with _span(span_name, trace_id=trace_id, attrs=attrs) as root:
            yield root
    finally:
        _current_trace_id.reset(_tid_token)
        _current_span_stack.reset(_stack_token)

