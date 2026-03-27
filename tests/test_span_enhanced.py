"""Tests for enhanced Span class"""
import pytest
from nanobot.tracing.spans import Span, hash_args, truncate


def test_hash_args():
    args1 = {"file": "test.py", "line": 10}
    args2 = {"line": 10, "file": "test.py"}  # Same content, different order
    assert hash_args(args1) == hash_args(args2)


def test_truncate():
    long_str = "a" * 1000
    result = truncate(long_str, 100)
    # 100 chars + suffix "... (truncated, 1000 chars)" (27 chars) = 127
    assert len(result) == 127
    assert "truncated" in result


def test_span_tool_marking():
    span = Span(trace_id="tr_test", name="tool.execute")
    span.mark_tool_span("read_file", {"path": "test.py"})
    assert span.span_type == "tool"
    assert span.tool_name == "read_file"


def test_span_subagent_marking():
    span = Span(trace_id="tr_test", name="subagent.spawn")
    span.mark_subagent_span("sa_123", "analyze_code")
    assert span.span_type == "subagent"
    assert span.subagent_id == "sa_123"


def test_set_tool_result():
    span = Span(trace_id="tr_test", name="tool.execute")
    span.set_tool_result("success", {"data": "hello"}, None)
    assert span.tool_result["status"] == "success"
    assert "hello" in str(span.tool_result["result"])


def test_mark_evolution_candidate():
    span = Span(trace_id="tr_test", name="tool.execute")
    span.mark_evolution_candidate(["slow", "pattern"])
    assert span.evolution_candidate is True
    assert "slow" in span.pattern_tags


def test_to_dict_includes_new_fields():
    span = Span(trace_id="tr_test", name="tool.execute")
    span.mark_tool_span("read_file", {"path": "test.py"})
    d = span.to_dict()
    assert d["span_type"] == "tool"
    assert d["tool_name"] == "read_file"
    assert d["evolution_candidate"] is False
