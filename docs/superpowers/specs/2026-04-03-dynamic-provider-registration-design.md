# Dynamic Provider Registration Design

**Date:** 2026-04-03
**Status:** Approved

## Problem

The current architecture hardcodes only 4 provider instances (`openai`, `anthropic`, `deepseek`, `azure`) in `ModelRouter` and `ProviderManager`. However, `system_providers.py` defines 30+ providers (including `ocoolai`, `cherryin`, `silicon`, `aihubmix`, etc.), and users can create custom providers through the UI.

When a model references a non-hardcoded provider (e.g., `provider_id: "ocoolai"`), `ModelRouter._resolve_model()` fails with:
- `No provider instance registered for ocoolai`
- `Profile smart has no available models in chain: deepseek-chat`

## Solution

Extend `ProviderManager` and `ModelRouter` to support dynamic provider instance registration. All OpenAI-compatible providers will be dynamically instantiated using `OpenAIProvider`, while native SDK providers (Anthropic, DeepSeek, Azure) remain as dedicated classes.

## Architecture

### 1. Provider Type Mapping

| Provider Type | Implementation | Notes |
|---------------|----------------|-------|
| `openai` | `OpenAIProvider` | Dynamic instance |
| `openai-compatible` | `OpenAIProvider` | Dynamic instance |
| `openai-response` | `OpenAIProvider` | Dynamic instance (OpenAI API compatible) |
| `anthropic` | `AnthropicProvider` | Native SDK |
| `deepseek` | `DeepSeekProvider` | Native SDK |
| `azure` | `AzureProvider` | Native SDK |

### 2. Component Changes

#### ProviderManager

```python
class ProviderManager:
    def __init__(self) -> None:
        self._providers: dict[str, "LLMProvider"] = {}

    # New: Dynamic registration
    def register_provider(
        self,
        provider_id: str,
        api_key: str | None,
        api_base: str | None,
        provider_type: str = "openai",
    ) -> None:
        """Register or update a provider instance dynamically."""
        ...

    # New: Register OpenAI-compatible provider
    def _create_openai_provider(
        self,
        api_key: str,
        api_base: str | None,
    ) -> OpenAIProvider:
        """Factory method for OpenAI-compatible providers."""
        return OpenAIProvider(api_key=api_key, api_base=api_base)
```

#### ModelRouter

```python
class ModelRouter:
    def __init__(self, repo: "ConfigRepository"):
        self.repo = repo
        self._cache: dict[str, ModelHandle] = {}
        self._providers: dict[str, "LLMProvider"] = {}

    # No changes needed - already generic via _providers dict
```

### 3. Initialization Flow

```
Startup:
1. Load config from database (providers, models)
2. For each enabled provider:
   a. If native type (anthropic/deepseek/azure) → use existing instance
   b. If openai-compatible → create OpenAIProvider dynamically
3. Register all instances with ProviderManager
4. ProviderManager.register_with_router(router)
```

### 4. Hot-Update Flow

```
User updates provider via API:
1. Save provider config to database
2. Call provider_manager.update_provider_config()
3. If provider doesn't exist → create new instance
4. Call router.clear_cache()
```

### 5. Provider Creation Factory

```python
def create_provider_instance(
    provider_type: str,
    api_key: str,
    api_base: str | None,
) -> LLMProvider:
    """Factory to create provider instance by type."""
    if provider_type in ("openai", "openai-compatible", "openai-response", ""):
        return OpenAIProvider(api_key=api_key, api_base=api_base)
    elif provider_type == "anthropic":
        return AnthropicProvider(api_key=api_key)
    elif provider_type == "deepseek":
        return DeepSeekProvider(api_key=api_key, api_base=api_base)
    elif provider_type == "azure":
        return AzureProvider(api_key=api_key, api_base=api_base)
    else:
        # Fallback: treat as OpenAI-compatible
        return OpenAIProvider(api_key=api_key, api_base=api_base)
```

## Files to Modify

| File | Changes |
|------|---------|
| `nanobot/providers/provider_manager.py` | Add dynamic registration methods |
| `nanobot/providers/router.py` | No changes needed (already generic) |
| `nanobot/config/loader.py` | Initialize dynamic providers on startup |
| `nanobot/web/api.py` | Update create/update provider to trigger instance creation |

## Data Flow

```
Database (config_providers table)
    │
    ▼
ProviderManager.register_provider()
    │
    ├──► OpenAIProvider (dynamic) ──► ModelRouter._providers
    │
    ├──► AnthropicProvider (native) ──► ModelRouter._providers
    │
    ├──► DeepSeekProvider (native) ──► ModelRouter._providers
    │
    └──► AzureProvider (native) ──► ModelRouter._providers
            │
            ▼
    router.get("smart") / router.get("deepseek-chat")
            │
            ▼
    _resolve_model() → _get_provider_instance() → ModelHandle
```

## Error Handling

| Error Case | Behavior |
|------------|----------|
| Provider not registered | Return `None`, log debug message |
| Provider disabled | Skip during resolution |
| No API key | Skip during resolution |
| Invalid provider type | Fallback to OpenAIProvider |

## Testing Considerations

1. Verify all system providers can be resolved
2. Verify hot-update creates/replaces instances correctly
3. Verify profile resolution works with any provider
4. Verify cache invalidation on provider update
