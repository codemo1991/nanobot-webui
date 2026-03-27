"""
Tests for nanobot.tracing.anomaly — anomaly detection on AggregatedMetrics.

Constructs AggregatedMetrics objects directly using dataclass constructors so
tests are completely independent of any trace files.
"""

from __future__ import annotations

import pytest

from nanobot.tracing.analysis import AggregatedMetrics, SpanMetrics
from nanobot.tracing.anomaly import Anomaly, AnomalyConfig, AnomalyDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_metrics(
    *,
    count: int = 10,
    ok_count: int | None = None,
    error_count: int = 0,
    p95_duration_ms: float | None = 1000.0,
    success_rate: float | None = None,
    error_rate: float | None = None,
) -> SpanMetrics:
    """
    Convenience factory for SpanMetrics with sensible defaults.

    The ok_count, success_rate, and error_rate are derived from each other
    when not explicitly provided.
    """
    if ok_count is None:
        ok_count = count - error_count
    if success_rate is None:
        success_rate = ok_count / count if count > 0 else 0.0
    if error_rate is None:
        error_rate = error_count / count if count > 0 else 0.0

    return SpanMetrics(
        count=count,
        ok_count=ok_count,
        error_count=error_count,
        success_rate=success_rate,
        error_rate=error_rate,
        p95_duration_ms=p95_duration_ms,
    )


def make_anomalous_metrics(
    anomaly_type: str,
    group_count: int = 10,
) -> SpanMetrics:
    """
    Factory that produces a SpanMetrics that triggers exactly one anomaly type.
    """
    if anomaly_type == "high_error_rate":
        return make_metrics(
            count=group_count,
            error_count=int(group_count * 0.15),  # 15% > default 10%
            success_rate=0.85,
            error_rate=0.15,
            p95_duration_ms=1000.0,
        )
    elif anomaly_type == "latency_spike":
        return make_metrics(
            count=group_count,
            error_count=0,
            success_rate=1.0,
            error_rate=0.0,
            p95_duration_ms=8000.0,  # 8000ms > default 5000ms
        )
    elif anomaly_type == "low_success_rate":
        return make_metrics(
            count=group_count,
            error_count=int(group_count * 0.30),  # 70% < default 80%
            success_rate=0.70,
            error_rate=0.30,
            p95_duration_ms=1000.0,
        )
    else:
        raise ValueError(f"Unknown anomaly_type: {anomaly_type!r}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_detector_no_anomalies_when_all_healthy():
    """When all groups are within thresholds, detect() returns an empty list."""
    healthy_metrics = AggregatedMetrics(
        total_spans=100,
        by_tool={
            "read_file": make_metrics(count=20, error_count=1, p95_duration_ms=500.0),
            "write_file": make_metrics(count=20, error_count=0, p95_duration_ms=3000.0),
        },
        by_template={
            "summarize": make_metrics(count=20, error_count=0, p95_duration_ms=4000.0),
        },
    )

    detector = AnomalyDetector()
    anomalies = detector.detect(healthy_metrics)

    assert anomalies == []


def test_detector_flags_high_error_rate():
    """A tool with error_rate above threshold is detected."""
    metrics = AggregatedMetrics(
        total_spans=50,
        by_tool={
            "flaky_tool": make_metrics(
                count=10,
                error_count=3,  # 30% error rate > default 10%
                success_rate=0.70,
                error_rate=0.30,
                p95_duration_ms=200.0,
            ),
            "good_tool": make_metrics(count=10, error_count=0, p95_duration_ms=200.0),
        },
    )

    detector = AnomalyDetector()
    anomalies = detector.detect(metrics)

    # fl
    flaky = [a for a in anomalies if a.group_key == "flaky_tool"]
    assert len(flaky) == 2
    types = {a.anomaly_type for a in flaky}
    # high_error_rate fires; low_success_rate also fires (70% < 80% threshold)
    assert types == {"high_error_rate", "low_success_rate"}
    high_err = next(a for a in flaky if a.anomaly_type == "high_error_rate")
    assert high_err.actual_value == 0.30
    assert high_err.threshold == 0.10
    assert "flaky_tool" in high_err.suggestion


def test_detector_flags_latency_spike():
    """A tool with p95_duration_ms above threshold is detected."""
    metrics = AggregatedMetrics(
        total_spans=50,
        by_tool={
            "slow_tool": make_metrics(
                count=10,
                error_count=0,
                success_rate=1.0,
                error_rate=0.0,
                p95_duration_ms=9000.0,  # > default 5000ms
            ),
        },
    )

    detector = AnomalyDetector()
    anomalies = detector.detect(metrics)

    assert len(anomalies) == 1
    a = anomalies[0]
    assert a.anomaly_type == "latency_spike"
    assert a.group_key == "slow_tool"
    assert a.span_type == "tool"
    assert a.actual_value == 9000.0
    assert a.threshold == 5000.0
    assert a.span_count == 10
    assert "slow_tool" in a.suggestion
    assert "9000" in a.suggestion


def test_detector_flags_low_success_rate():
    """A subagent template with success_rate below threshold is detected."""
    # Use success_rate=0.75 (< 80% threshold) with error_rate=0.05 (< 10% threshold)
    # so only low_success_rate fires, not high_error_rate.
    metrics = AggregatedMetrics(
        total_spans=50,
        by_template={
            "unreliable_template": SpanMetrics(
                count=10,
                ok_count=8,
                error_count=2,  # 20% error rate > 10% → would also fire high_error_rate
                success_rate=0.75,  # < 80% threshold → fires low_success_rate
                error_rate=0.25,   # > 10% threshold → also fires high_error_rate
                p95_duration_ms=1000.0,
            ),
        },
    )

    detector = AnomalyDetector()
    anomalies = detector.detect(metrics)

    # Both anomaly types fire because error_rate and success_rate are linked.
    assert len(anomalies) == 2
    types = {a.anomaly_type for a in anomalies}
    assert types == {"high_error_rate", "low_success_rate"}
    low_sr = next(a for a in anomalies if a.anomaly_type == "low_success_rate")
    assert low_sr.group_key == "unreliable_template"
    assert low_sr.span_type == "subagent"
    assert low_sr.actual_value == 0.75
    assert low_sr.threshold == 0.80
    assert low_sr.span_count == 10


def test_detector_respects_min_sample_size():
    """Groups with fewer spans than min_sample_size are ignored."""
    metrics = AggregatedMetrics(
        total_spans=10,
        by_tool={
            # count == 2 < default min_sample_size == 5 → should be skipped
            "rare_tool": make_metrics(
                count=2,
                error_count=1,  # 50% error rate > 10%, but skipped
                success_rate=0.50,
                error_rate=0.50,
                p95_duration_ms=9000.0,
            ),
        },
    )

    detector = AnomalyDetector()  # min_sample_size=5 by default
    anomalies = detector.detect(metrics)

    assert anomalies == []

    # With min_sample_size lowered, all three anomalies should be detected:
    # high_error_rate (50% > 10%), latency_spike (9000ms > 5000ms),
    # and low_success_rate (50% < 80%).
    low_config = AnomalyConfig(min_sample_size=2)
    detector_low = AnomalyDetector(low_config)
    anomalies_low = detector_low.detect(metrics)
    assert len(anomalies_low) == 3
    types_low = {a.anomaly_type for a in anomalies_low}
    assert types_low == {"high_error_rate", "latency_spike", "low_success_rate"}


def test_detector_handles_none_duration():
    """Spans with None p95_duration_ms do not trigger latency spikes."""
    metrics = AggregatedMetrics(
        total_spans=10,
        by_tool={
            "durationless_tool": SpanMetrics(
                count=10,
                ok_count=10,
                error_count=0,
                success_rate=1.0,
                error_rate=0.0,
                p95_duration_ms=None,  # No duration data
            ),
        },
    )

    detector = AnomalyDetector()
    anomalies = detector.detect(metrics)

    # Should not flag latency_spike since p95 is None
    assert all(a.anomaly_type != "latency_spike" for a in anomalies)
    assert anomalies == []


def test_detector_empty_metrics():
    """Empty AggregatedMetrics produces no anomalies."""
    empty_metrics = AggregatedMetrics()

    detector = AnomalyDetector()
    anomalies = detector.detect(empty_metrics)

    assert anomalies == []


def test_detector_custom_config():
    """Custom AnomalyConfig thresholds are respected."""
    # Tool with 20% error rate — default threshold is 10% so it should fire,
    # but with a custom threshold of 50% it should not.
    metrics = AggregatedMetrics(
        total_spans=20,
        by_tool={
            "moderate_tool": make_metrics(
                count=10,
                error_count=2,  # 20% error rate
                success_rate=0.80,
                error_rate=0.20,
                p95_duration_ms=3000.0,
            ),
        },
    )

    strict_config = AnomalyConfig(
        error_rate_threshold=0.50,   # very strict — 20% won't trigger
        latency_p95_threshold_ms=1000.0,  # very strict — 3000ms will trigger
        success_rate_threshold=0.95,  # very strict — 80% will trigger
    )
    detector = AnomalyDetector(strict_config)
    anomalies = detector.detect(metrics)

    types = {a.anomaly_type for a in anomalies}
    # high_error_rate should NOT fire (20% < 50%)
    assert "high_error_rate" not in types
    # latency_spike SHOULD fire (3000ms > 1000ms)
    assert "latency_spike" in types
    # low_success_rate SHOULD fire (80% < 95%)
    assert "low_success_rate" in types
    assert len(anomalies) == 2


def test_detector_multiple_anomalies_same_group():
    """A single group can trigger multiple anomaly types simultaneously."""
    # Group that is both slow AND error-prone AND low success
    metrics = AggregatedMetrics(
        total_spans=50,
        by_tool={
            "terrible_tool": SpanMetrics(
                count=10,
                ok_count=3,
                error_count=7,
                success_rate=0.30,
                error_rate=0.70,
                p95_duration_ms=12000.0,
            ),
        },
    )

    detector = AnomalyDetector()
    anomalies = detector.detect(metrics)

    assert len(anomalies) == 3
    types = {a.anomaly_type for a in anomalies}
    assert types == {"high_error_rate", "latency_spike", "low_success_rate"}
    assert all(a.group_key == "terrible_tool" for a in anomalies)


def test_detector_by_template_and_by_tool_both_scanned():
    """Both by_tool and by_template are scanned independently."""
    metrics = AggregatedMetrics(
        total_spans=50,
        by_tool={
            "bad_tool": make_metrics(
                count=10,
                error_count=5,  # 50% error rate
                success_rate=0.50,
                error_rate=0.50,
                p95_duration_ms=100.0,
            ),
        },
        by_template={
            "bad_template": make_metrics(
                count=10,
                error_count=5,  # 50% error rate
                success_rate=0.50,
                error_rate=0.50,
                p95_duration_ms=100.0,
            ),
        },
    )

    detector = AnomalyDetector()
    anomalies = detector.detect(metrics)

    # Both bad_tool (tool) and bad_template (subagent) should be flagged.
    # Each group fires 2 anomaly types (high_error_rate + low_success_rate
    # since error_rate=50% > 10% AND success_rate=50% < 80%).
    assert len(anomalies) == 4
    group_keys = {a.group_key for a in anomalies}
    assert group_keys == {"bad_tool", "bad_template"}
    span_types = {a.span_type for a in anomalies}
    assert span_types == {"tool", "subagent"}
    # Verify high_error_rate anomaly is present for both groups
    for key in ("bad_tool", "bad_template"):
        group_anomalies = [a for a in anomalies if a.group_key == key]
        types = {a.anomaly_type for a in group_anomalies}
        assert types == {"high_error_rate", "low_success_rate"}


def test_detector_threshold_edge_cases():
    """Values exactly equal to thresholds do NOT trigger anomalies."""
    metrics = AggregatedMetrics(
        total_spans=20,
        by_tool={
            "edge_tool": SpanMetrics(
                count=10,
                ok_count=9,
                error_count=1,
                success_rate=0.90,
                error_rate=0.10,  # exactly equal to default threshold (0.10)
                p95_duration_ms=5000.0,  # exactly equal to default threshold (5000ms)
            ),
        },
    )

    detector = AnomalyDetector()
    anomalies = detector.detect(metrics)

    # Exactly at threshold → NOT an anomaly (strict ">" or "<" comparison)
    assert anomalies == []
