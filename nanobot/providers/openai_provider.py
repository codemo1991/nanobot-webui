"""OpenAI provider using native openai>=1.0 SDK."""

import json
from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageToolCall

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest

_KIMI_THINKING_MODELS: frozenset[str] = frozenset({
    "kimi-k2.5",
    "k2.6-code-preview",
})


def _is_kimi_thinking_model(model_name: str) -> bool:
    """Return True if model_name refers to a Kimi thinking-capable model."""
    name = model_name.lower()
    if name in _KIMI_THINKING_MODELS:
        return True
    if "/" in name and name.rsplit("/", 1)[1] in _KIMI_THINKING_MODELS:
        return True
    return False


def _is_kimi_code_base(api_base: str | None) -> bool:
    if not api_base:
        return False
    b = api_base.lower()
    return "api.kimi.com" in b or "kimi.com/coding" in b


def _kimi_default_headers(api_base: str | None) -> dict[str, str] | None:
    """Return Kimi Code required headers if base URL points to Kimi Code."""
    if _is_kimi_code_base(api_base):
        return {"User-Agent": "RooCode/3.0.0"}
    return None


class OpenAIProvider(LLMProvider):
    """OpenAI provider using AsyncOpenAI client."""

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        super().__init__(api_key, api_base)
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "api_key": self.api_key or "",
                "base_url": self.api_base,
            }
            headers = _kimi_default_headers(self.api_base)
            if headers:
                kwargs["default_headers"] = headers
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def get_default_model(self) -> str:
        return "gpt-4o"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        api_base: str | None = None,
        stream_callback: Any | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        # Per-call api_base override for OpenAI-compatible providers (minimax, etc.)
        if api_base and api_base != self.api_base:
            client_kwargs: dict[str, Any] = {"api_key": self.api_key or "", "base_url": api_base}
            headers = _kimi_default_headers(api_base)
            if headers:
                client_kwargs["default_headers"] = headers
            client = AsyncOpenAI(**client_kwargs)
        else:
            client = self._get_client()
        native_model = model or self.get_default_model()

        # Kimi k2.5 enforces temperature >= 1.0
        if native_model and "kimi-k2.5" in native_model.lower():
            temperature = 1.0

        kwargs: dict[str, Any] = {
            "model": native_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Kimi thinking-capable models support explicit thinking toggle
        if reasoning_effort is not None and _is_kimi_thinking_model(native_model):
            thinking_enabled = reasoning_effort.lower() != "minimal"
            kwargs.setdefault("extra_body", {}).update(
                {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
            )

        try:
            if stream_callback:
                return await self._stream_chat(client, native_model, messages, tools, max_tokens, temperature, stream_callback)
            else:
                response = await client.chat.completions.create(**kwargs)
                return self._parse_response(response)
        except Exception as e:
            logger.error(f"OpenAI chat error: {e}")
            raise

    async def _stream_chat(
        self,
        client: AsyncOpenAI,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        temperature: float,
        stream_callback: Any | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Streaming chat — accumulates content for compatibility."""
        # Kimi k2.5 enforces temperature >= 1.0
        if model and "kimi-k2.5" in model.lower():
            temperature = 1.0

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Kimi thinking-capable models support explicit thinking toggle
        if reasoning_effort is not None and _is_kimi_thinking_model(model or ""):
            thinking_enabled = reasoning_effort.lower() != "minimal"
            kwargs.setdefault("extra_body", {}).update(
                {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
            )

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_bufs: dict[int, dict[str, Any]] = {}
        usage: dict[str, int] = {}

        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
                if not chunk.choices:
                    if chunk.usage:
                        usage = {
                            "input_tokens": chunk.usage.prompt_tokens or 0,
                            "output_tokens": chunk.usage.completion_tokens or 0,
                            "total_tokens": chunk.usage.total_tokens or 0,
                        }
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    content_parts.append(delta.content)
                    if stream_callback:
                        try:
                            stream_callback(delta.content)
                        except Exception as e:
                            logger.debug(f"OpenAI stream_callback error: {e}")
                if delta and getattr(delta, "reasoning_content", None):
                    reasoning_parts.append(delta.reasoning_content)
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        buf = tc_bufs.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            buf["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                buf["name"] = tc.function.name
                            if tc.function.arguments:
                                buf["arguments"] += str(tc.function.arguments)

        tool_calls: list[ToolCallRequest] = []
        for idx in sorted(tc_bufs.keys()):
            b = tc_bufs[idx]
            args: dict[str, Any] = {}
            if b["arguments"]:
                try:
                    parsed = json.loads(b["arguments"])
                    if isinstance(parsed, dict):
                        args = parsed
                except json.JSONDecodeError:
                    pass
            tool_calls.append(ToolCallRequest(
                id=b["id"],
                name=b["name"],
                arguments=args,
            ))

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason="stop",
            usage=usage,
            reasoning_content="".join(reasoning_parts) or None,
        )

    def _parse_response(self, response: Any) -> LLMResponse:
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCallRequest] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                if isinstance(tc, ChatCompletionMessageToolCall):
                    args: dict[str, Any] = {}
                    if tc.function and tc.function.arguments:
                        args_str = str(tc.function.arguments).strip()
                        if args_str:
                            try:
                                args = json.loads(args_str)
                            except json.JSONDecodeError:
                                args = {}
                    tool_calls.append(
                        ToolCallRequest(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=args,
                        )
                    )

        usage: dict[str, int] = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens or 0,
                "output_tokens": response.usage.completion_tokens or 0,
                "total_tokens": response.usage.total_tokens or 0,
            }

        reasoning_content = getattr(message, "reasoning_content", None) or None

        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )
