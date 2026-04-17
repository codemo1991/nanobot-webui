"""Tests for ClaudeCodeManager persistence and recovery."""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from nanobot.claude_code.manager import ClaudeCodeManager
from nanobot.bus.queue import MessageBus


class FakeSessionManager:
    def __init__(self):
        self._tasks = {}

    def save_claude_task(self, task_id, session_key, pid, status, prompt, workdir, result=None):
        self._tasks[task_id] = {
            "task_id": task_id,
            "session_key": session_key,
            "pid": pid,
            "status": status,
            "prompt": prompt,
            "workdir": workdir,
            "result": result,
        }

    def update_claude_task(self, task_id, status, result=None):
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = status
            if result is not None:
                self._tasks[task_id]["result"] = result

    def get_claude_tasks_by_status(self, status):
        return [dict(v) for v in self._tasks.values() if v["status"] == status]


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


@pytest.fixture
def manager(bus, workspace):
    sessions = FakeSessionManager()
    mgr = ClaudeCodeManager(
        workspace=workspace,
        bus=bus,
        default_timeout=3600,
        sessions=sessions,
    )
    mgr.sessions = sessions
    return mgr


@pytest.mark.asyncio
async def test_start_task_persists_running(manager, workspace):
    # Mock _run_claude_code to avoid spawning real process
    original = manager._run_claude_code
    manager._run_claude_code = AsyncMock()
    task_id = await manager.start_task("test prompt")
    manager._run_claude_code = original
    assert task_id in manager.sessions._tasks
    assert manager.sessions._tasks[task_id]["status"] == "running"
    meta_path = workspace / ".claude-results" / f"{task_id}.meta.json"
    assert meta_path.exists()


def test_recover_tasks_updates_finished(manager, workspace):
    task_id = "abc123"
    manager.sessions.save_claude_task(
        task_id=task_id,
        session_key="web:s1",
        pid=99999,
        status="running",
        prompt="p",
        workdir=".",
    )
    # Write a result file as if the process finished while backend was down
    result = {
        "task_id": task_id,
        "timestamp": "2024-01-01T00:00:00",
        "output": "done",
        "status": "done",
        "origin": {"channel": "web", "chat_id": "s1"},
    }
    result_dir = workspace / ".claude-results"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_path = result_dir / f"{task_id}.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    manager._recover_tasks()
    assert manager.sessions._tasks[task_id]["status"] == "done"


def test_recover_tasks_writes_lost_when_no_result(manager, workspace):
    task_id = "lost01"
    manager.sessions.save_claude_task(
        task_id=task_id,
        session_key="web:s1",
        pid=99999,
        status="running",
        prompt="p",
        workdir=".",
    )
    manager._recover_tasks()
    assert manager.sessions._tasks[task_id]["status"] == "lost"
    result_path = workspace / ".claude-results" / f"{task_id}.json"
    assert result_path.exists()


@pytest.mark.asyncio
async def test_cancel_task_updates_db(manager, workspace):
    original = manager._run_claude_code
    manager._run_claude_code = AsyncMock()
    task_id = await manager.start_task("test prompt")
    manager._run_claude_code = original
    assert manager.cancel_task(task_id) is True
    assert manager.sessions._tasks[task_id]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_by_session_updates_db(manager, workspace):
    original = manager._run_claude_code
    manager._run_claude_code = AsyncMock()
    task_id = await manager.start_task("test prompt", origin_channel="web", origin_chat_id="s1")
    manager._run_claude_code = original
    assert manager.cancel_by_session("web", "s1") == 1
    assert manager.sessions._tasks[task_id]["status"] == "cancelled"
