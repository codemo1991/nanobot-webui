"""MCP client loader - connects to MCP servers and registers tools with the agent."""

import asyncio
import sys
from pathlib import Path
from typing import Any

from loguru import logger

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    ClientSession = None  # type: ignore
    StdioServerParameters = None  # type: ignore
    stdio_client = None  # type: ignore


def _safe_id(name: str) -> str:
    """Convert tool name to safe identifier (alphanumeric, underscore)."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


class McpToolLoader:
    """
    Loads MCP servers from config, connects to them, and provides tools for the agent.

    When mcp package is not installed, all methods no-op gracefully.
    """

    def __init__(self, mcps_config: list[Any], workspace: Path) -> None:
        self.mcps_config = mcps_config or []
        self.workspace = workspace
        self._sessions: dict[str, Any] = {}
        self._transport_refs: list[Any] = []  # Keep transport contexts alive
        self._adapters: list[Any] = []

    def _get_enabled_mcps(self) -> list[Any]:
        return [m for m in self.mcps_config if getattr(m, "enabled", True)]

    async def health_check(self, timeout: float = 3.0) -> dict[str, bool]:
        """
        Quick health check for all enabled MCP servers.
        Returns dict of server_id -> is_healthy.
        Uses timeout to prevent hanging on unresponsive servers.
        """
        results: dict[str, bool] = {}
        if not MCP_AVAILABLE:
            return results

        for mcp_cfg in self._get_enabled_mcps():
            server_id = getattr(mcp_cfg, "id", "") or _safe_id(getattr(mcp_cfg, "name", "mcp"))
            transport = (getattr(mcp_cfg, "transport", "stdio") or "stdio").lower()

            try:
                async def check_one():
                    session = await self._connect_one(mcp_cfg, server_id, transport)
                    if session:
                        await session.close()
                        return True
                    return False

                result = await asyncio.wait_for(check_one(), timeout=timeout)
                results[server_id] = result
            except asyncio.TimeoutError:
                logger.warning(f"MCP {server_id}: health check timeout after {timeout}s")
                results[server_id] = False
            except Exception as e:
                logger.warning(f"MCP {server_id}: health check failed: {e}")
                results[server_id] = False

        return results
    
    async def register_tools_async(self, tools_registry: Any) -> int:
        """
        Connect to enabled MCP servers, list their tools, and register adapters.
        Call this from async context (e.g. first message processing).
        Returns number of MCP tools registered.
        """
        if not MCP_AVAILABLE:
            logger.debug("MCP package not installed. Run: pip install nanobot-ai[mcp]")
            return 0
        if self._sessions:
            return len(self._adapters)  # Already loaded
        return await self._connect_and_register(tools_registry)
    
    async def _connect_and_register(self, tools_registry: Any) -> int:
        """Connect to each enabled MCP server and register tools."""
        from nanobot.agent.tools.mcp import McpToolAdapter
        
        total = 0
        for mcp_cfg in self._get_enabled_mcps():
            server_id = getattr(mcp_cfg, "id", "") or _safe_id(getattr(mcp_cfg, "name", "mcp"))
            transport = (getattr(mcp_cfg, "transport", "stdio") or "stdio").lower()
            try:
                session = await self._connect_one(mcp_cfg, server_id, transport)
                if session is None:
                    continue
                self._sessions[server_id] = session
                result = await session.list_tools()
                for tool in result.tools:
                    adapter = McpToolAdapter(
                        server_id=server_id,
                        tool_name=tool.name,
                        description=tool.description or "",
                        parameters=tool.inputSchema or {"type": "object", "properties": {}},
                        session=session,
                    )
                    tools_registry.register(adapter)
                    self._adapters.append(adapter)
                    total += 1
                logger.info(f"MCP server {server_id}: registered {len(result.tools)} tools")
            except (asyncio.CancelledError, BaseExceptionGroup) as e:
                logger.warning(f"MCP server {server_id}: connection cancelled/failed ({type(e).__name__}), skipping")
            except Exception as e:
                logger.exception(f"MCP server {server_id} connection failed")
        
        return total
    
    async def _connect_one(self, mcp_cfg: Any, server_id: str, transport: str) -> Any | None:
        """Connect to a single MCP server. Returns ClientSession or None."""
        if transport == "stdio":
            cmd = getattr(mcp_cfg, "command", None)
            if not cmd:
                logger.warning(f"MCP {server_id}: stdio requires command")
                return None
            args = getattr(mcp_cfg, "args", None) or []
            params = StdioServerParameters(command=cmd, args=args)
            stdio_ctx = stdio_client(params)
            read_write = await stdio_ctx.__aenter__()
            try:
                session = ClientSession(read_write[0], read_write[1])
                await session.__aenter__()
                await session.initialize()
                self._transport_refs.append(stdio_ctx)
                return session
            except BaseException:
                exc_type, exc_val, exc_tb = sys.exc_info()
                try:
                    await stdio_ctx.__aexit__(exc_type, exc_val, exc_tb)
                except Exception:
                    pass
                raise
        
        if transport == "streamable_http":
            # Streamable HTTP: POST-based, supports both application/json and text/event-stream.
            url = getattr(mcp_cfg, "url", None)
            if not url:
                logger.warning(f"MCP {server_id}: streamable_http requires url")
                return None

            # Get optional headers
            headers = getattr(mcp_cfg, "headers", None) or {}

            try:
                from mcp.client.streamable_http import streamable_http_client
            except ImportError:
                logger.warning(f"MCP {server_id}: streamable_http client not available")
                return None

            logger.debug(f"MCP {server_id}: connecting to streamable_http: {url}")
            try:
                # streamable_http_client accepts url and optional auth headers
                streamable_ctx = streamable_http_client(url, headers=headers if headers else None)
                read_stream, write_stream, _ = await streamable_ctx.__aenter__()
                session = ClientSession(read_stream, write_stream)
                await session.__aenter__()
                await session.initialize()
                self._transport_refs.append(streamable_ctx)  # Only on full success
                return session
            except Exception as e:
                logger.warning(f"MCP {server_id}: streamable_http connection failed: {type(e).__name__}: {e}")
                # Close in same task to avoid "exit cancel scope in different task".
                exc_type, exc_val, exc_tb = sys.exc_info()
                try:
                    await streamable_ctx.__aexit__(exc_type, exc_val, exc_tb)
                except Exception:
                    pass
                raise

        if transport in ("http", "sse"):
            url = getattr(mcp_cfg, "url", None)
            if not url:
                logger.warning(f"MCP {server_id}: http/sse requires url")
                return None
            try:
                from mcp.client.sse import sse_client
            except ImportError:
                logger.warning(f"MCP {server_id}: SSE client not available (pip install httpx-sse)")
                return None
            sse_ctx = sse_client(url)
            read_write = await sse_ctx.__aenter__()
            try:
                session = ClientSession(read_write[0], read_write[1])
                await session.__aenter__()
                await session.initialize()
                self._transport_refs.append(sse_ctx)  # Only on full success
                return session
            except BaseException:
                exc_type, exc_val, exc_tb = sys.exc_info()
                try:
                    await sse_ctx.__aexit__(exc_type, exc_val, exc_tb)
                except Exception:
                    pass
                raise
        
        logger.warning(f"MCP {server_id}: unsupported transport {transport}")
        return None
    
    async def close(self) -> None:
        """Close all MCP sessions and transports."""
        for sid, session in list(self._sessions.items()):
            try:
                await session.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"MCP {sid} close: {e}", exc_info=True)
        for ctx in reversed(self._transport_refs):
            try:
                await ctx.__aexit__(None, None, None)
            except (RuntimeError, BaseExceptionGroup) as e:
                # MCP streamable_http/sse clients use anyio TaskGroup; cleanup must run in
                # the same task as __aenter__. When called from a different task (e.g.
                # reload/shutdown), anyio raises BaseExceptionGroup containing:
                # "Attempted to exit cancel scope in a different task than it was entered in"
                # See: modelcontextprotocol/python-sdk#521
                msg = str(e)
                if "different task" in msg or (isinstance(e, BaseExceptionGroup) and any("different task" in str(x) for x in e.exceptions)):
                    logger.debug("MCP transport close (task mismatch, expected): %s", msg[:80])
                else:
                    logger.debug("MCP transport close: %s", e, exc_info=True)
            except Exception as e:
                logger.debug(f"MCP transport close: {e}", exc_info=True)
        self._sessions.clear()
        self._transport_refs.clear()
        self._adapters.clear()

    # ====================== 按需加载支持 ======================

    async def connect_lazy(self, server_id: str, timeout: float = 3.0) -> tuple[Any, list[Any]] | None:
        """
        按需连接单个 MCP 服务器并获取工具列表。
        返回 (session, tools) 或 None（如果连接失败）。

        Args:
            server_id: MCP 服务器 ID
            timeout: 连接超时时间（秒）

        Returns:
            (session, list[tool]) 或 None
        """
        if not MCP_AVAILABLE:
            logger.warning("MCP package not installed")
            return None

        # 找到对应的 MCP 配置
        mcp_cfg = None
        for cfg in self._get_enabled_mcps():
            sid = getattr(cfg, "id", "") or _safe_id(getattr(cfg, "name", "mcp"))
            if sid == server_id:
                mcp_cfg = cfg
                break

        if not mcp_cfg:
            logger.warning(f"MCP {server_id}: not found in config")
            return None

        transport = (getattr(mcp_cfg, "transport", "stdio") or "stdio").lower()

        try:
            session = await asyncio.wait_for(
                self._connect_one(mcp_cfg, server_id, transport),
                timeout=timeout
            )
            if session is None:
                return None

            # 获取工具列表
            result = await session.list_tools()
            self._sessions[server_id] = session
            return session, result.tools
        except asyncio.TimeoutError:
            logger.warning(f"MCP {server_id}: connection timed out after {timeout}s")
            return None
        except Exception as e:
            logger.warning(f"MCP {server_id}: lazy connection failed: {e}")
            return None

    async def disconnect_lazy(self, server_id: str) -> None:
        """按需断开单个 MCP 服务器连接。"""
        if server_id in self._sessions:
            try:
                session = self._sessions[server_id]
                await session.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"MCP {server_id} lazy disconnect: {e}")
            del self._sessions[server_id]
