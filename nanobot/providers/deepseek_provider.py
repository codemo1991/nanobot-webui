"""DeepSeek provider using native openai>=1.0 SDK with DeepSeek endpoint."""

from typing import Any

from openai import AsyncOpenAI

from nanobot.providers.openai_provider import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek provider using AsyncOpenAI with DeepSeek base URL."""

    DEEPSEEK_BASE_URL = "https://api.deepseek.com"

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        super().__init__(api_key=api_key, api_base=api_base or self.DEEPSEEK_BASE_URL)

    def get_default_model(self) -> str:
        return "deepseek-chat"
