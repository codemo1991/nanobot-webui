"""Tests for nanobot.tracing.types module."""

from __future__ import annotations

from enum import Enum

import pytest

from nanobot.tracing.types import (
    ARGS_PREVIEW_MAX_LEN,
    RESULT_PREVIEW_MAX_LEN,
    TRACE_VERSION,
    SpanStatus,
    SpanType,
    ToolResultStatus,
)


# ---------------------------------------------------------------------------
# SpanType enum tests
# ---------------------------------------------------------------------------

class TestSpanType:
    """Tests for SpanType enum."""

    def test_span_type_values(self) -> None:
        """Verify all SpanType enum values are correct."""
        assert SpanType.AGENT_TURN.value == "agent.turn"
        assert SpanType.LLM_CALL.value == "llm.call"
        assert SpanType.TOOL_EXECUTE.value == "tool.execute"
        assert SpanType.SUBAGENT_SPAWN.value == "subagent.spawn"
        assert SpanType.SUBAGENT_RESULT.value == "subagent.result"

    def test_span_type_count(self) -> None:
        """Verify all expected span types exist."""
        expected_types = {
            "agent.turn",
            "llm.call",
            "tool.execute",
            "subagent.spawn",
            "subagent.result",
        }
        actual_types = {st.value for st in SpanType}
        assert actual_types == expected_types

    def test_span_type_is_string_enum(self) -> None:
        """Verify SpanType inherits from str and Enum."""
        assert issubclass(SpanType, str)
        assert issubclass(SpanType, Enum)

    def test_span_type_string_comparison(self) -> None:
        """Verify SpanType members can be compared with strings."""
        assert SpanType.AGENT_TURN == "agent.turn"
        assert SpanType.LLM_CALL == "llm.call"
        assert SpanType.TOOL_EXECUTE == "tool.execute"
        assert SpanType.SUBAGENT_SPAWN == "subagent.spawn"
        assert SpanType.SUBAGENT_RESULT == "subagent.result"

    def test_span_type_from_string(self) -> None:
        """Verify SpanType members can be created from string values."""
        assert SpanType("agent.turn") == SpanType.AGENT_TURN
        assert SpanType("llm.call") == SpanType.LLM_CALL
        assert SpanType("tool.execute") == SpanType.TOOL_EXECUTE
        assert SpanType("subagent.spawn") == SpanType.SUBAGENT_SPAWN
        assert SpanType("subagent.result") == SpanType.SUBAGENT_RESULT

    def test_span_type_invalid_value(self) -> None:
        """Verify invalid string raises ValueError."""
        with pytest.raises(ValueError):
            SpanType("invalid.span")

    def test_span_type_name_property(self) -> None:
        """Verify name property returns enum member name."""
        assert SpanType.AGENT_TURN.name == "AGENT_TURN"
        assert SpanType.LLM_CALL.name == "LLM_CALL"
        assert SpanType.TOOL_EXECUTE.name == "TOOL_EXECUTE"
        assert SpanType.SUBAGENT_SPAWN.name == "SUBAGENT_SPAWN"
        assert SpanType.SUBAGENT_RESULT.name == "SUBAGENT_RESULT"


# ---------------------------------------------------------------------------
# SpanStatus enum tests
# ---------------------------------------------------------------------------

class TestSpanStatus:
    """Tests for SpanStatus enum."""

    def test_span_status_values(self) -> None:
        """Verify all SpanStatus enum values are correct."""
        assert SpanStatus.RUNNING.value == "running"
        assert SpanStatus.OK.value == "ok"
        assert SpanStatus.ERROR.value == "error"

    def test_span_status_count(self) -> None:
        """Verify all expected span statuses exist."""
        expected_statuses = {"running", "ok", "error"}
        actual_statuses = {ss.value for ss in SpanStatus}
        assert actual_statuses == expected_statuses

    def test_span_status_is_string_enum(self) -> None:
        """Verify SpanStatus inherits from str and Enum."""
        assert issubclass(SpanStatus, str)
        assert issubclass(SpanStatus, Enum)

    def test_span_status_string_comparison(self) -> None:
        """Verify SpanStatus members can be compared with strings."""
        assert SpanStatus.RUNNING == "running"
        assert SpanStatus.OK == "ok"
        assert SpanStatus.ERROR == "error"

    def test_span_status_from_string(self) -> None:
        """Verify SpanStatus members can be created from string values."""
        assert SpanStatus("running") == SpanStatus.RUNNING
        assert SpanStatus("ok") == SpanStatus.OK
        assert SpanStatus("error") == SpanStatus.ERROR

    def test_span_status_invalid_value(self) -> None:
        """Verify invalid string raises ValueError."""
        with pytest.raises(ValueError):
            SpanStatus("pending")

    def test_span_status_name_property(self) -> None:
        """Verify name property returns enum member name."""
        assert SpanStatus.RUNNING.name == "RUNNING"
        assert SpanStatus.OK.name == "OK"
        assert SpanStatus.ERROR.name == "ERROR"


# ---------------------------------------------------------------------------
# ToolResultStatus enum tests
# ---------------------------------------------------------------------------

class TestToolResultStatus:
    """Tests for ToolResultStatus enum."""

    def test_tool_result_status_values(self) -> None:
        """Verify all ToolResultStatus enum values are correct."""
        assert ToolResultStatus.SUCCESS.value == "success"
        assert ToolResultStatus.ERROR.value == "error"
        assert ToolResultStatus.TIMEOUT.value == "timeout"

    def test_tool_result_status_count(self) -> None:
        """Verify all expected tool result statuses exist."""
        expected_statuses = {"success", "error", "timeout"}
        actual_statuses = {trs.value for trs in ToolResultStatus}
        assert actual_statuses == expected_statuses

    def test_tool_result_status_is_string_enum(self) -> None:
        """Verify ToolResultStatus inherits from str and Enum."""
        assert issubclass(ToolResultStatus, str)
        assert issubclass(ToolResultStatus, Enum)

    def test_tool_result_status_string_comparison(self) -> None:
        """Verify ToolResultStatus members can be compared with strings."""
        assert ToolResultStatus.SUCCESS == "success"
        assert ToolResultStatus.ERROR == "error"
        assert ToolResultStatus.TIMEOUT == "timeout"

    def test_tool_result_status_from_string(self) -> None:
        """Verify ToolResultStatus members can be created from string values."""
        assert ToolResultStatus("success") == ToolResultStatus.SUCCESS
        assert ToolResultStatus("error") == ToolResultStatus.ERROR
        assert ToolResultStatus("timeout") == ToolResultStatus.TIMEOUT

    def test_tool_result_status_invalid_value(self) -> None:
        """Verify invalid string raises ValueError."""
        with pytest.raises(ValueError):
            ToolResultStatus("cancelled")

    def test_tool_result_status_name_property(self) -> None:
        """Verify name property returns enum member name."""
        assert ToolResultStatus.SUCCESS.name == "SUCCESS"
        assert ToolResultStatus.ERROR.name == "ERROR"
        assert ToolResultStatus.TIMEOUT.name == "TIMEOUT"


# ---------------------------------------------------------------------------
# Constant tests
# ---------------------------------------------------------------------------

class TestConstants:
    """Tests for module-level constants."""

    def test_trace_version_value(self) -> None:
        """Verify TRACE_VERSION is a string with expected format."""
        assert isinstance(TRACE_VERSION, str)
        assert TRACE_VERSION == "2.0"

    def test_args_preview_max_len_value(self) -> None:
        """Verify ARGS_PREVIEW_MAX_LEN is a positive integer."""
        assert isinstance(ARGS_PREVIEW_MAX_LEN, int)
        assert ARGS_PREVIEW_MAX_LEN == 500
        assert ARGS_PREVIEW_MAX_LEN > 0

    def test_result_preview_max_len_value(self) -> None:
        """Verify RESULT_PREVIEW_MAX_LEN is a positive integer."""
        assert isinstance(RESULT_PREVIEW_MAX_LEN, int)
        assert RESULT_PREVIEW_MAX_LEN == 1000
        assert RESULT_PREVIEW_MAX_LEN > 0

    def test_preview_length_relationship(self) -> None:
        """Verify RESULT_PREVIEW_MAX_LEN is greater than ARGS_PREVIEW_MAX_LEN."""
        assert RESULT_PREVIEW_MAX_LEN > ARGS_PREVIEW_MAX_LEN


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestTypesIntegration:
    """Integration tests for types module."""

    def test_all_enums_are_string_based(self) -> None:
        """Verify all enums inherit from str for JSON serialization compatibility."""
        for enum_class in [SpanType, SpanStatus, ToolResultStatus]:
            for member in enum_class:
                assert isinstance(member, str)
                assert isinstance(member.value, str)

    def test_enum_values_are_snake_case(self) -> None:
        """Verify enum string values use dot notation for span types."""
        for member in SpanType:
            assert "." in member.value

    def test_span_status_values_match_span_class_usage(self) -> None:
        """Verify SpanStatus values match expected usage in Span class."""
        expected_statuses = {"running", "ok", "error"}
        actual_statuses = {s.value for s in SpanStatus}
        assert actual_statuses == expected_statuses

    def test_tool_result_status_covers_all_outcomes(self) -> None:
        """Verify ToolResultStatus covers success, failure, and timeout cases."""
        outcomes = {s.value for s in ToolResultStatus}
        assert "success" in outcomes
        assert "error" in outcomes
        assert "timeout" in outcomes
        assert len(outcomes) == 3

    def test_constants_can_be_used_for_truncation(self) -> None:
        """Verify constants can be used for string truncation logic."""
        long_args = "x" * 1000
        long_result = "y" * 2000

        truncated_args = long_args[:ARGS_PREVIEW_MAX_LEN]
        truncated_result = long_result[:RESULT_PREVIEW_MAX_LEN]

        assert len(truncated_args) == ARGS_PREVIEW_MAX_LEN
        assert len(truncated_result) == RESULT_PREVIEW_MAX_LEN

    def test_span_type_values_suitable_for_span_names(self) -> None:
        """Verify SpanType values can be used as span names."""
        for member in SpanType:
            name = member.value
            # span names should be non-empty and suitable for use
            assert len(name) > 0
            assert " " not in name  # no spaces
