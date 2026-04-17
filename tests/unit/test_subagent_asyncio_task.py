"""Tests for SubagentManager asyncio.Task migration."""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus


class FakeProvider:
    def get_default_model(self):
        return "test-model"


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def manager(bus, tmp_path):
    return SubagentManager(
        provider=FakeProvider(),
        workspace=tmp_path,
        bus=bus,
        max_concurrent_subagents=3,
    )


@pytest.mark.asyncio
async def test_spawn_creates_asyncio_task(manager):
    with patch.object(manager, "_run_subagent_task", new=AsyncMock()) as mock_run:
        result = await manager.spawn("test task")
        assert "后台任务已启动" in result
        # One task should be tracked
        assert len(manager._running_tasks) == 1
        task_id = list(manager._running_tasks.keys())[0]
        _, task = manager._running_tasks[task_id]
        assert task is not None
        assert isinstance(task, asyncio.Task)


@pytest.mark.asyncio
async def test_cancel_by_session_cancels_task(manager):
    async def _mock(*args, **kwargs):
        await asyncio.Event().wait()  # hang so the task stays alive until cancelled
    with patch.object(manager, "_run_subagent_task", new=_mock):
        await manager.spawn("test task", origin_channel="web", origin_chat_id="s1")
        task_id = list(manager._running_tasks.keys())[0]
        _, task = manager._running_tasks[task_id]
        cancelled = manager.cancel_by_session("web", "s1")
        assert cancelled == 1
        # Give the event loop a chance to process the cancellation
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_cancel_task_cancels_task(manager):
    async def _mock(*args, **kwargs):
        await asyncio.Event().wait()  # hang so the task stays alive until cancelled
    with patch.object(manager, "_run_subagent_task", new=_mock):
        await manager.spawn("test task")
        task_id = list(manager._running_tasks.keys())[0]
        _, task = manager._running_tasks[task_id]
        assert manager.cancel_task(task_id) is True
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()
