"""Tests for provider initialization in loader."""
import pytest
from unittest.mock import MagicMock, patch


def test_init_dynamic_providers_calls_register():
    """Test that init_dynamic_providers registers all enabled providers."""
    from nanobot.config.loader import init_dynamic_providers

    # Mock dependencies
    mock_repo = MagicMock()
    mock_provider_manager = MagicMock()

    # Mock enabled providers
    mock_repo.get_all_providers.return_value = [
        {"id": "ocoolai", "api_key": "sk-test1", "api_base": "https://api.ocoolai.com", "provider_type": "openai", "enabled": True},
        {"id": "cherryin", "api_key": "sk-test2", "api_base": "https://api.cherryin.com", "provider_type": "openai", "enabled": True},
        {"id": "disabled-provider", "api_key": "", "api_base": None, "provider_type": "openai", "enabled": False},
    ]

    init_dynamic_providers(mock_repo, mock_provider_manager)

    # Verify register_provider was called for enabled providers only
    calls = mock_provider_manager.register_provider.call_args_list
    assert len(calls) == 2
    assert calls[0][1]["provider_id"] == "ocoolai"
    assert calls[1][1]["provider_id"] == "cherryin"


def test_init_dynamic_providers_skips_disabled():
    """Test that disabled providers are not registered."""
    from nanobot.config.loader import init_dynamic_providers

    mock_repo = MagicMock()
    mock_provider_manager = MagicMock()

    mock_repo.get_all_providers.return_value = [
        {"id": "disabled", "api_key": "sk-test", "api_base": None, "provider_type": "openai", "enabled": False},
    ]

    init_dynamic_providers(mock_repo, mock_provider_manager)

    # Should not call register_provider for disabled provider
    mock_provider_manager.register_provider.assert_not_called()


def test_init_dynamic_providers_empty_list():
    """Test that empty provider list is handled gracefully."""
    from nanobot.config.loader import init_dynamic_providers

    mock_repo = MagicMock()
    mock_provider_manager = MagicMock()

    mock_repo.get_all_providers.return_value = []

    init_dynamic_providers(mock_repo, mock_provider_manager)

    mock_provider_manager.register_provider.assert_not_called()
