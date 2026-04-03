"""Tests for provider factory function."""
import pytest
from unittest.mock import MagicMock


def test_create_openai_provider():
    """Test OpenAI provider creation."""
    from nanobot.providers.provider_manager import create_provider_instance

    provider = create_provider_instance(
        provider_type="openai",
        api_key="sk-test",
        api_base="https://api.example.com",
    )

    assert provider.__class__.__name__ == "OpenAIProvider"
    assert provider.api_key == "sk-test"
    assert provider.api_base == "https://api.example.com"


def test_create_openai_compatible_provider():
    """Test openai-compatible type creates OpenAIProvider."""
    from nanobot.providers.provider_manager import create_provider_instance

    provider = create_provider_instance(
        provider_type="openai-compatible",
        api_key="sk-test",
        api_base="https://custom.api.com",
    )

    assert provider.__class__.__name__ == "OpenAIProvider"


def test_create_anthropic_provider():
    """Test Anthropic provider creation."""
    from nanobot.providers.provider_manager import create_provider_instance

    provider = create_provider_instance(
        provider_type="anthropic",
        api_key="sk-ant-test",
        api_base=None,
    )

    assert provider.__class__.__name__ == "AnthropicProvider"


def test_create_deepseek_provider():
    """Test DeepSeek provider creation."""
    from nanobot.providers.provider_manager import create_provider_instance

    provider = create_provider_instance(
        provider_type="deepseek",
        api_key="sk-ds-test",
        api_base="https://api.deepseek.com",
    )

    assert provider.__class__.__name__ == "DeepSeekProvider"


def test_create_azure_provider():
    """Test Azure provider creation."""
    from nanobot.providers.provider_manager import create_provider_instance

    provider = create_provider_instance(
        provider_type="azure",
        api_key="azure-key",
        api_base="https://example.openai.azure.com",
    )

    assert provider.__class__.__name__ == "AzureProvider"


def test_unknown_type_fallback_to_openai():
    """Test unknown provider type falls back to OpenAIProvider."""
    from nanobot.providers.provider_manager import create_provider_instance

    provider = create_provider_instance(
        provider_type="unknown-type",
        api_key="sk-test",
        api_base=None,
    )

    assert provider.__class__.__name__ == "OpenAIProvider"


def test_create_empty_provider_type():
    """Test empty provider type falls back to OpenAIProvider."""
    from nanobot.providers.provider_manager import create_provider_instance

    provider = create_provider_instance(
        provider_type="",
        api_key="sk-test",
        api_base=None,
    )

    assert provider.__class__.__name__ == "OpenAIProvider"


def test_create_openai_response_provider():
    """Test openai-response type creates OpenAIProvider."""
    from nanobot.providers.provider_manager import create_provider_instance

    provider = create_provider_instance(
        provider_type="openai-response",
        api_key="sk-test",
        api_base="https://api.openai.com",
    )

    assert provider.__class__.__name__ == "OpenAIProvider"
