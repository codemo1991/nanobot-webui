"""MCP tool adapter - wraps MCP server tools as nanobot Tools."""

import re
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool

# DeepSeek/OpenAI require: ^[a-zA-Z0-9_-]+$
_SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")


def _sanitize_tool_name(name: str) -> str:
    """Ensure tool name matches ^[a-zA-Z0-9_-]+$ for LLM API compatibility."""
    return _SAFE_NAME_PATTERN.sub("_", name) or "unnamed"


class McpToolAdapter(Tool):
    """
    Adapts a single MCP server tool to the nanobot Tool interface.
    Tool name is prefixed as mcp_{server_id}_{tool_name} to avoid collisions.
    Names are sanitized to match ^[a-zA-Z0-9_-]+$ for DeepSeek/OpenAI compatibility.
    """
    
    def __init__(
        self,
        server_id: str,
        tool_name: str,
        description: str,
        parameters: dict[str, Any],
        session: Any,
    ):
        self._server_id = _sanitize_tool_name(server_id)
        self._tool_name = tool_name  # Keep original for session.call_tool()
        self._description = description or f"MCP tool {tool_name} from {server_id}"
        self._parameters = parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}}
        self._session = session
    
    @property
    def name(self) -> str:
        return f"mcp_{self._server_id}_{_sanitize_tool_name(self._tool_name)}"
    
    @property
    def description(self) -> str:
        return f"[MCP/{self._server_id}] {self._description}"
    
    @property
    def parameters(self) -> dict[str, Any]:
        if "type" not in self._parameters:
            self._parameters.setdefault("type", "object")
        return self._parameters
    
    async def execute(self, **kwargs: Any) -> str:
        """Execute the MCP tool via the session."""
        try:
            result = await self._session.call_tool(self._tool_name, kwargs)
            if getattr(result, "isError", False):
                return f"MCP error: {getattr(result, 'content', result)}"
            content = getattr(result, "content", None)
            if content:
                parts = []
                for block in content:
                    text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
                    if text:
                        parts.append(str(text))
                return "\n".join(parts) if parts else "(no output)"
            return "(no output)"
        except Exception as e:
            logger.exception(f"MCP tool error: {self._server_id}/{self._tool_name}")
            return f"MCP tool error: {e}"
