"""
Tests for the trace CLI command and the scheduler entry point.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nanobot.cli.commands import app as cli_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_cli(args: list[str], **kwargs: Any) -> pytest.Mock:
    """
    Invoke the typer CLI app with the given arguments.

    Uses typer's testing client.
    """
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_app, args, **kwargs)
    return result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Test: trace analyze command is registered
# ---------------------------------------------------------------------------


def test_trace_analyze_command_registered() -> None:
    """The 'trace analyze' command should be registered with the CLI."""
    result = _invoke_cli(["trace", "analyze", "--help"])
    # --help exits with 0
    assert result.exit_code == 0
    assert "date-from" in result.stdout.lower() or "date_from" in result.stdout.lower()


def test_trace_analyze_help_shows_options() -> None:
    """The --help output should list all options."""
    result = _invoke_cli(["trace", "analyze", "--help"])
    assert result.exit_code == 0
    assert "--date-from" in result.stdout
    assert "--date-to" in result.stdout
    assert "--memory" in result.stdout
    assert "--error-rate" in result.stdout
    assert "--latency-ms" in result.stdout
    assert "--success-rate" in result.stdout


# ---------------------------------------------------------------------------
# Test: trace analyze runs without crashing (mocked service)
# ---------------------------------------------------------------------------


@patch("nanobot.tracing.service.TraceAnalysisService")
def test_trace_analyze_no_memory(mock_svc_cls: MagicMock) -> None:
    """trace analyze --no-memory should print the report."""
    mock_svc = MagicMock()
    mock_report = MagicMock()
    mock_report.__str__ = MagicMock(return_value="Trace Analysis Report\nTotal spans: 0")
    mock_svc.run.return_value = mock_report
    mock_svc_cls.return_value = mock_svc

    result = _invoke_cli(["trace", "analyze", "--no-memory"])

    assert result.exit_code == 0
    assert mock_svc.run.called
    assert not mock_svc.run_with_memory.called


@patch("nanobot.tracing.service.TraceAnalysisService")
def test_trace_analyze_with_memory(mock_svc_cls: MagicMock) -> None:
    """trace analyze --memory should call run_with_memory and print Memory written."""
    from nanobot.tracing.memory_writer import MemoryWriteResult

    mock_svc = MagicMock()
    mock_report = MagicMock()
    mock_report.__str__ = MagicMock(return_value="Trace Analysis Report\nTotal spans: 0")
    mock_result = MemoryWriteResult(success=True, files_written=["foo.md"], error=None)
    mock_svc.run_with_memory.return_value = (mock_report, mock_result)
    mock_svc_cls.return_value = mock_svc

    result = _invoke_cli(["trace", "analyze", "--memory"])

    assert result.exit_code == 0
    assert mock_svc.run_with_memory.called
    assert not mock_svc.run.called


@patch("nanobot.tracing.service.TraceAnalysisService")
def test_trace_analyze_memory_partial_failure(mock_svc_cls: MagicMock) -> None:
    """When memory writes partially fail, the command should still exit 0."""
    from nanobot.tracing.memory_writer import MemoryWriteResult

    mock_svc = MagicMock()
    mock_report = MagicMock()
    mock_report.__str__ = MagicMock(return_value="Trace Analysis Report")
    mock_result = MemoryWriteResult(
        success=False, files_written=["foo.md"], error="disk full"
    )
    mock_svc.run_with_memory.return_value = (mock_report, mock_result)
    mock_svc_cls.return_value = mock_svc

    result = _invoke_cli(["trace", "analyze", "--memory"])

    assert result.exit_code == 0
    assert "Memory partial failure" in result.stdout


@patch("nanobot.tracing.service.TraceAnalysisService")
def test_trace_analyze_custom_thresholds_passed(mock_svc_cls: MagicMock) -> None:
    """Custom thresholds should be forwarded to AnomalyConfig."""
    from nanobot.tracing.anomaly import AnomalyConfig

    mock_svc = MagicMock()
    mock_report = MagicMock()
    mock_report.__str__ = MagicMock(return_value="Report")
    mock_svc.run.return_value = mock_report
    mock_svc_cls.return_value = mock_svc

    result = _invoke_cli([
        "trace", "analyze", "--no-memory",
        "--error-rate", "0.05",
        "--latency-ms", "2000",
        "--success-rate", "0.9",
    ])

    assert result.exit_code == 0
    # Check the config passed to TraceAnalysisService.__init__
    call_kwargs = mock_svc_cls.call_args.kwargs
    config: AnomalyConfig = call_kwargs.get("anomaly_config") or call_kwargs.get("config")
    assert config is not None
    assert config.error_rate_threshold == 0.05
    assert config.latency_p95_threshold_ms == 2000.0
    assert config.success_rate_threshold == 0.9


# ---------------------------------------------------------------------------
# Scheduler tests
# ---------------------------------------------------------------------------


def test_scheduler_run_analysis_returns_zero_on_success() -> None:
    """run_analysis() should return 0 when the service runs without exceptions."""
    with patch("nanobot.tracing.service.TraceAnalysisService") as mock_cls:
        mock_svc = MagicMock()
        mock_report = MagicMock()
        mock_report.__str__ = MagicMock(return_value="Report")
        mock_svc.run.return_value = mock_report
        mock_cls.return_value = mock_svc

        from nanobot.tracing.scheduler import run_analysis

        rc = run_analysis(memory=False)
        assert rc == 0


def test_scheduler_run_analysis_returns_one_on_error() -> None:
    """run_analysis() should return 1 when the service raises an exception."""
    with patch("nanobot.tracing.service.TraceAnalysisService") as mock_cls:
        mock_cls.side_effect = RuntimeError("simulated failure")

        from nanobot.tracing.scheduler import run_analysis

        captured_stderr = MagicMock()
        with patch.object(sys, "stderr", captured_stderr):
            rc = run_analysis(memory=False)

        assert rc == 1


def test_scheduler_output_json() -> None:
    """When output_json=True, run_analysis() should print JSON to stdout."""
    with patch("nanobot.tracing.service.TraceAnalysisService") as mock_cls:
        mock_svc = MagicMock()
        mock_report = MagicMock()
        mock_report.to_json.return_value = '{"span_count": 0}'
        mock_report.__str__ = MagicMock(return_value="Report")
        mock_svc.run.return_value = mock_report
        mock_cls.return_value = mock_svc

        from nanobot.tracing.scheduler import run_analysis

        captured_stdout = MagicMock()
        with patch.object(sys, "stdout", captured_stdout):
            rc = run_analysis(memory=False, output_json=True)

        assert rc == 0
        mock_report.to_json.assert_called_once()


def test_scheduler_handles_memory_failure() -> None:
    """run_analysis() should return 0 even when memory write fails."""
    from nanobot.tracing.memory_writer import MemoryWriteResult

    with patch("nanobot.tracing.service.TraceAnalysisService") as mock_cls:
        mock_svc = MagicMock()
        mock_report = MagicMock()
        mock_report.__str__ = MagicMock(return_value="Report")
        mock_result = MemoryWriteResult(
            success=False, files_written=[], error="disk full"
        )
        mock_svc.run_with_memory.return_value = (mock_report, mock_result)
        mock_cls.return_value = mock_svc

        from nanobot.tracing.scheduler import run_analysis

        captured_stdout = MagicMock()
        with patch.object(sys, "stdout", captured_stdout):
            rc = run_analysis(memory=True)

        assert rc == 0
        assert "Memory partial failure" in str(captured_stdout.write.call_args_list)


# ---------------------------------------------------------------------------
# Serialization tests (to_dict / to_json on analysis classes)
# ---------------------------------------------------------------------------


def test_span_metrics_to_dict() -> None:
    """SpanMetrics.to_dict() should return a JSON-serializable dict."""
    from nanobot.tracing.analysis import SpanMetrics

    sm = SpanMetrics(
        count=10,
        ok_count=8,
        error_count=2,
        success_rate=0.8,
        error_rate=0.2,
        avg_duration_ms=500.0,
        p50_duration_ms=400.0,
        p95_duration_ms=800.0,
        p99_duration_ms=900.0,
        grouped={},
    )
    d = sm.to_dict()
    assert d["count"] == 10
    assert d["ok_count"] == 8
    assert d["error_count"] == 2
    assert d["success_rate"] == 0.8
    assert d["error_rate"] == 0.2
    assert d["avg_duration_ms"] == 500.0
    assert d["p95_duration_ms"] == 800.0
    # Should be JSON-serializable
    json.dumps(d)


def test_aggregated_metrics_to_dict() -> None:
    """AggregatedMetrics.to_dict() should return a JSON-serializable dict."""
    from nanobot.tracing.analysis import AggregatedMetrics, SpanMetrics

    metrics = AggregatedMetrics(
        total_spans=20,
        by_tool={"read_file": SpanMetrics(count=20, ok_count=18, error_count=2, success_rate=0.9, error_rate=0.1)},
        by_template={},
        by_type={},
    )
    d = metrics.to_dict()
    assert d["total_spans"] == 20
    assert "read_file" in d["by_tool"]
    json.dumps(d)


def test_anomaly_to_dict() -> None:
    """Anomaly.to_dict() should return a JSON-serializable dict."""
    from nanobot.tracing.anomaly import Anomaly

    a = Anomaly(
        anomaly_type="high_error_rate",
        group_key="read_file",
        span_type="tool",
        actual_value=0.15,
        threshold=0.10,
        span_count=47,
        suggestion="high error rate",
    )
    d = a.to_dict()
    assert d["anomaly_type"] == "high_error_rate"
    assert d["group_key"] == "read_file"
    assert d["actual_value"] == 0.15
    json.dumps(d)


def test_evolution_recommendation_to_dict() -> None:
    """EvolutionRecommendation.to_dict() should return a JSON-serializable dict."""
    from nanobot.tracing.evolution import EvolutionRecommendation

    rec = EvolutionRecommendation(
        should_evolve=True,
        severity_threshold=0.5,
        anomalies=[],
        top_anomaly=None,
        max_severity=0.0,
        recommendation="No anomalies.",
        suggested_action="Continue monitoring.",
    )
    d = rec.to_dict()
    assert d["should_evolve"] is True
    assert d["recommendation"] == "No anomalies."
    json.dumps(d)


def test_analysis_report_to_json() -> None:
    """AnalysisReport.to_json() should return a valid JSON string."""
    from nanobot.tracing.evolution import EvolutionRecommendation
    from nanobot.tracing.service import AnalysisReport, AggregatedMetrics

    report = AnalysisReport(
        date_from="2026-03-20",
        date_to="2026-03-27",
        span_count=0,
        metrics=AggregatedMetrics(),
        anomalies=[],
        recommendation=EvolutionRecommendation(
            should_evolve=False,
            severity_threshold=0.5,
        ),
        top_anomalies=[],
    )
    s = report.to_json()
    d = json.loads(s)
    assert d["date_from"] == "2026-03-20"
    assert d["date_to"] == "2026-03-27"
    assert d["span_count"] == 0


def test_analysis_report_to_dict() -> None:
    """AnalysisReport.to_dict() should return a JSON-serializable dict."""
    from nanobot.tracing.evolution import EvolutionRecommendation
    from nanobot.tracing.service import AnalysisReport, AggregatedMetrics

    report = AnalysisReport(
        date_from=None,
        date_to=None,
        span_count=5,
        metrics=AggregatedMetrics(),
        anomalies=[],
        recommendation=EvolutionRecommendation(
            should_evolve=False,
            severity_threshold=0.5,
        ),
        top_anomalies=[],
    )
    d = report.to_dict()
    assert d["span_count"] == 5
    json.dumps(d)
