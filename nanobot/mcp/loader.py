"""MCP client loader - connects to MCP servers and registers tools with the agent."""

import asyncio
import os
import sys
import time
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
    """Convert tool name to safe identifier - must match _safe_mcp_id in loop.py."""
    import re
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name) or "mcp"


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
        self._lazy_transport_by_server: dict[str, Any] = {}  # connect_lazy 时 server_id -> transport ctx
        self._adapters: list[Any] = []
        # 连接缓存：减少频繁建立/断开连接的开销
        self._connection_cache: dict[str, tuple[Any, list[Any], float]] = {}  # server_id -> (session, tools, timestamp)
        self._cache_ttl: float = 300.0  # 5 分钟 TTL

    def _get_enabled_mcps(self) -> list[Any]:
        return [m for m in self.mcps_config if getattr(m, "enabled", True)]

    def _find_mcp_cfg(self, server_id: str, enabled_only: bool = False) -> Any | None:
        pool = self._get_enabled_mcps() if enabled_only else (self.mcps_config or [])
        for cfg in pool:
            sid = getattr(cfg, "id", "") or _safe_id(getattr(cfg, "name", "mcp"))
            if sid == server_id:
                return cfg
        return None

    async def _await_cancelled_connect_task(self, task: asyncio.Task, server_id: str) -> None:
        """wait_for 超时后取消连接任务，在同一任务栈内收尾，减轻 anyio cancel scope 跨任务错误。"""
        try:
            await task
        except asyncio.CancelledError:
            pass
        except BaseExceptionGroup as eg:
            logger.debug("MCP %s: connect cancel cleanup: %s", server_id, eg)
        except Exception as e:
            logger.debug("MCP %s: connect cancel cleanup: %s", server_id, e)

    async def _connect_one_timed(
        self, mcp_cfg: Any, server_id: str, transport: str, timeout: float
    ) -> tuple[Any | None, Any | None]:
        async def _runner() -> tuple[Any | None, Any | None]:
            return await self._connect_one(mcp_cfg, server_id, transport)

        task = asyncio.create_task(_runner())
        try:
            return await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("MCP %s: connect timed out after %ss", server_id, timeout)
            task.cancel()
            await self._await_cancelled_connect_task(task, server_id)
            return None, None
        except asyncio.CancelledError:
            task.cancel()
            await self._await_cancelled_connect_task(task, server_id)
            raise

    async def _safe_close_session_and_transport(
        self, session: Any | None, transport_ctx: Any | None
    ) -> None:
        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except BaseException:
                pass
        if transport_ctx is not None:
            try:
                await transport_ctx.__aexit__(None, None, None)
            except (RuntimeError, BaseExceptionGroup) as e:
                msg = str(e).lower()
                if "different task" in msg or "already running" in msg:
                    logger.debug("MCP transport __aexit__ (benign): %s", str(e)[:160])
                elif isinstance(e, BaseExceptionGroup):
                    logger.debug("MCP transport __aexit__ (group): %s", e)
                else:
                    logger.debug("MCP transport __aexit__: %s", e)
            except Exception as e:
                logger.debug("MCP transport __aexit__: %s", e)

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
                    session, ctx = await self._connect_one(mcp_cfg, server_id, transport)
                    try:
                        return session is not None
                    finally:
                        await self._safe_close_session_and_transport(session, ctx)

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
    
    async def _connect_and_register(self, tools_registry: Any, connect_timeout: float = 8.0) -> int:
        """Connect to each enabled MCP server and register tools."""
        from nanobot.agent.tools.mcp import McpToolAdapter

        total = 0
        for mcp_cfg in self._get_enabled_mcps():
            server_id = getattr(mcp_cfg, "id", "") or _safe_id(getattr(mcp_cfg, "name", "mcp"))
            transport = (getattr(mcp_cfg, "transport", "stdio") or "stdio").lower()
            try:
                session, transport_ctx = await self._connect_one_timed(
                    mcp_cfg, server_id, transport, connect_timeout
                )
                if session is None:
                    continue
                if transport_ctx is not None:
                    self._transport_refs.append(transport_ctx)
                self._sessions[server_id] = session
                result = await asyncio.wait_for(session.list_tools(), timeout=10.0)
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
            except asyncio.TimeoutError:
                logger.warning(f"MCP server {server_id}: connection/list timeout after {connect_timeout}s, skipping")
            except (asyncio.CancelledError, BaseExceptionGroup) as e:
                logger.warning(f"MCP server {server_id}: connection cancelled/failed ({type(e).__name__}), skipping")
            except Exception as e:
                logger.warning(f"MCP server {server_id} connection failed: {e}")

        return total
    
    async def _connect_one(
        self, mcp_cfg: Any, server_id: str, transport: str
    ) -> tuple[Any | None, Any | None]:
        """连接单个 MCP；返回 (ClientSession, transport_async_ctx)。由调用方负责 __aexit__ 或登记 _transport_refs。"""
        if transport == "stdio":
            cmd = getattr(mcp_cfg, "command", None)
            if not cmd:
                logger.warning(f"MCP {server_id}: stdio requires command")
                return None, None
            args = list(getattr(mcp_cfg, "args", None) or [])
            env = getattr(mcp_cfg, "env", None) or None
            # Windows: npx/npm 等是 .cmd 批处理，stdio 无法直接执行，需用 cmd /c 包装
            if sys.platform == "win32" and not os.path.dirname(cmd):
                cmd, args = "cmd", ["/c", cmd] + args
            params = StdioServerParameters(command=cmd, args=args, env=env)
            stdio_ctx = stdio_client(params)
            read_write = await stdio_ctx.__aenter__()
            try:
                session = ClientSession(read_write[0], read_write[1])
                await session.__aenter__()
                await session.initialize()
                return session, stdio_ctx
            except BaseException:
                exc_type, exc_val, exc_tb = sys.exc_info()
                try:
                    await stdio_ctx.__aexit__(exc_type, exc_val, exc_tb)
                except BaseException:
                    pass
                raise

        if transport == "streamable_http":
            url = getattr(mcp_cfg, "url", None)
            if not url:
                logger.warning(f"MCP {server_id}: streamable_http requires url")
                return None, None

            headers = getattr(mcp_cfg, "headers", None) or {}

            try:
                from mcp.client.streamable_http import streamablehttp_client
            except ImportError:
                logger.warning(f"MCP {server_id}: streamable_http client not available")
                return None, None

            logger.debug(f"MCP {server_id}: connecting to streamable_http: {url}")
            streamable_ctx = None
            try:
                streamable_ctx = streamablehttp_client(url, headers=headers if headers else None)
                read_stream, write_stream, _ = await streamable_ctx.__aenter__()
                session = ClientSession(read_stream, write_stream)
                await session.__aenter__()
                await session.initialize()
                return session, streamable_ctx
            except Exception as e:
                logger.warning(f"MCP {server_id}: streamable_http connection failed: {type(e).__name__}: {e}")
                exc_type, exc_val, exc_tb = sys.exc_info()
                if streamable_ctx is not None:
                    try:
                        await streamable_ctx.__aexit__(exc_type, exc_val, exc_tb)
                    except BaseException:
                        pass
                raise

        if transport in ("http", "sse"):
            url = getattr(mcp_cfg, "url", None)
            if not url:
                logger.warning(f"MCP {server_id}: http/sse requires url")
                return None, None
            try:
                from mcp.client.sse import sse_client
            except ImportError:
                logger.warning(f"MCP {server_id}: SSE client not available (pip install httpx-sse)")
                return None, None
            sse_ctx = sse_client(url)
            read_write = await sse_ctx.__aenter__()
            try:
                session = ClientSession(read_write[0], read_write[1])
                await session.__aenter__()
                await session.initialize()
                return session, sse_ctx
            except BaseException:
                exc_type, exc_val, exc_tb = sys.exc_info()
                try:
                    await sse_ctx.__aexit__(exc_type, exc_val, exc_tb)
                except BaseException:
                    pass
                raise

        logger.warning(f"MCP {server_id}: unsupported transport {transport}")
        return None, None
    
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
        self._lazy_transport_by_server.clear()
        self._adapters.clear()

    # ====================== 按需加载支持 ======================

    async def list_tools_ephemeral(self, server_id: str, timeout: float = 10.0) -> list[Any] | None:
        """
        仅用于配置页「刷新工具」：连接、list_tools、再完整关闭 session + transport。
        避免只关 session 不关 streamable_http/stdio 上下文导致 anyio 报错与连接泄漏。
        整条链路跑在单一 Task 内，超时通过 cancel 该任务收尾。
        """
        if not MCP_AVAILABLE:
            return None
        mcp_cfg = self._find_mcp_cfg(server_id, enabled_only=False)
        if not mcp_cfg:
            logger.warning("MCP %s: not found in config", server_id)
            return None
        transport = (getattr(mcp_cfg, "transport", "stdio") or "stdio").lower()

        async def _run() -> list[Any] | None:
            session, ctx = await self._connect_one(mcp_cfg, server_id, transport)
            if session is None:
                return None
            try:
                lr = await session.list_tools()
                return list(lr.tools)
            finally:
                await self._safe_close_session_and_transport(session, ctx)

        task = asyncio.create_task(_run())
        try:
            # 使用传入的 timeout，不再额外增加缓冲时间
            return await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("MCP %s: list_tools_ephemeral exceeded %ss", server_id, timeout)
            task.cancel()
            await self._await_cancelled_connect_task(task, server_id)
            return None
        except Exception as e:
            logger.warning("MCP %s: list_tools_ephemeral failed: %s", server_id, e)
            return None

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

        # 检查缓存
        cached = self._get_cached_connection(server_id)
        if cached is not None:
            session, tools = cached
            # 验证 session 仍然有效
            if session is not None:
                logger.debug(f"MCP {server_id}: using cached connection")
                self._sessions[server_id] = session
                return session, tools
            self._connection_cache.pop(server_id, None)

        mcp_cfg = self._find_mcp_cfg(server_id, enabled_only=True)
        if not mcp_cfg:
            logger.warning(f"MCP {server_id}: not found in config or disabled")
            return None

        transport = (getattr(mcp_cfg, "transport", "stdio") or "stdio").lower()

        session: Any | None = None
        transport_ctx: Any | None = None
        try:
            session, transport_ctx = await self._connect_one_timed(
                mcp_cfg, server_id, transport, timeout
            )
            if session is None:
                return None
            if transport_ctx is not None:
                self._transport_refs.append(transport_ctx)
                self._lazy_transport_by_server[server_id] = transport_ctx

            result = await session.list_tools()
            self._sessions[server_id] = session
            # 缓存连接
            self._cache_connection(server_id, session, list(result.tools))
            return session, result.tools
        except Exception as e:
            logger.warning(f"MCP {server_id}: lazy connection failed: {e}")
            self._lazy_transport_by_server.pop(server_id, None)
            if transport_ctx is not None:
                try:
                    self._transport_refs.remove(transport_ctx)
                except ValueError:
                    pass
            await self._safe_close_session_and_transport(session, transport_ctx)
            return None

    async def disconnect_lazy(self, server_id: str) -> None:
        """按需断开：先关 session，再关对应 transport，并从 _transport_refs 移除。"""
        session = self._sessions.pop(server_id, None)
        ctx = self._lazy_transport_by_server.pop(server_id, None)
        if ctx is not None:
            try:
                self._transport_refs.remove(ctx)
            except ValueError:
                pass
        await self._safe_close_session_and_transport(session, ctx)

    def _is_cache_valid(self, server_id: str) -> bool:
        """检查缓存是否有效（未过期）。"""
        if server_id not in self._connection_cache:
            return False
        _, _, timestamp = self._connection_cache[server_id]
        return (time.time() - timestamp) < self._cache_ttl

    def _get_cached_connection(self, server_id: str) -> tuple[Any, list[Any]] | None:
        """获取缓存的连接（如果有效）。"""
        if not self._is_cache_valid(server_id):
            self._connection_cache.pop(server_id, None)
            return None
        session, tools, _ = self._connection_cache[server_id]
        return session, tools

    def _cache_connection(self, server_id: str, session: Any, tools: list[Any]) -> None:
        """缓存连接。"""
        self._connection_cache[server_id] = (session, tools, time.time())

    def invalidate_cache(self, server_id: str | None = None) -> None:
        """
        使缓存失效。

        Args:
            server_id: 指定服务器，不传则全部失效
        """
        if server_id:
            self._connection_cache.pop(server_id, None)
        else:
            self._connection_cache.clear()
