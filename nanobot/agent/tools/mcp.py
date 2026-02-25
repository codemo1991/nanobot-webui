"""MCP tool adapter - wraps MCP server tools as nanobot Tools."""

import re
import asyncio
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


class McpLazyToolAdapter(Tool):
    """
    懒加载 MCP 工具适配器。
    只有当 LLM 实际调用该工具时，才建立 MCP 连接。

    特点：
    - 启动时不建立任何 MCP 连接
    - 首次调用时动态连接（3s 超时）
    - 连接失败时返回错误，不阻塞主流程
    - 支持自动重连
    """

    def __init__(
        self,
        server_id: str,
        tool_name: str,
        description: str,
        parameters: dict[str, Any],
        mcp_loader: Any,  # McpToolLoader 实例
        lazy_tools: dict[str, "McpLazyToolAdapter"],  # 同服务器的其他懒加载工具
    ):
        self._server_id = _sanitize_tool_name(server_id)
        self._tool_name = tool_name
        self._description = description or f"MCP tool {tool_name} from {server_id}"
        self._parameters = parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}}
        self._mcp_loader = mcp_loader
        self._lazy_tools = lazy_tools
        self._session = None
        self._lock = asyncio.Lock()
        self._disposed = False

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

    async def _ensure_connected(self) -> bool:
        """确保 MCP 连接已建立。返回是否连接成功。"""
        if self._disposed:
            return False

        # 如果已经有连接，直接返回
        if self._session is not None:
            return True

        # 防止并发连接
        async with self._lock:
            # 双重检查
            if self._session is not None or self._disposed:
                return self._session is not None

            try:
                # 按需连接单个 MCP 服务器
                result = await self._mcp_loader.connect_lazy(self._server_id, timeout=3.0)
                if result is None:
                    logger.warning(f"MCP {self._server_id}: failed to connect on first call")
                    return False

                self._session, tools = result

                # 为同服务器的其他工具设置 session
                for other_tool in self._lazy_tools.values():
                    if other_tool is not self and other_tool._session is None:
                        other_tool._session = self._session

                logger.info(f"MCP {self._server_id}: connected on first tool call ({len(tools)} tools available)")
                return True
            except Exception as e:
                logger.warning(f"MCP {self._server_id}: connection error on first call: {e}")
                return False

    async def execute(self, **kwargs: Any) -> str:
        """执行 MCP 工具（懒加载）。"""
        try:
            # 确保连接已建立
            if not await self._ensure_connected():
                return f"MCP {self._server_id}: failed to connect to server. Please check MCP configuration."

            # 调用工具
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

    def dispose(self) -> None:
        """标记该工具已废弃（配置删除时调用）。"""
        self._disposed = True
        self._session = None
