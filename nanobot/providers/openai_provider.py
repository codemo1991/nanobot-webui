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
            self._client = AsyncOpenAI(
                api_key=self.api_key or "",
                base_url=self.api_base,
            )
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
    ) -> LLMResponse:
        client = self._get_client()
        native_model = model or self.get_default_model()

        kwargs: dict[str, Any] = {
            "model": native_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = kwargs.pop("stream", False)

        try:
            if stream:
                return await self._stream_chat(client, native_model, messages, tools, max_tokens, temperature)
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
    ) -> LLMResponse:
        """Streaming chat — accumulates content for compatibility."""
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

        content_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        usage: dict[str, int] = {}

        async with client.chat.completions.create(**kwargs) as stream:
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    content_parts.append(delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        while len(tool_calls) <= idx:
                            tool_calls.append(
                                ToolCallRequest(
                                    id="",
                                    name="",
                                    arguments={},
                                )
                            )
                        if tc.id:
                            tool_calls[idx] = ToolCallRequest(
                                id=tc.id,
                                name=tc.function.name if tc.function else tool_calls[idx].name,
                                arguments=tc.function.arguments
                                if tc.function
                                else tool_calls[idx].arguments,
                            )
                if chunk.usage:
                    usage = {
                        "input_tokens": chunk.usage.prompt_tokens or 0,
                        "output_tokens": chunk.usage.completion_tokens or 0,
                        "total_tokens": chunk.usage.total_tokens or 0,
                    }

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason="stop",
            usage=usage,
        )

    def _parse_response(self, response: Any) -> LLMResponse:
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCallRequest] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                if isinstance(tc, ChatCompletionMessageToolCall):
                    tool_calls.append(
                        ToolCallRequest(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=tc.function.arguments,
                        )
                    )

        usage: dict[str, int] = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens or 0,
                "output_tokens": response.usage.completion_tokens or 0,
                "total_tokens": response.usage.total_tokens or 0,
            }

        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
        )
