"""Unit tests for StatusRepository."""

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from nanobot.storage.status_repository import StatusRepository


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    
    yield db_path
    
    # Cleanup - use missing_ok to avoid errors on Windows
    try:
        if db_path.exists():
            db_path.unlink()
    except PermissionError:
        # On Windows, SQLite may still have the file locked
        pass


def test_init_creates_table(temp_db):
    """Test that initialization creates the system_status table."""
    repo = StatusRepository(temp_db)
    
    # Verify table exists
    with sqlite3.connect(temp_db) as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='system_status'"
        )
        assert cursor.fetchone() is not None


def test_get_nonexistent_key(temp_db):
    """Test getting a key that doesn't exist returns None."""
    repo = StatusRepository(temp_db)
    assert repo.get("nonexistent") is None


def test_set_and_get(temp_db):
    """Test setting and getting a value."""
    repo = StatusRepository(temp_db)
    
    repo.set("test_key", "test_value")
    assert repo.get("test_key") == "test_value"


def test_set_updates_existing_key(temp_db):
    """Test that setting an existing key updates the value."""
    repo = StatusRepository(temp_db)
    
    repo.set("test_key", "value1")
    repo.set("test_key", "value2")
    
    assert repo.get("test_key") == "value2"


def test_get_start_time_nonexistent(temp_db):
    """Test getting start_time when it doesn't exist returns None."""
    repo = StatusRepository(temp_db)
    assert repo.get_start_time() is None


def test_set_and_get_start_time(temp_db):
    """Test setting and getting start_time."""
    repo = StatusRepository(temp_db)
    
    timestamp = time.time()
    repo.set_start_time(timestamp)
    
    retrieved = repo.get_start_time()
    assert retrieved is not None
    assert abs(retrieved - timestamp) < 0.001  # Allow small floating point difference


def test_set_start_time_updates_existing(temp_db):
    """Test that setting start_time multiple times updates the value."""
    repo = StatusRepository(temp_db)
    
    timestamp1 = time.time()
    repo.set_start_time(timestamp1)
    
    time.sleep(0.01)  # Small delay to ensure different timestamp
    
    timestamp2 = time.time()
    repo.set_start_time(timestamp2)
    
    retrieved = repo.get_start_time()
    assert retrieved is not None
    assert abs(retrieved - timestamp2) < 0.001


def test_get_start_time_handles_invalid_value(temp_db):
    """Test that get_start_time handles invalid stored values gracefully."""
    repo = StatusRepository(temp_db)
    
    # Set an invalid value directly
    repo.set("start_time", "invalid_float")
    
    # Should return None instead of raising an exception
    assert repo.get_start_time() is None


def test_multiple_keys(temp_db):
    """Test storing multiple different keys."""
    repo = StatusRepository(temp_db)
    
    repo.set("key1", "value1")
    repo.set("key2", "value2")
    repo.set("key3", "value3")
    
    assert repo.get("key1") == "value1"
    assert repo.get("key2") == "value2"
    assert repo.get("key3") == "value3"
