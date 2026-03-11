"""Model Router: Unified model resolution and provider routing."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.storage.config_repository import ConfigRepository


@dataclass(frozen=True)
class ModelHandle:
    """Model call handle containing all necessary information for LLM invocation."""

    model: str              # LiteLLM model ID, e.g., "anthropic/claude-opus-4-6"
    api_key: str
    api_base: str | None
    provider_id: str        # For error tracking and health checks
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

        # By profile (recommended)
        handle = router.get("smart")      # Deep thinking
        handle = router.get("fast")       # Quick response
        handle = router.get("coding")     # Code assistant

        # By model ID or alias (fallback)
        handle = router.get("claude-opus-4-6")
        handle = router.get("opus")
    """

    def __init__(self, repo: "ConfigRepository"):
        self.repo = repo
        self._cache: dict[str, ModelHandle] = {}

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
            ModelHandle with complete invocation parameters

        Raises:
            ModelNotFoundError: If no matching model found
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
        """Resolve model dict to ModelHandle."""
        if not model.get("enabled"):
            return None

        provider = self.repo.get_provider(model["provider_id"])
        if not provider:
            logger.debug(f"Provider {model['provider_id']} not found for model {model['id']}")
            return None

        if not provider.get("enabled"):
            logger.debug(f"Provider {model['provider_id']} is disabled")
            return None

        if not provider.get("api_key"):
            logger.debug(f"Provider {model['provider_id']} has no API key")
            return None

        return ModelHandle(
            model=model["litellm_id"],
            api_key=provider["api_key"],
            api_base=provider.get("api_base"),
            provider_id=provider["id"],
            capabilities=set(model.get("capabilities", "").split(",")),
            context_window=model.get("context_window", 128000),
        )

    def clear_cache(self) -> None:
        """Clear resolution cache. Call when config changes."""
        self._cache.clear()
        logger.debug("ModelRouter cache cleared")


class ModelNotFoundError(Exception):
    """Raised when no profile or model matches the reference."""
    pass


class NoModelAvailableError(Exception):
    """Raised when profile matches but no models are available."""
    pass
