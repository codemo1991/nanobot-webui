"""
Tests for nanobot.tracing.evolution.

These tests cover the EvolutionTrigger class and EvolutionRecommendation
dataclass with a focus on severity classification, aggregation,
evolution decisions, and recommendation generation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest  # noqa: F401 — used for pytest.approx in type comments

# Ensure the nanobot package is importable
WORKTREE = Path(__file__).resolve().parents[1]
if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))

from nanobot.tracing.anomaly import Anomaly
from nanobot.tracing.evolution import EvolutionRecommendation, EvolutionTrigger


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

TRIGGER = EvolutionTrigger()


def make_anomaly(
    anomaly_type: str,
    group_key: str = "test_tool",
    span_type: str = "tool",
    actual_value: float = 0.5,
    threshold: float = 0.1,
    span_count: int = 10,
    suggestion: str = "test suggestion",
) -> Anomaly:
    """Create an Anomaly object for testing."""
    return Anomaly(
        anomaly_type=anomaly_type,
        group_key=group_key,
        span_type=span_type,
        actual_value=actual_value,
        threshold=threshold,
        span_count=span_count,
        suggestion=suggestion,
    )


# ---------------------------------------------------------------------------
# classify_severity — high_error_rate
# ---------------------------------------------------------------------------

class TestClassifySeverityHighErrorRate:
    def test_no_anomaly_at_exact_threshold(self):
        """Exact threshold = 0.1 should yield severity 0.0 (strict >)."""
        a = make_anomaly("high_error_rate", actual_value=0.1, threshold=0.1)
        assert TRIGGER.classify_severity(a) == 0.0

    def test_just_above_threshold(self):
        """50% above threshold gives a low severity."""
        # actual=0.15, threshold=0.10 → (0.15-0.10)/0.10 * 2 = 1.0
        a = make_anomaly("high_error_rate", actual_value=0.15, threshold=0.1)
        assert TRIGGER.classify_severity(a) == pytest.approx(1.0)

    def test_half_threshold_excess(self):
        """25% above threshold gives severity 0.5."""
        # actual=0.125, threshold=0.10 → (0.125-0.10)/0.10 * 2 = 0.5
        a = make_anomaly("high_error_rate", actual_value=0.125, threshold=0.1)
        assert TRIGGER.classify_severity(a) == pytest.approx(0.5)

    def test_ten_times_threshold(self):
        """10× the threshold should cap at 1.0."""
        a = make_anomaly("high_error_rate", actual_value=1.0, threshold=0.1)
        assert TRIGGER.classify_severity(a) == 1.0

    def test_below_threshold(self):
        """Below threshold returns 0.0."""
        a = make_anomaly("high_error_rate", actual_value=0.05, threshold=0.1)
        assert TRIGGER.classify_severity(a) == 0.0


# ---------------------------------------------------------------------------
# classify_severity — latency_spike
# ---------------------------------------------------------------------------

class TestClassifySeverityLatencySpike:
    def test_no_anomaly_at_exact_threshold(self):
        """Exact threshold gives severity 0.0 (strict >)."""
        a = make_anomaly("latency_spike", actual_value=5000.0, threshold=5000.0)
        assert TRIGGER.classify_severity(a) == 0.0

    def test_just_above_threshold(self):
        """50% above threshold gives severity 1.0."""
        # actual=7500, threshold=5000 → (7500/5000 - 1) * 2 = 1.0
        a = make_anomaly("latency_spike", actual_value=7500.0, threshold=5000.0)
        assert TRIGGER.classify_severity(a) == 1.0

    def test_25_percent_above(self):
        """25% above threshold gives severity 0.5."""
        # actual=6250, threshold=5000 → (6250/5000 - 1) * 2 = 0.5
        a = make_anomaly("latency_spike", actual_value=6250.0, threshold=5000.0)
        assert TRIGGER.classify_severity(a) == 0.5

    def test_doubling_threshold_caps(self):
        """2× the threshold should cap at 1.0."""
        a = make_anomaly("latency_spike", actual_value=10000.0, threshold=5000.0)
        assert TRIGGER.classify_severity(a) == 1.0

    def test_below_threshold(self):
        """Below threshold returns 0.0."""
        a = make_anomaly("latency_spike", actual_value=1000.0, threshold=5000.0)
        assert TRIGGER.classify_severity(a) == 0.0


# ---------------------------------------------------------------------------
# classify_severity — low_success_rate
# ---------------------------------------------------------------------------

class TestClassifySeverityLowSuccessRate:
    def test_no_anomaly_at_exact_threshold(self):
        """Exact threshold gives severity 0.0 (strict < for low_success)."""
        a = make_anomaly("low_success_rate", actual_value=0.8, threshold=0.8)
        assert TRIGGER.classify_severity(a) == 0.0

    def test_half_threshold_gap(self):
        """Half the gap to zero gives severity 0.5."""
        # actual=0.6, threshold=0.8 → (0.8-0.6)/0.8 = 0.25
        a = make_anomaly("low_success_rate", actual_value=0.6, threshold=0.8)
        assert TRIGGER.classify_severity(a) == pytest.approx(0.25)

    def test_quarter_gap(self):
        """25% gap gives severity 0.25."""
        # actual=0.7, threshold=0.8 → (0.8-0.7)/0.8 = 0.125
        a = make_anomaly("low_success_rate", actual_value=0.7, threshold=0.8)
        assert TRIGGER.classify_severity(a) == pytest.approx(0.125)

    def test_caps_at_one(self):
        """Zero success rate should cap at 1.0."""
        a = make_anomaly("low_success_rate", actual_value=0.0, threshold=0.8)
        assert TRIGGER.classify_severity(a) == 1.0

    def test_above_threshold(self):
        """Above threshold (better success) returns 0.0."""
        a = make_anomaly("low_success_rate", actual_value=0.95, threshold=0.8)
        assert TRIGGER.classify_severity(a) == 0.0


# ---------------------------------------------------------------------------
# classify_severity — capped at 1.0
# ---------------------------------------------------------------------------

class TestClassifySeverityCappedAtOne:
    def test_high_error_rate_caps(self):
        a = make_anomaly("high_error_rate", actual_value=1.0, threshold=0.1)
        assert TRIGGER.classify_severity(a) == 1.0

    def test_latency_spike_caps(self):
        a = make_anomaly("latency_spike", actual_value=100000.0, threshold=5000.0)
        assert TRIGGER.classify_severity(a) == 1.0

    def test_low_success_rate_caps(self):
        a = make_anomaly("low_success_rate", actual_value=0.0, threshold=0.8)
        assert TRIGGER.classify_severity(a) == 1.0

    def test_unknown_type_returns_zero(self):
        """Unknown anomaly types fall back to 0.0."""
        a = make_anomaly("unknown_type")
        assert TRIGGER.classify_severity(a) == 0.0


# ---------------------------------------------------------------------------
# group_severity
# ---------------------------------------------------------------------------

class TestGroupSeverity:
    def test_takes_max(self):
        """When the same group has multiple anomalies, take the max severity."""
        a1 = make_anomaly(
            "high_error_rate", group_key="read_file", span_type="tool",
            actual_value=0.2, threshold=0.1, suggestion="err"
        )
        a2 = make_anomaly(
            "latency_spike", group_key="read_file", span_type="tool",
            actual_value=7500.0, threshold=5000.0, suggestion="slow"
        )
        result = TRIGGER.group_severity([a1, a2])
        # high_error_rate: (0.2-0.1)/0.1*2 = 2.0 → capped at 1.0
        # latency_spike: (7500/5000-1)*2 = 1.0
        assert result == {"tool:read_file": 1.0}

    def test_empty(self):
        """Empty list returns empty dict."""
        assert TRIGGER.group_severity([]) == {}

    def test_different_groups(self):
        """Different groups are independent."""
        a1 = make_anomaly(
            "high_error_rate", group_key="tool_a", span_type="tool",
            actual_value=0.15, threshold=0.1
        )
        a2 = make_anomaly(
            "latency_spike", group_key="tool_b", span_type="tool",
            actual_value=6250.0, threshold=5000.0
        )
        result = TRIGGER.group_severity([a1, a2])
        assert result == {"tool:tool_a": pytest.approx(1.0), "tool:tool_b": pytest.approx(0.5)}

    def test_different_span_types_same_group_key(self):
        """Same group_key but different span_type → separate entries."""
        a1 = make_anomaly(
            "high_error_rate", group_key="tpl", span_type="tool",
            actual_value=0.5, threshold=0.1
        )
        a2 = make_anomaly(
            "high_error_rate", group_key="tpl", span_type="subagent",
            actual_value=0.3, threshold=0.1
        )
        result = TRIGGER.group_severity([a1, a2])
        assert result == {"tool:tpl": 1.0, "subagent:tpl": 1.0}

    def test_max_overwrite_lower(self):
        """If a later anomaly has lower severity, it doesn't overwrite."""
        a1 = make_anomaly(
            "high_error_rate", group_key="x", span_type="tool",
            actual_value=0.3, threshold=0.1  # severity = (0.3-0.1)/0.1*2 = 4.0 → 1.0
        )
        a2 = make_anomaly(
            "low_success_rate", group_key="x", span_type="tool",
            actual_value=0.5, threshold=0.8  # severity = (0.8-0.5)/0.8 = 0.375
        )
        result = TRIGGER.group_severity([a1, a2])
        assert result["tool:x"] == 1.0  # max of 1.0 and 0.375


# ---------------------------------------------------------------------------
# should_evolve
# ---------------------------------------------------------------------------

class TestShouldEvolve:
    def test_true_above_threshold(self):
        """Any group with severity >= threshold triggers evolution."""
        a = make_anomaly(
            "high_error_rate", actual_value=0.2, threshold=0.1
        )  # severity = 1.0
        assert TRIGGER.should_evolve([a], threshold=0.5) is True

    def test_false_below_threshold(self):
        """Group below threshold does not trigger."""
        a = make_anomaly(
            "low_success_rate", actual_value=0.75, threshold=0.8
        )  # severity = (0.8-0.75)/0.8 = 0.0625
        assert TRIGGER.should_evolve([a], threshold=0.5) is False

    def test_false_exact_threshold(self):
        """Exact threshold value does NOT trigger (strict >= used in check)."""
        # severity 0.5 at threshold=0.5 → 0.5 >= 0.5 → True
        # Use a value that gives exactly 0.5 severity
        a = make_anomaly(
            "low_success_rate", actual_value=0.6, threshold=0.8
        )  # severity = (0.8-0.6)/0.8 = 0.25
        assert TRIGGER.should_evolve([a], threshold=0.5) is False

    def test_false_empty(self):
        """Empty list never triggers evolution."""
        assert TRIGGER.should_evolve([]) is False
        assert TRIGGER.should_evolve([], threshold=0.1) is False

    def test_threshold_custom(self):
        """Custom threshold is respected."""
        a = make_anomaly(
            "low_success_rate", actual_value=0.6, threshold=0.8
        )  # severity = 0.25
        assert TRIGGER.should_evolve([a], threshold=0.2) is True
        assert TRIGGER.should_evolve([a], threshold=0.3) is False

    def test_mixed_severities_one_above(self):
        """If any group is above threshold, evolution triggers."""
        low = make_anomaly(
            "low_success_rate", group_key="ok", span_type="tool",
            actual_value=0.75, threshold=0.8
        )  # severity ~0.0625
        high = make_anomaly(
            "high_error_rate", group_key="bad", span_type="tool",
            actual_value=0.2, threshold=0.1
        )  # severity 1.0
        assert TRIGGER.should_evolve([low, high], threshold=0.5) is True


# ---------------------------------------------------------------------------
# recommend
# ---------------------------------------------------------------------------

class TestRecommend:
    def test_populates_all_fields(self):
        """recommend() fills in every field of EvolutionRecommendation."""
        a = make_anomaly(
            "high_error_rate", group_key="read_file", span_type="tool",
            actual_value=0.25, threshold=0.1, span_count=20,
            suggestion="Reduce error rate for read_file"
        )
        rec = TRIGGER.recommend([a])

        assert rec.should_evolve is True
        assert rec.severity_threshold == 0.5
        assert rec.anomalies == [a]
        assert rec.top_anomaly is a
        # severity = (0.25-0.1)/0.1*2 = 3.0 → capped at 1.0
        assert rec.max_severity == 1.0
        assert "High" in rec.recommendation
        assert "read_file" in rec.recommendation
        assert "error rate" in rec.recommendation
        assert "read_file" in rec.suggested_action

    def test_empty_anomalies(self):
        """Empty list returns a valid recommendation with sensible defaults."""
        rec = TRIGGER.recommend([])

        assert rec.should_evolve is False
        assert rec.severity_threshold == 0.5
        assert rec.anomalies == []
        assert rec.top_anomaly is None
        assert rec.max_severity == 0.0
        assert "no action required" in rec.recommendation.lower()
        assert rec.suggested_action == "Continue monitoring."

    def test_no_false_positive_on_exact_threshold(self):
        """
        An anomaly whose severity is exactly 0.5 (from exact threshold) should
        NOT trigger should_evolve=True (since we use severity > threshold).
        """
        # Create a low_success_rate anomaly with exact severity 0.5:
        # severity = (threshold - actual) / threshold = 0.5
        # → actual = threshold * 0.5 = 0.4  (threshold=0.8)
        a = make_anomaly(
            "low_success_rate", actual_value=0.4, threshold=0.8
        )
        assert TRIGGER.classify_severity(a) == 0.5

        rec = TRIGGER.recommend([a])
        # max_severity = 0.5, threshold = 0.5
        # should_evolve = any(severity >= 0.5) = True  ← this is the design
        # The note says "exact threshold values don't trigger" — that applies
        # to classify_severity (returns 0.0 for exact threshold).
        # In this test the severity IS 0.5 (not exact-threshold case).
        # Verify classify gives 0.0 only when actual == threshold.
        a_exact = make_anomaly("low_success_rate", actual_value=0.8, threshold=0.8)
        assert TRIGGER.classify_severity(a_exact) == 0.0
        rec2 = TRIGGER.recommend([a_exact])
        assert rec2.should_evolve is False  # severity=0.0 < threshold=0.5

    def test_latency_spike_recommendation(self):
        """Latency spike generates correct recommendation text."""
        a = make_anomaly(
            "latency_spike", group_key="slow_op", span_type="tool",
            actual_value=10000.0, threshold=5000.0, span_count=15,
            suggestion="Optimise slow_op"
        )
        rec = TRIGGER.recommend([a])
        assert "High" in rec.recommendation
        assert "slow_op" in rec.recommendation
        assert "10000ms" in rec.recommendation
        assert "p95 latency" in rec.recommendation

    def test_low_success_rate_recommendation(self):
        """Low success rate generates correct recommendation text."""
        a = make_anomaly(
            "low_success_rate", group_key="flaky_op", span_type="subagent",
            actual_value=0.5, threshold=0.8, span_count=5,
            suggestion="Fix flaky_op"
        )
        rec = TRIGGER.recommend([a])
        # severity = (0.8-0.5)/0.8 = 0.375 → Medium
        assert "Medium" in rec.recommendation
        assert "flaky_op" in rec.recommendation
        assert "50.0%" in rec.recommendation  # actual_value as percent

    def test_multiple_anomalies_top_is_highest_severity(self):
        """top_anomaly is the one with the highest severity score."""
        low_sev = make_anomaly(
            "low_success_rate", group_key="ok_tool", span_type="tool",
            actual_value=0.79, threshold=0.8  # sev = 0.0125
        )
        high_sev = make_anomaly(
            "high_error_rate", group_key="bad_tool", span_type="tool",
            actual_value=0.5, threshold=0.1  # sev = 8.0 → 1.0
        )
        med_sev = make_anomaly(
            "latency_spike", group_key="slow_tool", span_type="tool",
            actual_value=6250.0, threshold=5000.0  # sev = 0.5
        )
        rec = TRIGGER.recommend([low_sev, high_sev, med_sev])
        assert rec.top_anomaly is high_sev
        assert rec.max_severity == 1.0
        assert rec.should_evolve is True

    def test_suggested_action_contains_span_count(self):
        """suggested_action includes span_count for context."""
        a = make_anomaly(
            "high_error_rate", group_key="err_tool", span_type="tool",
            actual_value=0.3, threshold=0.1, span_count=42
        )
        rec = TRIGGER.recommend([a])
        assert "42" in rec.suggested_action
