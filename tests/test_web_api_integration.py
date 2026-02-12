"""Integration tests for NanobotWebAPI with SystemStatusService."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    # Cleanup
    if db_path.exists():
        db_path.unlink()


@patch("nanobot.web.api.load_config")
@patch("nanobot.web.api.LiteLLMProvider")
@patch("nanobot.web.api.AgentLoop")
def test_web_api_initializes_status_service(mock_agent_loop, mock_provider, mock_load_config, temp_db):
    """Test that NanobotWebAPI properly initializes SystemStatusService."""
    # Mock configuration
    mock_config = MagicMock()
    mock_config.get_api_key.return_value = "test-key"
    mock_config.get_api_base.return_value = "https://api.test.com"
    mock_config.agents.defaults.model = "gpt-4"
    mock_config.agents.defaults.max_tool_iterations = 10
    mock_config.workspace_path = Path("/tmp/workspace")
    mock_config.tools.web.search.api_key = None
    mock_config.tools.exec = MagicMock()
    mock_load_config.return_value = mock_config
    
    # Mock AgentLoop
    mock_agent_instance = MagicMock()
    mock_agent_instance.sessions = MagicMock()
    mock_agent_loop.return_value = mock_agent_instance
    
    # Patch the database path to use temp_db
    with patch("nanobot.web.api.Path.home") as mock_home:
        mock_home.return_value = temp_db.parent
        
        # Import and create API instance
        from nanobot.web.api import NanobotWebAPI
        
        api = NanobotWebAPI()
        
        # Verify status_service was initialized
        assert hasattr(api, "status_service")
        assert api.status_service is not None
        
        # Verify status_service has the correct attributes
        assert hasattr(api.status_service, "status_repo")
        assert hasattr(api.status_service, "session_manager")
        assert hasattr(api.status_service, "workspace")
        
        # Verify initialize was called (start_time should be set)
        start_time = api.status_service.status_repo.get_start_time()
        assert start_time is not None
        assert isinstance(start_time, float)
        assert start_time > 0


@patch("nanobot.web.api.load_config")
@patch("nanobot.web.api.LiteLLMProvider")
@patch("nanobot.web.api.AgentLoop")
def test_web_api_status_service_methods_work(mock_agent_loop, mock_provider, mock_load_config, temp_db):
    """Test that SystemStatusService methods work through NanobotWebAPI."""
    # Mock configuration
    mock_config = MagicMock()
    mock_config.get_api_key.return_value = "test-key"
    mock_config.get_api_base.return_value = "https://api.test.com"
    mock_config.agents.defaults.model = "gpt-4"
    mock_config.agents.defaults.max_tool_iterations = 10
    mock_config.workspace_path = Path("/tmp/workspace")
    mock_config.tools.web.search.api_key = None
    mock_config.tools.exec = MagicMock()
    mock_load_config.return_value = mock_config
    
    # Mock AgentLoop with sessions
    mock_agent_instance = MagicMock()
    mock_sessions = MagicMock()
    mock_sessions.list_sessions.return_value = [
        {"key": "session1"},
        {"key": "session2"}
    ]
    mock_agent_instance.sessions = mock_sessions
    mock_agent_loop.return_value = mock_agent_instance
    
    # Patch the database path to use temp_db
    with patch("nanobot.web.api.Path.home") as mock_home:
        mock_home.return_value = temp_db.parent
        
        # Import and create API instance
        from nanobot.web.api import NanobotWebAPI
        
        api = NanobotWebAPI()
        
        # Test get_uptime
        uptime = api.status_service.get_uptime()
        assert isinstance(uptime, int)
        assert uptime >= 0
        
        # Test get_session_count
        session_count = api.status_service.get_session_count()
        assert isinstance(session_count, int)
        assert session_count == 2
        
        # Test get_status
        status = api.status_service.get_status()
        assert isinstance(status, dict)
        assert "uptime" in status
        assert "sessions" in status
        assert "skills" in status
        assert status["sessions"] == 2
