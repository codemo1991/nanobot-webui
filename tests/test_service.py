"""
Tests for nanobot.tracing.service.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nanobot.tracing.analysis import AggregatedMetrics, SpanMetrics
from nanobot.tracing.anomaly import Anomaly, AnomalyConfig
from nanobot.tracing.evolution import EvolutionRecommendation
from nanobot.tracing.memory_writer import MemoryWriteResult
from nanobot.tracing.service import AnalysisReport, TraceAnalysisService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_anomaly(
    group_key: str = "tool_a",
    anomaly_type: str = "high_error_rate",
    span_type: str = "tool",
    actual_value: float = 0.15,
    threshold: float = 0.10,
    span_count: int = 10,
    suggestion: str | None = None,
) -> Anomaly:
    if suggestion is None:
        suggestion = f"{span_type} '{group_key}' has {actual_value:.1%} error rate"
    return Anomaly(
        anomaly_type=anomaly_type,
        group_key=group_key,
        span_type=span_type,
        actual_value=actual_value,
        threshold=threshold,
        span_count=span_count,
        suggestion=suggestion,
    )


def _make_metrics(
    total: int = 100,
    by_tool: dict[str, SpanMetrics] | None = None,
    by_template: dict[str, SpanMetrics] | None = None,
) -> AggregatedMetrics:
    metrics = AggregatedMetrics(total_spans=total)
    if by_tool:
        metrics.by_tool = by_tool
    if by_template:
        metrics.by_template = by_template
    return metrics


def _make_recommendation(
    anomalies: list[Anomaly] | None = None,
) -> EvolutionRecommendation:
    from nanobot.tracing.evolution import EvolutionTrigger

    trigger = EvolutionTrigger()
    return trigger.recommend(anomalies or [])


# ---------------------------------------------------------------------------
# Test: run() returns a well-formed AnalysisReport
# ---------------------------------------------------------------------------


def test_service_run_returns_report() -> None:
    """run() should return an AnalysisReport with all expected fields."""
    service = TraceAnalysisService()
    report = service.run()

    assert isinstance(report, AnalysisReport)
    assert report.date_from is None
    assert report.date_to is None
    assert isinstance(report.metrics, AggregatedMetrics)
    assert isinstance(report.anomalies, list)
    assert isinstance(report.recommendation, EvolutionRecommendation)
    assert isinstance(report.top_anomalies, list)
    # formatted_summary is intentionally empty by default
    assert report.formatted_summary == ""


# ---------------------------------------------------------------------------
# Test: run_with_memory() returns a tuple of (AnalysisReport, MemoryWriteResult)
# ---------------------------------------------------------------------------


def test_service_run_with_memory_returns_tuple() -> None:
    """run_with_memory() should return (AnalysisReport, MemoryWriteResult)."""
    service = TraceAnalysisService()
    result = service.run_with_memory()

    assert isinstance(result, tuple)
    assert len(result) == 2
    report, memory_result = result
    assert isinstance(report, AnalysisReport)
    assert isinstance(memory_result, MemoryWriteResult)


# ---------------------------------------------------------------------------
# Test: date_from / date_to are passed through to read_spans
# ---------------------------------------------------------------------------


@patch("nanobot.tracing.service.read_spans")
@patch("nanobot.tracing.service.aggregate_spans")
@patch("nanobot.tracing.service.AnomalyDetector")
@patch("nanobot.tracing.service.EvolutionTrigger")
def test_service_date_filter_passed_to_read_spans(
    mock_trigger_cls: MagicMock,
    mock_detector_cls: MagicMock,
    mock_agg: MagicMock,
    mock_read: MagicMock,
) -> None:
    """date_from and date_to should be forwarded to read_spans."""
    # Configure mocks to return safe empty values
    mock_read.return_value = []
    mock_agg.return_value = _make_metrics()
    mock_detector = MagicMock()
    mock_detector.detect.return_value = []
    mock_detector_cls.return_value = mock_detector
    mock_trigger = MagicMock()
    mock_trigger.recommend.return_value = _make_recommendation()
    mock_trigger.classify_severity.return_value = 0.0
    mock_trigger_cls.return_value = mock_trigger

    service = TraceAnalysisService(date_from="2026-03-20", date_to="2026-03-27")
    service.run()

    mock_read.assert_called_once_with(date_from="2026-03-20", date_to="2026-03-27")


# ---------------------------------------------------------------------------
# Test: AnomalyConfig is passed to AnomalyDetector
# ---------------------------------------------------------------------------


@patch("nanobot.tracing.service.read_spans")
@patch("nanobot.tracing.service.aggregate_spans")
@patch("nanobot.tracing.service.AnomalyDetector")
@patch("nanobot.tracing.service.EvolutionTrigger")
def test_service_anomaly_config_passed_to_detector(
    mock_trigger_cls: MagicMock,
    mock_detector_cls: MagicMock,
    mock_agg: MagicMock,
    mock_read: MagicMock,
) -> None:
    """The provided AnomalyConfig should be forwarded to AnomalyDetector."""
    mock_read.return_value = []
    mock_agg.return_value = _make_metrics()
    mock_detector = MagicMock()
    mock_detector.detect.return_value = []
    mock_detector_cls.return_value = mock_detector
    mock_trigger = MagicMock()
    mock_trigger.recommend.return_value = _make_recommendation()
    mock_trigger.classify_severity.return_value = 0.0
    mock_trigger_cls.return_value = mock_trigger

    custom_config = AnomalyConfig(
        error_rate_threshold=0.05,
        latency_p95_threshold_ms=2000.0,
        min_sample_size=10,
    )
    service = TraceAnalysisService(anomaly_config=custom_config)
    service.run()

    mock_detector_cls.assert_called_once_with(config=custom_config)


# ---------------------------------------------------------------------------
# Test: top_anomalies are sorted by severity descending
# ---------------------------------------------------------------------------


@patch("nanobot.tracing.service.read_spans")
@patch("nanobot.tracing.service.aggregate_spans")
@patch("nanobot.tracing.service.AnomalyDetector")
@patch("nanobot.tracing.service.EvolutionTrigger")
def test_service_top_anomalies_sorted_by_severity(
    mock_trigger_cls: MagicMock,
    mock_detector_cls: MagicMock,
    mock_agg: MagicMock,
    mock_read: MagicMock,
) -> None:
    """top_anomalies should contain the top-5 anomalies sorted by severity descending."""
    mock_read.return_value = []
    mock_agg.return_value = _make_metrics()
    mock_detector = MagicMock()
    # Create anomalies with known severity ordering:
    # severity formula for high_error_rate: min(1, (actual-threshold)/threshold*2)
    # low_sev: (0.12 - 0.10) / 0.10 * 2 = 0.40
    # mid_sev: (0.20 - 0.10) / 0.10 * 2 = 2.00 -> capped at 1.0
    # high_sev: (0.15 - 0.10) / 0.10 * 2 = 1.00
    anomalies = [
        _make_anomaly("low", actual_value=0.12, threshold=0.10),  # 0.40
        _make_anomaly("high", actual_value=0.20, threshold=0.10),  # 1.00 (capped)
        _make_anomaly("mid", actual_value=0.15, threshold=0.10),  # 1.00 (capped, appears later)
    ]
    mock_detector.detect.return_value = anomalies
    mock_detector_cls.return_value = mock_detector

    mock_trigger = MagicMock()
    # Assign explicit severity values to the trigger's classify_severity
    sev_map = {"low": 0.40, "high": 1.00, "mid": 1.00}
    mock_trigger.classify_severity.side_effect = lambda a: sev_map.get(a.group_key, 0.0)
    mock_trigger.recommend.return_value = _make_recommendation(anomalies)
    mock_trigger_cls.return_value = mock_trigger

    service = TraceAnalysisService()
    report = service.run()

    # Top anomalies should be sorted by severity descending; ties broken by sort stability
    assert len(report.top_anomalies) == 3
    severities = [mock_trigger.classify_severity(a) for a in report.top_anomalies]
    assert severities == sorted(severities, reverse=True)


# ---------------------------------------------------------------------------
# Test: handles memory write failure gracefully
# ---------------------------------------------------------------------------


@patch("nanobot.tracing.service.read_spans")
@patch("nanobot.tracing.service.aggregate_spans")
@patch("nanobot.tracing.service.AnomalyDetector")
@patch("nanobot.tracing.service.EvolutionTrigger")
@patch("nanobot.tracing.service.TraceMemoryWriter")
def test_service_handles_memory_write_failure(
    mock_writer_cls: MagicMock,
    mock_trigger_cls: MagicMock,
    mock_detector_cls: MagicMock,
    mock_agg: MagicMock,
    mock_read: MagicMock,
) -> None:
    """run_with_memory should still return the report even if memory writes fail."""
    mock_read.return_value = []
    mock_agg.return_value = _make_metrics()
    mock_detector = MagicMock()
    mock_detector.detect.return_value = []
    mock_detector_cls.return_value = mock_detector
    mock_trigger = MagicMock()
    mock_trigger.recommend.return_value = _make_recommendation()
    mock_trigger.classify_severity.return_value = 0.0
    mock_trigger_cls.return_value = mock_trigger

    # Make write_summary fail
    failure_result = MemoryWriteResult(
        success=False, files_written=[], error="disk full"
    )
    mock_writer = MagicMock()
    mock_writer.write_summary.return_value = failure_result
    mock_writer.write_error_pattern.return_value = MemoryWriteResult(
        success=True, files_written=[]
    )
    mock_writer.write_latency_insight.return_value = MemoryWriteResult(
        success=True, files_written=[]
    )
    mock_writer_cls.return_value = mock_writer

    service = TraceAnalysisService()
    report, memory_result = service.run_with_memory()

    # Report should still be returned
    assert isinstance(report, AnalysisReport)
    # Memory result should reflect the failure
    assert memory_result.success is False
    assert "disk full" in (memory_result.error or "")


# ---------------------------------------------------------------------------
# Test: empty spans produce a valid empty report
# ---------------------------------------------------------------------------


def test_service_empty_spans_returns_empty_report() -> None:
    """With no spans, run() should return a report with zero counts and empty lists."""
    service = TraceAnalysisService(date_from="2026-03-01", date_to="2026-03-02")
    report = service.run()

    assert report.span_count == 0
    assert report.anomalies == []
    assert report.top_anomalies == []
    assert report.metrics.total_spans == 0


# ---------------------------------------------------------------------------
# Test: AnalysisReport.__str__ produces a formatted string
# ---------------------------------------------------------------------------


def test_service_str_formatted() -> None:
    """__str__ should return a readable string without crashing."""
    service = TraceAnalysisService(date_from="2026-03-20", date_to="2026-03-27")
    report = service.run()

    s = str(report)

    assert isinstance(s, str)
    assert "Trace Analysis Report" in s
    assert "2026-03-20" in s
    assert "2026-03-27" in s
    assert "Total spans:" in s


# ---------------------------------------------------------------------------
# Test: tool anomaly routed to write_error_pattern
# ---------------------------------------------------------------------------


@patch("nanobot.tracing.service.read_spans")
@patch("nanobot.tracing.service.aggregate_spans")
@patch("nanobot.tracing.service.AnomalyDetector")
@patch("nanobot.tracing.service.EvolutionTrigger")
@patch("nanobot.tracing.service.TraceMemoryWriter")
def test_service_passes_tool_anomaly_to_error_pattern(
    mock_writer_cls: MagicMock,
    mock_trigger_cls: MagicMock,
    mock_detector_cls: MagicMock,
    mock_agg: MagicMock,
    mock_read: MagicMock,
) -> None:
    """Tool anomalies (span_type == 'tool') should call write_error_pattern."""
    mock_read.return_value = []
    mock_agg.return_value = _make_metrics()
    tool_anomaly = _make_anomaly(
        group_key="read_file",
        anomaly_type="high_error_rate",
        span_type="tool",
        actual_value=0.153,
        threshold=0.10,
        span_count=47,
        suggestion="read_file has high error rate",
    )
    mock_detector = MagicMock()
    mock_detector.detect.return_value = [tool_anomaly]
    mock_detector_cls.return_value = mock_detector

    mock_trigger = MagicMock()
    mock_trigger.recommend.return_value = _make_recommendation([tool_anomaly])
    mock_trigger.classify_severity.return_value = 0.53
    mock_trigger_cls.return_value = mock_trigger

    ok_result = MemoryWriteResult(success=True, files_written=["/path/to/file.md"])
    mock_writer = MagicMock()
    mock_writer.write_summary.return_value = MemoryWriteResult(success=True, files_written=[])
    mock_writer.write_error_pattern.return_value = ok_result
    mock_writer.write_latency_insight.return_value = ok_result
    mock_writer_cls.return_value = mock_writer

    service = TraceAnalysisService()
    report, _ = service.run_with_memory()

    # write_error_pattern should be called once for the tool anomaly
    mock_writer.write_error_pattern.assert_called_once_with(
        tool_name="read_file",
        error_rate=0.153,
        span_count=47,
        suggestion="read_file has high error rate",
    )
    # write_latency_insight should NOT be called for a tool anomaly
    mock_writer.write_latency_insight.assert_not_called()


# ---------------------------------------------------------------------------
# Test: template anomaly routed to write_latency_insight
# ---------------------------------------------------------------------------


@patch("nanobot.tracing.service.read_spans")
@patch("nanobot.tracing.service.aggregate_spans")
@patch("nanobot.tracing.service.AnomalyDetector")
@patch("nanobot.tracing.service.EvolutionTrigger")
@patch("nanobot.tracing.service.TraceMemoryWriter")
def test_service_passes_template_anomaly_to_latency_insight(
    mock_writer_cls: MagicMock,
    mock_trigger_cls: MagicMock,
    mock_detector_cls: MagicMock,
    mock_agg: MagicMock,
    mock_read: MagicMock,
) -> None:
    """Template anomalies (span_type == 'subagent') should call write_latency_insight."""
    mock_read.return_value = []
    mock_agg.return_value = _make_metrics()

    template_anomaly = Anomaly(
        anomaly_type="latency_spike",
        group_key="coding_agent",
        span_type="subagent",
        actual_value=8200.0,
        threshold=5000.0,
        span_count=15,
        suggestion="coding_agent has high latency",
    )
    mock_detector = MagicMock()
    mock_detector.detect.return_value = [template_anomaly]
    mock_detector_cls.return_value = mock_detector

    mock_trigger = MagicMock()
    mock_trigger.recommend.return_value = _make_recommendation([template_anomaly])
    mock_trigger.classify_severity.return_value = 0.64
    mock_trigger_cls.return_value = mock_trigger

    ok_result = MemoryWriteResult(success=True, files_written=["/path/to/file.md"])
    mock_writer = MagicMock()
    mock_writer.write_summary.return_value = MemoryWriteResult(success=True, files_written=[])
    mock_writer.write_error_pattern.return_value = ok_result
    mock_writer.write_latency_insight.return_value = ok_result
    mock_writer_cls.return_value = mock_writer

    service = TraceAnalysisService()
    report, _ = service.run_with_memory()

    # write_latency_insight should be called once for the template anomaly
    mock_writer.write_latency_insight.assert_called_once_with(
        tool_name=None,
        template="coding_agent",
        p95_ms=8200.0,
        span_count=15,
    )
    # write_error_pattern should NOT be called for a subagent anomaly
    mock_writer.write_error_pattern.assert_not_called()
