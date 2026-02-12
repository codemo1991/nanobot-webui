"""Unit tests for SystemStatusService."""

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nanobot.services.system_status_service import SystemStatusService
from nanobot.storage.status_repository import StatusRepository


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    
    yield db_path
    
    # Cleanup
    try:
        if db_path.exists():
            db_path.unlink()
    except PermissionError:
        pass


@pytest.fixture
def status_repo(temp_db):
    """Create a StatusRepository instance."""
    return StatusRepository(temp_db)


@pytest.fixture
def mock_session_manager():
    """Create a mock SessionManager."""
    manager = MagicMock()
    manager.list_sessions.return_value = []
    return manager


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def service(status_repo, mock_session_manager, temp_workspace):
    """Create a SystemStatusService instance."""
    return SystemStatusService(
        status_repo=status_repo,
        session_manager=mock_session_manager,
        workspace=temp_workspace
    )


def test_initialize_records_start_time(service, status_repo):
    """Test that initialize records the start time."""
    before = time.time()
    service.initialize()
    after = time.time()
    
    start_time = status_repo.get_start_time()
    assert start_time is not None
    assert before <= start_time <= after


def test_get_uptime_returns_zero_when_no_start_time(service):
    """Test that get_uptime returns 0 when start_time doesn't exist."""
    uptime = service.get_uptime()
    assert uptime == 0


def test_get_uptime_calculates_correctly(service):
    """Test that get_uptime calculates the correct uptime."""
    service.initialize()
    time.sleep(0.1)  # Wait a bit
    
    uptime = service.get_uptime()
    assert uptime >= 0
    assert uptime < 2  # Should be less than 2 seconds


def test_get_uptime_is_non_negative(service, status_repo):
    """Test that get_uptime always returns non-negative values."""
    # Set a future start time (edge case)
    future_time = time.time() + 100
    status_repo.set_start_time(future_time)
    
    uptime = service.get_uptime()
    assert uptime >= 0


def test_get_session_count_returns_zero_by_default(service):
    """Test that get_session_count returns 0 when no sessions exist."""
    count = service.get_session_count()
    assert count == 0


def test_get_session_count_returns_correct_count(service, mock_session_manager):
    """Test that get_session_count returns the correct session count."""
    mock_session_manager.list_sessions.return_value = [
        {"key": "session1"},
        {"key": "session2"},
        {"key": "session3"}
    ]
    
    count = service.get_session_count()
    assert count == 3


def test_get_session_count_handles_error(service, mock_session_manager):
    """Test that get_session_count returns 0 on error."""
    mock_session_manager.list_sessions.side_effect = Exception("Database error")
    
    count = service.get_session_count()
    assert count == 0


def test_get_skills_info_returns_list(service):
    """Test that get_skills_info returns a list (may include builtin skills)."""
    skills = service.get_skills_info()
    assert isinstance(skills, list)
    # Each skill should have required fields
    for skill in skills:
        assert "name" in skill
        assert "version" in skill
        assert "description" in skill
        assert "source" in skill


def test_get_skills_info_returns_skill_information(service, temp_workspace):
    """Test that get_skills_info returns skill information."""
    # Create a mock skill
    skills_dir = temp_workspace / "skills" / "test_skill"
    skills_dir.mkdir(parents=True)
    
    skill_file = skills_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "version: 1.0.0\n"
        "description: Test skill\n"
        "---\n"
        "# Test Skill\n"
    )
    
    skills = service.get_skills_info()
    # Find our test skill
    test_skill = next((s for s in skills if s["name"] == "test_skill"), None)
    assert test_skill is not None
    assert test_skill["version"] == "1.0.0"
    assert test_skill["description"] == "Test skill"
    assert test_skill["source"] == "workspace"


def test_get_skills_info_handles_missing_metadata(service, temp_workspace):
    """Test that get_skills_info handles skills without metadata."""
    # Create a skill without frontmatter
    skills_dir = temp_workspace / "skills" / "simple_skill"
    skills_dir.mkdir(parents=True)
    
    skill_file = skills_dir / "SKILL.md"
    skill_file.write_text("# Simple Skill\n")
    
    skills = service.get_skills_info()
    # Find our simple skill
    simple_skill = next((s for s in skills if s["name"] == "simple_skill"), None)
    assert simple_skill is not None
    assert simple_skill["version"] == "unknown"
    assert simple_skill["description"] == "simple_skill"
    assert simple_skill["source"] == "workspace"


def test_get_status_returns_all_information(service, mock_session_manager, temp_workspace):
    """Test that get_status returns all system status information."""
    # Setup
    service.initialize()
    mock_session_manager.list_sessions.return_value = [{"key": "s1"}, {"key": "s2"}]
    
    # Create a skill
    skills_dir = temp_workspace / "skills" / "test_skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# Test")
    
    # Get status
    status = service.get_status()
    
    assert "uptime" in status
    assert "sessions" in status
    assert "skills" in status
    assert "skills_list" in status
    
    assert status["uptime"] >= 0
    assert status["sessions"] == 2
    assert status["skills"] >= 1  # At least our test skill (may have builtin skills too)
    assert len(status["skills_list"]) >= 1
    
    # Verify our test skill is in the list
    test_skill = next((s for s in status["skills_list"] if s["name"] == "test_skill"), None)
    assert test_skill is not None


def test_get_status_returns_defaults_on_error(service, status_repo, mock_session_manager):
    """Test that get_status returns default values on error."""
    # Make all components fail
    with patch.object(status_repo, 'get_start_time', side_effect=Exception("DB error")):
        with patch.object(mock_session_manager, 'list_sessions', side_effect=Exception("Session error")):
            with patch.object(service.skills_loader, 'list_skills', side_effect=Exception("Skills error")):
                status = service.get_status()
    
    assert status["uptime"] == 0
    assert status["sessions"] == 0
    assert status["skills"] == 0
    assert status["skills_list"] == []


def test_initialize_handles_error_gracefully(service, status_repo):
    """Test that initialize handles errors gracefully."""
    with patch.object(status_repo, 'set_start_time', side_effect=Exception("DB error")):
        # Should not raise an exception
        service.initialize()
