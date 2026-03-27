"""
Trace analysis module — reads spans from JSONL files and computes aggregation metrics.

This module provides tools for offline analysis of trace data:
- `read_spans()` reads spans from `~/.nanobot/traces/trace_YYYY-MM-DD.jsonl` files
- `aggregate_spans()` computes per-type, per-tool, and per-template metrics

All computations use only stdlib; no numpy or pandas required.

Quick start::

    from nanobot.tracing.analysis import read_spans, aggregate_spans

    # Read all spans from the last 7 days
    spans = read_spans()

    # Compute aggregate metrics
    metrics = aggregate_spans(spans)
    print(f"Total spans: {metrics.total_spans}")
    print(f"Tool success rates: {metrics.by_tool}")
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SpanMetrics:
    """
    Aggregated metrics for a group of spans.

    Attributes:
        count: Total number of spans in the group.
        ok_count: Number of spans with status == "ok".
        error_count: Number of spans with status == "error".
        success_rate: ok_count / count (0.0 if count == 0).
        error_rate: error_count / count (0.0 if count == 0).
        avg_duration_ms: Mean duration in milliseconds (None if no valid durations).
        p50_duration_ms: 50th percentile of duration (None if no valid durations).
        p95_duration_ms: 95th percentile of duration (None if no valid durations).
        p99_duration_ms: 99th percentile of duration (None if no valid durations).
        grouped: Sub-group metrics, keyed by tool_name or template.
    """

    count: int = 0
    ok_count: int = 0
    error_count: int = 0
    success_rate: float = 0.0
    error_rate: float = 0.0
    avg_duration_ms: float | None = None
    p50_duration_ms: float | None = None
    p95_duration_ms: float | None = None
    p99_duration_ms: float | None = None
    grouped: dict[str, "SpanMetrics"] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-serializable dict."""
        return {
            "count": self.count,
            "ok_count": self.ok_count,
            "error_count": self.error_count,
            "success_rate": self.success_rate,
            "error_rate": self.error_rate,
            "avg_duration_ms": self.avg_duration_ms,
            "p50_duration_ms": self.p50_duration_ms,
            "p95_duration_ms": self.p95_duration_ms,
            "p99_duration_ms": self.p99_duration_ms,
            "grouped": {k: v.to_dict() for k, v in self.grouped.items()},
        }


@dataclass
class AggregatedMetrics:
    """
    Top-level aggregated metrics from a set of spans.

    Attributes:
        total_spans: Total number of spans processed.
        by_type: Metrics grouped by span_type (tool, subagent, llm, etc.).
        by_template: Convenience view of subagent spans broken down by template.
        by_tool: Convenience view of tool spans broken down by tool_name.
    """

    total_spans: int = 0
    by_type: dict[str, SpanMetrics] = field(default_factory=dict)
    by_template: dict[str, SpanMetrics] = field(default_factory=dict)
    by_tool: dict[str, SpanMetrics] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-serializable dict."""
        return {
            "total_spans": self.total_spans,
            "by_type": {k: v.to_dict() for k, v in self.by_type.items()},
            "by_template": {k: v.to_dict() for k, v in self.by_template.items()},
            "by_tool": {k: v.to_dict() for k, v in self.by_tool.items()},
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_percentiles(values: list[float]) -> tuple[float | None, float | None, float | None]:
    """
    Compute p50, p95, p99 percentiles from a list of values.

    Uses linear interpolation (same method as numpy.percentile with midpoint
    interpolation). Returns None for each percentile when the input list is empty.

    Args:
        values: List of numeric values to compute percentiles from.

    Returns:
        A tuple of (p50, p95, p99). Each value is None if the input is empty.
    """
    if not values:
        return None, None, None

    if len(values) == 1:
        v = float(values[0])
        return v, v, v

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def _percentile(p: float) -> float:
        """Linear interpolation percentile."""
        index = (p / 100.0) * (n - 1)
        lower = int(index)
        fraction = index - lower
        if lower >= n - 1:
            return sorted_vals[-1]
        return sorted_vals[lower] + fraction * (sorted_vals[lower + 1] - sorted_vals[lower])

    return _percentile(50), _percentile(95), _percentile(99)


def _build_span_metrics(spans: list[dict[str, Any]]) -> SpanMetrics:
    """
    Build a SpanMetrics object from a list of span dicts.

    Handles spans with missing or None duration_ms gracefully.

    Args:
        spans: List of span dictionaries.

    Returns:
        A SpanMetrics dataclass with computed aggregates.
    """
    count = len(spans)
    if count == 0:
        return SpanMetrics()

    ok_count = sum(1 for s in spans if s.get("status") == "ok")
    error_count = sum(1 for s in spans if s.get("status") == "error")

    # Gather valid durations
    durations: list[float] = []
    for s in spans:
        d = s.get("duration_ms")
        if d is not None:
            try:
                durations.append(float(d))
            except (TypeError, ValueError):
                pass

    avg: float | None = statistics.mean(durations) if durations else None
    p50, p95, p99 = _compute_percentiles(durations)

    return SpanMetrics(
        count=count,
        ok_count=ok_count,
        error_count=error_count,
        success_rate=ok_count / count,
        error_rate=error_count / count,
        avg_duration_ms=avg,
        p50_duration_ms=p50,
        p95_duration_ms=p95,
        p99_duration_ms=p99,
        grouped={},
    )


def _parse_date(value: str | None, param_name: str) -> datetime | None:
    """Parse a YYYY-MM-DD date string, raising a descriptive ValueError on failure."""
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"{param_name} must be in YYYY-MM-DD format, got {value!r}"
        ) from None


def _read_trace_files(
    trace_dir: Path,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read and parse all matching trace JSONL files.

    Filters by date range when date_from/date_to are provided.

    Args:
        trace_dir: Path to the traces directory.
        date_from: Start date in "YYYY-MM-DD" format (inclusive).
        date_to: End date in "YYYY-MM-DD" format (inclusive).

    Returns:
        List of parsed span dictionaries.
    """
    if not trace_dir.is_dir():
        return []

    # Parse date bounds
    from_dt = _parse_date(date_from, "date_from")
    to_dt = _parse_date(date_to, "date_to")

    results: list[dict[str, Any]] = []

    for fpath in sorted(trace_dir.glob("trace_*.jsonl")):
        # Extract date from filename: trace_YYYY-MM-DD.jsonl
        stem = fpath.stem  # e.g. "trace_2026-03-27"
        if not stem.startswith("trace_"):
            continue
        date_str = stem[len("trace_") :]
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        # Apply date range filter
        if from_dt and file_date < from_dt:
            continue
        if to_dt and file_date > to_dt:
            continue

        # Parse JSONL
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if isinstance(record, dict):
                            results.append(record)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_spans(
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read spans from trace JSONL files in ``~/.nanobot/traces/``.

    Each line of a trace file is parsed as a JSON object representing a span.

    Args:
        date_from: Optional start date in "YYYY-MM-DD" format (inclusive).
        date_to: Optional end date in "YYYY-MM-DD" format (inclusive).

    Returns:
        List of span dictionaries. Malformed lines are skipped silently.
    """
    trace_dir = Path.home() / ".nanobot" / "traces"
    return _read_trace_files(trace_dir, date_from=date_from, date_to=date_to)


def aggregate_spans(spans: list[dict[str, Any]]) -> AggregatedMetrics:
    """
    Aggregate spans into per-type, per-tool, and per-template metrics.

    Args:
        spans: List of span dictionaries (as returned by ``read_spans``).

    Returns:
        An ``AggregatedMetrics`` object with computed statistics.

    Groupings:
        - ``by_type``: spans grouped by ``span_type`` field.
        - ``by_tool``: tool spans (``span_type == "tool"``) grouped by ``tool_name``.
        - ``by_template``: subagent spans (``span_type == "subagent"``) grouped by
          the ``template`` key in the ``attrs`` dict.

    Example::

        metrics = aggregate_spans(spans)
        print(metrics.by_type["tool"].success_rate)
        print(metrics.by_tool["read_file"].avg_duration_ms)
    """
    if not spans:
        return AggregatedMetrics()

    # Group by span_type
    by_type: dict[str, list[dict[str, Any]]] = {}
    for s in spans:
        stype = s.get("span_type", "")
        if stype not in by_type:
            by_type[stype] = []
        by_type[stype].append(s)

    result = AggregatedMetrics(total_spans=len(spans))

    # Build metrics for each type
    for stype, type_spans in by_type.items():
        metrics = _build_span_metrics(type_spans)
        result.by_type[stype] = metrics

        # Tool breakdown by tool_name
        if stype == "tool":
            by_tool_name: dict[str, list[dict[str, Any]]] = {}
            for s in type_spans:
                tname = s.get("tool_name", "") or ""
                if tname not in by_tool_name:
                    by_tool_name[tname] = []
                by_tool_name[tname].append(s)

            for tname, tool_spans in by_tool_name.items():
                tool_metrics = _build_span_metrics(tool_spans)
                result.by_type[stype].grouped[tname] = tool_metrics
                result.by_tool[tname] = tool_metrics

        # Subagent breakdown by template
        if stype == "subagent":
            by_template: dict[str, list[dict[str, Any]]] = {}
            for s in type_spans:
                tmpl = s.get("attrs", {}).get("template", "") or ""
                if tmpl not in by_template:
                    by_template[tmpl] = []
                by_template[tmpl].append(s)

            for tmpl, sub_spans in by_template.items():
                sub_metrics = _build_span_metrics(sub_spans)
                result.by_type[stype].grouped[tmpl] = sub_metrics
                result.by_template[tmpl] = sub_metrics

    return result
