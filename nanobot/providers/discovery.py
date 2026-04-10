"""Model discovery service for native SDK providers (static model lists)."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.providers.openai_compat_probe import (
    MINIMAX_FALLBACK_MODEL_ROWS,
    is_minimax_openai_base,
)

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
    # NEW FIELDS:
    model_type: str = "chat"       # "chat" | "completion" | "embedding" | "image" | "audio" | "vision"
    max_tokens: int = 4096
    supports_vision: bool = False
    supports_function_calling: bool = True
    supports_streaming: bool = True


class ProviderDiscovery(ABC):
    """Base class for provider model discovery."""

    @abstractmethod
    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        """Discover models from provider."""
        pass

    def _infer_model_type(self, m: dict) -> str:
        """Infer model type from ID or capabilities."""
        mid = m.get("id", "")
        caps = m.get("capabilities", [])
        if "embedding" in mid.lower() or "embed" in mid.lower():
            return "embedding"
        if "rerank" in mid.lower():
            return "embedding"
        if "tts" in mid.lower() or "speech" in mid.lower():
            return "audio"
        if "dall" in mid.lower() or ("image" in mid.lower() and "generation" in str(caps).lower()):
            return "image"
        if "vision" in mid.lower() or "vl-" in mid.lower() or bool(re.search(r"(^|[/-])gpt-4[ov]", mid.lower())):
            return "vision"
        return "chat"

    def _infer_supports_vision(self, m: dict) -> bool:
        """Check if model supports vision."""
        caps = m.get("capabilities", [])
        if isinstance(caps, list) and "vision" in caps:
            return True
        mid = m.get("id", "")
        vision_ids = ["vision", "vl-"]
        if any(v in mid.lower() for v in vision_ids):
            return True
        return bool(re.search(r"(^|[/-])(gpt-4[ov]|claude-3|claude-3\.5|claude-3\.7|claude-opus-4|claude-sonnet-4)", mid.lower()))

    def _infer_supports_function_calling(self, m: dict) -> bool:
        """Check if model supports function calling."""
        mid = m.get("id", "")
        no_function_calling = ["reasoner", "-r1", "qwq", "o1-", "o2-", "o3", "o4", "sonar-reasoning", "deepseek-reasoner", "qwq-32"]
        if any(v in mid.lower() for v in no_function_calling):
            return False
        caps = m.get("capabilities", [])
        if isinstance(caps, list) and "tools" in caps:
            return True
        return True  # default: capable


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
                litellm_id=m["id"],
                aliases=m["aliases"],
                capabilities=m["capabilities"],
                context_window=m["context_window"],
                model_type=self._infer_model_type(m),
                max_tokens=m.get("max_tokens", 4096),
                supports_vision=self._infer_supports_vision(m),
                supports_function_calling=self._infer_supports_function_calling(m),
                supports_streaming=True,
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
                model_type=self._infer_model_type(m),
                max_tokens=m.get("max_tokens", 4096),
                supports_vision=self._infer_supports_vision(m),
                supports_function_calling=self._infer_supports_function_calling(m),
                supports_streaming=True,
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
                model_type=self._infer_model_type(m),
                max_tokens=m.get("max_tokens", 4096),
                supports_vision=self._infer_supports_vision(m),
                supports_function_calling=self._infer_supports_function_calling(m),
                supports_streaming=True,
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
                model_type=self._infer_model_type(m),
                max_tokens=m.get("max_tokens", 4096),
                supports_vision=self._infer_supports_vision(m),
                supports_function_calling=self._infer_supports_function_calling(m),
                supports_streaming=True,
            )
            for m in self.MODELS
        ]


class DynamicOpenAICompatibleDiscovery(ProviderDiscovery):
    """
    Dynamic discovery for OpenAI-compatible providers.

    Calls the provider's /v1/models endpoint to fetch available models at runtime.
    Works with any OpenAI-compatible API (MiniMax, Silicon, CherryIN, etc.).
    """

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        if not api_key:
            logger.debug("DynamicOpenAICompatibleDiscovery: no API key, returning empty")
            return []
        if not api_base:
            logger.debug("DynamicOpenAICompatibleDiscovery: no api_base, returning empty")
            return []

        import httpx

        url = api_base.rstrip("/") + "/models"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
                if response.status_code == 200:
                    data = response.json()
                    models = data.get("data", []) if isinstance(data, dict) else []
                    return [self._parse_model(m) for m in models if self._is_chat_model(m)]
                if response.status_code == 404 and is_minimax_openai_base(api_base):
                    logger.info(
                        "DynamicOpenAICompatibleDiscovery: %s 无模型列表接口，使用 MiniMax 静态回退列表",
                        url,
                    )
                    return [self._minimax_fallback_model(r) for r in MINIMAX_FALLBACK_MODEL_ROWS]
                logger.warning(
                    f"DynamicOpenAICompatibleDiscovery: {url} returned {response.status_code}: {response.text[:200]}"
                )
                return []
        except Exception as e:
            logger.warning(f"DynamicOpenAICompatibleDiscovery: failed to fetch {url}: {e}")
            return []

    def _is_chat_model(self, m: dict) -> bool:
        """Filter out non-chat models (embeddings, images, etc.)."""
        mid = m.get("id", "")
        object_type = m.get("object", "")
        # Skip non-chat types
        if object_type == "embedding":
            return False
        # Skip known non-chat IDs
        skip_prefixes = ["embedding", "text-embedding", "dall-e", "tts", "whisper", "babbage", "ada"]
        for prefix in skip_prefixes:
            if mid.lower().startswith(prefix):
                return False
        return True

    def _minimax_fallback_model(self, row: dict[str, Any]) -> DiscoveredModel:
        """MiniMax 官方部分环境不提供 GET /models，使用与预置 provider 一致的静态模型行。"""
        mid = row["id"]
        name = row.get("name", mid)
        cw = int(row.get("context_window", 128000))
        return DiscoveredModel(
            id=mid,
            name=name,
            litellm_id=mid,
            aliases=[],
            capabilities=["tools"],
            context_window=cw,
            model_type="chat",
            max_tokens=4096,
            supports_vision=False,
            supports_function_calling=True,
            supports_streaming=True,
        )

    def _parse_model(self, m: dict) -> DiscoveredModel:
        """Parse an OpenAI-compatible /models response entry into DiscoveredModel."""
        mid = m.get("id", "")
        owned_by = m.get("owned_by", "")
        return DiscoveredModel(
            id=mid,
            name=mid,
            litellm_id=mid,
            aliases=[],
            capabilities=["tools"],  # assume capable; real FC info not in /models
            context_window=m.get("context_window", m.get("max_tokens", 128000)),
            model_type=self._infer_model_type(m),
            max_tokens=m.get("max_tokens", 4096),
            supports_vision=self._infer_supports_vision(m),
            supports_function_calling=self._infer_supports_function_calling(m),
            supports_streaming=True,
        )


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

    def _select_discovery_class(self, provider: dict[str, Any]) -> type[ProviderDiscovery]:
        """系统预置 provider 用静态列表；用户自建（含 MiniMax 等 OpenAI 兼容）一律走动态 /models。"""
        if provider.get("is_system"):
            pid = provider.get("id", "")
            cls = self.DISCOVERY_MAP.get(pid)
            if cls:
                return cls
            ptype = (provider.get("provider_type") or "openai").lower()
            cls = self.DISCOVERY_MAP.get(ptype)
            if cls:
                return cls
        return DynamicOpenAICompatibleDiscovery

    async def discover_for_provider(
        self,
        provider_id: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> list[DiscoveredModel]:
        """Discover models for a specific provider. Optional api_key/api_base override DB (表单未保存时)."""
        provider = self.repo.get_provider(provider_id)
        if not provider:
            logger.warning(f"Provider {provider_id} not found")
            return []

        discovery_class = self._select_discovery_class(provider)
        eff_key = provider.get("api_key", "") if api_key is None else api_key
        eff_base = provider.get("api_base") if api_base is None else api_base

        discovery = discovery_class()
        return await discovery.discover(api_key=eff_key, api_base=eff_base)

    async def discover_and_save(
        self,
        provider_id: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> list[DiscoveredModel]:
        """Discover models and save to database."""
        models = await self.discover_for_provider(
            provider_id, api_key=api_key, api_base=api_base
        )

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
                model_type=model.model_type,
                max_tokens=model.max_tokens,
                supports_vision=model.supports_vision,
                supports_function_calling=model.supports_function_calling,
                supports_streaming=model.supports_streaming,
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
