"""Model discovery service for automatically fetching available models from providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from loguru import logger

if TYPE_CHECKING:
    from nanobot.storage.config_repository import ConfigRepository


@dataclass
class DiscoveredModel:
    """Discovered model information."""

    id: str                 # System ID, e.g., "claude-opus-4-6"
    name: str               # Display name
    litellm_id: str         # LiteLLM format, e.g., "anthropic/claude-opus-4-6"
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
    """Anthropic model discovery."""

    # Anthropic 固定模型列表（API 不支持列表接口）
    MODELS = [
        {
            "id": "claude-opus-4-6",
            "name": "Claude Opus 4.6",
            "litellm_id": "anthropic/claude-opus-4-6",
            "aliases": ["opus", "smart", "4-6"],
            "capabilities": ["tools", "vision", "thinking"],
            "context_window": 200000,
        },
        {
            "id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "litellm_id": "anthropic/claude-sonnet-4-6",
            "aliases": ["sonnet", "balanced"],
            "capabilities": ["tools", "vision", "thinking"],
            "context_window": 200000,
        },
        {
            "id": "claude-haiku-4-5",
            "name": "Claude Haiku 4.5",
            "litellm_id": "anthropic/claude-haiku-4-5",
            "aliases": ["haiku", "fast"],
            "capabilities": ["tools", "vision"],
            "context_window": 200000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        # Anthropic 没有模型列表 API，返回固定列表
        return [DiscoveredModel(**m) for m in self.MODELS]


class OpenAIDiscovery(ProviderDiscovery):
    """OpenAI model discovery."""

    # OpenAI 主要模型（静态列表，API 返回的包含大量旧模型）
    MODELS = [
        {
            "id": "gpt-4o",
            "name": "GPT-4o",
            "litellm_id": "openai/gpt-4o",
            "aliases": ["4o"],
            "capabilities": ["tools", "vision"],
            "context_window": 128000,
        },
        {
            "id": "gpt-4o-mini",
            "name": "GPT-4o Mini",
            "litellm_id": "openai/gpt-4o-mini",
            "aliases": ["4o-mini", "mini"],
            "capabilities": ["tools", "vision"],
            "context_window": 128000,
        },
        {
            "id": "o3-mini",
            "name": "o3 Mini",
            "litellm_id": "openai/o3-mini",
            "aliases": ["o3"],
            "capabilities": ["tools", "reasoning"],
            "context_window": 200000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        return [DiscoveredModel(**m) for m in self.MODELS]


class DeepSeekDiscovery(ProviderDiscovery):
    """DeepSeek model discovery."""

    MODELS = [
        {
            "id": "deepseek-chat",
            "name": "DeepSeek Chat",
            "litellm_id": "deepseek/deepseek-chat",
            "aliases": ["deepseek"],
            "capabilities": ["tools"],
            "context_window": 64000,
        },
        {
            "id": "deepseek-reasoner",
            "name": "DeepSeek Reasoner",
            "litellm_id": "deepseek/deepseek-reasoner",
            "aliases": ["deepseek-r", "reasoner"],
            "capabilities": ["tools", "thinking"],
            "context_window": 64000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        return [DiscoveredModel(**m) for m in self.MODELS]


class OpenRouterDiscovery(ProviderDiscovery):
    """OpenRouter model discovery with filtering."""

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        base = api_base or "https://openrouter.ai/api/v1"
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{base}/models", headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()

            models = []
            for m in data.get("data", []):
                model_id = m.get("id", "")
                # 跳过旧模型和 embedding 模型
                if not self._is_usable(model_id):
                    continue

                # 提取提供商前缀
                parts = model_id.split("/")
                if len(parts) == 2:
                    provider, name = parts
                    litellm_id = f"openrouter/{model_id}"
                else:
                    continue

                models.append(
                    DiscoveredModel(
                        id=model_id.replace("/", "-"),
                        name=m.get("name", model_id),
                        litellm_id=litellm_id,
                        aliases=[],
                        capabilities=self._infer_capabilities(model_id),
                        context_window=m.get("context_length", 128000),
                    )
                )

            # 按 popularity 排序并限制数量
            models = sorted(models, key=lambda x: x.id)[:30]
            return models

        except Exception as e:
            logger.warning(f"OpenRouter discovery failed: {e}")
            return []

    def _is_usable(self, model_id: str) -> bool:
        """Filter out old/embedding models."""
        skip_keywords = [
            "embed", "embedding", "davinci", "curie", "babbage", "ada",
            "-0301", "-0314", "-0613", "32k", "16k"
        ]
        model_lower = model_id.lower()
        return not any(kw in model_lower for kw in skip_keywords)

    def _infer_capabilities(self, model_id: str) -> list[str]:
        """Infer capabilities from model ID."""
        caps = ["chat"]
        model_lower = model_id.lower()

        # Vision
        if any(x in model_lower for x in ["vision", "claude-3", "gpt-4o"]):
            caps.append("vision")

        # Tools
        if any(x in model_lower for x in ["claude", "gpt-4", "gpt-3.5"]):
            caps.append("tools")

        return caps


class OllamaDiscovery(ProviderDiscovery):
    """Ollama local model discovery."""

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        base = api_base or "http://localhost:11434"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{base}/api/tags", timeout=10)
                resp.raise_for_status()
                data = resp.json()

            models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                if not name:
                    continue

                models.append(
                    DiscoveredModel(
                        id=name.replace(":", "-"),
                        name=name,
                        litellm_id=f"ollama/{name}",
                        aliases=[],
                        capabilities=["chat"],
                        context_window=32768,  # Ollama 默认
                    )
                )

            return models

        except Exception as e:
            logger.warning(f"Ollama discovery failed (is Ollama running?): {e}")
            return []


class MinimaxDiscovery(ProviderDiscovery):
    """Minimax model discovery."""

    MODELS = [
        {
            "id": "minimax-m1",
            "name": "MiniMax-M1",
            "litellm_id": "minimax/MiniMax-M1",
            "aliases": ["m1"],
            "capabilities": ["tools", "thinking"],
            "context_window": 1000000,
        },
        {
            "id": "minimax-text-01",
            "name": "MiniMax-Text-01",
            "litellm_id": "minimax/MiniMax-Text-01",
            "aliases": ["text-01"],
            "capabilities": ["tools", "thinking"],
            "context_window": 1000000,
        },
        {
            "id": "minimax-m2.5",
            "name": "MiniMax-M2.5",
            "litellm_id": "minimax/MiniMax-M2.5",
            "aliases": ["m2.5"],
            "capabilities": ["tools", "vision"],
            "context_window": 1000000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        # Minimax 使用静态列表
        return [DiscoveredModel(**m) for m in self.MODELS]


class ZhipuDiscovery(ProviderDiscovery):
    """Zhipu (智谱) GLM model discovery."""

    MODELS = [
        {
            "id": "glm-4-plus",
            "name": "GLM-4-Plus",
            "litellm_id": "zhipu/glm-4-plus",
            "aliases": ["glm4", "plus"],
            "capabilities": ["tools", "vision"],
            "context_window": 128000,
        },
        {
            "id": "glm-4-air",
            "name": "GLM-4-Air",
            "litellm_id": "zhipu/glm-4-air",
            "aliases": ["glm4-air", "air"],
            "capabilities": ["tools"],
            "context_window": 128000,
        },
        {
            "id": "glm-4-flash",
            "name": "GLM-4-Flash",
            "litellm_id": "zhipu/glm-4-flash",
            "aliases": ["glm4-flash", "flash"],
            "capabilities": ["tools"],
            "context_window": 128000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        return [DiscoveredModel(**m) for m in self.MODELS]


class DashScopeDiscovery(ProviderDiscovery):
    """Aliyun DashScope (通义) model discovery."""

    MODELS = [
        {
            "id": "qwen-max",
            "name": "Qwen-Max",
            "litellm_id": "dashscope/qwen-max",
            "aliases": ["qwen-max"],
            "capabilities": ["tools", "vision", "thinking"],
            "context_window": 32000,
        },
        {
            "id": "qwen-plus",
            "name": "Qwen-Plus",
            "litellm_id": "dashscope/qwen-plus",
            "aliases": ["qwen-plus"],
            "capabilities": ["tools", "vision"],
            "context_window": 32000,
        },
        {
            "id": "qwen-turbo",
            "name": "Qwen-Turbo",
            "litellm_id": "dashscope/qwen-turbo",
            "aliases": ["qwen-turbo"],
            "capabilities": ["tools"],
            "context_window": 32000,
        },
        {
            "id": "qwen-coder-plus",
            "name": "Qwen-Coder-Plus",
            "litellm_id": "dashscope/qwen-coder-plus",
            "aliases": ["qwen-coder"],
            "capabilities": ["tools"],
            "context_window": 32000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        return [DiscoveredModel(**m) for m in self.MODELS]


class GroqDiscovery(ProviderDiscovery):
    """Groq model discovery."""

    MODELS = [
        {
            "id": "llama-3.3-70b",
            "name": "Llama 3.3 70B",
            "litellm_id": "groq/llama-3.3-70b-versatile",
            "aliases": ["llama-70b"],
            "capabilities": ["tools"],
            "context_window": 128000,
        },
        {
            "id": "mixtral-8x7b",
            "name": "Mixtral 8x7B",
            "litellm_id": "groq/mixtral-8x7b-32768",
            "aliases": ["mixtral"],
            "capabilities": ["tools"],
            "context_window": 32768,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        return [DiscoveredModel(**m) for m in self.MODELS]


class GeminiDiscovery(ProviderDiscovery):
    """Google Gemini model discovery."""

    MODELS = [
        {
            "id": "gemini-2.5-pro",
            "name": "Gemini 2.5 Pro",
            "litellm_id": "gemini/gemini-2.5-pro-exp-03-25",
            "aliases": ["gemini-pro", "pro"],
            "capabilities": ["tools", "vision", "thinking"],
            "context_window": 1000000,
        },
        {
            "id": "gemini-2.0-flash",
            "name": "Gemini 2.0 Flash",
            "litellm_id": "gemini/gemini-2.0-flash",
            "aliases": ["gemini-flash", "flash"],
            "capabilities": ["tools", "vision"],
            "context_window": 1000000,
        },
    ]

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        return [DiscoveredModel(**m) for m in self.MODELS]


class ModelDiscoveryService:
    """Service for discovering models from configured providers."""

    DISCOVERY_MAP: dict[str, type[ProviderDiscovery]] = {
        "anthropic": AnthropicDiscovery,
        "openai": OpenAIDiscovery,
        "deepseek": DeepSeekDiscovery,
        "openrouter": OpenRouterDiscovery,
        "ollama": OllamaDiscovery,
        "gemini": GeminiDiscovery,
        "minimax": MinimaxDiscovery,
        "zhipu": ZhipuDiscovery,
        "dashscope": DashScopeDiscovery,
        "groq": GroqDiscovery,
    }

    def __init__(self, repo: "ConfigRepository"):
        self.repo = repo

    async def discover_for_provider(self, provider_id: str) -> list[DiscoveredModel]:
        """Discover models for a specific provider."""
        provider = self.repo.get_provider(provider_id)
        if not provider:
            logger.warning(f"Provider {provider_id} not found")
            return []

        provider_type = provider["id"]  # provider ID is the type
        discovery_class = self.DISCOVERY_MAP.get(provider_type)

        if not discovery_class:
            logger.warning(f"No discovery strategy for provider type: {provider_type}")
            return []

        discovery = discovery_class()
        return await discovery.discover(
            api_key=provider["api_key"],
            api_base=provider.get("api_base"),
        )

    async def discover_and_save(self, provider_id: str) -> list[DiscoveredModel]:
        """Discover models and save to database."""
        models = await self.discover_for_provider(provider_id)

        for i, model in enumerate(models):
            # 设置第一个为默认模型
            is_default = (i == 0)

            # 推断 cost_rank 和 quality_rank
            cost_rank = self._infer_cost_rank(model.litellm_id)
            quality_rank = self._infer_quality_rank(model.litellm_id)

            self.repo.set_model(
                model_id=model.id,
                provider_id=provider_id,
                name=model.name,
                litellm_id=model.litellm_id,
                aliases=",".join(model.aliases),
                capabilities=",".join(model.capabilities),
                context_window=model.context_window,
                cost_rank=cost_rank,
                quality_rank=quality_rank,
                enabled=True,
                is_default=is_default,
            )

        logger.info(f"Discovered and saved {len(models)} models for {provider_id}")
        return models

    def _infer_cost_rank(self, litellm_id: str) -> int:
        """Infer cost rank from model ID (1=cheap, 10=expensive)."""
        model_lower = litellm_id.lower()

        if any(x in model_lower for x in ["haiku", "mini", "flash"]):
            return 2
        if any(x in model_lower for x in ["sonnet", "gpt-4o", "4o"]):
            return 5
        if any(x in model_lower for x in ["opus", "o1", "o3", "pro"]):
            return 8

        return 5

    def _infer_quality_rank(self, litellm_id: str) -> int:
        """Infer quality rank from model ID (1=best, 10=worst)."""
        model_lower = litellm_id.lower()

        if any(x in model_lower for x in ["opus", "o1", "o3", "pro"]):
            return 1
        if any(x in model_lower for x in ["sonnet", "gpt-4o", "4o"]):
            return 3
        if any(x in model_lower for x in ["haiku", "mini", "flash"]):
            return 6

        return 5
