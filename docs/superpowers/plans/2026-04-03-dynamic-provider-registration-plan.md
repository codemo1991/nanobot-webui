# Dynamic Provider Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend ProviderManager to support dynamic provider instance registration, enabling all system_providers.py definitions to be used at runtime.

**Architecture:** Add a factory function `create_provider_instance()` that maps provider types to appropriate provider classes. Extend ProviderManager with `register_provider()` method for dynamic instance creation. Update initialization flow to create instances for all enabled providers.

**Tech Stack:** Python, AsyncOpenAI SDK, existing Provider classes (OpenAIProvider, AnthropicProvider, DeepSeekProvider, AzureProvider)

---

## File Overview

| File | Changes |
|------|---------|
| `nanobot/providers/provider_manager.py` | Add `create_provider_instance()` factory and `register_provider()` method |
| `nanobot/config/loader.py` | Initialize dynamic providers on startup |
| `nanobot/web/api.py` | Trigger instance creation on provider create/update |

---

## Task 1: Add Provider Factory Function

**Files:**
- Modify: `nanobot/providers/provider_manager.py` (add at top after imports)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_provider_factory.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:\workSpace\nanobot-webui && python -m pytest tests/unit/test_provider_factory.py -v`
Expected: FAIL - `create_provider_instance` not found

- [ ] **Step 3: Add imports and factory function**

Add to top of `nanobot/providers/provider_manager.py` after imports section:

```python
def create_provider_instance(
    provider_type: str,
    api_key: str,
    api_base: str | None = None,
) -> "LLMProvider":
    """
    Factory function to create provider instance by type.

    Args:
        provider_type: Provider type identifier (openai, anthropic, deepseek, azure, etc.)
        api_key: API key for the provider
        api_base: Optional API base URL

    Returns:
        Provider instance matching the type
    """
    # Avoid circular imports
    from nanobot.providers.openai_provider import OpenAIProvider
    from nanobot.providers.anthropic_provider import AnthropicProvider
    from nanobot.providers.deepseek_provider import DeepSeekProvider
    from nanobot.providers.azure_provider import AzureProvider

    provider_type_lower = provider_type.lower() if provider_type else ""

    # OpenAI-compatible types
    if provider_type_lower in ("", "openai", "openai-compatible", "openai-response"):
        return OpenAIProvider(api_key=api_key, api_base=api_base)

    # Native SDK providers
    if provider_type_lower == "anthropic":
        return AnthropicProvider(api_key=api_key)

    if provider_type_lower == "deepseek":
        return DeepSeekProvider(api_key=api_key, api_base=api_base)

    if provider_type_lower == "azure":
        return AzureProvider(api_key=api_key, api_base=api_base)

    # Fallback: treat as OpenAI-compatible
    logger.warning(f"Unknown provider type '{provider_type}', falling back to OpenAIProvider")
    return OpenAIProvider(api_key=api_key, api_base=api_base)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:\workSpace\nanobot-webui && python -m pytest tests/unit/test_provider_factory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/providers/provider_manager.py tests/unit/test_provider_factory.py
git commit -m "feat(provider): add create_provider_instance factory function"
```

---

## Task 2: Add register_provider Method to ProviderManager

**Files:**
- Modify: `nanobot/providers/provider_manager.py`

- [ ] **Step 1: Write the failing test**

Update `tests/unit/test_provider_factory.py` with new test cases:

```python
def test_provider_manager_register_provider():
    """Test ProviderManager.register_provider creates and stores instance."""
    from nanobot.providers.provider_manager import ProviderManager

    pm = ProviderManager()

    # Register a dynamic provider
    pm.register_provider(
        provider_id="ocoolai",
        api_key="sk-ocoolai-test",
        api_base="https://api.ocoolai.com",
        provider_type="openai",
    )

    # Verify instance was created and stored
    provider = pm.get("ocoolai")
    assert provider is not None
    assert provider.__class__.__name__ == "OpenAIProvider"
    assert provider.api_key == "sk-ocoolai-test"
    assert provider.api_base == "https://api.ocoolai.com"


def test_provider_manager_register_provider_updates_existing():
    """Test registering existing provider updates its config."""
    from nanobot.providers.provider_manager import ProviderManager

    pm = ProviderManager()

    # Register first time
    pm.register_provider(
        provider_id="test-provider",
        api_key="sk-v1",
        api_base="https://v1.api.com",
        provider_type="openai",
    )

    # Register again with new config
    pm.register_provider(
        provider_id="test-provider",
        api_key="sk-v2",
        api_base="https://v2.api.com",
        provider_type="openai",
    )

    provider = pm.get("test-provider")
    assert provider.api_key == "sk-v2"
    assert provider.api_base == "https://v2.api.com"


def test_provider_manager_register_provider_with_none_api_key():
    """Test registering provider with None api_key creates instance."""
    from nanobot.providers.provider_manager import ProviderManager

    pm = ProviderManager()

    pm.register_provider(
        provider_id="no-key-provider",
        api_key=None,
        api_base="https://api.example.com",
        provider_type="openai",
    )

    provider = pm.get("no-key-provider")
    assert provider is not None
    assert provider.api_key is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:\workSpace\nanobot-webui && python -m pytest tests/unit/test_provider_factory.py -v`
Expected: FAIL - `register_provider` not found

- [ ] **Step 3: Add register_provider method to ProviderManager**

Add method to `ProviderManager` class in `nanobot/providers/provider_manager.py`:

```python
def register_provider(
    self,
    provider_id: str,
    api_key: str | None,
    api_base: str | None = None,
    provider_type: str = "openai",
) -> None:
    """
    Register or update a provider instance dynamically.

    Creates a new provider instance if one doesn't exist, or updates
    the existing instance's credentials if it does.

    Args:
        provider_id: Unique identifier for the provider
        api_key: API key (can be None to create placeholder)
        api_base: Optional API base URL
        provider_type: Provider type (openai, anthropic, deepseek, azure, etc.)
    """
    # Check if provider already exists
    existing = self._providers.get(provider_id)

    if existing:
        # Update existing instance credentials
        if api_key is not None:
            existing.api_key = api_key
        if api_base is not None:
            existing.api_base = api_base
        logger.debug(f"ProviderManager: updated credentials for {provider_id}")
    else:
        # Create new instance
        if api_key is not None:
            instance = create_provider_instance(
                provider_type=provider_type,
                api_key=api_key,
                api_base=api_base,
            )
            self._providers[provider_id] = instance
            logger.debug(f"ProviderManager: registered new provider {provider_id}")
        else:
            # Create with empty key as placeholder
            instance = create_provider_instance(
                provider_type=provider_type,
                api_key="",
                api_base=api_base,
            )
            self._providers[provider_id] = instance
            logger.debug(f"ProviderManager: registered placeholder for {provider_id}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd E:\workSpace\nanobot-webui && python -m pytest tests/unit/test_provider_factory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/providers/provider_manager.py
git commit -m "feat(provider): add register_provider method for dynamic instances"
```

---

## Task 3: Initialize Dynamic Providers on Startup

**Files:**
- Modify: `nanobot/config/loader.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_loader_providers.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd E:\workSpace\nanobot-webui && python -m pytest tests/unit/test_loader_providers.py -v`
Expected: FAIL - `init_dynamic_providers` not found

- [ ] **Step 3: Add init_dynamic_providers function**

Add to `nanobot/config/loader.py` after `init_system_providers()`:

```python
def init_dynamic_providers(
    repo: "ConfigRepository",
    provider_manager: "ProviderManager",
) -> None:
    """
    Initialize all enabled providers from database as dynamic instances.

    This registers all providers (including system providers from
    system_providers.py and user-created providers) with the
    ProviderManager so they can be used for model routing.

    Args:
        repo: ConfigRepository instance
        provider_manager: ProviderManager instance
    """
    providers = repo.get_all_providers()

    for p in providers:
        if not p.get("enabled"):
            continue

        provider_id = p["id"]
        api_key = p.get("api_key", "")
        api_base = p.get("api_base")
        provider_type = p.get("provider_type", "openai")

        if not api_key:
            continue

        provider_manager.register_provider(
            provider_id=provider_id,
            api_key=api_key,
            api_base=api_base,
            provider_type=provider_type,
        )

    logger.debug(f"Dynamic providers initialized from database")
```

- [ ] **Step 4: Add get_all_providers to ConfigRepository**

Add method to `ConfigRepository` class in `nanobot/storage/config_repository.py`:

```python
def get_all_providers(self) -> list[dict[str, Any]]:
    """Get all provider configurations."""
    with self._connect() as conn:
        rows = conn.execute(
            "SELECT id, name, api_key, api_base, enabled, priority, display_name, provider_type, is_system, sort_order, config_json FROM config_providers"
        ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "api_key": row[2],
                "api_base": row[3],
                "enabled": bool(row[4]),
                "priority": row[5],
                "display_name": row[6],
                "provider_type": row[7],
                "is_system": bool(row[8]),
                "sort_order": row[9],
                "config_json": row[10],
            }
            for row in rows
        ]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd E:\workSpace\nanobot-webui && python -m pytest tests/unit/test_loader_providers.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add nanobot/config/loader.py nanobot/storage/config_repository.py
git commit -m "feat(config): add init_dynamic_providers for database providers"
```

---

## Task 4: Wire Up Initialization in API Server

**Files:**
- Modify: `nanobot/web/api.py`

- [ ] **Step 1: Find initialization location**

Search for where providers are initialized in api.py:

Run: `grep -n "update_from_config\|register_with_router" nanobot/web/api.py`

Expected output shows line numbers for initialization

- [ ] **Step 2: Add dynamic provider initialization**

Find the section after `self.provider_manager.update_from_config(config)` and `self.provider_manager.register_with_router(self.router)` and add:

```python
# Initialize dynamic providers from database
from nanobot.config.loader import init_dynamic_providers
init_dynamic_providers(repo, self.provider_manager)
```

- [ ] **Step 3: Run tests to verify**

Run: `cd E:\workSpace\nanobot-webui && python -m pytest tests/unit/ -v -k "provider" --tb=short`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add nanobot/web/api.py
git commit -m "feat(api): initialize dynamic providers on startup"
```

---

## Task 5: Update Provider Create/Update to Trigger Instance Creation

**Files:**
- Modify: `nanobot/web/api.py`

- [ ] **Step 1: Update create_provider method**

Find `create_provider` method and add after saving to database:

In the section after `repo.set_provider(...)`, add:

```python
# Create/update provider instance
if data.get("apiKey"):
    self.provider_manager.register_provider(
        provider_id=provider_id,
        api_key=data["apiKey"],
        api_base=data.get("apiBase"),
        provider_type=data.get("providerType", "openai"),
    )
    # Notify router to clear cache
    self.router.clear_cache()
```

- [ ] **Step 2: Update update_provider method**

Find `update_provider` method and add similar logic:

In the section after saving to database, add:

```python
# Update provider instance
if data.get("apiKey"):
    self.provider_manager.register_provider(
        provider_id=provider_id,
        api_key=data["apiKey"],
        api_base=data.get("apiBase"),
        provider_type=data.get("providerType", "openai"),
    )
    # Notify router to clear cache
    self.router.clear_cache()
```

- [ ] **Step 3: Run integration test**

Run: `cd E:\workSpace\nanobot-webui && python -m pytest tests/integration/ -v -k "provider" --tb=short` (or manual test)

- [ ] **Step 4: Commit**

```bash
git add nanobot/web/api.py
git commit -m "feat(api): trigger dynamic provider creation on create/update"
```

---

## Task 6: End-to-End Verification

- [ ] **Step 1: Start the application**

Run: `python -m nanobot.web.main` (or your usual startup command)

- [ ] **Step 2: Verify ocoolai provider resolves**

Check logs for: `No provider instance registered for ocoolai` should NOT appear

- [ ] **Step 3: Verify profile resolution works**

Configure "smart" profile to use a model from a dynamic provider (e.g., `deepseek-chat` with `provider_id: "ocoolai"`)

- [ ] **Step 4: Commit all changes**

```bash
git add -A
git commit -m "feat(provider): complete dynamic provider registration implementation"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add provider factory function | `provider_manager.py` |
| 2 | Add register_provider method | `provider_manager.py` |
| 3 | Initialize dynamic providers on startup | `loader.py`, `config_repository.py` |
| 4 | Wire up in API server | `api.py` |
| 5 | Trigger on create/update | `api.py` |
| 6 | E2E verification | - |
