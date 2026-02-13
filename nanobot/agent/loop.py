"""Agent loop: the core processing engine."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.memory import RememberTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.memory import RememberTool
from nanobot.agent.subagent import SubagentManager
from nanobot.session.manager import SessionManager
from nanobot.utils.helpers import parse_session_key


class AgentLoop:
    """
    The agent loop is the core processing engine.
    
    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """
    
    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        max_execution_time: int = 600,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        filesystem_config: "FilesystemToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        message_timeout: float = 300.0,  # 5 min max per message
    ):
        from nanobot.config.schema import ExecToolConfig, FilesystemToolConfig
        from nanobot.cron.service import CronService
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.max_execution_time = max_execution_time  # 秒, 0=不限
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.filesystem_config = filesystem_config or FilesystemToolConfig()
        self.cron_service = cron_service
        self.message_timeout = message_timeout
        
        self.context = ContextBuilder(workspace)
        self.sessions = SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
        )
        
        self._running = False
        self._mcp_loaded = False
        self._mcp_loop_id: int | None = None
        self._mcp_fail_time: float = 0.0  # Cooldown after MCP load failure
        self._register_default_tools()
        self._init_mcp_loader()

    def update_agent_params(self, max_iterations: int | None = None, max_execution_time: int | None = None) -> None:
        """Hot-update agent params without restart."""
        if max_iterations is not None:
            self.max_iterations = max(1, min(max_iterations, 200))
        if max_execution_time is not None:
            self.max_execution_time = max(0, max_execution_time)

    def _init_mcp_loader(self) -> None:
        """Initialize MCP tool loader from config."""
        self.mcp_loader = None
        try:
            from nanobot.config.loader import load_config
            from nanobot.mcp.loader import McpToolLoader
            config = load_config()
            mcps = getattr(config, "mcps", None) or []
            if mcps:
                self.mcp_loader = McpToolLoader(mcps, self.workspace)
        except Exception as e:
            logger.warning(f"MCP loader init skipped: {e}", exc_info=True)

    async def reload_mcp_config(self) -> None:
        """
        Reload MCP config and tools (hot-add). Call after MCP create/update/delete.
        Next message will trigger re-registration with fresh config.
        """
        # Unregister existing MCP tools
        removed = self.tools.unregister_by_prefix("mcp_")
        if removed:
            logger.debug(f"MCP: unregistered {removed} tools for reload")
        # Close old loader (releases connections)
        if self.mcp_loader:
            try:
                await self.mcp_loader.close()
            except BaseException as e:
                logger.debug("MCP loader close: %s", e)
            self.mcp_loader = None
        self._mcp_loaded = False
        self._mcp_loop_id = None
        self._mcp_fail_time = 0.0
        self._init_mcp_loader()
        if self.mcp_loader:
            logger.info("MCP config reloaded (tools will load on next message)")

    def update_model(self, model: str) -> None:
        """Update default model at runtime (hot config)."""
        self.model = model
        self.subagents.model = model
    
    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        ws = str(self.workspace)
        fs_cfg = self.filesystem_config
        # File tools (workspace restriction from config)
        self.tools.register(ReadFileTool(workspace=ws, restrict_to_workspace=fs_cfg.restrict_to_workspace))
        self.tools.register(WriteFileTool(workspace=ws, restrict_to_workspace=fs_cfg.restrict_to_workspace))
        self.tools.register(EditFileTool(workspace=ws, restrict_to_workspace=fs_cfg.restrict_to_workspace))
        self.tools.register(ListDirTool(workspace=ws, restrict_to_workspace=fs_cfg.restrict_to_workspace))
        
        # Memory tool (用户说「记住」时必须调用，否则不会真正写入)
        self.tools.register(RememberTool(workspace=ws))
        
        # Shell tool
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.exec_config.restrict_to_workspace,
        ))
        
        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        
        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)
        
        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)
        
        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
    
    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")
        
        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                
                # Process it (with overall timeout to prevent hanging)
                try:
                    response = await asyncio.wait_for(
                        self._process_message(msg),
                        timeout=self.message_timeout,
                    )
                    if response:
                        await self.bus.publish_outbound(response)
                except asyncio.CancelledError:
                    raise  # Propagate for clean shutdown
                except asyncio.TimeoutError:
                    logger.warning(f"Message processing timed out after {self.message_timeout}s")
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Sorry, processing timed out. Please try again with a shorter request."
                    ))
                except Exception as e:
                    logger.exception("Error processing message")
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                # From consume_inbound (1s poll), not message timeout
                continue
    
    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
        
        Returns:
            The response message, or None if no response needed.
        """
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            return await self._process_system_message(msg)
        
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}")
        
        # Get or create session
        session = self.sessions.get_or_create(msg.session_key)
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)
        
        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(msg.channel, msg.chat_id)
        
        # MCP tools are tied to the event loop that created them. Web uses asyncio.run() per request,
        # so each request gets a new loop; prior MCP sessions become stale (ClosedResourceError).
        # Reload MCP when the loop has changed.
        try:
            current_loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            current_loop_id = None
        if self.mcp_loader and self._mcp_loaded and self._mcp_loop_id is not None and current_loop_id != self._mcp_loop_id:
            logger.debug("Event loop changed, reloading MCP for fresh connections")
            await self.reload_mcp_config()
        # Lazy-load MCP tools on first message (requires async)
        # Cooldown: skip retry for 5 min after a failure to avoid hammering unreachable servers
        mcp_cooldown = 300.0
        now = time.monotonic()
        if self.mcp_loader and not self._mcp_loaded and now >= self._mcp_fail_time:
            try:
                n = await self.mcp_loader.register_tools_async(self.tools)
                if n > 0:
                    self._mcp_loaded = True
                    self._mcp_loop_id = id(asyncio.get_running_loop())
                    logger.info(f"MCP: registered {n} tools from configured servers")
            except (asyncio.CancelledError, BaseExceptionGroup) as e:
                self._mcp_fail_time = now + mcp_cooldown
                logger.warning("MCP connection failed (%s), continuing without MCP tools (retry in 5 min)", type(e).__name__)
            except Exception as e:
                self._mcp_fail_time = now + mcp_cooldown
                logger.exception("MCP tool loading failed")
        
        # Build initial messages (use get_history for LLM-formatted messages)
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        
        # Agent loop
        iteration = 0
        final_content = None
        tool_steps: list[dict[str, Any]] = []
        usage_acc = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        loop_start = time.monotonic()

        def _truncate(val: str, max_len: int = 2000) -> str:
            if not isinstance(val, str):
                val = str(val)
            return val[:max_len] + "…" if len(val) > max_len else val

        def _make_fallback_from_tools(steps: list[dict[str, Any]]) -> str:
            if steps:
                names = [s["name"] for s in steps]
                return (
                    f"I've completed {len(steps)} tool call(s): {', '.join(names)}. "
                    "Please review the results above. If you need a specific summary, try asking again."
                )
            return "I've completed processing but have no response to give."

        def _accumulate_usage(usage: dict[str, Any] | None) -> None:
            if not usage:
                return
            usage_acc["prompt_tokens"] += max(0, int(usage.get("prompt_tokens", 0) or 0))
            usage_acc["completion_tokens"] += max(0, int(usage.get("completion_tokens", 0) or 0))
            usage_acc["total_tokens"] += max(0, int(usage.get("total_tokens", 0) or 0))

        while iteration < self.max_iterations:
            iteration += 1

            # 时间限制: 防止 runaway，与 max_iterations 互补
            if self.max_execution_time > 0:
                elapsed = time.monotonic() - loop_start
                if elapsed >= self.max_execution_time:
                    logger.info("Max execution time %ds reached (elapsed %.0fs)", self.max_execution_time, elapsed)
                    break

            # Notify progress: thinking / about to call LLM
            progress = msg.metadata.get("progress_callback")
            if progress:
                try:
                    progress({"type": "thinking"})
                except Exception:
                    pass
            # Call LLM
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )
            _accumulate_usage(response.usage)

            # Handle tool calls
            if response.has_tool_calls:
                # Add assistant message with tool calls
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)  # Must be JSON string
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )

                # Execute tools and collect steps for UI display
                loop_detected = False
                for tool_call in response.tool_calls:
                    # 循环检测: 连续两次完全相同的调用视为 loop，提前终止
                    call_key = (tool_call.name, json.dumps(tool_call.arguments, sort_keys=True))
                    if len(tool_steps) >= 1:
                        last_key = (tool_steps[-1]["name"], json.dumps(tool_steps[-1]["arguments"], sort_keys=True))
                        if call_key == last_key:
                            logger.info("Loop detected: identical tool call %s, forcing synthesis", tool_call.name)
                            loop_detected = True
                            break
                    progress = msg.metadata.get("progress_callback")
                    if progress:
                        try:
                            progress({"type": "tool_start", "name": tool_call.name, "arguments": tool_call.arguments})
                        except Exception:
                            pass  # Non-blocking; don't fail agent on callback error
                    args_str = json.dumps(tool_call.arguments)
                    logger.debug(f"Executing tool: {tool_call.name} with arguments: {args_str}")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    truncated = _truncate(result)
                    tool_steps.append({
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "result": truncated,
                    })
                    if progress:
                        try:
                            progress({"type": "tool_end", "name": tool_call.name, "arguments": tool_call.arguments, "result": truncated})
                        except Exception:
                            pass
                if loop_detected:
                    break
            else:
                # No tool calls, we're done
                final_content = response.content
                break

        if final_content is None:
            # Hit max_iterations with only tool calls - force one final LLM call without tools
            # to get a proper summary/response instead of generic fallback
            logger.info("Max iterations reached with no text response; requesting final synthesis (no tools)")
            progress = msg.metadata.get("progress_callback")
            if progress:
                try:
                    progress({"type": "thinking"})
                except Exception:
                    pass
            try:
                synth = await self.provider.chat(
                    messages=messages,
                    tools=None,
                    model=self.model,
                )
                _accumulate_usage(synth.usage)
                if synth.content and synth.content.strip():
                    final_content = synth.content.strip()
                else:
                    final_content = _make_fallback_from_tools(tool_steps)
            except Exception as e:
                logger.warning("Final synthesis call failed: %s", e)
                final_content = _make_fallback_from_tools(tool_steps)

        # Save to session (include tool_steps for UI display)
        user_token_usage = {
            "prompt_tokens": usage_acc["prompt_tokens"],
            "completion_tokens": 0,
            "total_tokens": usage_acc["prompt_tokens"],
        }
        session.add_message("user", msg.content, token_usage=user_token_usage)
        session.add_message(
            "assistant",
            final_content,
            tool_steps=tool_steps,
            token_usage=usage_acc.copy(),
        )
        self.sessions.save(session)
        self.sessions.increment_token_usage(
            session.key,
            prompt_tokens=usage_acc["prompt_tokens"],
            completion_tokens=usage_acc["completion_tokens"],
            total_tokens=usage_acc["total_tokens"],
        )
        
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content
        )
    
    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")
        
        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)
        
        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(origin_channel, origin_chat_id)
        
        # Same loop check as _process_message (MCP sessions tied to event loop)
        try:
            _loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            _loop_id = None
        if self.mcp_loader and self._mcp_loaded and self._mcp_loop_id is not None and _loop_id != self._mcp_loop_id:
            await self.reload_mcp_config()
        # Lazy-load MCP tools if not yet loaded (same cooldown as _process_message)
        now = time.monotonic()
        if self.mcp_loader and not self._mcp_loaded and now >= self._mcp_fail_time:
            try:
                n = await self.mcp_loader.register_tools_async(self.tools)
                if n > 0:
                    self._mcp_loaded = True
                    self._mcp_loop_id = id(asyncio.get_running_loop())
            except (asyncio.CancelledError, BaseExceptionGroup):
                self._mcp_fail_time = now + 300.0
                logger.warning("MCP connection failed (system msg), continuing without MCP tools")
            except Exception as e:
                self._mcp_fail_time = now + 300.0
                logger.warning(f"MCP tool loading failed (system message): {e}", exc_info=True)
        
        # Build messages with the announce content
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )
        
        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None
        tool_steps: list[dict[str, Any]] = []
        usage_acc = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        loop_start = time.monotonic()

        def _truncate(val: str, max_len: int = 2000) -> str:
            if not isinstance(val, str):
                val = str(val)
            return val[:max_len] + "…" if len(val) > max_len else val

        def _make_fallback_from_tools(steps: list[dict[str, Any]]) -> str:
            if steps:
                names = [s["name"] for s in steps]
                return (
                    f"I've completed {len(steps)} tool call(s): {', '.join(names)}. "
                    "Please review the results above."
                )
            return "Background task completed."

        def _accumulate_usage(usage: dict[str, Any] | None) -> None:
            if not usage:
                return
            usage_acc["prompt_tokens"] += max(0, int(usage.get("prompt_tokens", 0) or 0))
            usage_acc["completion_tokens"] += max(0, int(usage.get("completion_tokens", 0) or 0))
            usage_acc["total_tokens"] += max(0, int(usage.get("total_tokens", 0) or 0))

        while iteration < self.max_iterations:
            iteration += 1
            if self.max_execution_time > 0:
                elapsed = time.monotonic() - loop_start
                if elapsed >= self.max_execution_time:
                    logger.info("System msg: max execution time %ds reached", self.max_execution_time)
                    break

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )
            _accumulate_usage(response.usage)

            if response.has_tool_calls:
                loop_detected = False
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )

                for tool_call in response.tool_calls:
                    call_key = (tool_call.name, json.dumps(tool_call.arguments, sort_keys=True))
                    if len(tool_steps) >= 1:
                        last_key = (tool_steps[-1]["name"], json.dumps(tool_steps[-1]["arguments"], sort_keys=True))
                        if call_key == last_key:
                            logger.info("System msg: loop detected %s", tool_call.name)
                            loop_detected = True
                            break
                    args_str = json.dumps(tool_call.arguments)
                    logger.debug(f"Executing tool: {tool_call.name} with arguments: {args_str}")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    tool_steps.append({
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "result": _truncate(result),
                    })
                if loop_detected:
                    break
            else:
                final_content = response.content
                break

        if final_content is None:
            try:
                logger.info("System msg: max iterations, requesting final synthesis (no tools)")
                synth = await self.provider.chat(
                    messages=messages,
                    tools=None,
                    model=self.model,
                )
                _accumulate_usage(synth.usage)
                if synth.content and synth.content.strip():
                    final_content = synth.content.strip()
                else:
                    final_content = _make_fallback_from_tools(tool_steps)
            except Exception as e:
                logger.warning("Final synthesis failed: %s", e)
                final_content = _make_fallback_from_tools(tool_steps)

        # Save to session (mark as system message in history)
        user_token_usage = {
            "prompt_tokens": usage_acc["prompt_tokens"],
            "completion_tokens": 0,
            "total_tokens": usage_acc["prompt_tokens"],
        }
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}", token_usage=user_token_usage)
        session.add_message(
            "assistant",
            final_content,
            tool_steps=tool_steps,
            token_usage=usage_acc.copy(),
        )
        self.sessions.save(session)
        self.sessions.increment_token_usage(
            session.key,
            prompt_tokens=usage_acc["prompt_tokens"],
            completion_tokens=usage_acc["completion_tokens"],
            total_tokens=usage_acc["total_tokens"],
        )
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    
    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier.
            channel: Source channel (for context).
            chat_id: Source chat ID (for context).
            progress_callback: Optional callback for streaming progress (tool_start, tool_end, etc.).

        Returns:
            The agent's response.
        """
        if ":" in session_key:
            try:
                parsed_channel, parsed_chat_id = parse_session_key(session_key)
                channel = parsed_channel
                chat_id = parsed_chat_id
            except ValueError:
                logger.warning(f"Invalid session key '{session_key}', fallback to {channel}:{chat_id}")

        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            metadata={"progress_callback": progress_callback} if progress_callback else {},
        )

        response = await self._process_message(msg)
        return response.content if response else ""
