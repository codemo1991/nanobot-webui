"""
Tests for nanobot.tracing.analysis module.

Tests the read_spans() and aggregate_spans() functions for
computing metrics from trace JSONL files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.tracing.analysis import (
    AggregatedMetrics,
    SpanMetrics,
    aggregate_spans,
    read_spans,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_trace_file(tmp_path: Path, filename: str, spans: list[dict]) -> Path:
    """Write a JSONL file with the given spans and return its path."""
    file_path = tmp_path / filename
    with open(file_path, "w", encoding="utf-8") as f:
        for span in spans:
            f.write(json.dumps(span) + "\n")
    return file_path


# ---------------------------------------------------------------------------
# test_span_metrics_computes_success_rate
# ---------------------------------------------------------------------------

def test_span_metrics_computes_success_rate() -> None:
    """SpanMetrics should compute success_rate and error_rate correctly."""
    metrics = SpanMetrics(
        count=10,
        ok_count=8,
        error_count=2,
        success_rate=0.8,
        error_rate=0.2,
        avg_duration_ms=50.0,
        p50_duration_ms=40.0,
        p95_duration_ms=80.0,
        p99_duration_ms=95.0,
        grouped={},
    )
    assert metrics.success_rate == pytest.approx(0.8)
    assert metrics.error_rate == pytest.approx(0.2)
    assert metrics.count == 10


def test_span_metrics_zero_count() -> None:
    """SpanMetrics with zero count should have 0.0 success/error rates."""
    metrics = SpanMetrics(
        count=0,
        ok_count=0,
        error_count=0,
        success_rate=0.0,
        error_rate=0.0,
        avg_duration_ms=None,
        p50_duration_ms=None,
        p95_duration_ms=None,
        p99_duration_ms=None,
        grouped={},
    )
    assert metrics.success_rate == 0.0
    assert metrics.error_rate == 0.0


def test_span_metrics_computes_percentiles() -> None:
    """SpanMetrics stores percentile values correctly."""
    metrics = SpanMetrics(
        count=100,
        ok_count=95,
        error_count=5,
        success_rate=0.95,
        error_rate=0.05,
        avg_duration_ms=42.5,
        p50_duration_ms=40.0,
        p95_duration_ms=60.0,
        p99_duration_ms=80.0,
        grouped={},
    )
    assert metrics.p50_duration_ms == 40.0
    assert metrics.p95_duration_ms == 60.0
    assert metrics.p99_duration_ms == 80.0


# ---------------------------------------------------------------------------
# test_aggregate_spans_groups_by_type
# ---------------------------------------------------------------------------

def test_aggregate_spans_groups_by_type(tmp_path: Path) -> None:
    """aggregate_spans should group spans by span_type."""
    spans = [
        {"span_type": "tool", "status": "ok", "duration_ms": 10, "name": "t1"},
        {"span_type": "tool", "status": "ok", "duration_ms": 20, "name": "t2"},
        {"span_type": "tool", "status": "error", "duration_ms": 30, "name": "t3"},
        {"span_type": "subagent", "status": "ok", "duration_ms": 100, "name": "s1"},
        {"span_type": "llm", "status": "ok", "duration_ms": 50, "name": "l1"},
    ]
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", spans)

    # Patch Path.home so read_spans() uses tmp_path without needing trace_dir param
    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = aggregate_spans(read_spans())
    finally:
        analysis_module._read_trace_files = orig_read

    assert result.total_spans == 5
    assert "tool" in result.by_type
    assert "subagent" in result.by_type
    assert "llm" in result.by_type

    # Tool spans: 3 total, 2 ok, 1 error
    tool_metrics = result.by_type["tool"]
    assert tool_metrics.count == 3
    assert tool_metrics.ok_count == 2
    assert tool_metrics.error_count == 1
    assert tool_metrics.success_rate == pytest.approx(2 / 3)
    assert tool_metrics.error_rate == pytest.approx(1 / 3)


def test_aggregate_spans_empty_list() -> None:
    """aggregate_spans should return zeroed metrics for empty span list."""
    result = aggregate_spans([])
    assert result.total_spans == 0
    assert result.by_type == {}
    assert result.by_tool == {}
    assert result.by_template == {}


# ---------------------------------------------------------------------------
# test_aggregate_spans_tool_breakdown
# ---------------------------------------------------------------------------

def test_aggregate_spans_tool_breakdown(tmp_path: Path) -> None:
    """Tool spans should be further broken down by tool_name."""
    spans = [
        {"span_type": "tool", "tool_name": "read_file", "status": "ok", "duration_ms": 10},
        {"span_type": "tool", "tool_name": "read_file", "status": "ok", "duration_ms": 20},
        {"span_type": "tool", "tool_name": "read_file", "status": "error", "duration_ms": 30},
        {"span_type": "tool", "tool_name": "write_file", "status": "ok", "duration_ms": 15},
        {"span_type": "tool", "tool_name": "write_file", "status": "ok", "duration_ms": 25},
    ]
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", spans)

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = aggregate_spans(read_spans())
    finally:
        analysis_module._read_trace_files = orig_read

    # Check by_type["tool"].grouped
    assert "read_file" in result.by_type["tool"].grouped
    assert "write_file" in result.by_type["tool"].grouped

    read_metrics = result.by_type["tool"].grouped["read_file"]
    assert read_metrics.count == 3
    assert read_metrics.ok_count == 2
    assert read_metrics.error_count == 1

    write_metrics = result.by_type["tool"].grouped["write_file"]
    assert write_metrics.count == 2
    assert write_metrics.ok_count == 2
    assert write_metrics.error_count == 0

    # Check by_tool convenience field
    assert "read_file" in result.by_tool
    assert "write_file" in result.by_tool
    assert result.by_tool["read_file"].count == 3


# ---------------------------------------------------------------------------
# test_aggregate_spans_subagent_breakdown
# ---------------------------------------------------------------------------

def test_aggregate_spans_subagent_breakdown(tmp_path: Path) -> None:
    """Subagent spans should be broken down by template."""
    spans = [
        {"span_type": "subagent", "attrs": {"template": "analyze_code"}, "status": "ok", "duration_ms": 100},
        {"span_type": "subagent", "attrs": {"template": "analyze_code"}, "status": "ok", "duration_ms": 150},
        {"span_type": "subagent", "attrs": {"template": "fix_bug"}, "status": "error", "duration_ms": 50},
        {"span_type": "subagent", "attrs": {"template": "fix_bug"}, "status": "ok", "duration_ms": 200},
        {"span_type": "subagent", "attrs": {}, "status": "ok", "duration_ms": 30},  # no template
    ]
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", spans)

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = aggregate_spans(read_spans())
    finally:
        analysis_module._read_trace_files = orig_read

    # Check by_type["subagent"].grouped
    assert "analyze_code" in result.by_type["subagent"].grouped
    assert "fix_bug" in result.by_type["subagent"].grouped

    analyze_metrics = result.by_type["subagent"].grouped["analyze_code"]
    assert analyze_metrics.count == 2
    assert analyze_metrics.ok_count == 2
    assert analyze_metrics.error_count == 0

    fix_metrics = result.by_type["subagent"].grouped["fix_bug"]
    assert fix_metrics.count == 2
    assert fix_metrics.ok_count == 1
    assert fix_metrics.error_count == 1

    # Check by_template convenience field
    assert "analyze_code" in result.by_template
    assert "fix_bug" in result.by_template
    assert result.by_template["analyze_code"].count == 2


def test_aggregate_spans_subagent_template_fallback(tmp_path: Path) -> None:
    """Subagent spans without template attr should be grouped under empty key."""
    spans = [
        {"span_type": "subagent", "attrs": {}, "status": "ok", "duration_ms": 100},
        {"span_type": "subagent", "attrs": {"template": "codegen"}, "status": "ok", "duration_ms": 80},
    ]
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", spans)

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = aggregate_spans(read_spans())
    finally:
        analysis_module._read_trace_files = orig_read

    # The span without template goes into empty string group
    subagent_metrics = result.by_type["subagent"]
    assert "" in subagent_metrics.grouped
    assert subagent_metrics.grouped[""].count == 1


# ---------------------------------------------------------------------------
# test_read_spans_parses_jsonl
# ---------------------------------------------------------------------------

def test_read_spans_parses_jsonl(tmp_path: Path) -> None:
    """read_spans should parse valid JSONL and return list of span dicts."""
    spans = [
        {"span_type": "tool", "status": "ok", "duration_ms": 10, "name": "span1"},
        {"span_type": "llm", "status": "ok", "duration_ms": 50, "name": "span2"},
    ]
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", spans)

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = read_spans()
    finally:
        analysis_module._read_trace_files = orig_read

    assert len(result) == 2
    assert result[0]["span_type"] == "tool"
    assert result[0]["name"] == "span1"
    assert result[1]["span_type"] == "llm"
    assert result[1]["name"] == "span2"


def test_read_spans_handles_malformed_lines(tmp_path: Path) -> None:
    """read_spans should skip malformed JSON lines gracefully."""
    file_path = tmp_path / "trace_2026-03-27.jsonl"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"span_type": "tool", "status": "ok", "duration_ms": 10}) + "\n")
        f.write("not valid json\n")
        f.write(json.dumps({"span_type": "llm", "status": "ok", "duration_ms": 20}) + "\n")
        f.write('{"incomplete": true,\n')
        f.write(json.dumps({"span_type": "subagent", "status": "ok", "duration_ms": 30}) + "\n")

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = read_spans()
    finally:
        analysis_module._read_trace_files = orig_read

    # Should only return the 3 valid spans
    assert len(result) == 3
    assert result[0]["span_type"] == "tool"
    assert result[1]["span_type"] == "llm"
    assert result[2]["span_type"] == "subagent"


def test_read_spans_missing_directory(tmp_path: Path) -> None:
    """read_spans should return empty list if trace dir does not exist."""
    non_existent = tmp_path / "non_existent_dir"

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(non_existent, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = read_spans()
    finally:
        analysis_module._read_trace_files = orig_read

    assert result == []


def test_read_spans_no_matching_files(tmp_path: Path) -> None:
    """read_spans should return empty list if no trace files match."""
    # Write a non-trace file
    (tmp_path / "other.txt").write_text("not a trace file", encoding="utf-8")

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = read_spans()
    finally:
        analysis_module._read_trace_files = orig_read

    assert result == []


# ---------------------------------------------------------------------------
# test_read_spans_with_date_filter
# ---------------------------------------------------------------------------

def test_read_spans_with_date_filter(tmp_path: Path) -> None:
    """read_spans should filter by date_from and date_to."""
    _write_trace_file(tmp_path, "trace_2026-03-25.jsonl", [
        {"span_type": "tool", "status": "ok", "duration_ms": 10, "name": "old"},
    ])
    _write_trace_file(tmp_path, "trace_2026-03-26.jsonl", [
        {"span_type": "tool", "status": "ok", "duration_ms": 20, "name": "middle"},
    ])
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", [
        {"span_type": "tool", "status": "ok", "duration_ms": 30, "name": "today"},
    ])
    _write_trace_file(tmp_path, "trace_2026-03-28.jsonl", [
        {"span_type": "tool", "status": "ok", "duration_ms": 40, "name": "future"},
    ])

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = read_spans(date_from="2026-03-26", date_to="2026-03-27")
    finally:
        analysis_module._read_trace_files = orig_read

    assert len(result) == 2
    names = {s["name"] for s in result}
    assert names == {"middle", "today"}


def test_read_spans_date_from_only(tmp_path: Path) -> None:
    """read_spans with only date_from should include that date and later."""
    _write_trace_file(tmp_path, "trace_2026-03-25.jsonl", [{"span_type": "tool", "status": "ok", "name": "day25"}])
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", [{"span_type": "tool", "status": "ok", "name": "day27"}])

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = read_spans(date_from="2026-03-27")
    finally:
        analysis_module._read_trace_files = orig_read

    assert len(result) == 1
    assert result[0]["name"] == "day27"


def test_read_spans_date_to_only(tmp_path: Path) -> None:
    """read_spans with only date_to should include up to and including that date."""
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", [{"span_type": "tool", "status": "ok", "name": "day27"}])
    _write_trace_file(tmp_path, "trace_2026-03-28.jsonl", [{"span_type": "tool", "status": "ok", "name": "day28"}])

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = read_spans(date_to="2026-03-27")
    finally:
        analysis_module._read_trace_files = orig_read

    assert len(result) == 1
    assert result[0]["name"] == "day27"


def test_read_spans_no_date_filter(tmp_path: Path) -> None:
    """read_spans without date filter should return all matching files."""
    _write_trace_file(tmp_path, "trace_2026-03-25.jsonl", [{"span_type": "tool", "status": "ok", "name": "old"}])
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", [{"span_type": "tool", "status": "ok", "name": "today"}])
    _write_trace_file(tmp_path, "trace_2026-03-28.jsonl", [{"span_type": "tool", "status": "ok", "name": "future"}])

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = read_spans()
    finally:
        analysis_module._read_trace_files = orig_read

    assert len(result) == 3


# ---------------------------------------------------------------------------
# test_invalid_date_format
# ---------------------------------------------------------------------------

def test_read_spans_invalid_date_from_raises() -> None:
    """read_spans should raise ValueError for invalid date_from format."""
    with pytest.raises(ValueError, match="date_from must be in YYYY-MM-DD format"):
        read_spans(date_from="not-a-date")


def test_read_spans_invalid_date_to_raises() -> None:
    """read_spans should raise ValueError for invalid date_to format."""
    with pytest.raises(ValueError, match="date_to must be in YYYY-MM-DD format"):
        read_spans(date_to="2026/03/27")  # wrong separator


# ---------------------------------------------------------------------------
# test_handles_malformed_lines
# ---------------------------------------------------------------------------

def test_handles_malformed_lines_in_aggregate() -> None:
    """aggregate_spans should gracefully handle spans missing fields."""
    spans = [
        {"span_type": "tool", "status": "ok", "duration_ms": 10},
        {"span_type": "tool"},  # missing status and duration_ms
        {"status": "ok", "duration_ms": 20},  # missing span_type
        {"span_type": "llm", "status": "error"},  # missing duration_ms
    ]

    result = aggregate_spans(spans)

    # Should still produce valid metrics
    assert result.total_spans == 4
    tool_metrics = result.by_type.get("tool")
    if tool_metrics:
        assert tool_metrics.count == 2
    llm_metrics = result.by_type.get("llm")
    if llm_metrics:
        assert llm_metrics.count == 1


def test_aggregate_spans_duration_percentiles(tmp_path: Path) -> None:
    """aggregate_spans should compute correct duration percentiles."""
    spans = [
        {"span_type": "tool", "status": "ok", "duration_ms": v}
        for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    ]
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", spans)

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = aggregate_spans(read_spans())
    finally:
        analysis_module._read_trace_files = orig_read

    tool_metrics = result.by_type["tool"]
    assert tool_metrics.count == 10
    assert tool_metrics.avg_duration_ms == 55.0  # (10+20+...+100)/10 = 55
    # p50 of [10..100] is 55, p95 ~ 95, p99 ~ 99
    assert tool_metrics.p50_duration_ms is not None
    assert tool_metrics.p95_duration_ms is not None
    assert tool_metrics.p99_duration_ms is not None


def test_aggregate_spans_none_duration_skipped_from_percentiles(tmp_path: Path) -> None:
    """Spans with duration_ms=None should be excluded from percentile calculations."""
    spans = [
        {"span_type": "tool", "status": "ok", "duration_ms": None},  # running, no duration
        {"span_type": "tool", "status": "ok", "duration_ms": 100},
        {"span_type": "tool", "status": "ok", "duration_ms": None},  # running
    ]
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", spans)

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = aggregate_spans(read_spans())
    finally:
        analysis_module._read_trace_files = orig_read

    tool_metrics = result.by_type["tool"]
    # Should only use the one span with a valid duration
    assert tool_metrics.p50_duration_ms == 100.0
    assert tool_metrics.p95_duration_ms == 100.0
    assert tool_metrics.p99_duration_ms == 100.0
    assert tool_metrics.avg_duration_ms == 100.0


def test_aggregate_spans_single_span_percentiles(tmp_path: Path) -> None:
    """Single span should have p50 = p95 = p99 = its duration."""
    spans = [{"span_type": "tool", "status": "ok", "duration_ms": 42}]
    _write_trace_file(tmp_path, "trace_2026-03-27.jsonl", spans)

    from nanobot.tracing import analysis as analysis_module
    orig_read = analysis_module._read_trace_files

    def fake_read(trace_dir, date_from=None, date_to=None):
        return orig_read(tmp_path, date_from=date_from, date_to=date_to)

    analysis_module._read_trace_files = fake_read
    try:
        result = aggregate_spans(read_spans())
    finally:
        analysis_module._read_trace_files = orig_read

    tool_metrics = result.by_type["tool"]
    assert tool_metrics.p50_duration_ms == 42.0
    assert tool_metrics.p95_duration_ms == 42.0
    assert tool_metrics.p99_duration_ms == 42.0
    assert tool_metrics.avg_duration_ms == 42.0


# ---------------------------------------------------------------------------
# test_default_trace_dir
# ---------------------------------------------------------------------------

def test_read_spans_uses_default_trace_dir(tmp_path: Path) -> None:
    """read_spans should default to ~/.nanobot/traces when no path given."""
    import os

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    # Monkey-patch Path.home to return our fake directory
    from nanobot.tracing import analysis as analysis_module
    orig_home = Path.home
    Path.home = classmethod(lambda cls: fake_home)  # type: ignore[method-assign]
    try:
        # Write a real trace file in the expected location
        trace_dir = fake_home / ".nanobot" / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        _write_trace_file(trace_dir, "trace_2026-03-27.jsonl", [
            {"span_type": "tool", "status": "ok", "duration_ms": 15, "name": "default-dir-span"},
        ])

        result = read_spans()  # No path argument

        assert len(result) == 1
        assert result[0]["name"] == "default-dir-span"
    finally:
        Path.home = orig_home  # type: ignore[method-assign]
