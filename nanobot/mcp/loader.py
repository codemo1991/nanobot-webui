"""MCP client loader - connects to MCP servers and registers tools with the agent.

Inspired by hermes-agent's robust MCP architecture:
- Dedicated background event loop in a daemon thread
- Long-lived persistent connections (no frequent reconnect overhead)
- Safe environment filtering for stdio subprocesses
- Credential stripping in error messages
- Sampling support (MCP servers can request LLM completions)
- Dynamic tool discovery via notifications/tools/list_changed
"""

import asyncio
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    ClientSession = None  # type: ignore
    StdioServerParameters = None  # type: ignore
    stdio_client = None  # type: ignore

# Optional HTTP transport
try:
    from mcp.client.streamable_http import streamablehttp_client
    _MCP_HTTP_AVAILABLE = True
except ImportError:
    try:
        from mcp.client.streamablehttp_client import streamablehttp_client
        _MCP_HTTP_AVAILABLE = True
    except ImportError:
        _MCP_HTTP_AVAILABLE = False
        streamablehttp_client = None  # type: ignore

# Sampling support
try:
    from mcp.types import SamplingCapability, SamplingMessage, TextContent
    _MCP_SAMPLING = True
except ImportError:
    _MCP_SAMPLING = False
    SamplingCapability = None  # type: ignore
    SamplingMessage = None  # type: ignore
    TextContent = None  # type: ignore

# Notification types for dynamic discovery
try:
    from mcp.types import ServerNotification, ToolListChangedNotification
    _MCP_NOTIFICATIONS = True
except ImportError:
    _MCP_NOTIFICATIONS = False
    ServerNotification = None  # type: ignore
    ToolListChangedNotification = None  # type: ignore

# message_handler support
try:
    import inspect
    _MCP_MESSAGE_HANDLER = "message_handler" in inspect.signature(ClientSession).parameters if ClientSession else False
except Exception:
    _MCP_MESSAGE_HANDLER = False


def _safe_id(name: str) -> str:
    """Convert tool name to safe identifier - must match _safe_mcp_id in loop.py."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name) or "mcp"


# ---------------------------------------------------------------------------
# Security helpers (inspired by hermes-agent)
# ---------------------------------------------------------------------------

_SAFE_ENV_KEYS = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR",
})

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


def _build_safe_env(user_env: dict | None) -> dict:
    """Build a filtered environment dict for stdio subprocesses."""
    env = {}
    for key, value in os.environ.items():
        if key in _SAFE_ENV_KEYS or key.startswith("XDG_"):
            env[key] = value
    if user_env:
        env.update(user_env)
    return env


def _sanitize_error(text: str) -> str:
    """Strip credential-like patterns from error text before returning to LLM."""
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", text)


def _prepend_path(env: dict, directory: str) -> dict:
    """Prepend directory to env PATH if not already present."""
    updated = dict(env or {})
    if not directory:
        return updated
    existing = updated.get("PATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if directory not in parts:
        parts = [directory, *parts]
    updated["PATH"] = os.pathsep.join(parts) if parts else directory
    return updated


def _resolve_stdio_command(command: str, env: dict) -> tuple[str, dict]:
    """Resolve a stdio MCP command against the subprocess environment."""
    resolved = os.path.expanduser(str(command).strip())
    resolved_env = dict(env or {})

    if os.sep not in resolved:
        path_arg = resolved_env.get("PATH")
        which_hit = shutil.which(resolved, path=path_arg)
        if which_hit:
            resolved = which_hit
        elif resolved in {"npx", "npm", "node"}:
            home = os.path.expanduser("~")
            candidates = [
                os.path.join(home, ".nanobot", "node", "bin", resolved),
                os.path.join(home, ".local", "bin", resolved),
            ]
            for candidate in candidates:
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    resolved = candidate
                    break

    cmd_dir = os.path.dirname(resolved)
    if cmd_dir:
        resolved_env = _prepend_path(resolved_env, cmd_dir)

    return resolved, resolved_env


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class McpToolLoader:
    """
    Loads MCP servers from config, connects to them in a background event loop,
    and provides tools for the agent.
    """

    def __init__(
        self,
        mcps_config: list[Any],
        workspace: Path,
        sampling_callback: Any | None = None,
    ) -> None:
        self.mcps_config = mcps_config or []
        self.workspace = workspace
        self.sampling_callback = sampling_callback
        self._tools_registry: Any | None = None

        # Background event loop for all MCP I/O
        self._mcp_loop: asyncio.AbstractEventLoop | None = None
        self._mcp_thread: threading.Thread | None = None
        self._loop_lock = threading.Lock()

        # Persistent server state (server_id -> metadata dict)
        self._servers: dict[str, dict[str, Any]] = {}
        self._servers_lock = threading.Lock()

        # Lifecycle
        self._closed = False
        self._pending_connections: dict[str, asyncio.Future] = {}

    def _ensure_mcp_loop(self) -> None:
        """Start the background event loop thread if not already running."""
        with self._loop_lock:
            if self._closed:
                raise RuntimeError("McpToolLoader is closed")
            if self._mcp_loop is not None and self._mcp_loop.is_running():
                return
            if self._mcp_thread is not None and self._mcp_thread.is_alive():
                self._mcp_thread.join(timeout=1)
            self._mcp_loop = asyncio.new_event_loop()
            self._mcp_thread = threading.Thread(
                target=self._mcp_loop.run_forever,
                name="mcp-event-loop",
                daemon=True,
            )
            self._mcp_thread.start()
            logger.debug("MCP background event loop started")

    async def _run_on_mcp_loop(self, coro, timeout: float | None = None) -> Any:
        """Schedule a coroutine on the MCP event loop and await its result."""
        self._ensure_mcp_loop()
        loop = self._mcp_loop
        if loop is None or not loop.is_running():
            raise RuntimeError("MCP event loop is not running")

        async def _with_timeout():
            if timeout is not None:
                return await asyncio.wait_for(coro, timeout=timeout)
            return await coro

        future = asyncio.run_coroutine_threadsafe(_with_timeout(), loop)
        return await asyncio.wrap_future(future)

    def _get_enabled_mcps(self) -> list[Any]:
        return [m for m in self.mcps_config if getattr(m, "enabled", True)]

    def _find_mcp_cfg(self, server_id: str, enabled_only: bool = False) -> Any | None:
        pool = self._get_enabled_mcps() if enabled_only else (self.mcps_config or [])
        for cfg in pool:
            sid = getattr(cfg, "id", "") or _safe_id(getattr(cfg, "name", "mcp"))
            if sid == server_id:
                return cfg
        return None

    # -----------------------------------------------------------------------
    # Connection lifecycle (runs on background loop)
    # -----------------------------------------------------------------------

    async def _connect_one(
        self, mcp_cfg: Any, server_id: str, transport: str
    ) -> tuple[Any | None, Any | None]:
        """Connect to a single MCP server on the background event loop."""
        if not _MCP_AVAILABLE:
            return None, None

        session: Any | None = None
        transport_ctx: Any | None = None

        if transport == "stdio":
            cmd = getattr(mcp_cfg, "command", None)
            if not cmd:
                logger.warning(f"MCP {server_id}: stdio requires command")
                return None, None
            args = list(getattr(mcp_cfg, "args", None) or [])
            user_env = getattr(mcp_cfg, "env", None)
            safe_env = _build_safe_env(user_env)
            cmd, safe_env = _resolve_stdio_command(cmd, safe_env)

            if sys.platform == "win32" and not os.path.dirname(cmd):
                cmd, args = "cmd", ["/c", cmd] + args

            params = StdioServerParameters(command=cmd, args=args, env=safe_env if safe_env else None)
            transport_ctx = stdio_client(params)
            read_write = await transport_ctx.__aenter__()
            try:
                session = ClientSession(read_write[0], read_write[1], **_session_kwargs(self.sampling_callback, self._make_message_handler(server_id) if _MCP_MESSAGE_HANDLER else None))
                await session.__aenter__()
                await session.initialize()
                return session, transport_ctx
            except BaseException:
                exc_type, exc_val, exc_tb = sys.exc_info()
                try:
                    await transport_ctx.__aexit__(exc_type, exc_val, exc_tb)
                except BaseException:
                    pass
                raise

        if transport == "streamable_http":
            url = getattr(mcp_cfg, "url", None)
            if not url:
                logger.warning(f"MCP {server_id}: streamable_http requires url")
                return None, None
            if not _MCP_HTTP_AVAILABLE:
                logger.warning(f"MCP {server_id}: streamable_http client not available")
                return None, None

            headers = getattr(mcp_cfg, "headers", None) or {}
            logger.debug(f"MCP {server_id}: connecting to streamable_http: {url}")
            try:
                transport_ctx = streamablehttp_client(url, headers=headers if headers else None)
                read_stream, write_stream, _ = await transport_ctx.__aenter__()
                session = ClientSession(read_stream, write_stream, **_session_kwargs(self.sampling_callback, self._make_message_handler(server_id) if _MCP_MESSAGE_HANDLER else None))
                await session.__aenter__()
                await session.initialize()
                return session, transport_ctx
            except Exception:
                exc_type, exc_val, exc_tb = sys.exc_info()
                if transport_ctx is not None:
                    try:
                        await transport_ctx.__aexit__(exc_type, exc_val, exc_tb)
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
                logger.warning(f"MCP {server_id}: SSE client not available")
                return None, None
            sse_ctx = sse_client(url)
            read_write = await sse_ctx.__aenter__()
            try:
                session = ClientSession(read_write[0], read_write[1], **_session_kwargs(self.sampling_callback, self._make_message_handler(server_id) if _MCP_MESSAGE_HANDLER else None))
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

    async def _disconnect_one(self, server_id: str) -> None:
        """Disconnect a single server on the background loop."""
        with self._servers_lock:
            state = self._servers.pop(server_id, None)
        if not state:
            return
        session = state.get("session")
        transport = state.get("transport")
        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except BaseException:
                pass
        if transport is not None:
            try:
                await transport.__aexit__(None, None, None)
            except BaseException:
                pass
        logger.info(f"MCP {server_id}: disconnected")

    # -----------------------------------------------------------------------
    # Dynamic discovery (runs on background loop)
    # -----------------------------------------------------------------------

    def _make_message_handler(self, server_id: str):
        """Build a message_handler for ClientSession to handle dynamic tool updates."""
        async def _handler(message):
            try:
                if isinstance(message, Exception):
                    logger.debug(f"MCP message handler ({server_id}): exception: {message}")
                    return
                if _MCP_NOTIFICATIONS and isinstance(message, ServerNotification):
                    root = getattr(message, "root", None)
                    if isinstance(root, ToolListChangedNotification):
                        logger.info(f"MCP server '{server_id}': received tools/list_changed notification")
                        await self._refresh_tools(server_id)
            except Exception:
                logger.exception(f"Error in MCP message handler for '{server_id}'")
        return _handler

    async def _refresh_tools(self, server_id: str) -> None:
        """Re-fetch tools from the server and update the registry."""
        with self._servers_lock:
            state = self._servers.get(server_id)
        if not state:
            return
        session = state.get("session")
        if session is None:
            return
        try:
            result = await session.list_tools()
            new_tools = result.tools if hasattr(result, "tools") else []
            # Update stored tools
            state["tools"] = new_tools
            # Re-register with registry if available
            registry = self._tools_registry
            if registry is not None:
                from nanobot.agent.tools.mcp import McpLazyToolAdapter
                # Deregister all existing MCP tools for this server
                registry.unregister_by_prefix(f"mcp_{server_id}_")
                # Register new ones
                lazy_tools: dict[str, McpLazyToolAdapter] = {}
                for tool in new_tools:
                    tool_name = tool.name
                    adapter = McpLazyToolAdapter(
                        server_id=server_id,
                        tool_name=tool_name,
                        description=tool.description or "",
                        parameters=tool.inputSchema or {"type": "object", "properties": {}},
                        mcp_loader=self,
                        lazy_tools=lazy_tools,
                    )
                    lazy_tools[tool_name] = adapter
                    registry.register(adapter)
                logger.info(f"MCP {server_id}: dynamically refreshed {len(lazy_tools)} tool(s)")
        except Exception as e:
            logger.warning(f"MCP {server_id}: dynamic tool refresh failed: {e}")

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def register_tools_async(self, tools_registry: Any) -> int:
        """
        Register lazy-loading MCP tool adapters.
        Does NOT eagerly connect to servers (connections happen on first use).
        """
        if not _MCP_AVAILABLE:
            logger.debug("MCP package not installed. Run: pip install mcp")
            return 0
        self._tools_registry = tools_registry
        from nanobot.agent.tools.mcp import McpLazyToolAdapter

        total = 0
        for mcp_cfg in self._get_enabled_mcps():
            server_id = getattr(mcp_cfg, "id", "") or _safe_id(getattr(mcp_cfg, "name", "mcp"))
            tools = getattr(mcp_cfg, "tools", None) or []

            lazy_tools: dict[str, McpLazyToolAdapter] = {}
            if tools:
                for tool_cfg in tools:
                    if isinstance(tool_cfg, dict):
                        tool_name = tool_cfg.get("name")
                        description = tool_cfg.get("description", "")
                        parameters = tool_cfg.get("parameters", {}) or {"type": "object", "properties": {}}
                    else:
                        tool_name = getattr(tool_cfg, "name", None)
                        description = getattr(tool_cfg, "description", "")
                        parameters = getattr(tool_cfg, "parameters", {}) or {"type": "object", "properties": {}}
                    if not tool_name:
                        continue
                    adapter = McpLazyToolAdapter(
                        server_id=server_id,
                        tool_name=tool_name,
                        description=description,
                        parameters=parameters,
                        mcp_loader=self,
                        lazy_tools=lazy_tools,
                    )
                    lazy_tools[tool_name] = adapter
                    tools_registry.register(adapter)
                    total += 1
                logger.info(f"MCP {server_id}: registered {len(lazy_tools)} lazy tools (yaml)")
            else:
                # No yaml tools: skip placeholder registration.
                logger.debug(f"MCP {server_id}: no yaml tools, skipping lazy registration")

        return total

    async def connect_lazy(self, server_id: str, timeout: float = 10.0) -> tuple[Any, list[Any]] | None:
        """On-demand connect a single MCP server on the background loop."""
        if not _MCP_AVAILABLE:
            logger.warning("MCP package not installed")
            return None

        with self._servers_lock:
            state = self._servers.get(server_id)
            if state is not None and state.get("session") is not None:
                return state["session"], state.get("tools", [])
            pending = self._pending_connections.get(server_id)
            if pending is None:
                pending = asyncio.get_running_loop().create_future()
                self._pending_connections[server_id] = pending
                should_connect = True
            else:
                should_connect = False

        if not should_connect:
            return await pending

        mcp_cfg = self._find_mcp_cfg(server_id, enabled_only=True)
        if not mcp_cfg:
            logger.warning(f"MCP {server_id}: not found in config or disabled")
            pending.set_result(None)
            with self._servers_lock:
                self._pending_connections.pop(server_id, None)
            return None

        transport = (getattr(mcp_cfg, "transport", "stdio") or "stdio").lower()

        async def _do_connect():
            session, transport_ctx = await self._connect_one(mcp_cfg, server_id, transport)
            if session is None:
                return None
            result = await session.list_tools()
            tools = list(result.tools) if hasattr(result, "tools") else []
            with self._servers_lock:
                self._servers[server_id] = {
                    "session": session,
                    "transport": transport_ctx,
                    "tools": tools,
                    "config": mcp_cfg,
                }
            logger.info(f"MCP {server_id}: connected ({len(tools)} tools available)")
            return session, tools

        try:
            result = await self._run_on_mcp_loop(_do_connect(), timeout=timeout)
            pending.set_result(result)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"MCP {server_id}: lazy connection timed out after {timeout}s")
            pending.set_result(None)
            return None
        except Exception as e:
            logger.warning(f"MCP {server_id}: lazy connection failed: {e}")
            pending.set_result(None)
            return None
        finally:
            with self._servers_lock:
                self._pending_connections.pop(server_id, None)

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool on the background loop and return the raw result."""
        async def _do_call():
            with self._servers_lock:
                state = self._servers.get(server_id)
            if not state or state.get("session") is None:
                raise RuntimeError(f"MCP {server_id}: not connected")
            session = state["session"]
            return await session.call_tool(tool_name, arguments)

        try:
            return await self._run_on_mcp_loop(_do_call())
        except Exception as e:
            sanitized = _sanitize_error(str(e))
            logger.warning(f"MCP {server_id}/{tool_name}: call failed: {sanitized}")
            raise

    async def disconnect_lazy(self, server_id: str) -> None:
        """Disconnect a single server."""
        try:
            await self._run_on_mcp_loop(self._disconnect_one(server_id))
        except Exception as e:
            logger.debug(f"MCP {server_id}: disconnect error: {e}")

    async def health_check(self, timeout: float = 3.0) -> dict[str, bool]:
        """Quick health check for all enabled MCP servers."""
        results: dict[str, bool] = {}
        if not _MCP_AVAILABLE:
            return results

        for mcp_cfg in self._get_enabled_mcps():
            server_id = getattr(mcp_cfg, "id", "") or _safe_id(getattr(mcp_cfg, "name", "mcp"))
            try:
                result = await self.connect_lazy(server_id, timeout=timeout)
                results[server_id] = result is not None
            except asyncio.TimeoutError:
                results[server_id] = False
            except Exception:
                results[server_id] = False

        return results

    async def list_tools_ephemeral(self, server_id: str, timeout: float = 10.0) -> list[Any] | None:
        """Connect, list tools, and disconnect (for config refresh UI).
        Uses an isolated connection that does not touch the persistent state cache.
        """
        async def _do():
            mcp_cfg = self._find_mcp_cfg(server_id, enabled_only=False)
            if not mcp_cfg:
                return None
            transport = (getattr(mcp_cfg, "transport", "stdio") or "stdio").lower()
            session, transport_ctx = await self._connect_one(mcp_cfg, server_id, transport)
            if session is None:
                return None
            try:
                result = await session.list_tools()
                return list(result.tools) if hasattr(result, "tools") else []
            finally:
                if session is not None:
                    await session.__aexit__(None, None, None)
                if transport_ctx is not None:
                    await transport_ctx.__aexit__(None, None, None)

        try:
            return await self._run_on_mcp_loop(_do(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"MCP {server_id}: list_tools_ephemeral exceeded {timeout}s")
            return None
        except Exception as e:
            logger.warning(f"MCP {server_id}: list_tools_ephemeral failed: {e}")
            return None

    async def close(self) -> None:
        """Close all MCP sessions and the background event loop."""
        if not _MCP_AVAILABLE:
            return

        with self._servers_lock:
            server_ids = list(self._servers.keys())

        for sid in server_ids:
            try:
                await self._run_on_mcp_loop(self._disconnect_one(sid))
            except Exception as e:
                logger.debug(f"MCP {sid}: close error: {e}")

        # Clear persistent state
        with self._servers_lock:
            self._servers.clear()

        # Stop background loop
        with self._loop_lock:
            self._closed = True
            loop = self._mcp_loop
            self._mcp_loop = None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
            if self._mcp_thread is not None and self._mcp_thread.is_alive():
                self._mcp_thread.join(timeout=5)
        self._mcp_thread = None
        logger.debug("MCP background event loop stopped")


def _session_kwargs(sampling_callback: Any | None, message_handler: Any | None) -> dict:
    """Build kwargs for ClientSession (sampling + message_handler)."""
    kwargs: dict = {}
    if _MCP_SAMPLING and sampling_callback is not None:
        kwargs["sampling_callback"] = sampling_callback
        kwargs["sampling_capabilities"] = SamplingCapability()
    if _MCP_MESSAGE_HANDLER and message_handler is not None:
        kwargs["message_handler"] = message_handler
    return kwargs
