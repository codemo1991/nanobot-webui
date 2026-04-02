"""Anthropic provider using native anthropic>=0.20 SDK."""

from typing import Any

from anthropic import AsyncAnthropic
from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class AnthropicProvider(LLMProvider):
    """Anthropic provider using AsyncAnthropic client."""

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        super().__init__(api_key, api_base)
        self._client: AsyncAnthropic | None = None

    def _get_client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(
                api_key=self.api_key or "",
                base_url=self.api_base,
            )
        return self._client

    def get_default_model(self) -> str:
        return "claude-opus-4-6"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        thinking: dict[str, Any] | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        native_model = model or self.get_default_model()

        # Convert messages to Anthropic format
        anthropic_messages, system = self._convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": native_model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if thinking:
            kwargs["thinking"] = thinking

        try:
            response = await client.messages.create(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"Anthropic chat error: {e}")
            raise

    def _convert_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Convert OpenAI-style messages to Anthropic format."""
        anthropic_messages: list[dict[str, Any]] = []
        system_content: str | None = None

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                system_content = content
                continue

            # Map tool results to anthropic role
            if role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": content or "",
                    }],
                })
                continue

            # Convert content blocks
            anthropic_content: str | list[dict[str, Any]] = content or ""

            anthropic_messages.append({
                "role": role,
                "content": anthropic_content,
            })

        return anthropic_messages, system_content

    def _parse_response(self, response: Any) -> LLMResponse:
        content = ""
        tool_calls: list[ToolCallRequest] = []
        thinking: str | None = None

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCallRequest(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )

        # Extract extended thinking if present
        if hasattr(response, "thinking") and response.thinking:
            thinking = response.thinking

        usage: dict[str, int] = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens or 0,
                "output_tokens": response.usage.output_tokens or 0,
                "total_tokens": (
                    response.usage.input_tokens + response.usage.output_tokens
                    if response.usage.input_tokens and response.usage.output_tokens
                    else 0
                ),
            }

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=str(response.stop_reason) if response.stop_reason else "end_turn",
            usage=usage,
            thinking=thinking,
        )
