"""Model discovery service for native SDK providers (static model lists)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.storage.config_repository import ConfigRepository


@dataclass
class DiscoveredModel:
    """Discovered model information."""

    id: str                 # Native model ID, e.g., "claude-opus-4-6"
    name: str               # Display name
    litellm_id: str         # Kept for DB compatibility (same as id)
    aliases: list[str]      # Short aliases
    capabilities: list[str]  # ["tools", "vision", "thinking"]
    context_window: int


class ProviderDiscovery(ABC):
    """Base class for provider model discovery."""

    @abstractmethod
    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        """Discover models from provider."""
        pass


class AnthropicDiscovery(ProviderDiscovery):
    """Anthropic model discovery (static list)."""

    MODELS = [
        {
            "id": "claude-opus-4-6",
            "name": "Claude Opus 4.6",
            "aliases": ["opus", "smart", "4-6"],
            "capabilities": ["tools", "vision", "thinking"],
            "context_window": 200000,
        },
        {
            "id": "claude-sonnet-4-7",
            "name": "Claude Sonnet 4.7",
            "aliases": ["sonnet", "balanced"],
            "capabilities": ["tools", "vision", "thinking"],
            "context_window": 200000,
        },
        {
            "id": "claude-haiku-4-7",
            "name": "Claude Haiku 4.7",
            "aliases": ["haiku", "fast"],
            "capabilities": ["tools", "vision"],
            "context_window": 200000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        return [
            DiscoveredModel(
                id=m["id"],
                name=m["name"],
                litellm_id=m["id"],  # Native ID for DB compatibility
                aliases=m["aliases"],
                capabilities=m["capabilities"],
                context_window=m["context_window"],
            )
            for m in self.MODELS
        ]


class OpenAIDiscovery(ProviderDiscovery):
    """OpenAI model discovery (static list)."""

    MODELS = [
        {
            "id": "gpt-4o",
            "name": "GPT-4o",
            "aliases": ["4o"],
            "capabilities": ["tools", "vision"],
            "context_window": 128000,
        },
        {
            "id": "gpt-4o-mini",
            "name": "GPT-4o Mini",
            "aliases": ["4o-mini", "mini"],
            "capabilities": ["tools", "vision"],
            "context_window": 128000,
        },
        {
            "id": "gpt-4-turbo",
            "name": "GPT-4 Turbo",
            "aliases": ["4-turbo", "turbo"],
            "capabilities": ["tools", "vision"],
            "context_window": 128000,
        },
        {
            "id": "o3-mini",
            "name": "o3 Mini",
            "aliases": ["o3"],
            "capabilities": ["tools", "reasoning"],
            "context_window": 200000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        return [
            DiscoveredModel(
                id=m["id"],
                name=m["name"],
                litellm_id=m["id"],
                aliases=m["aliases"],
                capabilities=m["capabilities"],
                context_window=m["context_window"],
            )
            for m in self.MODELS
        ]


class DeepSeekDiscovery(ProviderDiscovery):
    """DeepSeek model discovery (static list)."""

    MODELS = [
        {
            "id": "deepseek-chat",
            "name": "DeepSeek Chat",
            "aliases": ["deepseek"],
            "capabilities": ["tools"],
            "context_window": 64000,
        },
        {
            "id": "deepseek-reasoner",
            "name": "DeepSeek Reasoner",
            "aliases": ["deepseek-r", "reasoner"],
            "capabilities": ["tools", "thinking"],
            "context_window": 64000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        return [
            DiscoveredModel(
                id=m["id"],
                name=m["name"],
                litellm_id=m["id"],
                aliases=m["aliases"],
                capabilities=m["capabilities"],
                context_window=m["context_window"],
            )
            for m in self.MODELS
        ]


class AzureDiscovery(ProviderDiscovery):
    """Azure OpenAI model discovery (user-defined deployments)."""

    MODELS = [
        {
            "id": "azure-gpt-4o",
            "name": "Azure GPT-4o (请在 Azure Portal 配置部署名)",
            "aliases": ["4o"],
            "capabilities": ["tools", "vision"],
            "context_window": 128000,
        },
        {
            "id": "azure-gpt-4o-mini",
            "name": "Azure GPT-4o Mini (请在 Azure Portal 配置部署名)",
            "aliases": ["4o-mini"],
            "capabilities": ["tools", "vision"],
            "context_window": 128000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        return [
            DiscoveredModel(
                id=m["id"],
                name=m["name"],
                litellm_id=m["id"],
                aliases=m["aliases"],
                capabilities=m["capabilities"],
                context_window=m["context_window"],
            )
            for m in self.MODELS
        ]


class ModelDiscoveryService:
    """Service for discovering models from configured providers (native SDK)."""

    DISCOVERY_MAP: dict[str, type[ProviderDiscovery]] = {
        "anthropic": AnthropicDiscovery,
        "openai": OpenAIDiscovery,
        "deepseek": DeepSeekDiscovery,
        "azure": AzureDiscovery,
    }

    def __init__(self, repo: "ConfigRepository"):
        self.repo = repo

    async def discover_for_provider(self, provider_id: str) -> list[DiscoveredModel]:
        """Discover models for a specific provider."""
        provider = self.repo.get_provider(provider_id)
        if not provider:
            logger.warning(f"Provider {provider_id} not found")
            return []

        discovery_class = self.DISCOVERY_MAP.get(provider_id)
        if not discovery_class:
            logger.warning(f"No discovery strategy for provider: {provider_id}")
            return []

        discovery = discovery_class()
        return await discovery.discover(
            api_key=provider.get("api_key", ""),
            api_base=provider.get("api_base"),
        )

    async def discover_and_save(self, provider_id: str) -> list[DiscoveredModel]:
        """Discover models and save to database."""
        models = await self.discover_for_provider(provider_id)

        for i, model in enumerate(models):
            is_default = (i == 0)
            if is_default:
                self.repo.clear_default_for_all_models_except(model.id)

            self.repo.set_model(
                model_id=model.id,
                provider_id=provider_id,
                name=model.name,
                litellm_id=model.litellm_id,
                aliases=",".join(model.aliases),
                capabilities=",".join(model.capabilities),
                context_window=model.context_window,
                cost_rank=self._infer_cost_rank(model.id),
                quality_rank=self._infer_quality_rank(model.id),
                enabled=True,
                is_default=is_default,
            )

        logger.info(f"Discovered and saved {len(models)} models for {provider_id}")
        return models

    def _infer_cost_rank(self, model_id: str) -> int:
        """Infer cost rank from model ID (1=cheap, 10=expensive)."""
        model_lower = model_id.lower()
        if any(x in model_lower for x in ["haiku", "mini", "flash"]):
            return 2
        if any(x in model_lower for x in ["sonnet", "gpt-4o", "4o"]):
            return 5
        if any(x in model_lower for x in ["opus", "o1", "o3", "pro"]):
            return 8
        return 5

    def _infer_quality_rank(self, model_id: str) -> int:
        """Infer quality rank from model ID (1=best, 10=worst)."""
        model_lower = model_id.lower()
        if any(x in model_lower for x in ["opus", "o1", "o3", "pro"]):
            return 1
        if any(x in model_lower for x in ["sonnet", "gpt-4o", "4o"]):
            return 3
        if any(x in model_lower for x in ["haiku", "mini", "flash"]):
            return 6
        return 5
