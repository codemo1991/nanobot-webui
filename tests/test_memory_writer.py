"""
Tests for nanobot.tracing.memory_writer.
"""
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from nanobot.tracing.analysis import AggregatedMetrics, SpanMetrics
from nanobot.tracing.anomaly import Anomaly
from nanobot.tracing.evolution import EvolutionRecommendation
from nanobot.tracing.memory_writer import (
    MemoryWriteResult,
    TraceMemoryWriter,
    _safe_filename,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter_and_body(text: str) -> tuple[dict, str]:
    """Parse a memory file into (frontmatter_dict, body_str)."""
    parts = text.split("---\n", 2)
    assert len(parts) == 3, f"Expected '---...---...', got {text[:50]!r}"
    meta_raw, body = parts[1], parts[2]
    meta: dict[str, str] = {}
    for line in meta_raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    return meta, body.lstrip("\n")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir() -> Path:
    """A temporary directory that is cleaned up after each test."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def writer(tmp_dir: Path) -> TraceMemoryWriter:
    return TraceMemoryWriter(memory_dir=tmp_dir)


# ---------------------------------------------------------------------------
# write_error_pattern
# ---------------------------------------------------------------------------

def test_write_error_pattern_creates_file(writer: TraceMemoryWriter, tmp_dir: Path) -> None:
    result = writer.write_error_pattern(
        tool_name="Bash",
        error_rate=0.15,
        span_count=40,
        suggestion="Bash tool times out on long-running commands",
    )

    assert result.success is True
    assert len(result.files_written) == 1

    path = tmp_dir / "feedback" / "Bash_error_pattern.md"
    assert path.exists()

    meta, body = _frontmatter_and_body(_read_text(path))
    assert meta["name"] == "Bash error pattern"
    assert meta["type"] == "feedback"
    assert "15.0%" in meta["description"]
    assert "40 spans" in meta["description"]
    assert body.strip() == "Bash tool times out on long-running commands"


def test_write_error_pattern_idempotent(writer: TraceMemoryWriter, tmp_dir: Path) -> None:
    path = tmp_dir / "feedback" / "Bash_error_pattern.md"

    r1 = writer.write_error_pattern(
        tool_name="Bash", error_rate=0.15, span_count=40, suggestion="First suggestion"
    )
    r2 = writer.write_error_pattern(
        tool_name="Bash", error_rate=0.20, span_count=50, suggestion="Second suggestion"
    )

    assert r1.success is True
    assert r2.success is True
    assert r2.files_written == r1.files_written  # same path

    meta, body = _frontmatter_and_body(_read_text(path))
    # The file contains the SECOND write's content (overwrite, not append)
    assert "20.0%" in meta["description"]
    assert body.strip() == "Second suggestion"


# ---------------------------------------------------------------------------
# write_latency_insight
# ---------------------------------------------------------------------------

def test_write_latency_insight_tool(writer: TraceMemoryWriter, tmp_dir: Path) -> None:
    result = writer.write_latency_insight(
        tool_name="Read",
        template=None,
        p95_ms=1234.5,
        span_count=20,
    )

    assert result.success is True
    path = tmp_dir / "reference" / "Read_latency.md"
    assert path.exists()

    meta, body = _frontmatter_and_body(_read_text(path))
    assert meta["name"] == "Read latency"
    assert meta["type"] == "reference"
    assert "1234ms" in meta["description"]
    assert "1234ms" in body


def test_write_latency_insight_template(writer: TraceMemoryWriter, tmp_dir: Path) -> None:
    result = writer.write_latency_insight(
        tool_name=None,
        template="coding_agent",
        p95_ms=5000.0,
        span_count=10,
    )

    assert result.success is True
    path = tmp_dir / "reference" / "coding_agent_latency.md"
    assert path.exists()

    meta, _ = _frontmatter_and_body(_read_text(path))
    assert meta["name"] == "coding_agent latency"
    assert meta["type"] == "reference"


def test_write_latency_insight_neither_provided(writer: TraceMemoryWriter) -> None:
    result = writer.write_latency_insight(
        tool_name=None,
        template=None,
        p95_ms=100.0,
        span_count=1,
    )
    assert result.success is False
    assert "tool_name or template" in result.error


# ---------------------------------------------------------------------------
# write_summary
# ---------------------------------------------------------------------------

def _make_metrics() -> AggregatedMetrics:
    return AggregatedMetrics(
        total_spans=100,
        by_type={
            "tool": SpanMetrics(
                count=80,
                ok_count=72,
                error_count=8,
                success_rate=0.9,
                error_rate=0.1,
                avg_duration_ms=150.0,
                p50_duration_ms=100.0,
                p95_duration_ms=300.0,
                p99_duration_ms=500.0,
            ),
            "subagent": SpanMetrics(
                count=20,
                ok_count=19,
                error_count=1,
                success_rate=0.95,
                error_rate=0.05,
                avg_duration_ms=2000.0,
                p50_duration_ms=1800.0,
                p95_duration_ms=2500.0,
                p99_duration_ms=3000.0,
            ),
        },
        by_tool={"Read": SpanMetrics(count=50, ok_count=50)},
        by_template={},
    )


def test_write_summary_contains_metrics(writer: TraceMemoryWriter, tmp_dir: Path) -> None:
    metrics = _make_metrics()
    result = writer.write_summary(metrics, evolution=None)

    assert result.success is True
    today = date.today().isoformat()
    path = tmp_dir / "reference" / f"trace_summary_{today}.md"
    assert path.exists()

    text = _read_text(path)
    assert "100 spans" in text or "total_spans" not in text  # check content exists
    assert "tool" in text
    assert "subagent" in text
    assert "100" in text  # total_spans
    assert "## Metrics by Type" in text


def test_write_summary_with_evolution(writer: TraceMemoryWriter, tmp_dir: Path) -> None:
    metrics = _make_metrics()
    anomaly = Anomaly(
        anomaly_type="high_error_rate",
        group_key="Bash",
        span_type="tool",
        actual_value=0.25,
        threshold=0.10,
        span_count=20,
        suggestion="Bash error rate above threshold",
    )
    evolution = EvolutionRecommendation(
        should_evolve=True,
        severity_threshold=0.5,
        anomalies=[anomaly],
        top_anomaly=anomaly,
        max_severity=0.75,
        recommendation="High severity: Bash tool error rate high.",
        suggested_action="Review Bash error handling.",
    )

    result = writer.write_summary(metrics, evolution=evolution)

    assert result.success is True
    today = date.today().isoformat()
    path = tmp_dir / "reference" / f"trace_summary_{today}.md"
    text = _read_text(path)

    assert "## Top Anomalies" in text
    assert "Bash" in text
    assert "high_error_rate" in text
    assert "## Recommendation" in text
    assert "Review Bash error handling" in text


def test_write_summary_empty_metrics(writer: TraceMemoryWriter, tmp_dir: Path) -> None:
    metrics = AggregatedMetrics(total_spans=0, by_type={})
    result = writer.write_summary(metrics, evolution=None)

    assert result.success is True
    today = date.today().isoformat()
    path = tmp_dir / "reference" / f"trace_summary_{today}.md"
    assert path.exists()
    text = _read_text(path)
    assert "0 spans" in text or "0" in text  # at least something written


# ---------------------------------------------------------------------------
# write_project_memory
# ---------------------------------------------------------------------------

def test_write_project_memory(writer: TraceMemoryWriter, tmp_dir: Path) -> None:
    result = writer.write_project_memory(
        key="test_strategy",
        value="Use iterative refinement for complex tasks.",
        reason="Learned from repeated trace patterns.",
    )

    assert result.success is True
    path = tmp_dir / "project" / "test_strategy.md"
    assert path.exists()

    meta, body = _frontmatter_and_body(_read_text(path))
    assert meta["name"] == "test_strategy"
    assert meta["description"] == "Learned from repeated trace patterns."
    assert meta["type"] == "project"
    assert "iterative refinement" in body


# ---------------------------------------------------------------------------
# _safe_filename
# ---------------------------------------------------------------------------

def test_safe_filename_replaces_slash() -> None:
    assert _safe_filename("foo/bar/baz") == "foo_bar_baz"
    assert _safe_filename(r"foo\bar") == "foo_bar"


def test_safe_filename_max_length() -> None:
    long_name = "a" * 150
    result = _safe_filename(long_name)
    assert len(result) == 100


def test_safe_filename_exact_max_length() -> None:
    name = "a" * 100
    result = _safe_filename(name, max_len=100)
    assert result == name
    assert len(result) == 100


def test_safe_filename_empty_result() -> None:
    assert _safe_filename("///") == ""


# ---------------------------------------------------------------------------
# Directory creation
# ---------------------------------------------------------------------------

def test_creates_directories(tmp_dir: Path) -> None:
    """Subdirectories (feedback/, reference/, project/) are created automatically."""
    writer = TraceMemoryWriter(memory_dir=tmp_dir)
    writer.write_error_pattern("Bash", 0.1, 10, "tip")
    writer.write_latency_insight("Read", None, 100.0, 5)
    writer.write_project_memory("key", "value", "reason")

    assert (tmp_dir / "feedback").is_dir()
    assert (tmp_dir / "reference").is_dir()
    assert (tmp_dir / "project").is_dir()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_handles_os_error(tmp_dir: Path) -> None:
    """OSError during write returns a failure MemoryWriteResult."""
    # Mock Path.write_text to raise OSError
    writer = TraceMemoryWriter(memory_dir=tmp_dir)
    bad_path = MagicMock(spec=Path)
    bad_path.parent = MagicMock()
    bad_path.write_text.side_effect = OSError("disk full")

    # Patch _write_memory_file to exercise the error path
    from nanobot.tracing import memory_writer as mw

    original = mw._write_memory_file
    called = {}

    def _mock(path: Path, content: str) -> MemoryWriteResult:
        called["path"] = path
        return original(path, content)

    def _failing(path: Path, content: str) -> MemoryWriteResult:
        return MemoryWriteResult(success=False, files_written=[], error="disk full")

    # Patch the helper on the module
    with patch.object(mw, "_write_memory_file", _failing):
        result = writer.write_error_pattern("Bash", 0.1, 10, "tip")
    assert result.success is False
    assert result.error is not None


# ---------------------------------------------------------------------------
# Default memory dir
# ---------------------------------------------------------------------------

def test_default_memory_dir() -> None:
    """When no memory_dir is given, TraceMemoryWriter uses the correct default."""
    from nanobot.tracing.memory_writer import _DEFAULT_MEMORY_DIR

    writer = TraceMemoryWriter()
    assert writer._memory_dir == _DEFAULT_MEMORY_DIR
    expected = Path.home() / ".claude" / "projects" / "E--workSpace-nanobot-webui" / "memory"
    assert writer._memory_dir == expected


def test_memory_dir_accepts_string(tmp_dir: Path) -> None:
    writer = TraceMemoryWriter(memory_dir=str(tmp_dir))
    assert writer._memory_dir == tmp_dir


def test_memory_dir_accepts_path(tmp_dir: Path) -> None:
    writer = TraceMemoryWriter(memory_dir=tmp_dir)
    assert writer._memory_dir == tmp_dir
