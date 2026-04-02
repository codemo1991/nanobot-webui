"""Azure OpenAI provider using native openai>=1.0 SDK."""

from typing import Any

from loguru import logger
from openai import AsyncAzureOpenAI
from openai.types.chat import ChatCompletionMessageToolCall

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class AzureProvider(LLMProvider):
    """Azure OpenAI provider using AsyncAzureOpenAI client."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        api_version: str | None = None,
        azure_deployment: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.api_version = api_version or "2024-02-01"
        self.azure_deployment = azure_deployment
        self._client: AsyncAzureOpenAI | None = None

    def _get_client(self) -> AsyncAzureOpenAI:
        if self._client is None:
            self._client = AsyncAzureOpenAI(
                api_key=self.api_key or "",
                azure_endpoint=self.api_base,
                api_version=self.api_version,
            )
        return self._client

    def get_default_model(self) -> str:
        return self.azure_deployment or "gpt-4o"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        client = self._get_client()
        # Azure uses deployment name as the model identifier
        deployment = model or self.azure_deployment or self.get_default_model()

        kwargs: dict[str, Any] = {
            "model": deployment,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await client.chat.completions.create(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"Azure OpenAI chat error: {e}")
            raise

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
