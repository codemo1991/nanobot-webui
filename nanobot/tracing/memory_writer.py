"""
Trace-based memory writer for the Claude Code memory system.

Writes structured learnings from trace analysis into the memory system at:
  C:\\Users\\GYENNO\\.claude\\projects\\E--workSpace-nanobot-webui\\memory\\

Memory file format::

    ---
    name: {descriptive name}
    description: {one-line description}
    type: {type}
    ---

    {free-form content}

Example usage::

    from nanobot.tracing.memory_writer import TraceMemoryWriter

    writer = TraceMemoryWriter()
    result = writer.write_error_pattern(
        tool_name="Bash",
        error_rate=0.15,
        span_count=40,
        suggestion="Bash tool times out on long-running commands",
    )
    assert result.success

    from nanobot.tracing.analysis import read_spans, aggregate_spans
    from nanobot.tracing.evolution import EvolutionTrigger

    spans = read_spans()
    metrics = aggregate_spans(spans)

    trigger = EvolutionTrigger()
    evolution = trigger.evaluate(metrics)

    result = writer.write_summary(metrics, evolution)
    assert result.success
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.tracing.analysis import AggregatedMetrics, SpanMetrics

# ---------------------------------------------------------------------------
# Default memory directory
# ---------------------------------------------------------------------------

_DEFAULT_MEMORY_DIR = (
    Path.home()
    / ".claude"
    / "projects"
    / "E--workSpace-nanobot-webui"
    / "memory"
)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class MemoryWriteResult:
    """Result of writing trace-based memories."""

    success: bool
    files_written: list[str]
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_filename(name: str, max_len: int = 100) -> str:
    """
    Convert a tool/template name into a safe filename.

    - Replaces ``/`` and ``\\`` with ``_``
    - Strips leading/trailing whitespace
    - Returns an empty string when the result is empty
    - Truncates to *max_len* characters

    Args:
        name: Raw tool or template name.
        max_len: Maximum filename length.

    Returns:
        A filesystem-safe filename string (empty if name contains only slashes).
    """
    safe = name.replace("/", "_").replace("\\", "_").strip().rstrip("_")
    return safe[:max_len] if safe else safe


def _frontmatter(name: str, description: str, mem_type: str) -> str:
    """Return a memory file frontmatter block."""
    return f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\n"


def _write_memory_file(path: Path, content: str) -> MemoryWriteResult:
    """
    Write a memory file, creating parent directories as needed.

    Args:
        path: Absolute path to the file to write.
        content: File content (assumed to be UTF-8 text).

    Returns:
        MemoryWriteResult with ``success=True`` and the written path on success,
        or ``success=False`` and the exception message on failure.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return MemoryWriteResult(success=True, files_written=[str(path)])
    except OSError as exc:  # pragma: no cover — OSError only pathologically
        return MemoryWriteResult(success=False, files_written=[], error=str(exc))


# ---------------------------------------------------------------------------
# TraceMemoryWriter
# ---------------------------------------------------------------------------


class TraceMemoryWriter:
    """
    Writes structured learnings from trace analysis into the Claude Code
    memory system.

    Args:
        memory_dir: Root memory directory. Defaults to
            ``~/.claude/projects/E--workSpace-nanobot-webui/memory/``.

    The directory layout is::

        {memory_dir}/
        ├── feedback/          ← recurring error patterns
        ├── reference/         ← latency insights and daily summaries
        └── project/           ← project-level memory entries
    """

    def __init__(self, memory_dir: Path | str | None = None) -> None:
        if memory_dir is None:
            self._memory_dir: Path = _DEFAULT_MEMORY_DIR
        else:
            self._memory_dir = Path(memory_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_error_pattern(
        self,
        tool_name: str,
        error_rate: float,
        span_count: int,
        suggestion: str,
    ) -> MemoryWriteResult:
        """
        Write a memory entry documenting a recurring error pattern.

        File: ``{memory_dir}/feedback/{tool_name}_error_pattern.md``

        Format::

            ---
            name: {tool_name} error pattern
            description: {error_rate:.1%} error rate across {span_count} spans
            type: feedback
            ---

            {suggestion}
        """
        safe_name = _safe_filename(tool_name)
        filename = f"{safe_name}_error_pattern.md"
        path = self._memory_dir / "feedback" / filename

        name_field = f"{tool_name} error pattern"
        desc_field = f"{error_rate:.1%} error rate across {span_count} spans"
        body = _frontmatter(name_field, desc_field, "feedback") + suggestion + "\n"

        return _write_memory_file(path, body)

    def write_latency_insight(
        self,
        tool_name: str | None,
        template: str | None,
        p95_ms: float,
        span_count: int,
    ) -> MemoryWriteResult:
        """
        Write a memory entry documenting a latency observation.

        File: ``{memory_dir}/reference/{name}_latency.md``

        Where *name* is the ``tool_name`` or ``template`` (whichever is set).
        Slashes in the name are replaced with ``_``.

        Format::

            ---
            name: {name} latency
            description: p95={p95_ms:.0f}ms across {span_count} spans
            type: reference
            ---

            Observed p95 latency for {name}: {p95_ms:.0f}ms over {span_count} spans.
        """
        if tool_name is not None:
            name = tool_name
        elif template is not None:
            name = template
        else:
            return MemoryWriteResult(
                success=False,
                files_written=[],
                error="Either tool_name or template must be provided",
            )

        safe_name = _safe_filename(name)
        filename = f"{safe_name}_latency.md"
        path = self._memory_dir / "reference" / filename

        name_field = f"{name} latency"
        desc_field = f"p95={p95_ms:.0f}ms across {span_count} spans"
        body = _frontmatter(name_field, desc_field, "reference") + f"Observed p95 latency for {name}: {p95_ms:.0f}ms over {span_count} spans.\n"

        return _write_memory_file(path, body)

    def write_summary(
        self,
        metrics: AggregatedMetrics,
        evolution: "EvolutionRecommendation | None" = None,  # type: ignore[name-defined]
    ) -> MemoryWriteResult:
        """
        Write a daily summary of trace metrics.

        File: ``{memory_dir}/reference/trace_summary_{YYYY-MM-DD}.md``

        Format::

            ---
            name: Trace summary {date}
            description: {total_spans} spans across {N} types
            type: reference
            ---

            ## Metrics by Type

            | Type | Count | Success Rate | Error Rate | Avg ms | P95 ms |
            |------|-------|--------------|------------|--------|--------|
            | ...  | ...   | ...          | ...        | ...    | ...    |

            {optional anomaly table}
        """
        today = date.today()
        filename = f"trace_summary_{today.isoformat()}.md"
        path = self._memory_dir / "reference" / filename

        # Build the by-type table
        rows: list[str] = [
            "| Type | Count | Success Rate | Error Rate | Avg ms | P95 ms |",
            "|------|-------|--------------|------------|--------|--------|",
        ]
        for stype, sm in sorted(metrics.by_type.items()):
            avg_str = f"{sm.avg_duration_ms:.1f}" if sm.avg_duration_ms is not None else "N/A"
            p95_str = f"{sm.p95_duration_ms:.1f}" if sm.p95_duration_ms is not None else "N/A"
            rows.append(
                f"| {stype} | {sm.count} | {sm.success_rate:.1%} | "
                f"{sm.error_rate:.1%} | {avg_str} | {p95_str} |"
            )

        # Build body
        name_field = f"Trace summary {today.isoformat()}"
        desc_field = f"{metrics.total_spans} spans across {len(metrics.by_type)} types"

        lines: list[str] = [
            _frontmatter(name_field, desc_field, "reference"),
            "## Metrics by Type",
            "",
            *rows,
        ]

        # Append anomaly table and action if evolution recommendations are present
        if evolution is not None:
            lines.append("")
            lines.append("## Top Anomalies")
            lines.append("")
            lines.append(
                "| Type | Group | Actual | Threshold | Spans | Suggestion |"
            )
            lines.append(
                "|------|-------|--------|-----------|-------|------------|"
            )
            for anomaly in evolution.anomalies[:5]:
                lines.append(
                    f"| {anomaly.anomaly_type} | {anomaly.group_key} | "
                    f"{anomaly.actual_value:.2%} | {anomaly.threshold:.2%} | "
                    f"{anomaly.span_count} | {anomaly.suggestion} |"
                )
            if evolution.suggested_action:
                lines.append("")
                lines.append("## Recommendation")
                lines.append("")
                lines.append(f"- {evolution.suggested_action}")

        lines.append("")
        body = "\n".join(lines)

        return _write_memory_file(path, body)

    def write_project_memory(
        self,
        key: str,
        value: str,
        reason: str,
    ) -> MemoryWriteResult:
        """
        Write a project-level memory entry.

        File: ``{memory_dir}/project/{key}.md``

        Format::

            ---
            name: {key}
            description: {reason}
            type: project
            ---

            {value}
        """
        safe_key = _safe_filename(key, max_len=80)
        filename = f"{safe_key}.md"
        path = self._memory_dir / "project" / filename

        name_field = key
        desc_field = reason
        body = _frontmatter(name_field, desc_field, "project") + value + "\n"

        return _write_memory_file(path, body)
