"""Model Router: Unified model resolution and provider routing with native SDK instances."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.providers.native_model_id import normalize_native_model_id, resolve_stored_model_id

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.storage.config_repository import ConfigRepository


@dataclass(frozen=True)
class ModelHandle:
    """Model call handle containing the provider instance and native model ID."""

    model: str              # Native model ID, e.g. "claude-opus-4-6" (not "anthropic/claude-opus-4-6")
    api_key: str
    api_base: str | None
    provider: "LLMProvider"  # The provider instance to call
    provider_id: str        # "openai" | "anthropic" | "deepseek" | "azure"
    capabilities: set[str]  # {"tools", "vision", "thinking", ...}
    context_window: int

    def has_capability(self, capability: str) -> bool:
        """Check if model has specific capability."""
        return capability in self.capabilities


class ModelRouter:
    """
    Unified model router - the ONLY entry point for model resolution.

    Usage:
        router = ModelRouter(repo)
        router.register_providers(
            openai=OpenAIProvider(...),
            anthropic=AnthropicProvider(...),
            deepseek=DeepSeekProvider(...),
            azure=AzureProvider(...),
        )

        # By profile (recommended)
        handle = router.get("smart")      # Deep thinking
        handle = router.get("fast")       # Quick response
        handle = router.get("coding")     # Code assistant

        # By model ID or alias (fallback)
        handle = router.get("claude-opus-4-6")
        handle = router.get("gpt-4o")
    """

    def __init__(self, repo: "ConfigRepository"):
        self.repo = repo
        self._cache: dict[str, ModelHandle] = {}
        self._providers: dict[str, "LLMProvider"] = {}

    def register_providers(
        self,
        openai: "LLMProvider | None" = None,
        anthropic: "LLMProvider | None" = None,
        deepseek: "LLMProvider | None" = None,
        azure: "LLMProvider | None" = None,
    ) -> None:
        """Register native provider instances for routing."""
        if openai:
            self._providers["openai"] = openai
        if anthropic:
            self._providers["anthropic"] = anthropic
        if deepseek:
            self._providers["deepseek"] = deepseek
        if azure:
            self._providers["azure"] = azure
        self.clear_cache()
        logger.debug("ModelRouter: providers registered")

    def register_all(
        self,
        openai: "LLMProvider | None" = None,
        anthropic: "LLMProvider | None" = None,
        deepseek: "LLMProvider | None" = None,
        azure: "LLMProvider | None" = None,
    ) -> None:
        """Register all native provider instances. Convenience alias for register_providers."""
        self.register_providers(openai=openai, anthropic=anthropic, deepseek=deepseek, azure=azure)

    def update_from_config(self, config: Any) -> None:
        """
        Update provider credentials from Config object (hot-update).

        Replaces litellm's _register_all_provider_keys.
        With native providers, credentials are already set on instances at startup.
        This method just updates them from the config object.
        """
        for provider_id in ("anthropic", "openai", "deepseek"):
            pc = getattr(config.providers, provider_id, None)
            if pc and getattr(pc, "api_key", None):
                provider = self._providers.get(provider_id)
                if provider:
                    provider.api_key = pc.api_key
                    if getattr(pc, "api_base", None):
                        provider.api_base = pc.api_base
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
        self.clear_cache()
        logger.info("ModelRouter: providers updated from config")

    def _get_provider_instance(self, provider_id: str) -> "LLMProvider | None":
        """Get the registered provider instance for a given provider_id."""
        return self._providers.get(provider_id)

    def get(self, profile_or_model: str) -> ModelHandle:
        """
        Resolve profile or model reference to ModelHandle.

        Resolution order:
        1. Profile ID -> resolve via model_chain
        2. Model ID -> direct lookup
        3. Alias -> lookup via aliases field

        Args:
            profile_or_model: Profile ID (smart/fast/coding) or model ID/alias

        Returns:
            ModelHandle with provider instance and native model ID

        Raises:
            ModelNotFoundError: If no profile or model found
            NoModelAvailableError: If matched but no enabled provider
        """
        # Check cache first
        if profile_or_model in self._cache:
            return self._cache[profile_or_model]

        # 1. Try as profile
        if profile := self.repo.get_model_profile(profile_or_model):
            if profile["enabled"]:
                handle = self._resolve_profile(profile)
                if handle:
                    self._cache[profile_or_model] = handle
                    return handle

        # 2. Try as direct model ID
        if model := self.repo.get_model(profile_or_model):
            handle = self._resolve_model(model)
            if handle:
                self._cache[profile_or_model] = handle
                return handle

        # 3. Try as alias
        if model := self.repo.get_model_by_alias(profile_or_model):
            handle = self._resolve_model(model)
            if handle:
                self._cache[profile_or_model] = handle
                return handle

        raise ModelNotFoundError(f"No profile or model found for: {profile_or_model}")

    def _resolve_profile(self, profile: dict) -> ModelHandle | None:
        """
        Resolve profile via model_chain with sequential fallback.

        model_chain format: "model1,model2,model3"
        Tries each model in order, returns first available.
        """
        model_chain = profile.get("model_chain", "")
        if not model_chain:
            logger.warning(f"Profile {profile['id']} has empty model_chain")
            return None

        model_ids = [m.strip() for m in model_chain.split(",")]

        for model_id in model_ids:
            if not model_id:
                continue

            model = self.repo.get_model(model_id)
            if not model:
                logger.debug(f"Model {model_id} not found in profile {profile['id']}")
                continue

            handle = self._resolve_model(model)
            if handle:
                logger.debug(f"Profile {profile['id']} resolved to {model_id}")
                return handle

        logger.warning(f"Profile {profile['id']} has no available models in chain: {model_chain}")
        return None

    def _resolve_model(self, model: dict) -> ModelHandle | None:
        """Resolve model dict to ModelHandle with native provider instance."""
        if not model.get("enabled"):
            return None

        provider_id = model["provider_id"]
        provider_instance = self._get_provider_instance(provider_id)
        if not provider_instance:
            logger.debug(f"No provider instance registered for {provider_id}")
            return None

        provider_cfg = self.repo.get_provider(provider_id)
        if not provider_cfg:
            logger.debug(f"Provider {provider_id} config not found")
            return None

        if not provider_cfg.get("enabled"):
            logger.debug(f"Provider {provider_id} is disabled")
            return None

        if not provider_cfg.get("api_key"):
            logger.debug(f"Provider {provider_id} has no API key")
            return None

        raw_model_id = resolve_stored_model_id(model)
        native_model_id = normalize_native_model_id(
            raw_model_id,
            api_base=provider_cfg.get("api_base"),
        )
        if not native_model_id:
            logger.debug(f"Model {model.get('id')} has empty resolved model id")
            return None

        return ModelHandle(
            model=native_model_id,
            api_key=provider_cfg["api_key"],
            api_base=provider_cfg.get("api_base"),
            provider=provider_instance,
            provider_id=provider_id,
            capabilities=set(model.get("capabilities", "").split(",")) if model.get("capabilities") else set(),
            context_window=model.get("context_window", 128000),
        )

    def clear_cache(self) -> None:
        """Clear resolution cache. Call when config changes."""
        self._cache.clear()
        logger.debug("ModelRouter cache cleared")

    def update_provider_instance(
        self,
        provider_id: str,
        api_key: str | None = None,
        api_base: str | None = None,
        provider_type: str = "openai",
    ) -> None:
        """
        Update or register a provider instance in the router (for dynamic providers from DB).
        After this, _get_provider_instance will return the updated instance.
        """
        from nanobot.providers.provider_manager import create_provider_instance

        existing = self._providers.get(provider_id)
        if existing:
            if api_key is not None:
                existing.api_key = api_key
            if api_base is not None:
                existing.api_base = api_base
            logger.debug(f"ModelRouter: updated instance for {provider_id}")
        else:
            instance = create_provider_instance(
                provider_type=provider_type,
                api_key=api_key,
                api_base=api_base,
            )
            self._providers[provider_id] = instance
            logger.debug(f"ModelRouter: registered new instance for {provider_id}")
        self.clear_cache()

class ModelNotFoundError(Exception):
    """Raised when no profile or model matches the reference."""
    pass


class NoModelAvailableError(Exception):
    """Raised when profile matches but no models are available."""
    pass
