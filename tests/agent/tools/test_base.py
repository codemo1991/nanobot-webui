"""Tests for the Tool base class."""

import pytest
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.progress import ToolProgressCallback, ToolProgressThrottler


class MockTool(Tool):
    """A mock tool implementation for testing."""

    @property
    def name(self) -> str:
        return "mock_tool"

    @property
    def description(self) -> str:
        return "A mock tool for testing."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "input": {"type": "string"}
            },
            "required": ["input"]
        }

    async def execute(self, **kwargs) -> str:
        return f"Executed with {kwargs.get('input')}"


class TestToolProgress:
    """Tests for Tool progress callback support."""

    def test_tool_can_be_initialized_with_progress_callback(self):
        """Test that Tool can be initialized with a progress_callback parameter."""
        calls = []

        def callback(tool_id: str, message: str, progress: float | None = None) -> None:
            calls.append({"tool_id": tool_id, "message": message, "progress": progress})

        tool = MockTool(progress_callback=callback)

        assert tool._progress_callback is callback
        assert isinstance(tool._progress_throttler, ToolProgressThrottler)

    def test_tool_has_unique_tool_id(self):
        """Test that each Tool instance has a unique auto-generated tool_id."""
        tool1 = MockTool()
        tool2 = MockTool()

        assert tool1.tool_id is not None
        assert tool2.tool_id is not None
        assert tool1.tool_id != tool2.tool_id

    def test_tool_accepts_custom_tool_id(self):
        """Test that a custom tool_id can be provided."""
        custom_id = "my-custom-tool-id"
        tool = MockTool(tool_id=custom_id)

        assert tool.tool_id == custom_id

    def test_report_progress_calls_callback(self):
        """Test that report_progress calls the callback when throttler allows."""
        calls = []

        def callback(tool_id: str, message: str, progress: float | None = None) -> None:
            calls.append({"tool_id": tool_id, "message": message, "progress": progress})

        tool = MockTool(progress_callback=callback, tool_id="test-tool")

        # First call should succeed (throttler allows first push)
        tool.report_progress("Starting...", 0)

        assert len(calls) == 1
        assert calls[0]["tool_id"] == "test-tool"
        assert calls[0]["message"] == "Starting..."
        assert calls[0]["progress"] == 0.0

    def test_report_progress_without_callback_does_not_error(self):
        """Test that report_progress doesn't error when no callback is set."""
        tool = MockTool()

        # Should not raise
        tool.report_progress("Starting...", 0)

    def test_report_stream_chunk_placeholder(self):
        """Test that report_stream_chunk exists as a placeholder."""
        tool = MockTool()

        # Should not raise
        tool.report_stream_chunk("chunk of output")
        tool.report_stream_chunk("error output", is_error=True)
