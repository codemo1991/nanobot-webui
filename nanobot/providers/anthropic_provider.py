"""Anthropic provider using native anthropic>=0.20 SDK."""

import re
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
            self._client = AsyncAnthropic(
                api_key=self.api_key or "",
                base_url=self.api_base,
            )
        return self._client

    def get_default_model(self) -> str:
        return "claude-sonnet-4-7"

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
        if stream_callback:
            from loguru import logger
            logger.warning("Anthropic provider does not support stream_callback yet")
        client = self._get_client()
        native_model = model or self.get_default_model()

        # Convert OpenAI-format messages to Anthropic format
        system_msg, anthropic_msgs = self._convert_messages(messages)

        # Convert tools to Anthropic format
        anthropic_tools = None
        if tools:
            anthropic_tools = [self._convert_tool(t) for t in tools]

        try:
            response = await client.messages.create(
                model=native_model,
                system=system_msg,
                messages=anthropic_msgs,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=anthropic_tools,
            )
            return self._parse_response(response)
        except Exception as e:
            from loguru import logger
            logger.error(f"Anthropic chat error: {e}")
            raise

    def _convert_messages(self, messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert OpenAI-format messages to Anthropic format."""
        system: str | None = None
        result: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                if isinstance(content, list):
                    text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                    system = "\n".join(text_parts)
                else:
                    system = content
                continue

            if role == "tool":
                result.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": msg.get("tool_call_id", ""), "content": content}
                    ],
                })
                continue

            if role not in ("user", "assistant"):
                continue

            # Convert list content (multimodal) to Anthropic format
            if isinstance(content, list):
                anthropic_content: list[dict[str, Any]] = []
                for block in content:
                    btype = block.get("type")
                    if btype == "text":
                        anthropic_content.append({"type": "text", "text": block.get("text", "")})
                    elif btype == "image_url":
                        converted = self._convert_image_block(block)
                        if converted:
                            anthropic_content.append(converted)
                result.append({"role": role, "content": anthropic_content})
            else:
                result.append({"role": role, "content": content})

        return system, result

    def _convert_image_block(self, block: dict[str, Any]) -> dict[str, Any] | None:
        """Convert OpenAI image_url block to Anthropic image block."""
        url = (block.get("image_url") or {}).get("url", "")
        m = re.match(r"data:(image/\w+);base64,(.+)", url, re.DOTALL)
        if m:
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)},
            }
        # Anthropic does not support remote URL images; try to fetch and base64-encode
        try:
            import base64
            import mimetypes
            from pathlib import Path

            # Local file path
            p = Path(url)
            if p.is_file():
                mime, _ = mimetypes.guess_type(str(p))
                if not mime:
                    ext = p.suffix.lower()
                    mime = {
                        ".png": "image/png",
                        ".gif": "image/gif",
                        ".webp": "image/webp",
                        ".bmp": "image/bmp",
                    }.get(ext, "image/jpeg")
                data = base64.b64encode(p.read_bytes()).decode()
                return {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": data},
                }

            # Remote URL
            import httpx

            resp = httpx.get(url, timeout=30)
            resp.raise_for_status()
            mime = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            data = base64.b64encode(resp.content).decode()
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            }
        except Exception as e:
            from loguru import logger
            logger.warning(f"[AnthropicProvider] Failed to convert image block (url={url[:80]}...): {e}")
            return None

    def _convert_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        """Convert OpenAI-format tool to Anthropic format."""
        name = tool.get("name", "")
        desc = tool.get("description", "")
        params = tool.get("parameters", {})
        return {
            "name": name,
            "description": desc,
            "input_schema": params,
        }

    def _parse_response(self, response: Message) -> LLMResponse:
        """Parse Anthropic response into standard LLMResponse format."""
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
            elif block.type == "thinking":
                thinking = block.thinking

        usage: dict[str, int] = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens or 0,
                "output_tokens": response.usage.output_tokens or 0,
                "total_tokens": (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0),
            }

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason="stop",
            usage=usage,
            thinking=thinking,
        )
