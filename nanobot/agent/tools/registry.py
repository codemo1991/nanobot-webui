"""Tool registry for dynamic tool management."""

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

from nanobot.agent.tool_errors import (
    format_invalid_params,
    format_tool_error,
    format_tool_not_found,
    is_retryable_error,
)
from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self, thread_pool_executor: ThreadPoolExecutor | None = None):
        self._tools: dict[str, Tool] = {}
        self._thread_pool_executor = thread_pool_executor
        self._loaded_deferred_tools: set[str] = set()  # deferred tools whose full schema has been loaded
    
    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def copy(self) -> "ToolRegistry":
        """Shallow copy for per-run tools (e.g. subagent) without mutating a shared cache."""
        reg = ToolRegistry(thread_pool_executor=self._thread_pool_executor)
        reg._tools = dict(self._tools)
        reg._loaded_deferred_tools = set(self._loaded_deferred_tools)
        return reg

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def unregister_by_prefix(self, prefix: str) -> int:
        """Unregister all tools whose name starts with prefix. Returns count removed."""
        to_remove = [k for k in self._tools if k.startswith(prefix)]
        for k in to_remove:
            self._tools.pop(k, None)
        return len(to_remove)
    
    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools
    
    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]
    
    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """
        Execute a tool by name with given parameters.

        Args:
            name: Tool name.
            params: Tool parameters.

        Returns:
            Tool execution result as string. 错误时返回标准化格式：
            [RETRYABLE] 可重试 / [ERROR] 永久性错误，便于 LLM 区分。
        """
        tool = self._tools.get(name)
        if not tool:
            return format_tool_not_found(name)

        # Deferred MCP：首次调用时在此加载完整 schema 并立即执行，避免把 [DEFERRED_TOOL_LOADED] 当结果返回给模型/用户
        if getattr(tool, "deferred", False) and name not in self._loaded_deferred_tools:
            self._loaded_deferred_tools.add(name)
            logger.info(
                "[DeferredMCP] 首次调用 '{}'：已标记 schema 就绪，立即用当前参数执行",
                name,
            )

        try:
            errors = tool.validate_params(params)
            if errors:
                return format_invalid_params(name, errors)
            return await tool.execute(**params)
        except Exception as e:
            logger.exception(f"Tool execution failed: {name}")
            return format_tool_error(name, e)

    async def execute_with_retry(
        self,
        name: str,
        params: dict[str, Any],
        max_attempts: int = 3,
    ) -> str:
        """
        Execute a tool with automatic retry for retryable errors.

        Args:
            name: Tool name.
            params: Tool parameters.
            max_attempts: Maximum retry attempts (default 3).

        Returns:
            Tool execution result as string.
        """
        last_result = None
        for attempt in range(1, max_attempts + 1):
            result = await self.execute(name, params)
            last_result = result

            # If not a retryable error, return immediately
            if not is_retryable_result(result):
                return result

            # Last attempt - don't retry
            if attempt >= max_attempts:
                return result

            # Retryable error - backoff and retry
            backoff = 0.5 * attempt
            logger.info(f"[ToolRetry] {name} failed (attempt {attempt}/{max_attempts}), retrying in {backoff}s...")
            await asyncio.sleep(backoff)

        return last_result or format_tool_not_found(name)

    def set_thread_pool(self, executor: ThreadPoolExecutor) -> None:
        """设置线程池执行器（用于CPU密集型任务）"""
        self._thread_pool_executor = executor

    async def execute_in_thread_pool(self, name: str, params: dict[str, Any], executor: "ThreadPoolExecutor | None" = None) -> str:
        """
        在线程池中执行工具（用于CPU密集型或阻塞IO任务）。

        Args:
            name: Tool name.
            params: Tool parameters.
            executor: Thread pool executor to use. If None, uses the default one.

        Returns:
            Tool execution result as string.
        """
        effective_executor = executor or self._thread_pool_executor
        if not effective_executor:
            # 如果没有线程池，回退到普通异步执行
            return await self.execute(name, params)

        tool = self._tools.get(name)
        if not tool:
            return format_tool_not_found(name)

        if getattr(tool, "deferred", False) and name not in self._loaded_deferred_tools:
            self._loaded_deferred_tools.add(name)
            logger.info(
                "[DeferredMCP] 首次调用 '{}'（线程池路径）：已标记 schema 就绪，立即执行",
                name,
            )

        try:
            errors = tool.validate_params(params)
            if errors:
                return format_invalid_params(name, errors)

            # 将异步工具包装为在线程池中同步执行
            # 注意：不能在已有 event loop 的线程中用 asyncio.run()，需创建新 loop
            loop = asyncio.get_running_loop()
            params_copy = dict(params)

            def _run_async_in_thread() -> str:
                thread_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(thread_loop)
                try:
                    return thread_loop.run_until_complete(tool.execute(**params_copy))
                finally:
                    thread_loop.close()

            return await loop.run_in_executor(effective_executor, _run_async_in_thread)
        except Exception as e:
            logger.exception(f"Tool execution failed in thread pool: {name}")
            return format_tool_error(name, e)
    
    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())
    
    def __len__(self) -> int:
        return len(self._tools)
    
    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def search_tools(
        self,
        query: str,
        mcp_server_scopes: dict[str, list[str]],
        max_results: int = 8,
    ) -> list[dict[str, Any]]:
        """
        Search and rank MCP tools by relevance to a query.

        Scoring (MCP tools only; built-in tools are handled in _select_tools_default):
        - Tool name exact keyword match: +10
        - Tool name substring match: +5
        - Tool description keyword match: +3
        - Server scope keyword match: +5 per keyword
        MCP tools get a 1.5x score multiplier.

        Args:
            query: User message to match against
            mcp_server_scopes: server_id → [scope keywords] mapping
            max_results: Maximum number of results to return

        Returns:
            List of tool schema dicts sorted by relevance score (descending)
        """
        if not query or not query.strip():
            return []

        query_lower = query.lower()
        query_tokens = re.findall(r"[a-z0-9]{2,}", query_lower)
        if not query_tokens:
            return []

        scored: list[tuple[float, str]] = []

        for tool in self._tools.values():
            # Only score MCP tools; built-in tools handled separately
            server_id = getattr(tool, "server_id", None)
            if not server_id:
                continue

            score = 0.0
            name_lower = tool.name.lower()
            desc_lower = tool.description.lower()

            # Name and description keyword matching
            for token in query_tokens:
                if token in name_lower:
                    score += 10 if token == name_lower else 5
                if token in desc_lower:
                    score += 3

            # Server scope bonus (already matched in _select_tools_default, here as tiebreaker)
            if server_id in mcp_server_scopes:
                for scope_kw in mcp_server_scopes[server_id]:
                    if scope_kw in query_lower:
                        score += 5

            score *= 1.5

            if score > 0:
                scored.append((score, tool.name))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, name in scored[:max_results]:
            tool = self._tools.get(name)
            if tool:
                results.append(tool.to_schema())

        logger.debug(f"[ToolSearch] query={query_lower!r}, tokens={query_tokens}, scored={len(scored)}, returned={len(results)}")
        return results
