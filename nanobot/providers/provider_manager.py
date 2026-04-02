"""ProviderManager: manages native SDK provider instances with hot-update support.

Replaces litellm's global env var injection and per-call credential override
patterns with clean provider instance management.

Usage:
    pm = ProviderManager(config_repo)
    pm.register_with_router(router)
    # Later, hot-update a provider's credentials
    pm.update_provider_config("anthropic", api_key="sk-...", api_base=None)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.providers.router import ModelRouter


class ProviderManager:
    """
    Manages all native LLM provider instances.

    Holds references to all 4 provider instances (openai, anthropic, deepseek, azure).
    Provides hot-update methods to replace litellm's ensure_api_key_for_model / update_config.

    After native providers replace litellm, each provider instance holds its own api_key.
    Hot-update = update the provider instance's api_key / api_base directly.
    """

    def __init__(self) -> None:
        self._providers: dict[str, "LLMProvider"] = {}

    # -------------------------------------------------------------------------
    # Provider registration (called once during startup)
    # -------------------------------------------------------------------------

    def register(self, provider_id: str, provider: "LLMProvider") -> None:
        """Register a provider instance by ID."""
        self._providers[provider_id] = provider
        logger.debug(f"ProviderManager: registered {provider_id}")

    def register_all(
        self,
        openai: "LLMProvider | None" = None,
        anthropic: "LLMProvider | None" = None,
        deepseek: "LLMProvider | None" = None,
        azure: "LLMProvider | None" = None,
    ) -> None:
        """Register all provider instances at once."""
        if openai:
            self._providers["openai"] = openai
        if anthropic:
            self._providers["anthropic"] = anthropic
        if deepseek:
            self._providers["deepseek"] = deepseek
        if azure:
            self._providers["azure"] = azure

    def register_with_router(self, router: "ModelRouter") -> None:
        """Register all held provider instances with the ModelRouter."""
        router.register_providers(
            openai=self._providers.get("openai"),
            anthropic=self._providers.get("anthropic"),
            deepseek=self._providers.get("deepseek"),
            azure=self._providers.get("azure"),
        )
        logger.info("ProviderManager: all providers registered with router")

    # -------------------------------------------------------------------------
    # Hot-update methods (replace litellm's ensure_api_key_for_model / update_config)
    # -------------------------------------------------------------------------

    def update_provider_config(
        self,
        provider_id: str,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """
        Hot-update a provider's credentials.

        Replaces litellm's:
            provider.ensure_api_key_for_model(f"{provider_id}/placeholder", api_key, api_base)

        Called when user creates/updates a provider in the Config page.
        """
        provider = self._providers.get(provider_id)
        if provider is None:
            logger.warning(f"ProviderManager: no provider registered for {provider_id}")
            return

        if api_key is not None:
            provider.api_key = api_key
        if api_base is not None:
            provider.api_base = api_base

        logger.info(f"ProviderManager: updated {provider_id} config (api_key={'set' if api_key else 'unchanged'}, api_base={'set' if api_base else 'unchanged'})")

    def update_model_config(
        self,
        model_name: str,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """
        Hot-update the credentials for the provider that serves a specific model.

        Replaces litellm's:
            provider.update_config(model_name, api_key, api_base)

        Uses model name prefix to find the right provider, then updates it.

        Model name format: "claude-opus-4-6" (anthropic), "gpt-4o" (openai),
                          "deepseek-chat" (deepseek), etc.
        """
        provider = self._get_provider_for_model(model_name)
        if provider is None:
            logger.warning(f"ProviderManager: no provider for model {model_name}")
            return

        if api_key is not None:
            provider.api_key = api_key
        if api_base is not None:
            provider.api_base = api_base

        logger.debug(f"ProviderManager: updated provider for model {model_name}")

    def update_from_config(self, config: Any) -> None:
        """
        Update all provider credentials from Config object.

        Replaces litellm's _register_all_provider_keys.
        With native providers, each provider holds its own api_key — just update
        the instances directly.
        """
        for provider_id in ("anthropic", "openai", "deepseek"):
            pc = getattr(config.providers, provider_id, None)
            if pc and getattr(pc, "api_key", None):
                self.update_provider_config(
                    provider_id,
                    api_key=pc.api_key,
                    api_base=getattr(pc, "api_base", None) or None,
                )
        # Azure has additional fields
        azure_pc = getattr(config.providers, "azure", None)
        if azure_pc and getattr(azure_pc, "api_key", None):
            azure_provider = self._providers.get("azure")
            if azure_provider:
                azure_provider.api_key = azure_pc.api_key
                if azure_pc.api_base:
                    azure_provider.api_base = azure_pc.api_base
                if getattr(azure_pc, "api_version", None):
                    azure_provider.api_version = azure_pc.api_version
                if getattr(azure_pc, "azure_deployment", None):
                    azure_provider.azure_deployment = azure_pc.azure_deployment
                logger.info(f"ProviderManager: updated azure config (api_key={'set' if azure_pc.api_key else 'unchanged'}, api_base={'set' if azure_pc.api_base else 'unchanged'}, api_version={'set' if azure_pc.api_version else 'unchanged'}, azure_deployment={'set' if azure_pc.azure_deployment else 'unchanged'})")

    # -------------------------------------------------------------------------
    # Lookup helpers
    # -------------------------------------------------------------------------

    def get(self, provider_id: str) -> "LLMProvider | None":
        """Get provider instance by ID."""
        return self._providers.get(provider_id)

    def _get_provider_for_model(self, model_name: str) -> "LLMProvider | None":
        """
        Find the provider instance that should serve the given model.

        Matches by model name prefix:
          - "claude*" -> anthropic
          - "gpt*" / "4o*" -> openai
          - "deepseek*" -> deepseek
          - "azure*" -> azure
        """
        if not model_name:
            return None

        model_lower = model_name.lower()

        # Anthropic: claude-* models
        if model_lower.startswith("claude"):
            return self._providers.get("anthropic")

        # DeepSeek: deepseek-* models
        if model_lower.startswith("deepseek"):
            return self._providers.get("deepseek")

        # OpenAI: gpt-* or models with "4o" (gpt-4o, gpt-4o-mini)
        if model_lower.startswith("gpt") or "4o" in model_lower:
            return self._providers.get("openai")

        # Azure: models configured with azure deployment name
        # If the model name was resolved from a provider that uses azure, it would
        # be registered. Try to look up by scanning registered providers.
        for pid, provider in self._providers.items():
            if hasattr(provider, "azure_deployment") and getattr(provider, "azure_deployment", None):
                # Check if this model's deployment matches
                deployment = provider.azure_deployment
                if deployment and (deployment in model_name or model_name in deployment):
                    return provider

        # Fallback: unknown model, return None
        return None
