# litellm Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove litellm dependency, replace with native OpenAI/Anthropic/DeepSeek/Azure OpenAI SDKs, preserve Microkernel architecture.

**Architecture:** Each provider is an independent class (no shared runtime polymorphism). ModelRouter holds provider instances and routes by model ID prefix. LLMProvider ABC remains as type hint only.

**Tech Stack:** openai>=1.0, anthropic>=0.20, pydantic>=2.0

---

## File Map

| Role | Path |
|------|------|
| **NEW** | `nanobot/providers/anthropic_provider.py` |
| **NEW** | `nanobot/providers/openai_provider.py` |
| **NEW** | `nanobot/providers/deepseek_provider.py` |
| **NEW** | `nanobot/providers/azure_provider.py` |
| **DELETE** | `nanobot/providers/litellm_provider.py` |
| **MODIFY** | `nanobot/providers/base.py` |
| **MODIFY** | `nanobot/providers/router.py` |
| **MODIFY** | `nanobot/providers/discovery.py` |
| **MODIFY** | `nanobot/agent/loop.py` |
| **MODIFY** | `nanobot/web/api.py` |
| **MODIFY** | `nanobot/config/schema.py` |
| **MODIFY** | `nanobot/storage/config_repository.py` |
| **MODIFY** | `web-ui/src/types.ts` |
| **MODIFY** | `web-ui/src/pages/ConfigPage.tsx` |
| **MODIFY** | `pyproject.toml` |

---

## Task 1: Update dependencies

**Files:** `pyproject.toml`

- [ ] **Step 1: Remove litellm, add openai and anthropic SDKs**

```toml
dependencies = [
    # REPLACE: "litellm>=1.82.6",
    "openai>=1.0.0",
    "anthropic>=0.20.0",
    # ... rest unchanged
]
```

---

## Task 2: Update base provider data classes

**Files:** `nanobot/providers/base.py`

- [ ] **Step 1: Add thinking field to LLMResponse (Anthropic extended thinking support)**

```python
@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    thinking: str | None = None  # NEW: Anthropic extended thinking output

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0
```

> Note: Keep `LLMProvider` ABC as-is (type hint only). The `chat()` signature stays compatible.

---

## Task 3: Create OpenAI Provider

**Files:** `nanobot/providers/openai_provider.py` (CREATE)

- [ ] **Step 1: Write the OpenAI provider**

```python
"""OpenAI provider using native openai>=1.0 SDK."""

from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageToolCall

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class OpenAIProvider(LLMProvider):
    """OpenAI provider using AsyncOpenAI client."""

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        super().__init__(api_key, api_base)
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.api_base)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> LLMResponse:
        client = self._get_client()
        effective_model = model or self.get_default_model()

        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": self._prepare_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if stream:
            return await self._stream(client, kwargs)
        else:
            return await self._non_stream(client, kwargs)

    async def _non_stream(self, client: AsyncOpenAI, kwargs: dict[str, Any]) -> LLMResponse:
        resp = await client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        message = choice.message

        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=self._parse_arguments(tc.function.arguments),
                )
                for tc in message.tool_calls
            ]

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls or [],
            finish_reason=choice.finish_reason or "stop",
            usage={
                "input_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "output_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
        )

    async def _stream(self, client: AsyncOpenAI, kwargs: dict[str, Any]) -> LLMResponse:
        # For streaming: accumulate content + tool calls, return at end
        kwargs["stream"] = True
        content_parts: list[str] = []
        tool_calls_map: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"

        async with client.chat.completions.create(**kwargs) as stream:
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    content_parts.append(delta.content)
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc_delta.id:
                            tool_calls_map[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_map[idx]["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_map[idx]["arguments"] += tc_delta.function.arguments
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

        tool_calls = [
            ToolCallRequest(id=v["id"], name=v["name"], arguments=self._parse_arguments(v["arguments"]))
            for v in tool_calls_map.values()
        ] if tool_calls_map else None

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls or [],
            finish_reason=finish_reason,
        )

    def _prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Prepare messages, converting tool messages to OpenAI format."""
        prepared = []
        for msg in messages:
            m = {"role": msg["role"], "content": msg.get("content", "")}
            if msg.get("tool_call_id"):
                m["tool_call_id"] = msg["tool_call_id"]
                m["role"] = "tool"
            elif msg.get("tool_calls"):
                # Already in tool_call format from previous response
                m["tool_calls"] = msg["tool_calls"]
            prepared.append(m)
        return prepared

    def _parse_arguments(self, arguments: str | dict) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        try:
            import json
            return json.loads(arguments)
        except Exception:
            return {}

    def get_default_model(self) -> str:
        return "gpt-4o"
```

---

## Task 4: Create Anthropic Provider

**Files:** `nanobot/providers/anthropic_provider.py` (CREATE)

- [ ] **Step 1: Write the Anthropic provider**

```python
"""Anthropic provider using native anthropic>=0.20 SDK."""

import json
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class AnthropicProvider(LLMProvider):
    """Anthropic provider using AsyncAnthropic client."""

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        super().__init__(api_key, api_base)
        self._client: AsyncAnthropic | None = None

    def _get_client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(api_key=self.api_key, base_url=self.api_base)
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> LLMResponse:
        client = self._get_client()
        effective_model = model or self.get_default_model()

        # Convert messages to Anthropic format
        anthropic_messages, system = self._prepare_messages(messages)

        # Build request kwargs
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        if stream:
            return await self._stream(client, kwargs)
        else:
            return await self._non_stream(client, kwargs)

    async def _non_stream(self, client: AsyncAnthropic, kwargs: dict[str, Any]) -> LLMResponse:
        resp: Message = await client.messages.create(**kwargs)

        # Extract thinking block (Anthropic extended thinking)
        thinking = None
        content_blocks = resp.content
        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []

        for block in content_blocks:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "thinking":
                thinking = block.thinking
            elif block.type == "tool_use":
                tool_calls.append(ToolCallRequest(
                    id=block.id,
                    name=block.name,
                    arguments=self._parse_arguments(block.input),
                ))

        return LLMResponse(
            content="\n".join(text_parts) or None,
            tool_calls=tool_calls or [],
            finish_reason=str(resp.stop_reason) if resp.stop_reason else "stop",
            usage={
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
            thinking=thinking,
        )

    async def _stream(self, client: AsyncAnthropic, kwargs: dict[str, Any]) -> LLMResponse:
        kwargs["stream"] = True
        text_parts: list[str] = []
        tool_calls_map: dict[str, dict[str, Any]] = {}
        thinking_parts: list[str] = []
        finish_reason = "stop"

        async with client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        text_parts.append(delta.text)
                    elif delta.type == "thinking_delta":
                        thinking_parts.append(delta.thinking)
                    elif delta.type == "input_json_delta":
                        idx = event.content_block_index
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {"id": "", "name": "", "arguments": ""}
                        tool_calls_map[idx]["arguments"] += delta.partial_json
                elif event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        idx = event.content_block.index
                        tool_calls_map[idx] = {
                            "id": event.content_block.id,
                            "name": event.content_block.name,
                            "arguments": "",
                        }
                elif event.type == "message_delta":
                    if event.delta.stop_reason:
                        finish_reason = event.delta.stop_reason

        tool_calls_list = [
            ToolCallRequest(
                id=v["id"],
                name=v["name"],
                arguments=self._parse_arguments(v["arguments"]),
            )
            for v in tool_calls_map.values()
        ] if tool_calls_map else None

        return LLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls_list or [],
            finish_reason=finish_reason,
            thinking="".join(thinking_parts) if thinking_parts else None,
        )

    def _prepare_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Convert messages to Anthropic format, extracting system prompt."""
        anthropic_messages: list[dict[str, Any]] = []
        system_parts: list[str] = []

        for msg in messages:
            role = msg["role"]
            if role == "system":
                system_parts.append(msg.get("content", ""))
            elif role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg["tool_call_id"],
                        "content": msg.get("content", ""),
                    }],
                })
            elif role == "tool_call":
                # Tool call message from model
                tool_calls = msg.get("tool_calls", [])
                if isinstance(tool_calls, list):
                    content = [{"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": tc["function"]["arguments"]} for tc in tool_calls]
                    anthropic_messages.append({"role": "assistant", "content": content})
            else:
                anthropic_messages.append({"role": role, "content": msg.get("content", "")})

        return anthropic_messages, "\n".join(system_parts) if system_parts else None

    def _convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI-style tools to Anthropic tools format."""
        anthropic_tools = []
        for tool in tools:
            t = tool.get("function", tool)
            anthropic_tools.append({
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {}),
            })
        return anthropic_tools

    def _parse_arguments(self, arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        try:
            return json.loads(arguments)
        except Exception:
            return {}

    def get_default_model(self) -> str:
        return "claude-opus-4-6"
```

---

## Task 5: Create DeepSeek Provider

**Files:** `nanobot/providers/deepseek_provider.py` (CREATE)

- [ ] **Step 1: Write the DeepSeek provider (reuses OpenAI SDK with different base URL)**

```python
"""DeepSeek provider using native openai>=1.0 SDK with api.deepseek.com endpoint."""

from nanobot.providers.openai_provider import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek provider - reuses OpenAI SDK with DeepSeek endpoint."""

    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-chat"

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        # Ensure base_url is set to DeepSeek endpoint
        base = api_base or self.DEFAULT_BASE_URL
        super().__init__(api_key=api_key, api_base=base)

    def get_default_model(self) -> str:
        return self.DEFAULT_MODEL
```

---

## Task 6: Create Azure OpenAI Provider

**Files:** `nanobot/providers/azure_provider.py` (CREATE)

- [ ] **Step 1: Write the Azure OpenAI provider**

```python
"""Azure OpenAI provider using native openai>=1.0 SDK with Azure adapter."""

import json
from typing import Any

from openai import AsyncAzureOpenAI

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class AzureOpenAIProvider(LLMProvider):
    """Azure OpenAI provider using AsyncAzureOpenAI client."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        api_version: str = "2024-12-01-preview",
        azure_deployment: str | None = None,
    ):
        super().__init__(api_key=api_key, api_base=api_base)
        self._client: AsyncAzureOpenAI | None = None
        self.api_version = api_version
        self.azure_deployment = azure_deployment

    def _get_client(self) -> AsyncAzureOpenAI:
        if self._client is None:
            self._client = AsyncAzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.api_base,
                api_version=self.api_version,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> LLMResponse:
        client = self._get_client()
        # Azure uses deployment name as model
        deployment = model or self.azure_deployment or self.get_default_model()

        kwargs: dict[str, Any] = {
            "model": deployment,  # Azure uses deployment name here
            "messages": self._prepare_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if stream:
            return await self._stream(client, deployment, kwargs)
        else:
            return await self._non_stream(client, deployment, kwargs)

    async def _non_stream(self, client: AsyncAzureOpenAI, deployment: str, kwargs: dict[str, Any]) -> LLMResponse:
        resp = await client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        message = choice.message

        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=self._parse_arguments(tc.function.arguments),
                )
                for tc in message.tool_calls
            ]

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls or [],
            finish_reason=choice.finish_reason or "stop",
            usage={
                "input_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "output_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
        )

    async def _stream(self, client: AsyncAzureOpenAI, deployment: str, kwargs: dict[str, Any]) -> LLMResponse:
        kwargs["stream"] = True
        content_parts: list[str] = []
        tool_calls_map: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"

        async with client.chat.completions.create(**kwargs) as stream:
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    content_parts.append(delta.content)
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc_delta.id:
                            tool_calls_map[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_map[idx]["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_map[idx]["arguments"] += tc_delta.function.arguments
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

        tool_calls = [
            ToolCallRequest(id=v["id"], name=v["name"], arguments=self._parse_arguments(v["arguments"]))
            for v in tool_calls_map.values()
        ] if tool_calls_map else None

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls or [],
            finish_reason=finish_reason,
        )

    def _prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = []
        for msg in messages:
            m = {"role": msg["role"], "content": msg.get("content", "")}
            if msg.get("tool_call_id"):
                m["tool_call_id"] = msg["tool_call_id"]
                m["role"] = "tool"
            elif msg.get("tool_calls"):
                m["tool_calls"] = msg["tool_calls"]
            prepared.append(m)
        return prepared

    def _parse_arguments(self, arguments: str | dict) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        try:
            return json.loads(arguments)
        except Exception:
            return {}

    def get_default_model(self) -> str:
        return self.azure_deployment or "gpt-4o"
```

---

## Task 7: Rewrite ModelRouter

**Files:** `nanobot/providers/router.py`

The router needs to hold provider instances and route by model prefix. The current `_resolve_model` method returns `ModelHandle` with `litellm_id` — change to use native model IDs.

- [ ] **Step 1: Rewrite ModelRouter with provider instances**

```python
"""Model Router: Unified model resolution and provider routing."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from loguru import logger

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.storage.config_repository import ConfigRepository


@dataclass(frozen=True)
class ModelHandle:
    """Model call handle containing all necessary information for LLM invocation."""

    model: str              # Native model ID, e.g., "claude-opus-4-6" (NOT "anthropic/claude-opus-4-6")
    api_key: str
    api_base: str | None
    provider_id: str        # "openai" | "anthropic" | "deepseek" | "azure"
    capabilities: set[str]  # {"tools", "vision", "thinking", ...}
    context_window: int
    # Provider instance - call .chat() on this
    provider: "LLMProvider" = None  # type: ignore[assignment]

    def has_capability(self, capability: str) -> bool:
        """Check if model has specific capability."""
        return capability in self.capabilities


# Model ID prefix -> provider_id mapping
MODEL_PREFIX_MAP = {
    "claude": "anthropic",
    "gpt": "openai",
    "deepseek": "deepseek",
    "azure": "azure",
}


class ModelRouter:
    """
    Unified model router - the ONLY entry point for model resolution.

    Usage:
        router = ModelRouter(repo)
        router.register_providers(openai=..., anthropic=..., deepseek=..., azure=...)

        handle = router.get("smart")      # Deep thinking
        handle = router.get("fast")       # Quick response
        handle = router.get("coding")     # Code assistant
        handle = router.get("claude-opus-4-6")
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
        """Register provider instances. Call after construction."""
        if openai:
            self._providers["openai"] = openai
        if anthropic:
            self._providers["anthropic"] = anthropic
        if deepseek:
            self._providers["deepseek"] = deepseek
        if azure:
            self._providers["azure"] = azure

    def get(self, profile_or_model: str) -> ModelHandle:
        """
        Resolve profile or model reference to ModelHandle.

        Resolution order:
        1. Profile ID -> resolve via model_chain
        2. Model ID -> direct lookup
        3. Alias -> lookup via aliases field
        """
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

    def get_provider_for_model(self, model_name: str) -> "LLMProvider | None":
        """Get provider instance by model name prefix."""
        model_lower = model_name.lower()
        for prefix, provider_id in MODEL_PREFIX_MAP.items():
            if model_lower.startswith(prefix) or prefix in model_lower:
                return self._providers.get(provider_id)
        return None

    def _resolve_profile(self, profile: dict) -> ModelHandle | None:
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
                continue
            handle = self._resolve_model(model)
            if handle:
                return handle

        logger.warning(f"Profile {profile['id']} has no available models in chain: {model_chain}")
        return None

    def _resolve_model(self, model: dict) -> ModelHandle | None:
        if not model.get("enabled"):
            return None

        provider = self.repo.get_provider(model["provider_id"])
        if not provider:
            return None
        if not provider.get("enabled"):
            return None
        if not provider.get("api_key"):
            return None

        provider_instance = self._providers.get(model["provider_id"])
        if not provider_instance:
            # Fallback: try to resolve by model name prefix
            provider_instance = self.get_provider_for_model(model["id"])

        # Use native model ID (not litellm_id)
        native_model = model.get("model_name") or model["id"]

        return ModelHandle(
            model=native_model,
            api_key=provider["api_key"],
            api_base=provider.get("api_base"),
            provider_id=provider["id"],
            capabilities=set(model.get("capabilities", "").split(",")) if model.get("capabilities") else set(),
            context_window=model.get("context_window", 128000),
            provider=provider_instance,
        )

    def clear_cache(self) -> None:
        """Clear resolution cache. Call when config changes."""
        self._cache.clear()
        logger.debug("ModelRouter cache cleared")


class ModelNotFoundError(Exception):
    pass


class NoModelAvailableError(Exception):
    pass
```

---

## Task 8: Update Config Schema

**Files:** `nanobot/config/schema.py`

- [ ] **Step 1: Add AzureProviderConfig and simplify ProvidersConfig**

After `ProviderConfig` class, add:

```python
class AzureProviderConfig(BaseModel):
    """Azure OpenAI provider configuration."""
    api_key: str = ""
    api_base: str = ""  # e.g. "https://xxx.openai.azure.com"
    api_version: str = "2024-12-01-preview"
    azure_deployment: str = ""  # e.g. "gpt-4o"
```

- [ ] **Step 2: Update ProvidersConfig — remove old types, add azure**

Replace the `ProvidersConfig` class:

```python
class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    azure: AzureProviderConfig = Field(default_factory=AzureProviderConfig)
```

- [ ] **Step 3: Simplify _MODEL_PROVIDER_MAP**

Replace the existing `_MODEL_PROVIDER_MAP`:

```python
    _MODEL_PROVIDER_MAP: dict[tuple[str, ...], str] = {
        ("anthropic/", "claude"): "anthropic",
        ("openai/", "gpt"): "openai",
        ("deepseek/", "deepseek"): "deepseek",
        ("azure/", "azure"): "azure",
    }
```

- [ ] **Step 4: Simplify _FALLBACK_PROVIDER_ORDER**

```python
    _FALLBACK_PROVIDER_ORDER: list[str] = [
        "anthropic", "deepseek", "openai", "azure"
    ]
```

- [ ] **Step 5: Simplify _MODEL_API_BASE_MAP**

```python
    _MODEL_API_BASE_MAP: dict[str, str | None] = {
        "openai": None,
        "anthropic": None,
        "deepseek": "https://api.deepseek.com",
        "azure": None,  # azure_deployment is set per-model
    }
```

---

## Task 9: Rewrite Discovery

**Files:** `nanobot/providers/discovery.py`

Keep the static model lists (already have them for Anthropic, OpenAI, DeepSeek). Remove OpenRouterDiscovery and other deprecated providers.

- [ ] **Step 1: Remove OpenRouterDiscovery, ZhipuDiscovery, DashScopeDiscovery, GroqDiscovery, GeminiDiscovery, OllamaDiscovery, MinimaxDiscovery classes**

Keep only: `AnthropicDiscovery`, `OpenAIDiscovery`, `DeepSeekDiscovery`. Add `AzureDiscovery`.

- [ ] **Step 2: Add AzureDiscovery**

```python
class AzureDiscovery(ProviderDiscovery):
    """Azure OpenAI model discovery - returns configured deployments."""

    async def discover(self, api_key: str, api_base: str | None = None) -> list[DiscoveredModel]:
        # Azure uses configured deployments, not API discovery
        # Return a placeholder model for configuration UX
        return [
            DiscoveredModel(
                id="azure-deployment",
                name="Azure Deployment",
                litellm_id="azure",
                aliases=["azure"],
                capabilities=["tools", "vision"],
                context_window=128000,
            )
        ]
```

- [ ] **Step 3: Update DiscoveredModel dataclass — remove litellm_id**

```python
@dataclass
class DiscoveredModel:
    """Discovered model information."""

    id: str                 # System ID, e.g., "claude-opus-4-6"
    name: str               # Display name
    litellm_id: str         # REMOVED - kept for compat only (set to same as id)
    aliases: list[str]      # Short aliases
    capabilities: list[str]  # ["tools", "vision", "thinking"]
    context_window: int
```

- [ ] **Step 4: Update all existing MODELS lists — remove litellm_id or set to same as id**

For Anthropic, OpenAI, DeepSeek static lists: set `litellm_id` = same as `id`.

---

## Task 10: Update Agent Loop

**Files:** `nanobot/agent/loop.py`

The agent loop currently imports `LiteLLMProvider` and calls `provider.chat()`. Need to replace with native provider pattern.

- [ ] **Step 1: Replace LiteLLMProvider import with native providers**

```python
# REMOVE:
# from nanobot.providers.litellm_provider import LiteLLMProvider

# ADD:
from nanobot.providers.anthropic_provider import AnthropicProvider
from nanobot.providers.openai_provider import OpenAIProvider
from nanobot.providers.deepseek_provider import DeepSeekProvider
from nanobot.providers.azure_provider import AzureOpenAIProvider
```

- [ ] **Step 2: In AgentLoop.__init__, replace provider creation**

Find where `provider = LiteLLMProvider(...)` is constructed. Replace with:

```python
# Instantiate all native providers
self.openai_provider = OpenAIProvider()
self.anthropic_provider = AnthropicProvider()
self.deepseek_provider = DeepSeekProvider()
self.azure_provider = AzureOpenAIProvider()

# Register with router
self.router.register_providers(
    openai=self.openai_provider,
    anthropic=self.anthropic_provider,
    deepseek=self.deepseek_provider,
    azure=self.azure_provider,
)

# Default provider for backward compat (old code paths)
self.provider = self.anthropic_provider  # Was LiteLLMProvider
```

- [ ] **Step 3: Update _register_all_provider_keys — remove litellm-specific calls**

Replace the `provider_ids` list:

```python
provider_ids = ["anthropic", "openai", "deepseek", "azure"]
```

Remove all `provider.ensure_api_key_for_model()` calls — no longer needed since each provider holds its own key.

- [ ] **Step 4: Update provider hot-update calls in api.py path**

In places where `provider.update_config()` and `provider.ensure_api_key_for_model()` are called, replace with:

```python
# Hot-update a specific provider's config
if provider_config.type == "anthropic":
    self.anthropic_provider.api_key = api_key
    self.anthropic_provider.api_base = api_base
elif provider_config.type == "openai":
    self.openai_provider.api_key = api_key
    self.openai_provider.api_base = api_base
elif provider_config.type == "deepseek":
    self.deepseek_provider.api_key = api_key
    self.deepseek_provider.api_base = api_base
elif provider_config.type == "azure":
    self.azure_provider.api_key = api_key
    self.azure_provider.api_base = api_base
    if provider_config.api_version:
        self.azure_provider.api_version = provider_config.api_version
    if provider_config.azure_deployment:
        self.azure_provider.azure_deployment = provider_config.azure_deployment
```

- [ ] **Step 5: Update get_default_model — remove litellm fallback**

```python
@property
def default_model(self) -> str:
    if hasattr(self, "router") and hasattr(self, "default_profile"):
        try:
            return self.router.get(self.default_profile).model
        except Exception:
            pass
    return "claude-opus-4-6"  # Native model ID, not litellm format
```

---

## Task 11: Update Web API

**Files:** `nanobot/web/api.py`

- [ ] **Step 1: Remove LiteLLMProvider import**

```python
# REMOVE: from nanobot.providers.litellm_provider import LiteLLMProvider
```

- [ ] **Step 2: In API handler init — replace provider creation**

Find `provider = LiteLLMProvider(...)` and replace with native provider instantiation + registration on the agent's router.

- [ ] **Step 3: Replace ensure_api_key_for_model calls**

Replace:
```python
self.agent.provider.ensure_api_key_for_model(model, api_key, api_base)
```
With individual provider updates:
```python
# Update the specific provider's api_key based on model prefix
if self.agent.router:
    p = self.agent.router.get_provider_for_model(model)
    if p:
        p.api_key = api_key
        if hasattr(p, 'api_base'):
            p.api_base = api_base
```

- [ ] **Step 4: Replace update_config calls**

Replace:
```python
self.agent.provider.update_config(model_name, api_key, api_base)
```
With individual provider update on the agent:
```python
# Find the provider for this model and update it
if hasattr(self.agent, 'anthropic_provider') and 'claude' in model_name.lower():
    self.agent.anthropic_provider.api_key = api_key
    self.agent.anthropic_provider.api_base = api_base
elif hasattr(self.agent, 'openai_provider') and ('gpt' in model_name.lower() or '4o' in model_name):
    self.agent.openai_provider.api_key = api_key
    self.agent.openai_provider.api_base = api_base
# ... etc
```

---

## Task 12: Update Frontend Types

**Files:** `web-ui/src/types.ts`

- [ ] **Step 1: Narrow Provider.type to 4 types**

```typescript
export interface Provider {
  id: string
  name: string
  type: 'openai' | 'anthropic' | 'deepseek' | 'azure'
  apiKey?: string
  apiBase?: string
  enabled: boolean
}
```

---

## Task 13: Update ConfigPage

**Files:** `web-ui/src/pages/ConfigPage.tsx`

- [ ] **Step 1: Update provider type dropdown options**

Find where provider types are listed in the UI. Replace the options array with:

```typescript
const PROVIDER_TYPES = [
  { value: 'anthropic', label: 'Anthropic (Claude)' },
  { value: 'openai', label: 'OpenAI (GPT)' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'azure', label: 'Azure OpenAI' },
] as const
```

- [ ] **Step 2: Remove UI handling for deprecated provider types (openrouter, groq, etc.)**

- [ ] **Step 3: Add Azure-specific fields (api_version, azure_deployment) to the provider edit form**

---

## Task 14: Update ConfigRepository

**Files:** `nanobot/storage/config_repository.py`

- [ ] **Step 1: Verify provider column names**

The `config_providers` table has columns for each provider type as rows (id, name, api_key, api_base, enabled, priority). Check the SELECT queries to ensure they work with the new 4-provider set. The table structure uses a generic `id` column for provider identifiers, not per-provider columns — so no schema migration needed for the table itself.

If the table uses wide format with many nullable columns per provider type, add a migration to add `azure_api_version` and `azure_deployment` columns and remove deprecated columns.

---

## Task 15: Delete litellm_provider.py

**Files:** `nanobot/providers/litellm_provider.py` (DELETE)

- [ ] **Step 1: Verify no remaining references**

```bash
grep -r "litellm" nanobot/
```
Should return no matches (except potentially in comments/docs).

- [ ] **Step 2: Delete the file**

---

## Task 16: Final verification

- [ ] **Step 1: Run Python type check**

```bash
cd E:\workSpace\nanobot-webui
python -m py_compile nanobot/providers/openai_provider.py
python -m py_compile nanobot/providers/anthropic_provider.py
python -m py_compile nanobot/providers/deepseek_provider.py
python -m py_compile nanobot/providers/azure_provider.py
python -m py_compile nanobot/providers/router.py
python -m py_compile nanobot/agent/loop.py
python -m py_compile nanobot/web/api.py
```

- [ ] **Step 2: Verify litellm import gone**

```bash
grep -rn "litellm" nanobot/ --include="*.py"
```

Expected: No matches.

- [ ] **Step 3: Commit with message "refactor: remove litellm, add native SDK providers (openai/anthropic/deepseek/azure)"**

---

## Spec Coverage Check

| Spec Requirement | Tasks |
|-----------------|-------|
| OpenAI SDK provider | Task 3, 7 |
| Anthropic SDK provider | Task 4, 7 |
| DeepSeek SDK provider | Task 5 |
| Azure OpenAI SDK provider | Task 6 |
| ModelRouter rewrite | Task 7 |
| Config schema update | Task 8 |
| Discovery rewrite | Task 9 |
| Agent loop migration | Task 10 |
| Web API migration | Task 11 |
| Frontend types update | Task 12 |
| ConfigPage update | Task 13 |
| ConfigRepository update | Task 14 |
| Delete litellm_provider.py | Task 15 |
| Dependencies update | Task 1 |
| LLMResponse with thinking | Task 2 |
