"""Tests for ToolProgressThrottler."""

import time

import pytest

from nanobot.agent.tools.progress import ToolProgressThrottler


def test_first_push_should_succeed() -> None:
    """Test 1: First push should succeed."""
    throttler = ToolProgressThrottler(min_interval=1.0)
    assert throttler.should_push("tool-1") is True


def test_subsequent_pushes_should_be_throttled() -> None:
    """Test 2: Subsequent pushes should be throttled."""
    throttler = ToolProgressThrottler(min_interval=1.0)
    assert throttler.should_push("tool-1") is True
    assert throttler.should_push("tool-1") is False
    assert throttler.should_push("tool-1") is False


def test_push_after_interval_should_succeed() -> None:
    """Test 3: Push after interval should succeed."""
    throttler = ToolProgressThrottler(min_interval=0.1)
    assert throttler.should_push("tool-1") is True
    time.sleep(0.15)
    assert throttler.should_push("tool-1") is True


def test_different_tools_should_be_independent() -> None:
    """Test 4: Different tools should be throttled independently."""
    throttler = ToolProgressThrottler(min_interval=1.0)
    assert throttler.should_push("tool-1") is True
    assert throttler.should_push("tool-2") is True
    assert throttler.should_push("tool-1") is False
