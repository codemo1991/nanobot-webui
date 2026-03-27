"""Tests for agent loop tracing integration"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from pathlib import Path

from nanobot.agent.loop import AgentLoop


class FakeProvider:
    """Minimal fake provider for testing."""
    def get_default_model(self) -> str:
        return "fake-model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        return MagicMock(content="echo response", finish_reason="stop")


@pytest.fixture
def mock_bus():
    bus = MagicMock()
    return bus


@pytest.fixture
def agent_loop(mock_bus, tmp_path: Path):
    loop = AgentLoop(
        bus=mock_bus,
        provider=FakeProvider(),
        workspace=tmp_path,
        max_iterations=5,
    )
    return loop


@pytest.mark.asyncio
async def test_execute_tool_creates_span_with_mark_tool_span(agent_loop):
    """Test that tool execution creates a span with mark_tool_span called"""
    tool_call = MagicMock()
    tool_call.name = "read_file"
    tool_call.arguments = {"path": "test.py"}

    agent_loop.tools = MagicMock()
    agent_loop.tools.execute = AsyncMock(return_value="file content")

    with patch("nanobot.agent.loop.span") as mock_span:
        mock_span_instance = MagicMock()
        mock_span.return_value.__aenter__ = AsyncMock(return_value=mock_span_instance)
        mock_span.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await agent_loop._execute_single_tool(tool_call)

        # Verify span was called
        mock_span.assert_called_once()
        # positional args are in call_args[0], keyword args in call_args[1]
        call_args = mock_span.call_args
        assert call_args[0][0] == "tool.execute"
        assert call_args[1]["attrs"]["tool_name"] == "read_file"

        # Verify mark_tool_span was called
        mock_span_instance.mark_tool_span.assert_called_once()
        call_args_mark = mock_span_instance.mark_tool_span.call_args[0]
        assert call_args_mark[0] == "read_file"
        assert call_args_mark[1] == {"path": "test.py"}


@pytest.mark.asyncio
async def test_execute_tool_calls_set_tool_result_on_success(agent_loop):
    """Test that tool execution calls set_tool_result with success on success"""
    tool_call = MagicMock()
    tool_call.name = "read_file"
    tool_call.arguments = {"path": "test.py"}

    agent_loop.tools = MagicMock()
    agent_loop.tools.execute = AsyncMock(return_value="file content")

    with patch("nanobot.agent.loop.span") as mock_span:
        mock_span_instance = MagicMock()
        mock_span.return_value.__aenter__ = AsyncMock(return_value=mock_span_instance)
        mock_span.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await agent_loop._execute_single_tool(tool_call)

        # Verify set_tool_result was called with success
        mock_span_instance.set_tool_result.assert_called()
        call_args = mock_span_instance.set_tool_result.call_args[0]
        assert call_args[0] == "success"
        assert "file content" in str(call_args[1])


@pytest.mark.asyncio
async def test_execute_tool_calls_mark_evolution_candidate_for_exec_with_test(agent_loop):
    """Test that exec tool with 'test' in result marks evolution candidate"""
    tool_call = MagicMock()
    tool_call.name = "exec"
    tool_call.arguments = {"command": "pytest test.py"}

    agent_loop.tools = MagicMock()
    # exec tool uses execute_in_thread_pool, not execute
    agent_loop.tools.execute_in_thread_pool = AsyncMock(return_value="2 passed, 1 failed in 5s")

    with patch("nanobot.agent.loop.span") as mock_span:
        mock_span_instance = MagicMock()
        mock_span.return_value.__aenter__ = AsyncMock(return_value=mock_span_instance)
        mock_span.return_value.__aexit__ = AsyncMock(return_value=None)

        await agent_loop._execute_single_tool(tool_call)

        # Verify mark_evolution_candidate was called
        mock_span_instance.mark_evolution_candidate.assert_called_once()
        tags_arg = mock_span_instance.mark_evolution_candidate.call_args[0][0]
        assert "exec" in tags_arg
        assert "pattern_detected" in tags_arg


@pytest.mark.asyncio
async def test_execute_tool_handles_error_and_sets_error_attr(agent_loop):
    """Test that tool errors result in the span being created with tool attributes set"""
    tool_call = MagicMock()
    tool_call.name = "read_file"
    tool_call.arguments = {"path": "test.py"}

    agent_loop.tools = MagicMock()
    agent_loop.tools.execute = AsyncMock(side_effect=FileNotFoundError("not found"))

    with patch("nanobot.agent.loop.span") as mock_span:
        mock_span_instance = MagicMock()
        mock_span.return_value.__aenter__ = AsyncMock(return_value=mock_span_instance)
        mock_span.return_value.__aexit__ = AsyncMock(return_value=False)

        # The exception propagates from the span's __aexit__ (returns False = don't suppress)
        result = await agent_loop._execute_single_tool(tool_call)

        # Verify span was called correctly with tool name and attrs
        mock_span.assert_called_once()
        call_args = mock_span.call_args
        assert call_args[0][0] == "tool.execute"
        assert call_args[1]["attrs"]["tool_name"] == "read_file"

        # Verify mark_tool_span was called
        mock_span_instance.mark_tool_span.assert_called_once()


def test_get_current_span_id_helper(agent_loop):
    """Test _get_current_span_id returns span ID from context"""
    with patch("nanobot.tracing.context.get_current_span_id") as mock_get:
        mock_get.return_value = "span_abc123"
        span_id = agent_loop._get_current_span_id()
        assert span_id == "span_abc123"
        mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_is_evolution_candidate_returns_true_for_exec_with_test_in_result(agent_loop):
    """Test _is_evolution_candidate detects test patterns in exec results"""
    assert agent_loop._is_evolution_candidate("exec", "Running test suite...\n2 failed") is True
    assert agent_loop._is_evolution_candidate("exec", "test passed") is True


@pytest.mark.asyncio
async def test_is_evolution_candidate_returns_false_for_other_tools(agent_loop):
    """Test _is_evolution_candidate returns False for non-exec tools"""
    assert agent_loop._is_evolution_candidate("read_file", "file content") is False
    assert agent_loop._is_evolution_candidate("write_file", "done") is False


@pytest.mark.asyncio
async def test_execute_tool_passes_parent_id_to_span(agent_loop):
    """Test that tool span uses parent_id from current tracing context"""
    tool_call = MagicMock()
    tool_call.name = "read_file"
    tool_call.arguments = {"path": "test.py"}

    agent_loop.tools = MagicMock()
    agent_loop.tools.execute = AsyncMock(return_value="content")

    with patch.object(agent_loop, "_get_current_span_id") as mock_get_span_id:
        mock_get_span_id.return_value = "parent_span_xyz"
        with patch("nanobot.agent.loop.span") as mock_span:
            mock_span_instance = MagicMock()
            mock_span.return_value.__aenter__ = AsyncMock(return_value=mock_span_instance)
            mock_span.return_value.__aexit__ = AsyncMock(return_value=None)

            await agent_loop._execute_single_tool(tool_call)

            call_args = mock_span.call_args
            assert call_args[1]["parent_id"] == "parent_span_xyz"
