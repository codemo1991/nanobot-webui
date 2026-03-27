"""
Evolution trigger for trace self-improvement.

Takes anomaly detections from the anomaly detector and decides whether
to trigger a self-improvement event, along with actionable recommendations.

Example::

    from nanobot.tracing.evolution import EvolutionTrigger, EvolutionRecommendation
    from nanobot.tracing.anomaly import AnomalyDetector

    anomalies = detector.detect(metrics)
    trigger = EvolutionTrigger()
    recommendation = trigger.recommend(anomalies)

    if recommendation.should_evolve:
        print(recommendation.suggested_action)
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Types (imported for reference; avoid circular imports)
# ---------------------------------------------------------------------------

# The Anomaly type is defined in nanobot.tracing.anomaly and referenced
# only via docstrings / type annotations below to avoid circular imports.
# Code that uses this module should import Anomaly from anomaly.py directly.


# ---------------------------------------------------------------------------
# Evolution Recommendation
# ---------------------------------------------------------------------------


@dataclass
class EvolutionRecommendation:
    """
    Output of the evolution trigger — contains actionable recommendations.

    Attributes:
        should_evolve: Whether self-improvement should be triggered.
        severity_threshold: The threshold used when making the decision.
        anomalies: All anomalies that were evaluated.
        top_anomaly: The single highest-severity anomaly (or None).
        max_severity: The highest severity score across all anomalies.
        recommendation: Human-readable one-line recommendation.
        suggested_action: More specific action text.
    """

    should_evolve: bool
    severity_threshold: float
    anomalies: list = field(default_factory=list)
    top_anomaly: "Anomaly | None" = None  # type: ignore[name-defined]  # noqa: F821
    max_severity: float = 0.0
    recommendation: str = ""
    suggested_action: str = ""

    def to_dict(self) -> dict:
        """Serialize to a JSON-serializable dict."""
        from nanobot.tracing.anomaly import Anomaly as AnomalyType

        return {
            "should_evolve": self.should_evolve,
            "severity_threshold": self.severity_threshold,
            "anomalies": (
                [a.to_dict() if isinstance(a, AnomalyType) else a for a in self.anomalies]
                if self.anomalies
                else []
            ),
            "top_anomaly": (
                self.top_anomaly.to_dict()
                if self.top_anomaly is not None
                else None
            ),
            "max_severity": self.max_severity,
            "recommendation": self.recommendation,
            "suggested_action": self.suggested_action,
        }


# ---------------------------------------------------------------------------
# Evolution Trigger
# ---------------------------------------------------------------------------


@dataclass
class EvolutionTrigger:
    """
    Decides when to trigger self-improvement based on anomalies.

    All methods are pure — no instance state is maintained.
    """

    # ------------------------------------------------------------------
    # Severity classification
    # ------------------------------------------------------------------

    def classify_severity(self, anomaly: "Anomaly") -> float:  # type: ignore[name-defined]  # noqa: F821
        """
        Compute severity score based on how far the anomaly exceeds threshold.

        Returns a value in the range 0.0–1.0:
          - 0.0–0.3: low severity
          - 0.3–0.7: medium severity
          - 0.7–1.0: high severity

        Computation (strict comparisons — exact threshold values score 0.0):
          - high_error_rate:  min(1.0, (error_rate - threshold) / threshold * 2)
          - latency_spike:    min(1.0, (p95_ms / threshold_ms - 1) * 2)
          - low_success_rate: min(1.0, (threshold - success_rate) / threshold)
        """
        a_type = anomaly.anomaly_type
        actual = anomaly.actual_value
        threshold = anomaly.threshold

        if a_type == "high_error_rate":
            if actual <= threshold:
                return 0.0
            return min(1.0, (actual - threshold) / threshold * 2.0)

        if a_type == "latency_spike":
            if actual <= threshold:
                return 0.0
            return min(1.0, (actual / threshold - 1.0) * 2.0)

        if a_type == "low_success_rate":
            # Low success rate: actual is below threshold — more severe when
            # the gap (threshold - actual) is larger.
            if actual >= threshold:
                return 0.0
            return min(1.0, (threshold - actual) / threshold)

        # Unknown anomaly type — treat as low severity
        return 0.0

    # ------------------------------------------------------------------
    # Group severity aggregation
    # ------------------------------------------------------------------

    def group_severity(self, anomalies: list["Anomaly"]) -> dict[str, float]:  # type: ignore[name-defined]  # noqa: F821
        """
        For each unique (span_type, group_key) pair, take the MAX severity score.

        Returns a dict mapping "span_type:group_key" -> max_severity (float).
        Returns an empty dict when anomalies is empty.
        """
        group_max: dict[str, float] = {}
        for anomaly in anomalies:
            key = f"{anomaly.span_type}:{anomaly.group_key}"
            severity = self.classify_severity(anomaly)
            if key not in group_max or severity > group_max[key]:
                group_max[key] = severity
        return group_max

    # ------------------------------------------------------------------
    # Evolution decision
    # ------------------------------------------------------------------

    def should_evolve(self, anomalies: list["Anomaly"], threshold: float = 0.5) -> bool:  # type: ignore[name-defined]  # noqa: F821
        """
        Returns True if any group's max severity >= threshold.

        Default threshold = 0.5 (medium severity).
        An empty anomaly list always returns False.
        """
        if not anomalies:
            return False
        group_max = self.group_severity(anomalies)
        return any(severity >= threshold for severity in group_max.values())

    # ------------------------------------------------------------------
    # Recommendation builder
    # ------------------------------------------------------------------

    def recommend(self, anomalies: list["Anomaly"]) -> EvolutionRecommendation:  # type: ignore[name-defined]  # noqa: F821
        """
        Build an EvolutionRecommendation from a list of anomalies.

        Handles empty input gracefully (returns a recommendation with
        should_evolve=False and empty fields).
        """
        if not anomalies:
            return EvolutionRecommendation(
                should_evolve=False,
                severity_threshold=0.5,
                anomalies=[],
                top_anomaly=None,
                max_severity=0.0,
                recommendation="No anomalies detected — no action required.",
                suggested_action="Continue monitoring.",
            )

        # Compute severity for each anomaly
        scored = [(a, self.classify_severity(a)) for a in anomalies]

        # Find top anomaly (highest severity; ties broken by first occurrence)
        top_anomaly, max_severity = max(scored, key=lambda x: x[1])

        should_evolve = self.should_evolve(anomalies)

        # Build human-readable recommendation
        recommendation = self._build_recommendation(
            top_anomaly, max_severity, anomalies
        )
        suggested_action = self._build_action(top_anomaly, max_severity)

        return EvolutionRecommendation(
            should_evolve=should_evolve,
            severity_threshold=0.5,
            anomalies=anomalies,
            top_anomaly=top_anomaly,
            max_severity=max_severity,
            recommendation=recommendation,
            suggested_action=suggested_action,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_recommendation(
        self, top_anomaly: "Anomaly", max_severity: float, anomalies: list  # type: ignore[name-defined]  # noqa: F821
    ) -> str:
        """Build a one-line human-readable recommendation string."""
        count = len(anomalies)
        a = top_anomaly
        label = self._severity_label(max_severity)

        if a.anomaly_type == "high_error_rate":
            return (
                f"{label} severity: {a.span_type} '{a.group_key}' has "
                f"{a.actual_value:.1%} error rate, suggesting review of "
                f"error handling logic ({count} total anomaly(ies))."
            )
        if a.anomaly_type == "latency_spike":
            return (
                f"{label} severity: {a.span_type} '{a.group_key}' has "
                f"{a.actual_value:.0f}ms p95 latency, suggesting "
                f"performance profiling and optimisation "
                f"({count} total anomaly(ies))."
            )
        if a.anomaly_type == "low_success_rate":
            return (
                f"{label} severity: {a.span_type} '{a.group_key}' has "
                f"{a.actual_value:.1%} success rate, suggesting review of "
                f"success path logic ({count} total anomaly(ies))."
            )
        return (
            f"{label} severity anomaly on '{a.group_key}': "
            f"{a.suggestion} ({count} total anomaly(ies))."
        )

    def _build_action(self, top_anomaly: "Anomaly", max_severity: float) -> str:  # type: ignore[name-defined]  # noqa: F821
        """Build a more specific suggested-action string."""
        a = top_anomaly
        label = self._severity_label(max_severity)

        if a.anomaly_type == "high_error_rate":
            return (
                f"Review and improve error handling for {a.span_type} "
                f"'{a.group_key}' (observed {a.actual_value:.1%} error rate "
                f"across {a.span_count} spans, threshold {a.threshold:.1%})."
            )
        if a.anomaly_type == "latency_spike":
            return (
                f"Profile and optimise {a.span_type} '{a.group_key}' — "
                f"p95 latency of {a.actual_value:.0f}ms exceeds threshold "
                f"of {a.threshold:.0f}ms across {a.span_count} spans."
            )
        if a.anomaly_type == "low_success_rate":
            return (
                f"Investigate and fix success path issues in {a.span_type} "
                f"'{a.group_key}' (observed {a.actual_value:.1%} success rate, "
                f"threshold {a.threshold:.1%}, across {a.span_count} spans)."
            )
        return f"Investigate {a.span_type} '{a.group_key}': {a.suggestion}"

    @staticmethod
    def _severity_label(severity: float) -> str:
        """Return a human-readable severity band label."""
        if severity >= 0.7:
            return "High"
        if severity >= 0.3:
            return "Medium"
        return "Low"
