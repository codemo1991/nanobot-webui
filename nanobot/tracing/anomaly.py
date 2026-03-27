"""
Anomaly detection for trace metrics.

Detects three types of anomalies in aggregated span metrics:
- High error rate: error_rate exceeds the configured threshold
- Latency spike: p95_duration_ms exceeds the configured threshold
- Low success rate: success_rate falls below the configured threshold

Example::

    from nanobot.tracing.analysis import read_spans, aggregate_spans
    from nanobot.tracing.anomaly import AnomalyDetector, AnomalyConfig

    spans = read_spans()
    metrics = aggregate_spans(spans)

    config = AnomalyConfig(error_rate_threshold=0.05, latency_p95_threshold_ms=2000)
    detector = AnomalyDetector(config)
    anomalies = detector.detect(metrics)

    for a in anomalies:
        print(f"[{a.anomaly_type}] {a.group_key}: {a.actual_value:.2%} (threshold: {a.threshold})")
        print(f"  Suggestion: {a.suggestion}")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.tracing.analysis import AggregatedMetrics, SpanMetrics


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class AnomalyConfig:
    """
    Configuration for the anomaly detector.

    Attributes:
        error_rate_threshold: Flag when error_rate > this value (default 0.10 = 10%).
        latency_p95_threshold_ms: Flag when p95_duration_ms > this value (default 5000 ms).
        success_rate_threshold: Flag when success_rate < this value (default 0.80 = 80%).
        min_sample_size: Minimum span count in a group before flagging (default 5).
    """

    error_rate_threshold: float = 0.10
    latency_p95_threshold_ms: float = 5000.0
    success_rate_threshold: float = 0.80
    min_sample_size: int = 5


# ---------------------------------------------------------------------------
# Anomaly result
# ---------------------------------------------------------------------------


@dataclass
class Anomaly:
    """
    A detected anomaly in the trace metrics.

    Attributes:
        anomaly_type: One of "high_error_rate", "latency_spike", "low_success_rate".
        group_key: The tool_name or template name that triggered the anomaly.
        span_type: "tool" or "subagent" depending on which collection was scanned.
        actual_value: The metric value that triggered the anomaly.
        threshold: The configured threshold that was exceeded.
        span_count: Number of spans in the group.
        suggestion: Human-readable one-line description.
    """

    anomaly_type: str
    group_key: str
    span_type: str
    actual_value: float
    threshold: float
    span_count: int
    suggestion: str

    def to_dict(self) -> dict:
        """Serialize to a JSON-serializable dict."""
        return {
            "anomaly_type": self.anomaly_type,
            "group_key": self.group_key,
            "span_type": self.span_type,
            "actual_value": self.actual_value,
            "threshold": self.threshold,
            "span_count": self.span_count,
            "suggestion": self.suggestion,
        }


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class AnomalyDetector:
    """
    Scan aggregated trace metrics for anomalous patterns.

    Args:
        config: An AnomalyConfig instance. Uses defaults if None.
    """

    def __init__(self, config: AnomalyConfig | None = None) -> None:
        self.config = config if config is not None else AnomalyConfig()

    def detect(self, metrics: AggregatedMetrics) -> list[Anomaly]:
        """
        Scan all by_tool and by_template groups in metrics.

        Only evaluates groups with span_count >= self.config.min_sample_size.
        Skips duration checks when p95_duration_ms is None.

        Returns:
            List of detected Anomaly objects. Empty list when no anomalies are found.
        """
        anomalies: list[Anomaly] = []

        # Scan tool spans by tool_name
        for key, span_metrics in metrics.by_tool.items():
            anomalies.extend(self._scan_group(key, span_metrics, "tool"))

        # Scan subagent spans by template
        for key, span_metrics in metrics.by_template.items():
            anomalies.extend(self._scan_group(key, span_metrics, "subagent"))

        return anomalies

    def _scan_group(
        self,
        group_key: str,
        metrics: SpanMetrics,
        span_type: str,
    ) -> list[Anomaly]:
        """
        Scan a single metric group and return any detected anomalies.

        Returns at most three anomalies (one per type) for the group.
        """
        anomalies: list[Anomaly] = []
        cfg = self.config

        # Skip groups that don't meet the minimum sample size
        if metrics.count < cfg.min_sample_size:
            return anomalies

        # 1. High error rate
        if metrics.error_rate > cfg.error_rate_threshold:
            anomalies.append(
                Anomaly(
                    anomaly_type="high_error_rate",
                    group_key=group_key,
                    span_type=span_type,
                    actual_value=metrics.error_rate,
                    threshold=cfg.error_rate_threshold,
                    span_count=metrics.count,
                    suggestion=(
                        f"{span_type.title()} '{group_key}' has {metrics.error_rate:.1%} error rate "
                        f"(threshold: {cfg.error_rate_threshold:.1%}) across {metrics.count} spans"
                    ),
                )
            )

        # 2. Latency spike (skip if p95 is None)
        if (
            metrics.p95_duration_ms is not None
            and metrics.p95_duration_ms > cfg.latency_p95_threshold_ms
        ):
            anomalies.append(
                Anomaly(
                    anomaly_type="latency_spike",
                    group_key=group_key,
                    span_type=span_type,
                    actual_value=metrics.p95_duration_ms,
                    threshold=cfg.latency_p95_threshold_ms,
                    span_count=metrics.count,
                    suggestion=(
                        f"{span_type.title()} '{group_key}' has {metrics.p95_duration_ms:.0f}ms "
                        f"p95 latency (threshold: {cfg.latency_p95_threshold_ms:.0f}ms) "
                        f"across {metrics.count} spans"
                    ),
                )
            )

        # 3. Low success rate
        if metrics.success_rate < cfg.success_rate_threshold:
            anomalies.append(
                Anomaly(
                    anomaly_type="low_success_rate",
                    group_key=group_key,
                    span_type=span_type,
                    actual_value=metrics.success_rate,
                    threshold=cfg.success_rate_threshold,
                    span_count=metrics.count,
                    suggestion=(
                        f"{span_type.title()} '{group_key}' has {metrics.success_rate:.1%} success rate "
                        f"(threshold: {cfg.success_rate_threshold:.1%}) across {metrics.count} spans"
                    ),
                )
            )

        return anomalies
