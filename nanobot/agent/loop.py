"""Agent loop: the core processing engine."""

import asyncio
import json
import time
from datetime import datetime
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
from nanobot.agent.tools.claude_code import ClaudeCodeTool
from nanobot.agent.tools.self_update import SelfUpdateTool
from nanobot.agent.subagent import SubagentManager
from nanobot.session.manager import SessionManager
from nanobot.utils.helpers import parse_session_key, estimate_tokens


TOOL_KEYWORDS = {
    "read_file": [],
    "write_file": [],
    "edit_file": ["编辑", "修改", "edit", "replace"],
    "list_dir": ["列表", "目录", "list", "dir", "文件夹"],
    "exec": [],
    "web_search": ["搜索", "search", "查找", "百度", "google", "bing"],
    "web_fetch": ["网页", "url", "http", "fetch", "获取网页"],
    "message": ["发送消息", "通知", "message", "send"],
    "remember": ["记住", "remember", "记忆"],
    "spawn": ["子代理", "subagent", "spawn", "并行"],
    "cron": ["定时", "计划", "cron", "schedule"],
    "claude_code": ["claude code", "claude-code", "代码实现", "实现功能", "写代码"],
    "self_update": ["自更新", "自我更新", "self-update", "self_update", "evolve", "自我进化", "更新自己", "更新nanobot", "重启nanobot", "拉取最新", "更新并重启", "git pull", "git push"],
}

for tool, keywords in TOOL_KEYWORDS.items():
    TOOL_KEYWORDS[tool] = [kw.lower() for kw in keywords]

ESSENTIAL_TOOLS = ["read_file", "write_file", "exec", "remember"]

# Marker embedded in assistant reply when limits are hit.
# Used to detect "continue" commands from the user in the next turn.
LIMIT_REACHED_MARKER = "<!-- LIMIT_REACHED -->"
CONTINUE_KEYWORDS = {
    "继续", "continue", "重置", "reset",
    "继续执行", "继续任务", "go on", "proceed",
}


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
        subagent_model: str | None = None,
        max_iterations: int = 40,
        max_execution_time: int = 600,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        filesystem_config: "FilesystemToolConfig | None" = None,
        claude_code_config: "ClaudeCodeConfig | None" = None,
        cron_service: "CronService | None" = None,
        message_timeout: float = 300.0,
        max_history_messages: int = 30,
        tool_result_max_length: int = 2000,
        smart_tool_selection: bool = True,
        system_prompt_max_tokens: int = 5000,
        memory_max_tokens: int = 2000,
    ):
        from nanobot.config.schema import ExecToolConfig, FilesystemToolConfig, ClaudeCodeConfig
        from nanobot.cron.service import CronService
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.subagent_model = subagent_model or ""
        self.max_iterations = max_iterations
        self.max_execution_time = max_execution_time
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.filesystem_config = filesystem_config or FilesystemToolConfig()
        self.claude_code_config = claude_code_config or ClaudeCodeConfig()
        self.cron_service = cron_service
        self.message_timeout = message_timeout
        self.max_history_messages = max_history_messages
        self.tool_result_max_length = tool_result_max_length
        self._smart_tool_selection = smart_tool_selection
        self._system_prompt_max_tokens = system_prompt_max_tokens
        self._memory_max_tokens = memory_max_tokens
        
        token_budget = {
            "total": system_prompt_max_tokens,
            "memory": memory_max_tokens,
        }
        self.context = ContextBuilder(workspace, token_budget=token_budget)
        self.sessions = SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            sessions=self.sessions,
        )
        
        from nanobot.claude_code.manager import ClaudeCodeManager
        self.claude_code_manager = ClaudeCodeManager(
            workspace=workspace,
            bus=bus,
            default_timeout=self.claude_code_config.default_timeout,
            max_concurrent_tasks=self.claude_code_config.max_concurrent_tasks,
        )
        
        # 如果传入了独立的 subagent_model，更新 SubagentManager 使用它
        if subagent_model:
            self.subagents.model = subagent_model
        
        self._running = False
        self._mcp_loaded = False
        self._mcp_loop_id: int | None = None
        self._mcp_fail_time: float = 0.0
        self._cancel_event = asyncio.Event()
        self._register_default_tools()
        self._init_mcp_loader()


    def _select_tools_for_message(self, message: str, max_tools: int = 12) -> list[dict[str, Any]]:
        """
        根据消息内容选择相关工具定义。
        
        Args:
            message: 用户消息内容
            max_tools: 最大返回工具数量
        
        Returns:
            选中的工具定义列表
        """
        if not self._smart_tool_selection:
            return self.tools.get_definitions()
        
        message_lower = message.lower()
        selected_names = set(ESSENTIAL_TOOLS)
        
        for tool_name, keywords in TOOL_KEYWORDS.items():
            if tool_name in selected_names:
                continue
            if any(kw in message_lower for kw in keywords):
                selected_names.add(tool_name)
                if len(selected_names) >= max_tools:
                    break
        
        if len(selected_names) < max_tools:
            for tool_name in self.tools.tool_names:
                if tool_name.startswith("mcp_"):
                    selected_names.add(tool_name)
                    if len(selected_names) >= max_tools:
                        break
        
        definitions = []
        for name in selected_names:
            tool = self.tools.get(name)
            if tool:
                definitions.append(tool.to_schema())
        
        return definitions

    def update_agent_params(
        self,
        max_iterations: int | None = None,
        max_execution_time: int | None = None,
        max_history_messages: int | None = None,
        tool_result_max_length: int | None = None,
        smart_tool_selection: bool | None = None,
        system_prompt_max_tokens: int | None = None,
        memory_max_tokens: int | None = None,
    ) -> None:
        """Hot-update agent params without restart."""
        if max_iterations is not None:
            self.max_iterations = max(1, min(max_iterations, 200))
        if max_execution_time is not None:
            self.max_execution_time = max(0, max_execution_time)
        if max_history_messages is not None:
            self.max_history_messages = max(1, min(max_history_messages, 200))
        if tool_result_max_length is not None:
            self.tool_result_max_length = max(100, tool_result_max_length)
        if smart_tool_selection is not None:
            self._smart_tool_selection = smart_tool_selection
        if system_prompt_max_tokens is not None:
            self._system_prompt_max_tokens = max(1000, system_prompt_max_tokens)
            self.context.update_token_budget(total=self._system_prompt_max_tokens)
        if memory_max_tokens is not None:
            self._memory_max_tokens = max(200, memory_max_tokens)
            self.context.update_token_budget(memory=self._memory_max_tokens)

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
        if not self.subagent_model:
            self.subagents.model = model

    def update_subagent_model(self, subagent_model: str) -> None:
        """Update subagent model at runtime (hot config). Empty string means use main model."""
        self.subagent_model = subagent_model
        self.subagents.model = subagent_model if subagent_model else self.model
    
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
        
        # Claude Code tool (for complex coding tasks)
        claude_code_tool = ClaudeCodeTool(manager=self.claude_code_manager)
        self.tools.register(claude_code_tool)

        # Self-update tool (for self-evolution: git push + restart)
        self.tools.register(SelfUpdateTool(workspace=ws))
    
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
                    timeout_text = (
                        "⏳ 当前任务处理时间较长。"
                        "如有 Claude Code 任务正在执行，它将继续在后台运行，"
                        "完成后会自动通知您结果。\n\n"
                        "您也可以继续提问，我会记住本次对话上下文。"
                    )
                    # 保存会话历史，确保超时后上下文不丢失
                    try:
                        session_key = getattr(msg, "session_key", f"{msg.channel}:{msg.chat_id}")
                        session = self.sessions.get_or_create(session_key)
                        session.add_message("user", msg.content)
                        session.add_message("assistant", timeout_text)
                        self.sessions.save(session)
                    except Exception as _e:
                        logger.warning(f"Failed to save session on timeout: {_e}")
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=timeout_text,
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

    def cancel_current_request(self) -> None:
        """Cancel the current running request by setting the cancel event."""
        # Always try to set the event (set() is idempotent and safe to call multiple times)
        # This ensures that multiple clicks on stop button will correctly propagate the cancellation
        self._cancel_event.set()
        logger.info(f"Agent request cancellation requested, event is now set: {self._cancel_event.is_set()}")

    async def _check_cancelled(self) -> None:
        """Check if cancellation was requested and raise if so."""
        if self._cancel_event.is_set():
            self._cancel_event.clear()
            logger.info("Cancellation detected, event cleared, raising CancelledError")
            raise asyncio.CancelledError("Request cancelled by user")

    def _reset_cancel_event(self) -> None:
        """Reset the cancel event for a new request."""
        logger.debug(f"Resetting cancel event, current state: {self._cancel_event.is_set()}")
        self._cancel_event.clear()
    
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

        # 优先检查是否是对 Claude Code 决策请求的回复
        # 当 manager 有挂起的决策时，该消息直接路由给对应的 Future，不走 LLM
        if self.claude_code_manager.resolve_decision(msg.session_key, msg.content):
            logger.info(f"Message routed as Claude Code decision reply for {msg.session_key}")
            return None

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
            spawn_tool.set_media(msg.media if msg.media else [])
        
        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(msg.channel, msg.chat_id)
        
        claude_code_tool = self.tools.get("claude_code")
        if isinstance(claude_code_tool, ClaudeCodeTool):
            claude_code_tool.set_context(msg.channel, msg.chat_id)
        
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
        
        # Detect "继续" / "continue" command after a limit-reached pause.
        # If the last assistant message has the LIMIT_REACHED_MARKER and the user
        # is asking to continue, replace the bare keyword with an explicit instruction
        # so the LLM understands it should resume the interrupted task.
        current_message = msg.content
        if msg.content.strip().lower() in CONTINUE_KEYWORDS:
            last_msgs = session.messages[-3:] if session.messages else []
            last_assistant = next(
                (m for m in reversed(last_msgs) if m.get("role") == "assistant"),
                None,
            )
            if last_assistant and LIMIT_REACHED_MARKER in str(last_assistant.get("content", "")):
                logger.info("Detected continue command after limit-reached pause; resetting iteration budget")
                current_message = (
                    "请继续执行上一条消息中未完成的任务，从上次中断的地方接着做，"
                    "不需要重新解释已完成的部分。"
                )

        # Build initial messages (use get_history for LLM-formatted messages)
        mirror_attack_level = msg.metadata.get("attack_level") if msg.metadata else None
        messages = self.context.build_messages(
            history=session.get_history(max_messages=self.max_history_messages),
            current_message=current_message,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            mirror_attack_level=mirror_attack_level,
        )

        # Inline image recognition: 用视觉模型识别图片，将最后一条用户消息替换为纯文本
        # 避免主模型（可能不支持视觉）收到图片时回复“无法查看图片”
        if msg.media:
            img_desc = None
            if self.subagent_model and self.subagent_model != self.model:
                progress_cb = msg.metadata.get("progress_callback")
                if progress_cb:
                    try:
                        progress_cb({"type": "tool_start", "name": "image_recognition", "arguments": {"images": len(msg.media)}})
                    except Exception:
                        pass
                img_desc = await self._inline_image_recognition(msg.media, user_text=msg.content)
                if progress_cb:
                    try:
                        progress_cb({"type": "tool_end", "name": "image_recognition", "arguments": {}, "result": (img_desc or "")[:200]})
                    except Exception:
                        pass

            last_user = messages[-1] if messages and messages[-1].get("role") == "user" else None
            if last_user:
                if img_desc:
                    text_content = msg.content.strip() or "请描述这张图片。"
                    last_user["content"] = f"{text_content}\n\n[图片识别结果]\n{img_desc}"
                else:
                    has_vision_config = bool(self.subagent_model and self.subagent_model != self.model)
                    fallback = (
                        "用户发送了图片。图片识别失败（请检查 DashScope API key 及子 Agent 模型配置），"
                        "请用户用文字描述图片内容。"
                        if has_vision_config
                        else "用户发送了图片。系统未配置视觉模型，请用户用文字描述图片内容，或前往设置配置子 Agent 模型（如 dashscope/qwen-vl-plus）。"
                    )
                    last_user["content"] = f"{msg.content}\n\n[{fallback}]" if msg.content.strip() else fallback
        
        # Agent loop
        iteration = 0
        final_content = None
        exit_reason: str | None = None  # "time" | "iterations" | "loop" | None=normal
        tool_steps: list[dict[str, Any]] = []
        usage_acc = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        loop_start = time.monotonic()
        tool_result_max_len = self.tool_result_max_length

        def _truncate(val: str, max_len: int | None = None) -> str:
            if not isinstance(val, str):
                val = str(val)
            limit = max_len or tool_result_max_len
            return val[:limit] + "…" if len(val) > limit else val

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
            
            await self._check_cancelled()

            # 时间限制: 防止 runaway，与 max_iterations 互补
            if self.max_execution_time > 0:
                elapsed = time.monotonic() - loop_start
                if elapsed >= self.max_execution_time:
                    logger.info("Max execution time %ds reached (elapsed %.0fs)", self.max_execution_time, elapsed)
                    exit_reason = "time"
                    break

            # Notify progress: thinking / about to call LLM
            progress = msg.metadata.get("progress_callback")
            if progress:
                try:
                    progress({"type": "thinking"})
                except Exception:
                    pass
            # Call LLM
            selected_tools = self._select_tools_for_message(msg.content)
            response = await self.provider.chat(
                messages=messages,
                tools=selected_tools,
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
                    
                    # Set progress callback for claude_code tool
                    if tool_call.name == "claude_code":
                        claude_code_tool = self.tools.get("claude_code")
                        if claude_code_tool and hasattr(claude_code_tool, "set_progress_callback"):
                            claude_code_tool.set_progress_callback(progress)
                    
                    args_str = json.dumps(tool_call.arguments)
                    logger.debug(f"Executing tool: {tool_call.name} with arguments: {args_str}")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    
                    # Clear progress callback after execution
                    if tool_call.name == "claude_code":
                        claude_code_tool = self.tools.get("claude_code")
                        if claude_code_tool and hasattr(claude_code_tool, "set_progress_callback"):
                            claude_code_tool.set_progress_callback(None)
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
                    exit_reason = "loop"
                    break
            else:
                # No tool calls, we're done
                final_content = response.content
                break

        if final_content is None and exit_reason is None:
            exit_reason = "iterations"

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

        # Append user-visible limit notice so the user knows why the agent stopped
        # and can explicitly ask to continue.
        if exit_reason in ("iterations", "time"):
            if exit_reason == "iterations":
                reason_zh = f"工具调用次数已达上限（{self.max_iterations} 次）"
            else:
                elapsed_s = int(time.monotonic() - loop_start)
                reason_zh = f"执行时间已达上限（{elapsed_s} 秒 / 上限 {self.max_execution_time} 秒）"
            limit_notice = (
                f"\n\n---\n"
                f"⚠️ **任务已暂停**：{reason_zh}。\n"
                f"如需继续执行，请回复 **继续**；"
                f"或直接描述下一步您希望做什么。"
                f"{LIMIT_REACHED_MARKER}"
            )
            final_content = (final_content or "") + limit_notice

        # Save to session (include tool_steps for UI display)
        user_token_usage = {
            "prompt_tokens": usage_acc["prompt_tokens"],
            "completion_tokens": 0,
            "total_tokens": usage_acc["prompt_tokens"],
        }
        
        user_message_kwargs: dict[str, Any] = {"token_usage": user_token_usage}
        
        if msg.media:
            import base64
            user_images: list[str] = []
            for media_path in msg.media:
                try:
                    with open(media_path, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode("utf-8")
                        ext = Path(media_path).suffix.lower()
                        mime_type = "image/jpeg"
                        if ext in [".png"]:
                            mime_type = "image/png"
                        elif ext in [".gif"]:
                            mime_type = "image/gif"
                        elif ext in [".webp"]:
                            mime_type = "image/webp"
                        user_images.append(f"data:{mime_type};base64,{img_data}")
                except Exception as e:
                    logger.warning(f"Failed to read media file {media_path}: {e}")
            if user_images:
                user_message_kwargs["images"] = user_images
        
        session.add_message("user", msg.content, **user_message_kwargs)
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
    
    async def _inline_image_recognition(
        self,
        media_paths: list[str],
        user_text: str = "",
    ) -> str | None:
        """
        使用视觉模型对图片进行 inline 识别（同步 await）。

        对 DashScope 模型绕过 LiteLLM（LiteLLM 存在已知 Bug #16007 会丢弃图片），
        直接调用 DashScope OpenAI 兼容 API。其他 provider 走正常 provider.chat() 路径。

        Returns:
            图片描述文本，识别失败时返回 None。
        """
        import base64
        import mimetypes as _mimetypes

        vision_model = self.subagent_model if self.subagent_model else self.model

        images: list[dict[str, Any]] = []
        for path_str in media_paths:
            p = Path(path_str)
            mime, _ = _mimetypes.guess_type(path_str)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            try:
                b64 = base64.b64encode(p.read_bytes()).decode()
                images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            except Exception as e:
                logger.warning("Failed to read media file for recognition %s: %s", path_str, e)

        if not images:
            return None

        image_count = len(images)
        task = user_text or (
            f"请详细分析这{image_count}张图片的内容，包括："
            "1) 图片中的主要内容和对象；"
            "2) 场景和环境；"
            "3) 文字信息（如果有）；"
            "4) 任何值得注意的细节。用中文回复。"
        )

        model_lower = vision_model.lower()
        is_dashscope = any(k in model_lower for k in ("dashscope", "qwen"))

        if is_dashscope:
            return await self._dashscope_image_call(images, task, vision_model, image_count)

        user_content: list[dict[str, Any]] = images + [{"type": "text", "text": task}]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "你是一个图片分析助手。请仔细观察并描述图片内容。"},
            {"role": "user", "content": user_content},
        ]
        try:
            response = await self.provider.chat(
                messages=messages,
                tools=None,
                model=vision_model,
            )
            if response.content and response.content.strip():
                logger.info("Inline image recognition completed (%d images, model: %s)", image_count, vision_model)
                return response.content.strip()
            return None
        except Exception as e:
            logger.warning("Inline image recognition failed (model: %s): %s", vision_model, e)
            return None

    async def _dashscope_image_call(
        self,
        images: list[dict[str, Any]],
        task: str,
        vision_model: str,
        image_count: int,
    ) -> str | None:
        """
        直接调用 DashScope OpenAI 兼容 API 进行图片识别。
        绕过 LiteLLM 的 DashScope 适配层（存在已知 Bug 会丢弃 image_url 内容）。
        """
        import os
        import httpx

        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not api_key:
            from nanobot.config.loader import load_config
            cfg = load_config()
            api_key = (cfg.providers.dashscope.api_key or "").strip()
        if not api_key:
            logger.warning("DashScope API key not found, cannot perform image recognition")
            return None

        model_name = vision_model
        if "/" in model_name:
            model_name = model_name.split("/", 1)[1]

        user_content: list[dict[str, Any]] = images + [{"type": "text", "text": task}]
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "你是一个图片分析助手。请仔细观察并描述图片内容。"},
                {"role": "user", "content": user_content},
            ],
        }

        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content and content.strip():
                logger.info(
                    "DashScope image recognition completed (%d images, model: %s)",
                    image_count, model_name,
                )
                return content.strip()
            return None
        except httpx.HTTPStatusError as e:
            logger.warning(
                "DashScope image recognition HTTP error (model: %s): %s %s",
                model_name, e.response.status_code, e.response.text[:500],
            )
            return None
        except Exception as e:
            logger.warning("DashScope image recognition failed (model: %s): %s", model_name, e)
            return None
    
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
        
        claude_code_tool = self.tools.get("claude_code")
        if isinstance(claude_code_tool, ClaudeCodeTool):
            claude_code_tool.set_context(origin_channel, origin_chat_id)
        
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
            history=session.get_history(max_messages=self.max_history_messages),
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
        tool_result_max_len = self.tool_result_max_length

        def _truncate(val: str, max_len: int | None = None) -> str:
            if not isinstance(val, str):
                val = str(val)
            limit = max_len or tool_result_max_len
            return val[:limit] + "…" if len(val) > limit else val

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
            
            await self._check_cancelled()
            
            if self.max_execution_time > 0:
                elapsed = time.monotonic() - loop_start
                if elapsed >= self.max_execution_time:
                    logger.info("System msg: max execution time %ds reached", self.max_execution_time)
                    break

            selected_tools = self._select_tools_for_message(msg.content)
            response = await self.provider.chat(
                messages=messages,
                tools=selected_tools,
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
        extra_metadata: dict[str, Any] | None = None,
        media: list[str] | None = None,
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier.
            channel: Source channel (for context).
            chat_id: Source chat ID (for context).
            progress_callback: Optional callback for streaming progress (tool_start, tool_end, etc.).
            extra_metadata: Optional extra metadata (e.g. attack_level for mirror bian sessions).
            media: Optional list of local file paths for images to include in the message.

        Returns:
            The agent's response.
        """
        self._reset_cancel_event()
        logger.info(f"Starting new request, cancel event reset, state: {self._cancel_event.is_set()}")
        
        if ":" in session_key:
            try:
                parsed_channel, parsed_chat_id = parse_session_key(session_key)
                channel = parsed_channel
                chat_id = parsed_chat_id
            except ValueError:
                logger.warning(f"Invalid session key '{session_key}', fallback to {channel}:{chat_id}")

        metadata = {}
        if progress_callback:
            metadata["progress_callback"] = progress_callback
        if extra_metadata:
            metadata.update(extra_metadata)

        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            metadata=metadata,
            media=media or [],
        )

        if media and not content:
            return await self._handle_image_only(media, session_key, channel, chat_id)

        try:
            await self._check_cancelled()
            response = await self._process_message(msg)
            return response.content if response else ""
        except asyncio.CancelledError:
            logger.info("Agent request was cancelled")
            return ""
    
    async def _handle_image_only(
        self,
        media: list[str],
        session_key: str,
        channel: str,
        chat_id: str,
    ) -> str:
        """Handle the case when user sends only images without any text."""
        logger.info(f"Processing image-only message with {len(media)} images")

        key = session_key
        self.sessions.get_or_create(key)

        import base64
        user_images: list[str] = []
        for media_path in media:
            try:
                with open(media_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                    ext = Path(media_path).suffix.lower()
                    mime_type = "image/jpeg"
                    if ext in [".png"]:
                        mime_type = "image/png"
                    elif ext in [".gif"]:
                        mime_type = "image/gif"
                    elif ext in [".webp"]:
                        mime_type = "image/webp"
                    user_images.append(f"data:{mime_type};base64,{img_data}")
            except Exception as e:
                logger.warning(f"Failed to read media file {media_path}: {e}")

        if not user_images:
            return "无法读取图片文件"

        user_message_kwargs: dict[str, Any] = {
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "images": user_images,
        }

        session = self.sessions.get(key)
        if session:
            session.add_message("user", "[图片消息]", **user_message_kwargs)
            self.sessions.save(session)

        desc = await self._inline_image_recognition(media)
        if desc:
            if session:
                session.add_message("assistant", desc)
                self.sessions.save(session)
            return desc
        return "无法识别图片内容，请确认已配置视觉模型（如 dashscope/qwen-vl-plus）。"
