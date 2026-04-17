"""Tests for nanobot.tracing module."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from nanobot.tracing import (
    Span,
    init_tracing,
    span,
    trace_context,
    get_current_trace_id,
    get_current_span_id,
    get_parent_span_id,
    get_emitter,
)


class DummyEmitter:
    """Captures emitted spans for inspection."""

    def __init__(self) -> None:
        self.emitted: list[dict] = []

    def emit(self, obj) -> None:
        if hasattr(obj, "to_dict"):
            self.emitted.append(obj.to_dict())
        elif isinstance(obj, dict):
            self.emitted.append(obj)


# ---------------------------------------------------------------------------
# Span dataclass
# ---------------------------------------------------------------------------

def test_span_basic_fields() -> None:
    s = Span(trace_id="t1", name="test.span")
    assert s.trace_id == "t1"
    assert s.name == "test.span"
    assert s.status == "running"
    assert s.end_ms is None
    assert s.duration_ms is None


def test_span_end_sets_duration() -> None:
    import time
    s = Span(trace_id="t1", name="test.span")
    time.sleep(0.01)
    s.end()
    assert s.status == "ok"
    assert s.end_ms is not None
    assert s.duration_ms is not None
    assert s.duration_ms >= 10  # at least 10ms


def test_span_end_with_status() -> None:
    s = Span(trace_id="t1", name="test.span")
    s.end(status="error")
    assert s.status == "error"


def test_span_set_attr() -> None:
    s = Span(trace_id="t1", name="test.span")
    s.set_attr("model", "claude-3")
    s.set_attr("count", 42)
    assert s.attrs["model"] == "claude-3"
    assert s.attrs["count"] == 42


def test_span_add_event() -> None:
    s = Span(trace_id="t1", name="test.span")
    s.add_event("checkpoint", {"key": "value"})
    assert len(s.events) == 1
    assert s.events[0]["name"] == "checkpoint"
    assert s.events[0]["attrs"]["key"] == "value"


def test_span_to_dict() -> None:
    s = Span(trace_id="t1", name="test.span", span_id="s1", parent_id="p1")
    s.end()
    d = s.to_dict()
    assert d["type"] == "span"
    assert d["trace_id"] == "t1"
    assert d["span_id"] == "s1"
    assert d["parent_id"] == "p1"
    assert d["name"] == "test.span"
    assert d["status"] == "ok"
    assert d["duration_ms"] is not None


# ---------------------------------------------------------------------------
# Contextvars propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trace_context_sets_contextvar() -> None:
    assert get_current_trace_id() == ""
    async with trace_context("trace-abc", "test.turn"):
        assert get_current_trace_id() == "trace-abc"
    assert get_current_trace_id() == ""


@pytest.mark.asyncio
async def test_trace_context_resets_after_exit() -> None:
    async with trace_context("trace-xyz"):
        pass
    assert get_current_trace_id() == ""


@pytest.mark.asyncio
async def test_span_stack_nesting() -> None:
    """Nested spans push/pop correctly."""
    assert get_current_span_id() is None
    async with span("outer") as outer_span:
        assert get_current_span_id() == outer_span.span_id
        assert get_parent_span_id() is None
        async with span("inner") as inner_span:
            assert get_current_span_id() == inner_span.span_id
            assert get_parent_span_id() == outer_span.span_id
        assert get_current_span_id() == outer_span.span_id
    assert get_current_span_id() is None


@pytest.mark.asyncio
async def test_trace_context_inherits_to_nested_span() -> None:
    """Spans created inside trace_context inherit the trace_id."""
    from nanobot.tracing.context import _current_trace_id
    async with trace_context("my-trace-id", "agent.turn"):
        async with span("child") as s:
            assert s.trace_id == "my-trace-id"
        # after exiting child, still in trace context
        async with span("child2") as s2:
            assert s2.trace_id == "my-trace-id"


@pytest.mark.asyncio
async def test_async_propagation() -> None:
    """trace_id propagates across await boundaries."""
    async def inner() -> str:
        return get_current_trace_id()

    async with trace_context("async-trace"):
        result = await inner()
        assert result == "async-trace"


@pytest.mark.asyncio
async def test_concurrent_traces_isolated() -> None:
    """Concurrent traces don't interfere with each other."""
    async def run_trace(tid: str) -> tuple[str, str]:
        async with trace_context(tid, "agent.turn"):
            await asyncio.sleep(0.01)
            return tid, get_current_trace_id()

    results = await asyncio.gather(
        run_trace("trace-a"),
        run_trace("trace-b"),
    )
    for orig, captured in results:
        assert captured == orig, f"{captured} != {orig}"


# ---------------------------------------------------------------------------
# TraceEmitter
# ---------------------------------------------------------------------------

def test_emitter_emit_span_object(tmp_path: Path) -> None:
    emitter = init_tracing(
        trace_dir=str(tmp_path / "traces"),
        rotation="1 MB",
        retention_days=1,
        buffer_size=1,  # flush immediately
    )
    s = Span(trace_id="t1", name="test.span")
    emitter.emit(s)
    emitter.flush()
    # flush() clears the buffer after writing to disk
    trace_dir = tmp_path / "traces"
    files = list(trace_dir.glob("trace_*.jsonl"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    record = json.loads(content.strip().split("\n")[0])
    assert record["trace_id"] == "t1"
    assert record["name"] == "test.span"
    emitter.close()


def test_emitter_emit_dict(tmp_path: Path) -> None:
    emitter = init_tracing(
        trace_dir=str(tmp_path / "traces"),
        buffer_size=1,
    )
    emitter.emit({"type": "span", "trace_id": "t2", "name": "dict.span"})
    emitter.flush()
    trace_dir = tmp_path / "traces"
    files = list(trace_dir.glob("trace_*.jsonl"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    record = json.loads(content.strip().split("\n")[0])
    assert record["trace_id"] == "t2"
    assert record["name"] == "dict.span"
    emitter.close()


def test_emitter_jsonl_file_written(tmp_path: Path) -> None:
    emitter = init_tracing(
        trace_dir=str(tmp_path / "traces"),
        buffer_size=1,
    )
    s = Span(trace_id="t3", name="file.test")
    emitter.emit(s)
    emitter.flush()
    emitter.close()

    trace_dir = tmp_path / "traces"
    files = list(trace_dir.glob("trace_*.jsonl"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    record = json.loads(content.strip().split("\n")[0])
    assert record["trace_id"] == "t3"
    assert record["name"] == "file.test"


def test_get_recent_spans_includes_disk_after_flush(tmp_path: Path) -> None:
    """flush 会清空 buffer，但 Trace API 应仍能从 JSONL 读到最近 span。"""
    emitter = init_tracing(
        trace_dir=str(tmp_path / "traces"),
        rotation="1 MB",
        retention_days=1,
        buffer_size=1,
    )
    s = Span(trace_id="disk-visible", name="after.flush")
    emitter.emit(s)
    emitter.flush()
    recent = emitter.get_recent_spans(50)
    assert len(recent) >= 1
    assert recent[-1]["trace_id"] == "disk-visible"
    assert recent[-1]["name"] == "after.flush"
    emitter.close()


def test_emitter_query_by_trace_id(tmp_path: Path) -> None:
    emitter = init_tracing(
        trace_dir=str(tmp_path / "traces"),
        buffer_size=1,
    )
    for i in range(3):
        s = Span(trace_id="query-trace", name=f"span-{i}")
        s.seq = i
        emitter.emit(s)
    emitter.flush()

    results = emitter.query_by_trace_id("query-trace")
    assert len(results) == 3
    assert all(r["trace_id"] == "query-trace" for r in results)


def test_emitter_query_by_session(tmp_path: Path) -> None:
    emitter = init_tracing(
        trace_dir=str(tmp_path / "traces"),
        buffer_size=1,
    )
    emitter.emit(Span(trace_id="s1", name="span-a", attrs={"session_key": "telegram:123"}))
    emitter.emit(Span(trace_id="s2", name="span-b", attrs={"session_key": "telegram:456"}))
    emitter.emit(Span(trace_id="s3", name="span-c", attrs={"session_key": "telegram:123"}))
    emitter.flush()

    results = emitter.query_by_session("telegram:123")
    assert len(results) == 2
    assert all(r["attrs"]["session_key"] == "telegram:123" for r in results)


def test_emitter_disabled_is_noop(tmp_path: Path) -> None:
    emitter = init_tracing(
        trace_dir=str(tmp_path / "traces"),
        enabled=False,
        buffer_size=1,
    )
    emitter.emit(Span(trace_id="t1", name="disabled.test"))
    assert len(emitter._buffer) == 0


# ---------------------------------------------------------------------------
# Integration: span with emitter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_span_auto_emit_on_exit(tmp_path: Path) -> None:
    emitter = init_tracing(
        trace_dir=str(tmp_path / "traces"),
        buffer_size=9999,  # no auto-flush during test; check buffer directly
    )
    from nanobot.tracing.spans import _get_emitter
    assert _get_emitter() is emitter

    async with span("auto.emit", attrs={"inner": True}) as s:
        pass

    # Span should have been auto-emitted to buffer
    assert len(emitter._buffer) == 1
    record = emitter._buffer[0]
    assert record["name"] == "auto.emit"
    assert record["attrs"]["inner"] is True
    emitter.close()


@pytest.mark.asyncio
async def test_span_error_status_on_exception(tmp_path: Path) -> None:
    emitter = init_tracing(
        trace_dir=str(tmp_path / "traces"),
        buffer_size=9999,
    )
    try:
        async with span("error.test") as s:
            raise ValueError("boom")
    except ValueError:
        pass

    assert len(emitter._buffer) == 1
    record = emitter._buffer[0]
    assert record["status"] == "error"
    assert record["attrs"]["error_type"] == "ValueError"
    assert "boom" in record["attrs"]["error_msg"]
    emitter.close()


# ---------------------------------------------------------------------------
# End-to-end: trace_context + spans + emitter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_trace_chain(tmp_path: Path) -> None:
    """Simulate a full agent.turn → llm.inference → tool.execute chain."""
    emitter = init_tracing(trace_dir=str(tmp_path / "traces"), buffer_size=1)

    async with trace_context("full-trace", "agent.turn", attrs={"channel": "telegram"}) as root:
        assert root.trace_id == "full-trace"
        assert root.name == "agent.turn"
        assert root.attrs["channel"] == "telegram"

        async with span("llm.inference", attrs={"model": "claude-3"}) as llm:
            assert llm.parent_id == root.span_id
            async with span("tool.execute", attrs={"tool_name": "read_file"}) as tool:
                assert tool.parent_id == llm.span_id

    emitter.close()

    records = emitter.query_by_trace_id("full-trace")
    assert len(records) == 3
    # Verify hierarchy
    span_ids = {r["name"]: r for r in records}
    assert span_ids["agent.turn"]["parent_id"] is None
    assert span_ids["llm.inference"]["parent_id"] == span_ids["agent.turn"]["span_id"]
    assert span_ids["tool.execute"]["parent_id"] == span_ids["llm.inference"]["span_id"]
