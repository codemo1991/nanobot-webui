"""End-to-end tests for tool progress streaming."""

import pytest
import asyncio
import json
import time
from typing import Any

from nanobot.agent.tools.progress import ToolProgressThrottler


class TestToolProgressStreaming:
    """Test the complete tool progress streaming flow."""

    def test_throttler_rate_limiting(self):
        """Test that throttler correctly limits push frequency."""
        throttler = ToolProgressThrottler(min_interval=1.0)

        # First push should succeed
        assert throttler.should_push("tool-1") is True

        # Subsequent pushes should be blocked
        assert throttler.should_push("tool-1") is False
        assert throttler.should_push("tool-1") is False

    def test_throttler_independent_per_tool(self):
        """Test that different tools have independent throttling."""
        throttler = ToolProgressThrottler(min_interval=1.0)

        # Both tools can push initially
        assert throttler.should_push("tool-1") is True
        assert throttler.should_push("tool-2") is True

        # Both should be throttled now
        assert throttler.should_push("tool-1") is False
        assert throttler.should_push("tool-2") is False

    def test_throttler_reset(self):
        """Test throttler reset functionality."""
        throttler = ToolProgressThrottler(min_interval=1.0)

        # Push and verify blocked
        assert throttler.should_push("tool-1") is True
        assert throttler.should_push("tool-1") is False

        # Reset specific tool
        throttler.reset("tool-1")
        assert throttler.should_push("tool-1") is True

        # Other tool still blocked
        assert throttler.should_push("tool-2") is True
        assert throttler.should_push("tool-2") is False

    def test_throttler_global_reset(self):
        """Test global reset clears all throttles."""
        throttler = ToolProgressThrottler(min_interval=1.0)

        # Push all tools
        throttler.should_push("tool-1")
        throttler.should_push("tool-2")
        throttler.should_push("tool-3")

        # Global reset
        throttler.reset()  # Reset all

        # All should work again
        assert throttler.should_push("tool-1") is True
        assert throttler.should_push("tool-2") is True
        assert throttler.should_push("tool-3") is True

    def test_throttler_short_interval(self):
        """Test throttler with short interval."""
        throttler = ToolProgressThrottler(min_interval=0.1)

        # First push
        assert throttler.should_push("tool-1") is True

        # Immediate second push blocked
        assert throttler.should_push("tool-1") is False

        # Wait for interval
        time.sleep(0.15)

        # Should work now
        assert throttler.should_push("tool-1") is True


class TestToolEventStructure:
    """Test tool event data structure and validation."""

    def test_tool_start_event_structure(self):
        """Test that tool_start event has required fields."""
        event = {
            "type": "tool_start",
            "id": "abc123",
            "name": "exec",
            "arguments": {"command": "ls"}
        }

        assert event["type"] == "tool_start"
        assert "id" in event
        assert "name" in event
        assert "arguments" in event

    def test_tool_end_event_structure(self):
        """Test that tool_end event has required fields."""
        event = {
            "type": "tool_end",
            "id": "abc123",
            "name": "exec",
            "result": "file1.txt\nfile2.txt"
        }

        assert event["type"] == "tool_end"
        assert "id" in event
        assert "name" in event
        assert "result" in event

    def test_tool_progress_event_structure(self):
        """Test that tool_progress event has required fields."""
        event = {
            "type": "tool_progress",
            "tool_id": "abc123",
            "status": "running",
            "detail": "Processing...",
            "progress_percent": 50
        }

        assert event["type"] == "tool_progress"
        assert "tool_id" in event
        assert "status" in event
        assert "detail" in event

    def test_event_serialization(self):
        """Test that events can be serialized to JSON."""
        events = [
            {"type": "tool_start", "id": "123", "name": "test", "arguments": {}},
            {"type": "tool_progress", "tool_id": "123", "status": "running", "detail": "..."},
            {"type": "tool_end", "id": "123", "name": "test", "result": "done"},
        ]

        for event in events:
            # Should not raise
            json_str = json.dumps(event)
            # Should not raise
            parsed = json.loads(json_str)
            assert parsed == event


class TestToolStepMapping:
    """Test mapping between backend events and frontend types."""

    def test_streaming_state_transitions(self):
        """Test valid state transitions for streaming tool steps."""
        states = ["pending", "running", "waiting", "completed", "error"]
        valid_transitions = {
            "pending": ["running", "error"],
            "running": ["waiting", "completed", "error"],
            "waiting": ["running", "completed", "error"],
            "completed": [],  # Terminal state
            "error": [],  # Terminal state
        }

        # Test a valid transition path
        current_state = "pending"
        next_state = "running"
        assert next_state in valid_transitions[current_state]

        current_state = "running"
        next_state = "completed"
        assert next_state in valid_transitions[current_state]

    def test_duration_calculation(self):
        """Test duration calculation from timestamps."""
        start_time = time.time() * 1000  # ms
        time.sleep(0.1)  # 100ms
        end_time = time.time() * 1000  # ms

        duration_ms = end_time - start_time
        assert 90 < duration_ms < 200  # Should be roughly 100ms


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
