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


def _sanitize_mcp_schema(params: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitize MCP tool parameters schema for LLM API compatibility.

    Some MCP servers return schemas where `required` lists properties
    that don't exist in `properties`. Strict providers (Moonshot, etc.)
    reject these schemas. This function removes invalid required entries.
    """
    if not isinstance(params, dict):
        return {"type": "object", "properties": {}}

    # Work on a copy to avoid mutating the original
    sanitized = dict(params)
    props = sanitized.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
        sanitized["properties"] = props

    required = sanitized.get("required")
    if isinstance(required, list):
        valid_required = [r for r in required if r in props]
        removed = [r for r in required if r not in props]
        if removed:
            logger.warning(
                f"[MCP Schema] Removed invalid required properties not in properties: {removed}"
            )
        if valid_required:
            sanitized["required"] = valid_required
        else:
            sanitized.pop("required", None)

    # Ensure type is present
    if "type" not in sanitized:
        sanitized["type"] = "object"

    return sanitized


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
        self._parameters = _sanitize_mcp_schema(parameters) if isinstance(parameters, dict) else {"type": "object", "properties": {}}
        self._mcp_loader = mcp_loader
        self._lazy_tools = lazy_tools
        self._disposed = False

    @property
    def name(self) -> str:
        return f"mcp_{self._server_id}_{_sanitize_tool_name(self._tool_name)}"

    @property
    def server_id(self) -> str | None:
        return self._server_id

    @property
    def deferred(self) -> bool:
        """MCP tools are deferred - loaded on-demand when LLM calls them."""
        return True

    @property
    def description(self) -> str:
        return f"[MCP/{self._server_id}] {self._description}"

    @property
    def parameters(self) -> dict[str, Any]:
        # Always sanitize on access to catch any mutations or updates
        return _sanitize_mcp_schema(self._parameters)

    async def _ensure_connected(self) -> bool:
        """确保 MCP 连接已建立。返回是否连接成功。"""
        if self._disposed:
            return False
        # 按需连接单个 MCP 服务器（loader 内部有并发锁）
        try:
            result = await self._mcp_loader.connect_lazy(self._server_id, timeout=30.0)
            if result is None:
                logger.warning(f"MCP {self._server_id}: failed to connect on first call")
                return False
            session, tools = result
            # 更新本工具的 schema（如果原来是空的）
            self._update_schema_from_discovered(tools)
            logger.info(f"MCP {self._server_id}: connected on first tool call ({len(tools)} tools available)")
            return True
        except Exception as e:
            logger.warning(f"MCP {self._server_id}: connection error on first call: {e}")
            return False

    def _update_schema_from_discovered(self, discovered_tools: list[dict[str, Any]]) -> None:
        """
        从发现阶段获取的工具列表中更新本工具的 schema。
        当原 schema 明显为空或不完整时，从 discovery 结果中获取正确的 schema，
        使 LLM 能正确生成参数。
        """
        current_params = self._parameters or {}
        current_props = current_params.get("properties", {})

        # 判断 schema 是否明显为空或很可能不完整：
        # 1. 完全没有 properties 且没有 required
        is_empty = not current_props and not current_params.get("required")

        # 2. 有 properties 但它们都没有 description 和 type（很可能是不完整的 schema）
        is_likely_incomplete = False
        if current_props:
            all_props_have_nothing = all(
                not p.get("description") and not p.get("type")
                for p in current_props.values()
            )
            is_likely_incomplete = all_props_have_nothing and len(current_props) < 3

        if not (is_empty or is_likely_incomplete):
            return  # schema 看起来有效，无需更新

        # 在 discovered_tools 中找到本工具的定义
        for tool_spec in discovered_tools:
            if tool_spec.get("name") == self._tool_name:
                new_params = tool_spec.get("parameters") or tool_spec.get("inputSchema") or {}
                new_props = new_params.get("properties", {})

                # 只有当新 schema 有实质内容时才更新
                if new_props or new_params.get("required"):
                    logger.info(
                        f"MCP {self._server_id}/{self._tool_name}: "
                        f"更新 schema (was {'empty' if is_empty else 'incomplete'}, "
                        f"now has properties: {list(new_props.keys())})"
                    )
                    self._parameters = _sanitize_mcp_schema(new_params)
                break

    async def execute(self, **kwargs: Any) -> str:
        """执行 MCP 工具（懒加载，自动重连一次）。"""
        for attempt in range(2):
            try:
                if not await self._ensure_connected():
                    return f"MCP {self._server_id}: 连接失败，请检查 MCP 配置。"

                result = await self._mcp_loader.call_tool(self._server_id, self._tool_name, kwargs)
                return _format_mcp_result(result, self._server_id, self._tool_name)

            except Exception as e:
                # 判断是否为连接断开类错误（MCP server 重启 / 网络抖动）
                _err_type = type(e).__name__
                _is_conn_error = any(
                    kw in _err_type or kw in str(e).lower()
                    for kw in ("ClosedResource", "EOF", "ConnectionReset", "BrokenPipe", "anyio")
                )
                if _is_conn_error and attempt == 0:
                    logger.warning(
                        f"[MCP] {self._server_id}/{self._tool_name}: 连接断开（{_err_type}），尝试重连…"
                    )
                    # 重置连接，下次 _ensure_connected 会重新建立
                    try:
                        await self._mcp_loader.disconnect_lazy(self._server_id)
                    except Exception:
                        pass
                    continue  # 重试一次
                logger.exception(f"MCP tool error: {self._server_id}/{self._tool_name}")
                return f"MCP tool error: {e}"

        return f"MCP {self._server_id}: 重连后仍失败，工具暂不可用"

    def dispose(self) -> None:
        """标记该工具已废弃（配置删除时调用）。"""
        self._disposed = True


def _format_mcp_result(result: Any, server_id: str, tool_name: str) -> str:
    """Format an MCP CallToolResult into a string response."""
    if getattr(result, "isError", False):
        content = getattr(result, "content", result)
        sanitized = _sanitize_error(str(content))
        return f"MCP error: {sanitized}"
    content = getattr(result, "content", None)
    if content:
        parts = []
        for block in content:
            text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
            if text:
                parts.append(str(text))
        return "\n".join(parts) if parts else "(no output)"
    return "(no output)"


def _sanitize_error(text: str) -> str:
    """Strip credential-like patterns from error text before returning to LLM."""
    _CREDENTIAL_PATTERN = re.compile(
        r"(?:"
        r"ghp_[A-Za-z0-9_]{1,255}"
        r"|sk-[A-Za-z0-9_]{1,255}"
        r"|Bearer\s+\S+"
        r"|token=[^\s&,;\"']{1,255}"
        r"|key=[^\s&,;\"']{1,255}"
        r"|API_KEY=[^\s&,;\"']{1,255}"
        r"|password=[^\s&,;\"']{1,255}"
        r"|secret=[^\s&,;\"']{1,255}"
        r")",
        re.IGNORECASE,
    )
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", text)
