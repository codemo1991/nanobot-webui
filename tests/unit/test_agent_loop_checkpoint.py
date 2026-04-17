"""Tests for AgentLoop runtime checkpoint mechanisms."""

import pytest
from unittest.mock import MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.session.manager import Session


def test_find_overlap_identical():
    existing = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    restored = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert AgentLoop._find_overlap(existing, restored) == 2


def test_find_overlap_diverges():
    existing = [
        {"role": "user", "content": "hello"},
    ]
    restored = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert AgentLoop._find_overlap(existing, restored) == 1


def test_find_overlap_no_match():
    existing = [{"role": "user", "content": "hello"}]
    restored = [{"role": "user", "content": "world"}]
    assert AgentLoop._find_overlap(existing, restored) == 0


class MockLoop:
    _RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
    sessions = MagicMock()

    @staticmethod
    def _find_overlap(existing, restored):
        return AgentLoop._find_overlap(existing, restored)


def test_set_and_clear_checkpoint(tmp_path):
    loop = MockLoop()
    session = Session(key="web:test")
    session.add_message("user", "hello")

    AgentLoop._set_runtime_checkpoint(loop, session, {"test": True})
    assert "runtime_checkpoint" in session.metadata
    assert len(session.metadata["runtime_checkpoint"]["messages"]) == 1

    AgentLoop._clear_runtime_checkpoint(loop, session)
    assert "runtime_checkpoint" not in session.metadata


def test_restore_checkpoint_appends_missing_messages(tmp_path):
    loop = MockLoop()
    session = Session(key="web:test")
    session.add_message("user", "hello")

    # Simulate a checkpoint with more messages
    checkpoint_messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
        {"role": "user", "content": "foo"},
    ]
    session.metadata["runtime_checkpoint"] = {
        "messages": checkpoint_messages,
        "payload": {},
        "timestamp": 0,
    }

    restored = AgentLoop._restore_runtime_checkpoint(loop, session)
    assert restored is True
    assert len(session.messages) == 3
    assert session.messages[-1]["content"] == "foo"
    assert "runtime_checkpoint" not in session.metadata
