"""
High-level trace analysis service that orchestrates the full analysis pipeline:
read spans → aggregate metrics → detect anomalies → generate recommendations
→ optionally persist to memory.

Designed to be called from a CLI command or a cron job.

Example usage::

    from nanobot.tracing.service import TraceAnalysisService

    service = TraceAnalysisService(date_from="2026-03-20", date_to="2026-03-27")
    report = service.run()
    print(report)

    # With memory persistence
    report, memory_result = service.run_with_memory()
    print(f"Memory written: {memory_result.success}")
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nanobot.tracing.analysis import AggregatedMetrics, aggregate_spans, read_spans
from nanobot.tracing.anomaly import Anomaly, AnomalyConfig, AnomalyDetector
from nanobot.tracing.evolution import EvolutionRecommendation, EvolutionTrigger
from nanobot.tracing.memory_writer import MemoryWriteResult, TraceMemoryWriter


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class AnalysisReport:
    """
    Output of the full analysis pipeline.

    Attributes:
        date_from: Start date of the analysis window (None if no filter).
        date_to: End date of the analysis window (None if no filter).
        span_count: Total number of spans read from trace files.
        metrics: Aggregated metrics computed from all spans.
        anomalies: All anomalies detected across all groups.
        recommendation: Evolution recommendation based on anomalies.
        top_anomalies: Top 5 anomalies sorted by severity (descending).
        formatted_summary: Human-readable one-paragraph summary.
    """

    date_from: str | None
    date_to: str | None
    span_count: int
    metrics: AggregatedMetrics
    anomalies: list[Anomaly]
    recommendation: EvolutionRecommendation
    top_anomalies: list[Anomaly] = field(default_factory=list)
    formatted_summary: str = ""

    def __str__(self) -> str:
        """
        Human-readable report showing total spans, top anomalies, and recommendation.
        """
        from nanobot.tracing.evolution import EvolutionTrigger

        trigger = EvolutionTrigger()

        # Build date range header
        if self.date_from or self.date_to:
            if self.date_from and self.date_to:
                header = f"Trace Analysis Report ({self.date_from} to {self.date_to})"
            elif self.date_from:
                header = f"Trace Analysis Report (from {self.date_from})"
            else:
                header = f"Trace Analysis Report (until {self.date_to})"
        else:
            header = "Trace Analysis Report (all spans)"

        lines = [header, f"Total spans: {self.span_count}"]

        # Top anomalies
        if self.top_anomalies:
            lines.append("Top anomalies:")
            for anomaly in self.top_anomalies:
                severity = trigger.classify_severity(anomaly)

                if anomaly.anomaly_type == "high_error_rate":
                    desc = (
                        f"{anomaly.span_type.title()} '{anomaly.group_key}': "
                        f"{anomaly.actual_value:.1%} error rate "
                        f"(threshold: {anomaly.threshold:.1%}) "
                        f"[severity: {severity:.2f}]"
                    )
                elif anomaly.anomaly_type == "latency_spike":
                    desc = (
                        f"{anomaly.span_type.title()} '{anomaly.group_key}': "
                        f"p95 latency {anomaly.actual_value:.0f}ms "
                        f"(threshold: {anomaly.threshold:.0f}ms) "
                        f"[severity: {severity:.2f}]"
                    )
                elif anomaly.anomaly_type == "low_success_rate":
                    desc = (
                        f"{anomaly.span_type.title()} '{anomaly.group_key}': "
                        f"{anomaly.actual_value:.1%} success rate "
                        f"(threshold: {anomaly.threshold:.1%}) "
                        f"[severity: {severity:.2f}]"
                    )
                else:
                    desc = (
                        f"{anomaly.span_type.title()} '{anomaly.group_key}': "
                        f"{anomaly.anomaly_type} "
                        f"[severity: {severity:.2f}]"
                    )
                lines.append(f"  - {desc}")
        else:
            lines.append("Top anomalies: (none)")

        # Recommendation
        if self.recommendation.recommendation:
            lines.append(f"Recommendation: {self.recommendation.recommendation}")
        if self.recommendation.suggested_action:
            lines.append(f"Suggested action: {self.recommendation.suggested_action}")

        return "\n".join(lines)

    def to_json(self) -> str:
        """
        Serialize the report to a JSON string.

        Returns:
            A JSON string representation of the report.
        """
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_dict(self) -> dict:
        """
        Serialize the report to a JSON-serializable dict.

        Returns:
            A dict suitable for ``json.dumps()``.
        """
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "span_count": self.span_count,
            "metrics": self.metrics.to_dict(),
            "anomalies": [a.to_dict() for a in self.anomalies],
            "recommendation": self.recommendation.to_dict(),
            "top_anomalies": [a.to_dict() for a in self.top_anomalies],
            "formatted_summary": self.formatted_summary,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TraceAnalysisService:
    """
    High-level service that orchestrates the full trace analysis pipeline.

    Reads spans from trace files, computes aggregate metrics, detects anomalies,
    generates evolution recommendations, and optionally persists findings to memory.

    Args:
        date_from: Optional start date in "YYYY-MM-DD" format (inclusive).
            Passed directly to ``read_spans``.
        date_to: Optional end date in "YYYY-MM-DD" format (inclusive).
            Passed directly to ``read_spans``.
        trace_dir: Optional directory containing trace JSONL files.
            Defaults to ``~/.nanobot/traces``.
        memory_dir: Optional directory for memory persistence.
            Defaults to ``~/.claude/projects/E--workSpace-nanobot-webui/memory/``.
        anomaly_config: Optional anomaly detection configuration.
            Uses sensible defaults when None.
        severity_threshold: Minimum severity score to trigger an evolution
            recommendation (default 0.5).
    """

    def __init__(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        trace_dir: str | None = None,
        memory_dir: str | None = None,
        anomaly_config: AnomalyConfig | None = None,
        severity_threshold: float = 0.5,
    ) -> None:
        self.date_from = date_from
        self.date_to = date_to
        self.trace_dir = trace_dir
        self.memory_dir = memory_dir
        self.anomaly_config = anomaly_config
        self.severity_threshold = severity_threshold

    def run(self) -> AnalysisReport:
        """
        Run the full analysis pipeline and return an ``AnalysisReport``.

        Steps:
            1. Read spans from trace files (filtered by date range)
            2. Aggregate spans into per-type/per-tool/per-template metrics
            3. Detect anomalies using the configured ``AnomalyDetector``
            4. Generate an ``EvolutionRecommendation`` from anomalies
            5. Select top 5 anomalies by severity and build the report

        Returns:
            An ``AnalysisReport`` with all fields populated.
        """
        # Step 1: read spans (pass date filters through as-is)
        spans = read_spans(date_from=self.date_from, date_to=self.date_to)

        # Step 2: aggregate
        metrics = aggregate_spans(spans)

        # Step 3: detect anomalies
        detector = AnomalyDetector(config=self.anomaly_config)
        anomalies = detector.detect(metrics)

        # Step 4: generate recommendation
        trigger = EvolutionTrigger()
        recommendation = trigger.recommend(anomalies)

        # Step 5: select top 5 anomalies by severity
        scored = [(a, trigger.classify_severity(a)) for a in anomalies]
        top_anomalies = [a for a, _ in sorted(scored, key=lambda x: x[1], reverse=True)[:5]]

        return AnalysisReport(
            date_from=self.date_from,
            date_to=self.date_to,
            span_count=len(spans),
            metrics=metrics,
            anomalies=anomalies,
            recommendation=recommendation,
            top_anomalies=top_anomalies,
            formatted_summary="",
        )

    def run_with_memory(
        self,
    ) -> tuple[AnalysisReport, MemoryWriteResult]:
        """
        Run the analysis pipeline and persist findings to memory.

        Calls ``run()`` to produce the report, then writes:
            - A summary of metrics and recommendation to memory
            - For each top anomaly:
                - Tool anomalies (``span_type == "tool"``) →
                  ``TraceMemoryWriter.write_error_pattern(...)``
                - Template anomalies (``span_type == "subagent"``) →
                  ``TraceMemoryWriter.write_latency_insight(..., template=group_key)``

        Graceful degradation: if any memory write fails, the report is still
        returned and ``MemoryWriteResult.success`` is set to ``False``.

        Returns:
            A tuple of ``(AnalysisReport, MemoryWriteResult)``.
        """
        report = self.run()

        writer = TraceMemoryWriter(self.memory_dir)

        # Write summary (include recommendation if present)
        summary_result = writer.write_summary(report.metrics, report.recommendation)

        # Collect all file write results
        all_files: list[str] = list(summary_result.files_written)
        errors: list[str] = []
        if not summary_result.success and summary_result.error:
            errors.append(f"write_summary: {summary_result.error}")

        # Persist each top anomaly
        anomaly_results: list[MemoryWriteResult] = []
        for anomaly in report.top_anomalies:
            if anomaly.span_type == "tool":
                result = writer.write_error_pattern(
                    tool_name=anomaly.group_key,
                    error_rate=anomaly.actual_value,
                    span_count=anomaly.span_count,
                    suggestion=anomaly.suggestion,
                )
            else:
                # span_type == "subagent" (template)
                result = writer.write_latency_insight(
                    tool_name=None,
                    template=anomaly.group_key,
                    p95_ms=anomaly.actual_value,
                    span_count=anomaly.span_count,
                )

            anomaly_results.append(result)
            all_files.extend(result.files_written)
            if not result.success and result.error:
                errors.append(f"{anomaly.span_type}/{anomaly.group_key}: {result.error}")

        # Combine into a single MemoryWriteResult
        all_ok = summary_result.success and all(r.success for r in anomaly_results)
        memory_result = MemoryWriteResult(
            success=all_ok,
            files_written=all_files,
            error="; ".join(errors) if errors else None,
        )

        return report, memory_result

