"""Base LLM provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRequest:
    """A tool call request from the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    thinking: str | None = None  # Anthropic extended thinking output
    reasoning_content: str | None = None  # DeepSeek / Moonshot / Kimi reasoning

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.
    
    Implementations should handle the specifics of each provider's API
    while maintaining a consistent interface.
    """
    
    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base
    
    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        api_base: str | None = None,
        stream_callback: Any | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions.
            model: Model identifier (provider-specific).
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            api_base: Optional per-call base URL override (for OpenAI-compatible providers).
            stream_callback: Optional callback for streaming text deltas.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        pass
    
    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        pass

    @staticmethod
    def _strip_image_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Return a copy of messages with image_url blocks removed, or None if no images were present."""
        had_images = False
        stripped: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                new_blocks = []
                for block in content:
                    if block.get("type") == "image_url":
                        had_images = True
                        continue
                    new_blocks.append(block)
                if not new_blocks:
                    new_blocks = [{"type": "text", "text": ""}]
                stripped.append({**msg, "content": new_blocks})
            else:
                stripped.append(dict(msg))
        return stripped if had_images else None
