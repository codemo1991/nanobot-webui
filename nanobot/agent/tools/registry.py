"""Tool registry for dynamic tool management."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self, thread_pool_executor: ThreadPoolExecutor | None = None):
        self._tools: dict[str, Tool] = {}
        self._thread_pool_executor = thread_pool_executor
    
    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
    
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
            Tool execution result as string.

        Raises:
            KeyError: If tool not found.
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            return await tool.execute(**params)
        except Exception as e:
            logger.exception(f"Tool execution failed: {name}")
            return f"Error executing {name}: {str(e)}"

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
            return f"Error: Tool '{name}' not found"

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)

            # 将异步工具包装为在线程池中同步执行
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                effective_executor,
                lambda: asyncio.run(tool.execute(**params))
            )
        except Exception as e:
            logger.exception(f"Tool execution failed in thread pool: {name}")
            return f"Error executing {name}: {str(e)}"
    
    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())
    
    def __len__(self) -> int:
        return len(self._tools)
    
    def __contains__(self, name: str) -> bool:
        return name in self._tools
