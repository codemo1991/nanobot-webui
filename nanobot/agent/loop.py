"""Agent loop: the core processing engine."""

import asyncio
import difflib
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.providers.router import ModelRouter, ModelHandle
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.memory import RememberTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.self_update import SelfUpdateTool
from nanobot.agent.tools.get_subagent_results import GetSubagentResultsTool
from nanobot.agent.tool_errors import format_tool_error
from nanobot.agent.subagent import SubagentManager
from nanobot.session.manager import SessionManager
from nanobot.utils.helpers import parse_session_key

# Forward declaration to avoid circular imports
SystemStatusService = "SystemStatusService"


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
    "spawn": [],
    "cron": ["定时", "计划", "cron", "schedule"],
    "self_update": ["自更新", "自我更新", "self-update", "self_update", "evolve", "自我进化", "更新自己", "更新nanobot", "重启nanobot", "拉取最新", "更新并重启", "git pull", "git push"],
}

for tool, keywords in TOOL_KEYWORDS.items():
    TOOL_KEYWORDS[tool] = [kw.lower() for kw in keywords]

ESSENTIAL_TOOLS = ["read_file", "write_file", "exec", "remember", "spawn"]

# Marker embedded in assistant reply when limits are hit.
# Used to detect "continue" commands from the user in the next turn.
LIMIT_REACHED_MARKER = "<!-- LIMIT_REACHED -->"
CONTINUE_KEYWORDS = {
    "继续", "continue", "重置", "reset",
    "继续执行", "继续任务", "go on", "proceed",
}

# 用户回复"扩容"时，自动扩大本轮工具调用上限 20% 并继续执行
EXPAND_KEYWORDS = {"扩容", "扩大容量", "增加工具数", "扩大工具上限"}
EXPAND_RATIO = 1.2


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
        workspace: Path,
        provider: LLMProvider | None = None,
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
        # 并发配置
        max_parallel_tool_calls: int = 5,
        enable_parallel_tools: bool = True,
        thread_pool_size: int = 4,
        thread_pool_tools: list[str] | None = None,
        # 智能并行判断配置
        enable_smart_parallel: bool = True,
        smart_parallel_model: str | None = None,
        # 状态服务（用于监控指标）
        status_service: "SystemStatusService | None" = None,
        # Agent 模板管理器
        agent_template_manager: "AgentTemplateManager | None" = None,
        # 新增：模型路由器（新架构）
        router: ModelRouter | None = None,
        default_profile: str = "smart",
    ):
        from nanobot.config.schema import ExecToolConfig, FilesystemToolConfig, ClaudeCodeConfig
        from nanobot.cron.service import CronService
        self.bus = bus
        self.provider = provider
        self.router = router
        self._default_profile = default_profile
        self.workspace = workspace

        # 新架构：使用 router 获取默认模型
        if router:
            try:
                handle = router.get(default_profile)
                self.model = handle.model
                self._current_handle = handle
            except Exception as e:
                logger.warning(f"Failed to resolve default profile '{default_profile}': {e}")
                # 回退到旧方式
                self.model = model or (provider.get_default_model() if provider else "anthropic/claude-opus-4-6")
                self._current_handle = None
        else:
            self.model = model or (provider.get_default_model() if provider else "anthropic/claude-opus-4-6")
            self._current_handle = None

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

        # 并发配置
        self._max_parallel_tool_calls = max_parallel_tool_calls
        self._enable_parallel_tools = enable_parallel_tools
        self._thread_pool_size = thread_pool_size
        self._thread_pool_tools = thread_pool_tools or ["exec", "spawn"]
        self._enable_smart_parallel = enable_smart_parallel
        self._smart_parallel_model = smart_parallel_model
        self._status_service = status_service

        # 初始化智能并行判断器
        self._smart_parallel_decider = None
        if enable_smart_parallel:
            try:
                from nanobot.services.smart_parallel_decider import SmartParallelDecider
                self._smart_parallel_decider = SmartParallelDecider(
                    provider=provider,
                    model=smart_parallel_model,
                    use_simple_prompt=True,
                )
                logger.info("Smart parallel decider initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize smart parallel decider: {e}")

        # 初始化线程池（用于CPU密集型任务）
        self._thread_pool: asyncio.AbstractEventLoop | None = None
        
        token_budget = {
            "total": system_prompt_max_tokens,
            "memory": memory_max_tokens,
        }
        # 子 agent 模板：未传入时自动创建 AgentTemplateManager
        if agent_template_manager is None:
            from nanobot.config.agent_templates import AgentTemplateManager
            agent_template_manager = AgentTemplateManager(workspace)

        self.context = ContextBuilder(workspace, token_budget=token_budget, agent_template_manager=agent_template_manager)
        self.sessions = SessionManager(workspace)
        # 初始化线程池
        self._thread_pool_executor = ThreadPoolExecutor(max_workers=self._thread_pool_size)
        self.tools = ToolRegistry(thread_pool_executor=self._thread_pool_executor)

        from nanobot.claude_code.manager import ClaudeCodeManager
        self.claude_code_manager = ClaudeCodeManager(
            workspace=workspace,
            bus=bus,
            default_timeout=self.claude_code_config.default_timeout,
            max_concurrent_tasks=self.claude_code_config.max_concurrent_tasks,
        )

        # 子 agent 模板：未传入时自动创建 AgentTemplateManager，其从 SQLite 数据库加载模板（workspace/.nanobot/chat.db 或 ~/.nanobot/chat.db），确保所有渠道（Web/飞书/CLI 等）行为一致
        if agent_template_manager is None:
            from nanobot.config.agent_templates import AgentTemplateManager
            agent_template_manager = AgentTemplateManager(workspace)

        # Backend registry and resolver for subagent execution (after agent_template_manager is ready)
        from nanobot.agent.backend_registry import BackendRegistry
        from nanobot.agent.backend_resolver import BackendResolver
        self._backend_registry = BackendRegistry()
        self._backend_resolver = BackendResolver(agent_template_manager, self._backend_registry)

        # Register Claude Code backend
        from nanobot.agent.backends import claude_code as cc_backend
        cc_backend.register(self.claude_code_manager)

        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            main_model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            sessions=self.sessions,
            max_concurrent_subagents=self._max_parallel_tool_calls,
            claude_code_manager=self.claude_code_manager,
            claude_code_permission_mode=self.claude_code_config.permission_mode,
            status_service=status_service,
            agent_template_manager=agent_template_manager,
            backend_registry=self._backend_registry,
            backend_resolver=self._backend_resolver,
        )
        logger.info(f"[AgentLoop] AgentLoop id: {id(self)}, SubagentManager id: {id(self.subagents)}")

        # 如果传入了独立的 subagent_model，更新 SubagentManager 使用它
        if subagent_model:
            self.subagents.model = subagent_model

        self._running = False
        self._mcp_loaded = False
        self._mcp_loop_id: int | None = None
        self._mcp_fail_time: float = 0.0
        self._cancel_event = asyncio.Event()
        self._cancelled_sessions: set[str] = set()  # 按 session 取消，支持多会话并发
        # 初始化执行链路监控
        from nanobot.monitoring.execution_chain import ExecutionChainMonitor
        from nanobot.storage.execution_chain_repository import ExecutionChainRepository
        db_path = workspace / '.nanobot' / 'chat.db'
        repo = ExecutionChainRepository.get_instance(db_path)
        self._chain_monitor = ExecutionChainMonitor.get_instance(db_path)
        self._chain_monitor.set_repository(repo)
        logger.info(f'[ExecutionChain] Monitor initialized with db: {db_path}')

        self._register_default_tools()
        self._init_mcp_loader()


    def _select_tools_for_message(self, message: str, max_tools: int = 12) -> list[dict[str, Any]]:
        """
        根据消息内容通过关键词匹配选择相关工具定义。

        Args:
            message: 用户消息内容
            max_tools: 最大返回工具数量

        Returns:
            选中的工具定义列表
        """
        if not self._smart_tool_selection:
            return self.tools.get_definitions()
        return self._select_tools_default(message, max_tools)

    def _select_tools_default(self, message: str, max_tools: int = 12) -> list[dict[str, Any]]:
        """
        默认的工具选择逻辑（原有实现）。

        Args:
            message: 用户消息内容
            max_tools: 最大返回工具数量

        Returns:
            选中的工具定义列表
        """
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

    async def close(self) -> None:
        """关闭agent并清理资源"""
        # 关闭线程池
        if hasattr(self, '_thread_pool_executor') and self._thread_pool_executor:
            self._thread_pool_executor.shutdown(wait=True)
            self._thread_pool_executor = None

        # 关闭subagent manager
        if hasattr(self, 'subagents'):
            # 取消所有运行中的子agent任务
            for task_id, task in list(getattr(self.subagents, '_running_tasks', {}).items()):
                if not task.done():
                    task.cancel()
                    logger.info(f"Cancelled subagent task: {task_id}")

    def _normalize_spawn_task(self, task: str) -> str:
        """规范化 spawn 的 task 字符串，用于相似度比较。"""
        if not task or not isinstance(task, str):
            return ""
        s = re.sub(r"\s+", " ", task.strip())
        return s[:200] if len(s) > 200 else s

    def _spawn_tasks_similar(self, task_a: str, task_b: str, threshold: float = 0.78) -> bool:
        """判断两个 spawn task 是否语义相似。"""
        na, nb = self._normalize_spawn_task(task_a), self._normalize_spawn_task(task_b)
        if not na or not nb:
            return na == nb
        if na == nb:
            return True
        ratio = difflib.SequenceMatcher(None, na, nb).ratio()
        return ratio >= threshold

    def _is_duplicate_spawn(
        self,
        spawn_args: dict[str, Any],
        tool_steps: list[dict[str, Any]],
    ) -> bool:
        """检查是否已执行过语义相似的 spawn。vision 模板：本回合已有 vision spawn 即视为重复。"""
        new_template = str(spawn_args.get("template", "minimal")).lower()
        # vision：本回合已有任一 vision spawn 即视为重复（同一图片只需一次分析）
        if new_template == "vision":
            if any(
                s.get("name") == "spawn"
                and str((s.get("arguments") or {}).get("template", "")).lower() == "vision"
                for s in tool_steps
            ):
                return True
        new_task = spawn_args.get("task", "") or ""
        for s in tool_steps:
            if s.get("name") != "spawn":
                continue
            args = s.get("arguments") or {}
            old_task = args.get("task", "") or ""
            old_template = str(args.get("template", "minimal")).lower()
            if new_template != old_template:
                continue
            if self._spawn_tasks_similar(new_task, old_task):
                return True
        return False

    def _deduplicate_tool_calls(self, tool_calls: list) -> list:
        """同一轮内去重。非 spawn 用精确匹配；spawn 用 task 相似度。"""
        deduped = []
        seen_exact: set[tuple[str, str]] = set()
        for tc in tool_calls:
            if tc.name == "spawn":
                args = getattr(tc, "arguments", {}) or {}
                if not isinstance(args, dict):
                    args = {}
                for prev in deduped:
                    if prev.name != "spawn":
                        continue
                    prev_args = getattr(prev, "arguments", {}) or {}
                    if not isinstance(prev_args, dict):
                        continue
                    template_match = str(args.get("template", "")).lower() == str(prev_args.get("template", "")).lower()
                    # vision 模板使用更低阈值 0.55，减少重复 spawn
                    thresh = 0.55 if "vision" in str(args.get("template", "")).lower() else 0.78
                    if self._spawn_tasks_similar(args.get("task", ""), prev_args.get("task", ""), threshold=thresh) and template_match:
                        logger.info(f"[AgentLoop] Deduplicated spawn: task similar to previous")
                        break
                else:
                    deduped.append(tc)
            else:
                key = (tc.name, json.dumps(tc.arguments, sort_keys=True))
                if key not in seen_exact:
                    seen_exact.add(key)
                    deduped.append(tc)
        if len(deduped) < len(tool_calls):
            logger.info(f"[AgentLoop] Deduplicated tool calls: {len(tool_calls)} -> {len(deduped)}")
        return deduped

    def _resolve_vision_exec_groups(
        self,
        tool_calls: list,
        image_files: list[str],
    ) -> list[list] | None:
        """
        检测 spawn(vision) + exec(图片相关) 冲突，返回串行分组（vision 先行）。
        多个 spawn(vision) 按 task 相似度去重，只保留第一个。
        """
        if not image_files or len(tool_calls) < 2:
            return None

        spawn_vision: list = []
        exec_image: list = []
        others: list = []

        for tc in tool_calls:
            name = getattr(tc, "name", None) or (tc.get("name") if isinstance(tc, dict) else None)
            args = getattr(tc, "arguments", None) or (tc.get("arguments", {}) if isinstance(tc, dict) else {})
            if not isinstance(args, dict):
                args = {}

            if name == "spawn" and str(args.get("template", "")).lower() == "vision":
                spawn_vision.append(tc)
            elif name == "exec":
                cmd = args.get("command", "") or ""
                if self._is_exec_image_related(cmd):
                    exec_image.append(tc)
                else:
                    others.append(tc)
            else:
                others.append(tc)

        if not spawn_vision or not exec_image:
            return None

        # 同一批多个 spawn(vision) 只保留第一个（同一图片只需一次视觉分析）
        vision_deduped = spawn_vision[:1]
        if len(spawn_vision) > 1:
            logger.info("[AgentLoop] Vision spawn deduplicated in conflict group: keeping first only (%d -> 1)", len(spawn_vision))

        # 串行组 1: vision 先行，再 exec；其余工具各成一组
        group1 = vision_deduped + exec_image
        groups = [group1]
        for tc in others:
            groups.append([tc])
        return groups

    async def _execute_tool_parallel(
        self,
        tool_calls: list,
        progress: Callable | None = None,
        image_files: list[str] | None = None,
        session_key: str | None = None,
    ) -> list[tuple]:
        """
        并行执行多个工具调用。

        Args:
            tool_calls: 工具调用列表
            progress: 进度回调函数
            image_files: 当前消息的图片文件路径，用于 vision-exec 冲突检测

        Returns:
            按顺序排列的 (tool_call, result) 元组列表
        """
        if not self._enable_parallel_tools or len(tool_calls) <= 1:
            # 串行执行（兼容模式或单工具调用）
            # 记录串行执行指标
            if self._status_service and len(tool_calls) > 0:
                try:
                    for _ in tool_calls:
                        self._status_service.increment_tool_call(is_parallel=False)
                except Exception as e:
                    logger.debug(f"Failed to record serial tool metrics: {e}")
            results = []
            for tool_call in tool_calls:
                await self._check_cancelled(session_key)
                result = await self._execute_single_tool(tool_call, progress)
                results.append((tool_call, result))
            return results

        # vision-exec 冲突检测：有图片且同时存在 spawn(vision) 与 exec(图片相关) 时，串行分组（vision 先行）
        if image_files and len(image_files) > 0:
            groups = self._resolve_vision_exec_groups(tool_calls, image_files)
            if groups:
                logger.info("[AgentLoop] Vision-exec conflict detected, using serial groups (vision first)")
                if self._status_service:
                    try:
                        for _ in tool_calls:
                            self._status_service.increment_tool_call(is_parallel=False)
                    except Exception as e:
                        logger.debug(f"Failed to record serial tool metrics: {e}")
                results = []
                for group in groups:
                    for tool_call in group:
                        await self._check_cancelled(session_key)
                        result = await self._execute_single_tool(tool_call, progress)
                        results.append((tool_call, result))
                return results

        # 智能并行判断
        should_parallel = True
        reason = "default"

        if self._smart_parallel_decider and len(tool_calls) >= 2:
            try:
                decision = await self._smart_parallel_decider.should_parallel(tool_calls)
                should_parallel = decision.get("parallel", True)
                reason = decision.get("reason", "")
                logger.info(f"Smart parallel decision: parallel={should_parallel}, reason={reason}")

                # 如果需要分组执行（串行组）
                groups = decision.get("groups", [])
                if groups and len(groups) > 1:
                    # 分组串行执行
                    results = []
                    for group in groups:
                        for tool_call in group:
                            await self._check_cancelled(session_key)
                            result = await self._execute_single_tool(tool_call, progress)
                            results.append((tool_call, result))
                    return results
            except Exception as e:
                logger.warning(f"Smart parallel decision failed, using default: {e}")

        if not should_parallel:
            # 智能判断认为不适合并行，串行执行
            logger.info(f"Smart parallel disabled: {reason}")
            # 记录串行执行指标
            if self._status_service:
                try:
                    for _ in tool_calls:
                        self._status_service.increment_tool_call(is_parallel=False)
                except Exception as e:
                    logger.debug(f"Failed to record serial tool metrics: {e}")
            results = []
            for tool_call in tool_calls:
                await self._check_cancelled(session_key)
                result = await self._execute_single_tool(tool_call, progress)
                results.append((tool_call, result))
            return results

        # 并行执行多个独立的工具调用
        logger.info(f"并行执行 {len(tool_calls)} 个工具调用")

        # 记录并行执行指标
        if self._status_service:
            try:
                for _ in tool_calls:
                    self._status_service.increment_tool_call(is_parallel=True)
            except Exception as e:
                logger.debug(f"Failed to record parallel tool metrics: {e}")

        async def execute_with_progress(tc):
            try:
                result = await self._execute_single_tool(tc, progress)
                return (tc, result)
            except Exception as e:
                logger.exception(f"Tool execution failed in parallel: {tc.name}")
                return (tc, format_tool_error(tc.name, e))

        # 使用 asyncio.gather 并行执行，捕获异常避免整体失败
        results = await asyncio.gather(
            *[execute_with_progress(tc) for tc in tool_calls],
            return_exceptions=True
        )

        # 处理异常结果（gather return_exceptions=True 时，协程未捕获的异常会作为 Exception 返回）
        processed_results = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Parallel tool execution exception: {r}")
                processed_results.append((None, format_tool_error("parallel_tool", r)))
            else:
                processed_results.append(r)

        # 按原始顺序返回结果
        return processed_results

    async def _execute_single_tool(
        self,
        tool_call,
        progress: Callable | None = None,
        parent_node_id: str = None,
    ) -> str:
        """
        执行单个工具调用。

        Args:
            tool_call: 工具调用对象
            progress: 进度回调函数
            parent_node_id: 父节点ID，用于构建调用树

        Returns:
            工具执行结果
        """
        # 创建工具执行节点
        node = None
        if hasattr(self, '_chain_monitor') and self._chain_monitor.get_current_chain():
            try:
                node = self._chain_monitor.get_current_chain().create_node(
                    node_type='tool',
                    name=tool_call.name,
                    parent_node_id=parent_node_id,
                    arguments=tool_call.arguments
                )
                logger.info(f"[ExecutionChain] Created tool node: {node.node_id}, tool: {tool_call.name}")
            except Exception as e:
                logger.warning(f"[ExecutionChain] Failed to create tool node: {e}")

        # 循环检测
        call_key = (tool_call.name, json.dumps(tool_call.arguments, sort_keys=True))
        logger.info(f"[ToolExecution] Starting tool: {tool_call.name}")
        logger.debug(f"Executing tool: {tool_call.name} with arguments: {json.dumps(tool_call.arguments)}")

        if progress:
            try:
                evt = {"type": "tool_start", "name": tool_call.name, "arguments": tool_call.arguments}
                progress(evt)
                progress({**evt, "type": "tool_execution_start"})  # 细粒度事件流
                logger.info(f"[ToolProgress] Sent tool_start event: {tool_call.name}")
            except Exception:
                pass
        else:
            logger.warning(f"[ToolProgress] No progress callback, tool_start will not be sent for: {tool_call.name}")

        # 记录工具执行开始时间
        tool_start_time = time.time()
        tool_execution_error = None

        # 检查是否需要在线程池中执行
        try:
            use_thread_pool = tool_call.name in self._thread_pool_tools
            if use_thread_pool and self._thread_pool_executor:
                result = await self.tools.execute_in_thread_pool(
                    tool_call.name,
                    tool_call.arguments,
                    self._thread_pool_executor
                )
            else:
                result = await self.tools.execute(tool_call.name, tool_call.arguments)
        except Exception as e:
            tool_execution_error = e
            result = f"Error: {str(e)}"

        # 记录工具执行指标
        execution_time = time.time() - tool_start_time
        logger.info(f"[ToolExecution] Tool '{tool_call.name}' completed in {execution_time:.2f}s, error: {tool_execution_error is not None}")
        if self._status_service:
            try:
                self._status_service.update_tool_execution_time(execution_time)
                if tool_execution_error:
                    self._status_service.increment_failed_tool_call()
            except Exception as e:
                logger.debug(f"Failed to record tool metrics: {e}")

        if progress:
            try:
                truncated = _truncate(result)
                evt = {"type": "tool_end", "name": tool_call.name, "arguments": tool_call.arguments, "result": truncated}
                progress(evt)
                progress({**evt, "type": "tool_execution_end"})  # 细粒度事件流
            except Exception:
                pass

        # 详细日志：工具执行完成
        result_preview = result[:200] + "..." if len(result) > 200 else result
        logger.info(f"[ToolExecution] === TOOL COMPLETED ===")
        logger.info(f"[ToolExecution] tool: {tool_call.name}, duration: {execution_time:.2f}s, has_error: {tool_execution_error is not None}")
        logger.info(f"[ToolExecution] result_preview: {result_preview}")

        # 完成工具执行节点
        if node:
            try:
                error_msg = str(tool_execution_error) if tool_execution_error else None
                self._chain_monitor.get_current_chain().complete_node(
                    node.node_id,
                    result=result,
                    error=error_msg
                )
                logger.info(f"[ExecutionChain] Completed tool node: {node.node_id}, status: {node.status}")
            except Exception as e:
                logger.warning(f"[ExecutionChain] Failed to complete tool node: {e}")

        return result

    def _init_mcp_loader(self) -> None:
        """Initialize MCP tool loader from config - 按需加载模式。"""
        self.mcp_loader = None
        self._mcp_loaded = False  # 标记：是否已完成按需加载
        self._mcp_loop_id = None
        self._mcp_fail_time = 0.0  # 失败冷却时间

        try:
            from nanobot.config.loader import load_config
            from nanobot.mcp.loader import McpToolLoader
            from nanobot.agent.tools.mcp import McpLazyToolAdapter

            config = load_config()
            mcps = getattr(config, "mcps", None) or []
            if not mcps:
                return

            self.mcp_loader = McpToolLoader(mcps, self.workspace)

            # 为每个 MCP 服务器注册懒加载工具
            # 注意：这里只注册工具代理，不建立任何连接
            for mcp_cfg in mcps:
                if not getattr(mcp_cfg, "enabled", True):
                    continue

                server_id = getattr(mcp_cfg, "id", "") or self._safe_mcp_id(getattr(mcp_cfg, "name", "mcp"))

                # 获取该 MCP 的工具列表（从配置中）
                # 由于是懒加载，我们不知道具体有哪些工具
                # 所以我们需要一个通用的方法来获取工具列表
                # 这里我们先尝试获取，如果失败则注册一个通用代理
                tools = getattr(mcp_cfg, "tools", None) or []

                if tools:
                    # 如果配置中指定了工具列表，直接注册
                    lazy_tools: dict[str, McpLazyToolAdapter] = {}
                    for tool_cfg in tools:
                        tool_name = getattr(tool_cfg, "name", None)
                        if not tool_name:
                            continue

                        description = getattr(tool_cfg, "description", "") or f"MCP tool {tool_name}"
                        parameters = getattr(tool_cfg, "parameters", {}) or {"type": "object", "properties": {}}

                        adapter = McpLazyToolAdapter(
                            server_id=server_id,
                            tool_name=tool_name,
                            description=description,
                            parameters=parameters,
                            mcp_loader=self.mcp_loader,
                            lazy_tools=lazy_tools,
                        )
                        lazy_tools[tool_name] = adapter
                        self.tools.register(adapter)

                    logger.info(f"MCP {server_id}: registered {len(lazy_tools)} lazy tools (will connect on first call)")
                else:
                    # 如果没有配置工具列表，我们无法预知有哪些工具
                    # 这种情况下，需要一个不同的策略：让 MCP 服务器自己声明工具
                    # 暂时跳过，等待后续实现
                    logger.debug(f"MCP {server_id}: no tools defined in config, skipping lazy registration")

        except Exception as e:
            logger.warning(f"MCP loader init skipped: {e}", exc_info=True)

    def _safe_mcp_id(self, name: str) -> str:
        """Convert MCP name to safe ID."""
        import re
        return re.sub(r"[^a-zA-Z0-9_-]", "_", name) or "mcp"

    async def reload_mcp_config(self) -> None:
        """
        Reload MCP config and tools (hot-add). Call after MCP create/update/delete.
        使用懒加载模式：重新注册工具代理。
        """
        # Unregister existing MCP tools
        removed = self.tools.unregister_by_prefix("mcp_")
        if removed:
            logger.debug(f"MCP: unregistered {removed} tools for reload")

        # 重置状态
        self._mcp_loaded = False
        self._mcp_loop_id = None
        self._mcp_fail_time = 0.0

        # 重新初始化（会注册新的懒加载工具）
        self._init_mcp_loader()
        if self.mcp_loader:
            logger.info("MCP config reloaded (using lazy load mode)")

    def update_model(self, model: str) -> None:
        """Update default model at runtime (hot config)."""
        self.model = model
        if not self.subagent_model:
            self.subagents.model = model

    def update_subagent_model(self, subagent_model: str) -> None:
        """Update subagent model at runtime (hot config). Empty string means use main model."""
        self.subagent_model = subagent_model
        self.subagents.model = subagent_model if subagent_model else self.model

    def _is_vision_model(self, model: str) -> bool:
        """Check if a model supports vision/images."""
        if not model:
            return False
        model_lower = model.lower()
        vision_keywords = ["vision", "vl", "qwen-vl", "gpt-4v", "gpt-4o", "claude-3-opus", "claude-3-sonnet", "claude-3-5", "claude-4"]
        return any(kw in model_lower for kw in vision_keywords)

    def _is_dashscope_model(self, model: str) -> bool:
        """Check if model is DashScope/Qwen (LiteLLM has known bug #16007 that drops images)."""
        if not model:
            return False
        return any(k in model.lower() for k in ("dashscope", "qwen"))

    def _is_image_file(self, path: str) -> bool:
        """Check if a file is an image based on extension or mime type."""
        import mimetypes
        mime, _ = mimetypes.guess_type(path)
        if mime and mime.startswith("image/"):
            return True
        # Fallback: check common image extensions
        return str(path).lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'))

    def _is_audio_file(self, path: str) -> bool:
        """Check if a file is an audio file based on extension or mime type."""
        import mimetypes
        mime, _ = mimetypes.guess_type(path)
        if mime and mime.startswith("audio/"):
            return True
        # Fallback: check common audio extensions
        return str(path).lower().endswith(('.mp3', '.wav', '.ogg', '.m4a', '.opus', '.webm', '.aac'))

    async def _wait_for_subagents(self, task_ids: list[str], timeout: float = 120.0) -> None:
        """等待指定的所有子 Agent 任务完成。"""
        if not task_ids or not self.subagents:
            return

        start_time = time.monotonic()
        check_interval = 2.0  # 每 2 秒检查一次

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed >= timeout:
                raise asyncio.TimeoutError(f"Timeout waiting for subagents after {timeout}s")

            # 检查哪些任务还在运行
            running = []
            for task_id in task_ids:
                if task_id in self.subagents._running_tasks:
                    running.append(task_id)

            if not running:
                # 所有任务都完成了
                logger.info(f"[BatchMode] All subagent tasks completed: {task_ids}")
                break

            # 等待一段时间再检查
            await asyncio.sleep(check_interval)

    async def _synthesize_batch_results(
        self,
        tool_steps: list[dict[str, Any]],
        subagent_results: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> str | None:
        """综合工具执行结果和子 Agent 结果，调用 LLM 生成最终回复。"""
        if not subagent_results:
            return None

        # 构建综合 prompt
        parts = []

        # 添加工具执行结果
        if tool_steps:
            parts.append("## 工具执行结果\n")
            for step in tool_steps:
                name = step.get("name", "unknown")
                args = step.get("arguments", {})
                result = step.get("result", "")
                parts.append(f"### {name}\n- 参数: {json.dumps(args, ensure_ascii=False)[:200]}\n- 结果: {result[:500]}")

        # 添加子 Agent 结果
        if subagent_results:
            parts.append("\n## 子 Agent 执行结果\n")
            for sa in subagent_results:
                label = sa.get("label", "unknown")
                task = sa.get("task", "")
                result = sa.get("result", "")
                parts.append(f"### {label}\n- 任务: {task[:200]}\n- 结果: {result[:500]}")

        combined = "\n\n".join(parts)

        synthesis_prompt = f"""你是一个协调多任务执行的助手。请综合以下所有执行结果，给用户一个统一的回复。

{combined}

要求：
1. 简洁明了地告诉用户完成了哪些任务
2. 每个任务的关键结果要提到
3. 如果有失败的任务，要说明
4. 不要提及技术细节（如 task_id、subagent 等）"""

        try:
            response = await asyncio.wait_for(
                self.provider.chat(
                    messages=[{"role": "user", "content": synthesis_prompt}],
                    tools=None,
                    model=self.model,
                    max_tokens=1000,
                    temperature=0.5,
                ),
                timeout=30.0,
            )
            return response.content
        except asyncio.TimeoutError:
            logger.warning("[BatchMode] Synthesis LLM call timed out")
            # 超时时返回原始结果拼接
            fallback = "我完成了以下任务：\n"
            for sa in subagent_results:
                fallback += f"- {sa.get('label', '任务')}: {sa.get('result', '')[:200]}...\n"
            return fallback
        except Exception as e:
            logger.warning(f"[BatchMode] Synthesis failed: {e}")
            return None

    def _is_exec_image_related(self, command: str) -> bool:
        """判断 exec 的 command 是否疑似图片识别/分析相关。"""
        if not command or not isinstance(command, str):
            return False
        lower = command.lower()
        keywords = [
            "识别", "ocr", "图片", "图像", "分析图", "识别图",
            "image", "tesseract", "pytesseract", "opencv", "cv2", "pil", "pillow",
        ]
        return any(kw in lower for kw in keywords)

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
        logger.info(f"[AgentLoop] SpawnTool registered with SubagentManager id: {id(self.subagents)}")

        # Get subagent results tool (查询子agent执行结果)
        self.tools.register(GetSubagentResultsTool(sessions=self.sessions))

        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
        # Self-update tool (for self-evolution: git push + restart)
        self.tools.register(SelfUpdateTool(workspace=ws))
    
    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")

        # 按需加载模式：不再在启动时检查 MCP
        # MCP 工具会在首次被调用时才会建立连接

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
                        logger.info(
                            "[AgentLoop] Publishing reply to %s:%s (content_len=%d)",
                            response.channel, response.chat_id, len(response.content or ""),
                        )
                        await self.bus.publish_outbound(response)
                    else:
                        logger.warning(
                            "[AgentLoop] _process_message returned None, no reply for %s:%s",
                            msg.channel, msg.chat_id,
                        )
                except asyncio.CancelledError:
                    raise  # Propagate for clean shutdown
                except asyncio.TimeoutError:
                    try:
                        self._chain_monitor.end_chain(status="timeout")
                    except Exception as _e:
                        logger.error(f"[ExecutionChain] Failed to end chain: {_e}")
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
                    try:
                        self._chain_monitor.end_chain(status="failed")
                    except Exception as _e:
                        logger.error(f"[ExecutionChain] Failed to end chain: {_e}")
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

    def cancel_current_request(self, channel: str = "web", session_id: str | None = None) -> None:
        """Cancel the current running request for the given session."""
        if session_id:
            origin_key = f"{channel}:{session_id}"
            self._cancelled_sessions.add(origin_key)
            logger.info(f"Agent cancellation requested for session {origin_key}")
        else:
            self._cancel_event.set()
            logger.info("Agent cancellation requested (all sessions, backward compat)")

        # 取消指定 session 的子代理任务
        if hasattr(self, 'subagents') and self.subagents:
            if session_id:
                cancelled = self.subagents.cancel_by_session(channel, session_id)
                if cancelled > 0:
                    logger.info(f"Cancelled {cancelled} subagent tasks for session {channel}:{session_id}")
            else:
                count = self.subagents.cancel_all_tasks()
                if count > 0:
                    logger.info(f"Cancelled {count} subagent tasks (all sessions)")

        # 取消该 session 的 Claude Code 任务
        if hasattr(self, 'claude_code_manager') and self.claude_code_manager and session_id:
            cc_cancelled = self.claude_code_manager.cancel_by_session(channel, session_id)
            if cc_cancelled > 0:
                logger.info(f"Cancelled {cc_cancelled} Claude Code tasks for session {channel}:{session_id}")

    async def _check_cancelled(self, session_key: str | None = None) -> None:
        """Check if cancellation was requested for this session and raise if so."""
        if session_key and session_key in self._cancelled_sessions:
            self._cancelled_sessions.discard(session_key)
            logger.info("Cancellation detected for session %s, raising CancelledError", session_key)
            raise asyncio.CancelledError("Request cancelled by user")
        if not session_key and self._cancel_event.is_set():
            self._cancel_event.clear()
            logger.info("Cancellation detected (global), raising CancelledError")
            raise asyncio.CancelledError("Request cancelled by user")

    def _reset_cancel_event(self, session_key: str | None = None) -> None:
        """Reset the cancel event for a new request; optionally clear session from cancelled set."""
        if session_key:
            self._cancelled_sessions.discard(session_key)
        logger.debug(f"Resetting cancel state, session_key={session_key}, cancelled_sessions={self._cancelled_sessions}")
        self._cancel_event.clear()
    
    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
        
        Returns:
            The response message, or None if no response needed.
        """
        # 开始执行链路监控
        chain = self._chain_monitor.start_chain(
            session_key=msg.session_key,
            channel=msg.channel,
            chat_id=msg.chat_id,
            root_prompt=msg.content or ''
        )
        current_node_id = None  # 当前节点ID，用于追踪父子关系

        # 新请求开始前重置取消状态，确保该 session 可正常执行
        self._reset_cancel_event(msg.session_key)
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            return await self._process_system_message(msg)

        # 优先检查是否是对 Claude Code 决策请求的回复
        # 当 manager 有挂起的决策时，该消息直接路由给对应的 Future，不走 LLM
        if self.claude_code_manager.resolve_decision(msg.session_key, msg.content):
            logger.info(f"Message routed as Claude Code decision reply for {msg.session_key}")
            try:
                self._chain_monitor.end_chain(status="completed")
            except Exception as e:
                logger.error(f"[ExecutionChain] Failed to end chain: {e}")
            return None

        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}")

        # /clear 命令：清除聊天记录，清空上下文（飞书等渠道通用）
        _cmd = (msg.content or "").strip().lower()
        if _cmd in ("/clear", "/清空"):
            session = self.sessions.get_or_create(msg.session_key)
            session.clear()
            session.subagent_results.clear()
            self.sessions.save(session)
            logger.info(f"Session {msg.session_key} cleared by /clear command")
            try:
                self._chain_monitor.end_chain(status="completed")
            except Exception as e:
                logger.error(f"[ExecutionChain] Failed to end chain: {e}")
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="已清除所有聊天记录，上下文已清空。",
            )

        # Get or create session
        session = self.sessions.get_or_create(msg.session_key)

        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)
        if hasattr(self, "claude_code_manager") and self.claude_code_manager:
            self.claude_code_manager.set_context(msg.channel, msg.chat_id)
            spawn_tool.set_media(msg.media if msg.media else [])
            spawn_tool.set_batch_id(str(uuid.uuid4())[:12])
            spawn_tool.set_user_message(msg.content)  # 传递用户的原始消息

        # Get subagent results tool
        get_subagent_results_tool = self.tools.get("get_subagent_results")
        if get_subagent_results_tool:
            get_subagent_results_tool.set_context(msg.channel, msg.chat_id)

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
        # 按需加载模式：不再在这里预加载 MCP
        # MCP 工具已作为懒加载代理注册，会在首次被调用时建立连接
        # 见 McpLazyToolAdapter 类

        # Detect "继续" / "continue" / "扩容" command after a limit-reached pause..
        # If the last assistant message has the LIMIT_REACHED_MARKER and the user
        # is asking to continue or expand capacity, handle accordingly.
        current_message = msg.content
        _user_cmd = msg.content.strip()
        _is_continue = _user_cmd.lower() in CONTINUE_KEYWORDS
        _is_expand = _user_cmd in EXPAND_KEYWORDS
        if _is_continue or _is_expand:
            last_msgs = session.messages[-3:] if session.messages else []
            last_assistant = next(
                (m for m in reversed(last_msgs) if m.get("role") == "assistant"),
                None,
            )
            if last_assistant and LIMIT_REACHED_MARKER in str(last_assistant.get("content", "")):
                if _is_expand:
                    expanded = max(int(self.max_iterations * EXPAND_RATIO), self.max_iterations + 1)
                    logger.info(
                        "User requested capacity expansion: max_iterations %d -> %d",
                        self.max_iterations, expanded,
                    )
                    self.update_agent_params(max_iterations=expanded)
                else:
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

        # Inline image recognition: 处理用户发送的图片（不包括音频文件）
        # 逻辑调整：优先让主模型自己处理（或 spawn vision 子agent），只有当主模型不支持视觉时才做 inline recognition 作为兜底
        # 分离图片和音频文件
        image_files = [m for m in (msg.media or []) if self._is_image_file(m)]
        audio_files = [m for m in (msg.media or []) if self._is_audio_file(m)]

        if audio_files:
            logger.info(f"[Audio] Found {len(audio_files)} audio files, expecting model to choose appropriate subagent template")

        if image_files:
            main_model_supports_vision = self._is_vision_model(self.model)
            # LiteLLM 存在 Bug #16007：DashScope 图文混排时丢弃图片，仅传递文本
            # 对 DashScope 模型强制使用 inline recognition 以 bypass LiteLLM
            # 主模型不支持视觉 或 DashScope（LiteLLM Bug #16007 会丢图）时，用 vision 子 agent
            use_vision_subagent = (
                (not main_model_supports_vision)
                or (main_model_supports_vision and self._is_dashscope_model(self.model))
            )

            if main_model_supports_vision and not self._is_dashscope_model(self.model):
                # 主模型支持视觉且非 DashScope，直接发送图片
                logger.info("[Image] Main model supports vision, letting model handle images directly")
            elif use_vision_subagent:
                # 使用 vision 子 agent 分析（模板 model/system_prompt 可配置，支持不同视觉解读风格）
                logger.info("[Image] Using vision subagent for analysis (template configurable)")
                await self._check_cancelled(getattr(msg, "session_key", f"{msg.channel}:{msg.chat_id}"))
                progress_cb = msg.metadata.get("progress_callback")
                if progress_cb:
                    try:
                        progress_cb({"type": "tool_start", "name": "image_recognition", "arguments": {"images": len(image_files)}})
                    except Exception:
                        pass
                session_key = getattr(msg, "session_key", f"{msg.channel}:{msg.chat_id}")
                channel, chat_id = (session_key.split(":", 1) + [session_key])[:2]
                task_text = msg.content.strip() or "请详细分析这些图片的内容。"
                img_desc = await self.subagents.run_vision_analysis(
                    task=task_text, media=image_files,
                    origin_channel=channel, origin_chat_id=chat_id,
                )
                if progress_cb:
                    try:
                        progress_cb({"type": "tool_end", "name": "image_recognition", "arguments": {}, "result": (img_desc or "")[:200]})
                    except Exception:
                        pass
                last_user = messages[-1] if messages and messages[-1].get("role") == "user" else None
                if last_user:
                    if img_desc and img_desc.strip():
                        text_content = msg.content.strip() or "请描述这张图片。"
                        last_user["content"] = f"{text_content}\n\n[Vision 子 Agent 分析结果]\n{img_desc.strip()}"
                    else:
                        last_user["content"] = f"{msg.content}\n\n[图片分析失败，请检查 vision 模板配置或 DashScope API Key]"
        
        # Agent loop
        sk = msg.session_key
        iteration = 0
        final_content = None
        exit_reason: str | None = None  # "time" | "iterations" | "loop" | None=normal
        tool_steps: list[dict[str, Any]] = []
        usage_acc = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        loop_start = time.monotonic()
        tool_result_max_len = self.tool_result_max_length

        # Batch 模式：记录本轮 spawn 的 task_id，等待完成后聚合返回
        spawned_task_ids: list[str] = []
        # task_id -> template，用于区分需要注入的 spawn（如 vision）与异步通知的 spawn（如 coder/claude_code）
        spawned_task_templates: dict[str, str] = {}
        # 已注入 messages 的 subagent task_id，避免重复注入
        _injected_subagent_task_ids: set[str] = set()

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

        def _emit(evt: dict[str, Any]) -> None:
            """安全发送进度事件（细粒度事件流，兼容 pi-agent 风格）。"""
            progress = msg.metadata.get("progress_callback")
            if progress:
                try:
                    progress(evt)
                except Exception:
                    pass

        # 细粒度事件流：agent_start
        _emit({"type": "agent_start", "iteration": 0})

        # 细粒度事件流：message_start/end (user) - 用户消息已纳入 context
        _emit({"type": "message_start", "role": "user", "content": (current_message or "")[:200]})
        _emit({"type": "message_end", "role": "user"})

        while iteration < self.max_iterations:
            iteration += 1

            # 细粒度事件流：turn_start
            _emit({"type": "turn_start", "iteration": iteration})
            
            await self._check_cancelled(sk)

            # 时间限制: 防止 runaway，与 max_iterations 互补
            if self.max_execution_time > 0:
                elapsed = time.monotonic() - loop_start
                if elapsed >= self.max_execution_time:
                    logger.info("Max execution time %ds reached (elapsed %.0fs)", self.max_execution_time, elapsed)
                    exit_reason = "time"
                    _emit({"type": "turn_end", "iteration": iteration})
                    break

            # 方案 A：有上一轮 spawn 时，仅对需要注入的 spawn（如 vision）等待完成
            # coder/claude_code 等 spawn 异步通知结果，不在此等待，避免飞书等渠道半天无响应
            if iteration > 1 and spawned_task_ids and self.subagents:
                running_spawns = [
                    tid for tid in spawned_task_ids
                    if tid in self.subagents._running_tasks
                ]
                # 仅 vision 等需要注入结果的 spawn 才等待；coder/claude_code 不等待
                running_spawns_needing_inject = [
                    tid for tid in running_spawns
                    if (spawned_task_templates.get(tid) or "").lower() == "vision"
                ]
                if running_spawns_needing_inject:
                    logger.info(f"[SubagentInject] Waiting for vision spawns before LLM call: {running_spawns_needing_inject}")
                    try:
                        await self._wait_for_subagents(running_spawns_needing_inject, timeout=120.0)
                        logger.info("[SubagentInject] Vision spawn tasks completed")
                    except asyncio.TimeoutError:
                        logger.warning("[SubagentInject] Timeout waiting for vision spawn tasks")
                elif running_spawns:
                    logger.info(f"[SubagentInject] Skipping wait for non-inject spawns (coder/claude_code): {running_spawns}")

                # 注入已完成且尚未注入的子 Agent 结果
                to_inject = [
                    tid for tid in spawned_task_ids
                    if tid not in _injected_subagent_task_ids
                    and tid in session.subagent_results
                ]
                if to_inject:
                    parts = ["[子 Agent 已完成] 以下是已完成的子 Agent 执行结果，请基于这些结果继续处理用户请求：\n"]
                    for tid in to_inject:
                        sa = session.subagent_results[tid]
                        label = sa.get("label", "任务")
                        task = sa.get("task", "")[:200]
                        result = (sa.get("result") or "")[:1500]
                        parts.append(f"\n### {label}\n- 任务: {task}\n- 结果:\n{result}")
                        _injected_subagent_task_ids.add(tid)
                    inject_content = "\n".join(parts)
                    messages.append({"role": "user", "content": inject_content})
                    logger.info(f"[SubagentInject] Injected {len(to_inject)} subagent results into messages: {to_inject}")

            # 细粒度事件流：thinking
            _emit({"type": "thinking"})
            # Call LLM（单次最长 120 秒，等待期间每 2 秒检查一次取消）
            _LLM_CALL_TIMEOUT = 120
            _CANCEL_CHECK_INTERVAL = 2.0
            selected_tools = self._select_tools_for_message(msg.content)
            try:
                llm_task = asyncio.create_task(
                    self.provider.chat(
                        messages=messages,
                        tools=selected_tools,
                        model=self.model,
                    )
                )
                loop_start_llm = time.monotonic()
                while not llm_task.done():
                    elapsed_llm = time.monotonic() - loop_start_llm
                    remaining = _LLM_CALL_TIMEOUT - elapsed_llm
                    if remaining <= 0:
                        llm_task.cancel()
                        try:
                            await llm_task
                        except asyncio.CancelledError:
                            pass
                        raise asyncio.TimeoutError()
                    wait_time = min(_CANCEL_CHECK_INTERVAL, remaining)
                    done, _ = await asyncio.wait(
                        [llm_task],
                        timeout=wait_time,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if llm_task in done:
                        break
                    cancelled = (sk in self._cancelled_sessions) or self._cancel_event.is_set()
                    if cancelled:
                        llm_task.cancel()
                        try:
                            await llm_task
                        except asyncio.CancelledError:
                            pass
                        if sk in self._cancelled_sessions:
                            self._cancelled_sessions.discard(sk)
                        else:
                            self._cancel_event.clear()
                        raise asyncio.CancelledError("Request cancelled by user")
                response = await llm_task
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                logger.warning("LLM call timed out after %ds, breaking agent loop", _LLM_CALL_TIMEOUT)
                exit_reason = "time"
                _emit({"type": "turn_end", "iteration": iteration})
                break
            _accumulate_usage(response.usage)

            # 细粒度事件流：message_start/end (assistant)
            _emit({"type": "message_start", "role": "assistant", "content": (response.content or "")[:200]})
            _emit({"type": "message_end", "role": "assistant", "has_tool_calls": response.has_tool_calls})

            # 记录 LLM 调用指标
            if self._status_service:
                try:
                    self._status_service.increment_llm_call()
                    if response.usage:
                        total = response.usage.get("total_tokens") or response.usage.get("tokens_used") or 0
                        if total:
                            self._status_service.update_token_usage(int(total))
                except Exception as e:
                    logger.debug(f"Failed to record LLM metrics: {e}")

            # Handle tool calls
            if response.has_tool_calls:
                logger.info(f"[AgentLoop] LLM returned {len(response.tool_calls)} tool calls: {[tc.name for tc in response.tool_calls]}")
                if any(tc.name == 'spawn' for tc in response.tool_calls):
                    logger.info(f"[AgentLoop] SPAWN tool detected in tool calls!")
                # 批内去重：相同 (name, arguments) 只执行一次
                tool_calls_deduped = self._deduplicate_tool_calls(response.tool_calls)

                # 详细日志：工具调用信息
                tool_calls_info = []
                for tc in tool_calls_deduped:
                    args_str = json.dumps(tc.arguments, ensure_ascii=False)[:200]
                    tool_calls_info.append(f"{tc.name}({args_str})")
                logger.info(f"[AgentLoop] Tool calls prepared: {tool_calls_info}")

                # Add assistant message with tool calls（使用去重后的列表以保持一致性）
                # 确保 id 为字符串（MiniMax 等 API 对 tool call/result 匹配有严格要求）
                tool_call_dicts = [
                    {
                        "id": str(tc.id) if tc.id is not None else "",
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)  # Must be JSON string
                        }
                    }
                    for tc in tool_calls_deduped
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )

                # Execute tools and collect steps for UI display
                loop_detected = False

                # 执行前过滤：与本回合已执行的 spawn 语义重复的，不执行（避免重复 spawn vision）
                to_execute: list = []
                skipped_spawns: set = set()
                for tc in tool_calls_deduped:
                    if tc.name == "spawn":
                        args = getattr(tc, "arguments", {}) or {}
                        if isinstance(args, dict) and self._is_duplicate_spawn(args, tool_steps):
                            logger.info("[AgentLoop] Skipping duplicate spawn before execution (similar task already done this turn)")
                            skipped_spawns.add(id(tc))
                            continue
                    to_execute.append(tc)

                if not to_execute:
                    logger.info("[AgentLoop] All tool calls were duplicate spawns, forcing synthesis")
                    # 必须为被跳过的 tool_calls 注入占位 result，否则 messages 中 assistant 有 tool_calls 却无对应 result，MiniMax 等 API 会报 2013
                    for tc in tool_calls_deduped:
                        if id(tc) in skipped_spawns:
                            messages = self.context.add_tool_result(
                                messages, tc.id, tc.name,
                                "已跳过：本回合已执行过类似的视觉分析任务，请使用之前的识别结果。",
                            )
                            tool_steps.append({
                                "name": tc.name,
                                "arguments": tc.arguments,
                                "result": "已跳过：本回合已执行过类似的视觉分析任务，请使用之前的识别结果。",
                            })
                    loop_detected = True
                    exit_reason = "loop"
                    _emit({"type": "turn_end", "iteration": iteration})
                    break

                # 执行前检查取消，避免工具执行期间无法响应停止
                await self._check_cancelled(sk)

                # Batch 模式：spawn 优先级高于 exec
                # 实现：spawn 和 exec 同时启动，谁先完成用谁
                # - 如果 spawn 先完成且有正确结果，取消 exec
                # - 否则执行完所有工具
                has_spawn = any(tc.name == "spawn" for tc in to_execute)
                has_exec = any(tc.name == "exec" for tc in to_execute)

                progress = msg.metadata.get("progress_callback")

                # 如果有 spawn，仅对需要注入的 vision spawn 等待；coder/claude_code 不等待
                if spawned_task_ids and self.subagents:
                    running_spawns = [
                        tid for tid in spawned_task_ids
                        if tid in self.subagents._running_tasks
                    ]
                    running_spawns_needing_inject = [
                        tid for tid in running_spawns
                        if (spawned_task_templates.get(tid) or "").lower() == "vision"
                    ]
                    if running_spawns_needing_inject:
                        logger.info(f"[BatchMode] Waiting for vision spawns before next LLM call: {running_spawns_needing_inject}")
                        try:
                            await self._wait_for_subagents(running_spawns_needing_inject, timeout=120.0)
                            logger.info(f"[BatchMode] Vision spawn tasks completed before next LLM call")
                        except asyncio.TimeoutError:
                            logger.warning(f"[BatchMode] Timeout waiting for vision spawn tasks")
                    elif running_spawns:
                        logger.info(f"[BatchMode] Skipping wait for non-inject spawns: {running_spawns}")

                if has_spawn and has_exec and spawned_task_ids is not None:
                    # Batch 模式：按类型区分执行
                    # - spawn(vision) 优先执行（图片分析）
                    # - exec(图片相关) 需要在 spawn(vision) 完成后执行
                    # - exec(非图片) 和 spawn(非 vision) 可以并行执行
                    logger.info("[BatchMode] Type-aware execution mode")

                    # 分离不同类型的 spawn
                    spawn_calls = [tc for tc in to_execute if tc.name == "spawn"]
                    exec_calls = [tc for tc in to_execute if tc.name == "exec"]

                    spawn_vision = []
                    spawn_other = []
                    for tc in spawn_calls:
                        template = (tc.arguments.get("template") or "").lower() if tc.arguments else ""
                        if template == "vision":
                            spawn_vision.append(tc)
                        else:
                            spawn_other.append(tc)

                    # 分离不同类型的 exec
                    exec_image = []
                    exec_other = []
                    for tc in exec_calls:
                        cmd = tc.arguments.get("command", "") or "" if tc.arguments else ""
                        if self._is_exec_image_related(cmd):
                            exec_image.append(tc)
                        else:
                            exec_other.append(tc)

                    logger.info(f"[BatchMode] spawn_vision={len(spawn_vision)}, spawn_other={len(spawn_other)}, exec_image={len(exec_image)}, exec_other={len(exec_other)}")

                    spawn_results = []
                    exec_results = []

                    # 阶段 1: 先执行 spawn(vision)
                    for tc in spawn_vision:
                        result = await self._execute_single_tool(tc, progress)
                        spawn_results.append((tc, result))
                        task_id_match = re.search(r'\(id: ([^)]+)\)|session \[([^\]]+)\]', result)
                        if task_id_match:
                            task_id = task_id_match.group(1) or task_id_match.group(2)
                            spawned_task_ids.append(task_id)
                            spawned_task_templates[task_id] = (tc.arguments or {}).get("template", "minimal")
                            logger.info(f"[BatchMode] Spawn vision completed: {task_id}")

                    # 阶段 2: spawn(vision) 有结果后，exec(图片相关) 才能执行
                    spawn_vision_has_result = any(
                        "started" in r[1] or "id:" in r[1] for r in spawn_results
                    )

                    if exec_image:
                        if spawn_vision and not spawn_vision_has_result:
                            # 如果有 spawn(vision) 但还没完成，跳过 exec_image（避免重复分析）
                            # 添加占位结果以保持与 tool_calls_deduped 的顺序对应
                            logger.info("[BatchMode] Skipping exec_image due to pending spawn_vision")
                            for tc in exec_image:
                                exec_results.append((tc, "已跳过：spawn vision 任务正在执行中"))
                        else:
                            # spawn(vision) 已完成或没有 spawn(vision)，执行 exec_image
                            for tc in exec_image:
                                result = await self._execute_single_tool(tc, progress)
                                exec_results.append((tc, result))
                                logger.info(f"[BatchMode] Exec image completed")

                    # 阶段 3: exec(非图片) 和 spawn(非 vision) 并行执行
                    parallel_tasks = []
                    for tc in spawn_other + exec_other:
                        parallel_tasks.append(self._execute_single_tool(tc, progress))

                    if parallel_tasks:
                        parallel_results = await asyncio.gather(*parallel_tasks)
                        for tc, result in zip(spawn_other + exec_other, parallel_results):
                            if tc.name == "spawn":
                                spawn_results.append((tc, result))
                                task_id_match = re.search(r'\(id: ([^)]+)\)|session \[([^\]]+)\]', result)
                                if task_id_match:
                                    task_id = task_id_match.group(1) or task_id_match.group(2)
                                    spawned_task_ids.append(task_id)
                                    spawned_task_templates[task_id] = (tc.arguments or {}).get("template", "minimal")
                                    logger.info(f"[BatchMode] Spawn other completed: {task_id}")
                            else:
                                exec_results.append((tc, result))
                                logger.info(f"[BatchMode] Exec other completed")

                    # 合并所有结果
                    exec_results = spawn_results + exec_results
                else:
                    # 正常模式：并行/串行执行
                    exec_results = await self._execute_tool_parallel(
                        to_execute, progress, image_files=image_files, session_key=sk
                    )

                # 合并结果：保持 tool_calls_deduped 顺序，被跳过的 spawn 注入占位结果
                exec_iter = iter(exec_results)
                tool_results: list = []
                for tc in tool_calls_deduped:
                    if id(tc) in skipped_spawns:
                        tool_results.append((tc, "已跳过：本回合已执行过类似的视觉分析任务，请使用之前的识别结果。"))
                    else:
                        tool_results.append(next(exec_iter))

                for tool_call, result in tool_results:
                    # 跳过异常结果（tool_call 为 None）
                    if tool_call is None:
                        continue

                    # Batch 模式特殊处理：spawn 仍然要放入 messages，但最终回复基于综合结果
                    # 原因：否则 LLM 会报 "tool call and result not match" 错误
                    is_spawn = tool_call.name == "spawn"

                    if is_spawn:
                        # 放入 messages 避免 tool call 不匹配错误
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_call.name, result
                        )
                        # 记录到 tool_steps（用于后续综合）
                        tool_steps.append({
                            "name": tool_call.name,
                            "arguments": tool_call.arguments,
                            "result": _truncate(result),
                        })
                        # 非 Batch 路径也需记录 task_id，否则 Round 2 无法等待/注入（vision -> claude_code 链式调用）
                        task_id_match = re.search(r'\(id: ([^)]+)\)|session \[([^\]]+)\]', result)
                        if task_id_match:
                            task_id = task_id_match.group(1) or task_id_match.group(2)
                            spawned_task_ids.append(task_id)
                            spawned_task_templates[task_id] = (tool_call.arguments or {}).get("template", "minimal")
                            logger.info(f"[Spawn] Recorded task_id for inject: {task_id}")
                        logger.info(f"[BatchMode] Spawn result added to messages (final result will be synthesized later)")
                        continue

                    # 我们主动跳过的重复 spawn：加入 messages 但不触发 loop 检测
                    if id(tool_call) in skipped_spawns:
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_call.name, result
                        )
                        tool_steps.append({
                            "name": tool_call.name,
                            "arguments": tool_call.arguments,
                            "result": _truncate(result),
                        })
                        continue

                    # 循环检测: 连续两次完全相同的调用视为 loop；spawn 额外检查历史是否已有相同任务
                    call_key = (tool_call.name, json.dumps(tool_call.arguments, sort_keys=True))
                    if len(tool_steps) >= 1:
                        last_key = (tool_steps[-1]["name"], json.dumps(tool_steps[-1]["arguments"], sort_keys=True))
                        if call_key == last_key:
                            logger.info("Loop detected: identical tool call %s, forcing synthesis", tool_call.name)
                            loop_detected = True
                            break
                    # spawn 专项：语义相似重复 或 本轮 spawn 数量过多
                    if tool_call.name == "spawn":
                        spawn_count = sum(1 for s in tool_steps if s.get("name") == "spawn")
                        if spawn_count >= 5:
                            logger.info("Loop detected: too many spawns in this turn (%d), forcing synthesis", spawn_count)
                            loop_detected = True
                        else:
                            args = getattr(tool_call, "arguments", {}) or {}
                            if isinstance(args, dict) and self._is_duplicate_spawn(args, tool_steps):
                                logger.info("Loop detected: spawn for similar task already executed, forcing synthesis")
                                loop_detected = True

                    if loop_detected:
                        break

                    # Batch 模式：记录 spawn 产生的 task_id
                    if tool_call.name == "spawn":
                        # 从返回结果中提取 task_id
                        # 格式1: "Subagent [label] started (id: {task_id})."
                        # 格式2: "Continuing subagent session [{task_id}]."
                        task_id_match = re.search(r'\(id: ([^)]+)\)|session \[([^\]]+)\]', result)
                        if task_id_match:
                            task_id = task_id_match.group(1) or task_id_match.group(2)
                            spawned_task_ids.append(task_id)
                            spawned_task_templates[task_id] = (tool_call.arguments or {}).get("template", "minimal")
                            logger.info(f"[BatchMode] Recorded spawn task_id: {task_id}")

                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    truncated = _truncate(result)
                    tool_steps.append({
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "result": truncated,
                    })

                if loop_detected:
                    exit_reason = "loop"
                    _emit({"type": "turn_end", "iteration": iteration})
                    break

                # 有 tool calls 且未 break：本轮结束，继续下一轮
                _emit({"type": "turn_end", "iteration": iteration})
            else:
                # No tool calls, we're done
                final_content = response.content
                _emit({"type": "turn_end", "iteration": iteration})
                break

        # 细粒度事件流：agent_end
        _emit({"type": "agent_end", "exit_reason": exit_reason, "final_content_preview": (final_content or "")[:100]})

        # 非阻塞 Batch 模式：spawn 后立即返回，不等待子 Agent 完成
        # 子 Agent 完成后会触发综合并通过 SSE 推送结果
        # 若已注入过子 Agent 结果（方案 A：vision -> claude_code 链式调用），则返回 LLM 合成结果，不覆盖
        if spawned_task_ids and not _injected_subagent_task_ids:
            logger.info(f"[BatchMode] Non-blocking mode: spawn {len(spawned_task_ids)} tasks, returning immediately")
            # 直接返回 spawn 的结果，不再继续 LLM 调用
            # 使用 spawn 返回的消息（已经在 response.content 中）
            final_content = response.content if response.content else f"任务已启动，正在后台处理中 (id: {spawned_task_ids[0]})..."
            # 强制设置 exit_reason 避免后续继续循环
            exit_reason = "spawn_nonblocking"

        if final_content is None and exit_reason is None:
            exit_reason = "iterations"

        if final_content is None:
            if exit_reason == "time":
                # 已超时，直接降级，不再发起可能同样挂住的 LLM 请求
                logger.info("Time limit exceeded; skipping synthesis to avoid further hang")
                final_content = _make_fallback_from_tools(tool_steps)
            else:
                # Hit max_iterations with only tool calls - force one final LLM call without tools
                # to get a proper summary/response instead of generic fallback
                logger.info("Max iterations reached with no text response; requesting final synthesis (no tools)")
                _emit({"type": "thinking"})
                # 综合调用限时 60 秒，等待期间检查取消
                _SYNTHESIS_TIMEOUT = 60
                try:
                    synth_task = asyncio.create_task(
                        self.provider.chat(
                            messages=messages,
                            tools=None,
                            model=self.model,
                        )
                    )
                    synth_start = time.monotonic()
                    while not synth_task.done():
                        remaining = _SYNTHESIS_TIMEOUT - (time.monotonic() - synth_start)
                        if remaining <= 0:
                            synth_task.cancel()
                            try:
                                await synth_task
                            except asyncio.CancelledError:
                                pass
                            raise asyncio.TimeoutError()
                        done, _ = await asyncio.wait(
                            [synth_task],
                            timeout=min(2.0, remaining),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if synth_task in done:
                            break
                        if self._cancel_event.is_set():
                            synth_task.cancel()
                            try:
                                await synth_task
                            except asyncio.CancelledError:
                                pass
                            self._cancel_event.clear()
                            raise asyncio.CancelledError("Request cancelled by user")
                    synth = await synth_task
                    _accumulate_usage(synth.usage)
                    # 记录 LLM 调用指标
                    if self._status_service and synth.usage:
                        try:
                            self._status_service.increment_llm_call()
                            total = synth.usage.get("total_tokens") or synth.usage.get("tokens_used") or 0
                            if total:
                                self._status_service.update_token_usage(int(total))
                        except Exception as e:
                            logger.debug(f"Failed to record synthesis LLM metrics: {e}")
                    if synth.content and synth.content.strip():
                        final_content = synth.content.strip()
                    else:
                        final_content = _make_fallback_from_tools(tool_steps)
                except asyncio.TimeoutError:
                    logger.warning("Final synthesis call timed out after %ds", _SYNTHESIS_TIMEOUT)
                    final_content = _make_fallback_from_tools(tool_steps)
                except Exception as e:
                    logger.warning("Final synthesis call failed: %s", e)
                    final_content = _make_fallback_from_tools(tool_steps)

        # Append user-visible limit notice so the user knows why the agent stopped
        # and can explicitly ask to continue or expand capacity.
        if exit_reason in ("iterations", "time"):
            if exit_reason == "iterations":
                expanded = max(int(self.max_iterations * EXPAND_RATIO), self.max_iterations + 1)
                reason_zh = f"工具调用次数已达上限（{self.max_iterations} 次）"
                limit_notice = (
                    f"\n\n---\n"
                    f"⚠️ **任务已暂停**：{reason_zh}。\n"
                    f"- 回复 **扩容** 自动将本轮上限扩大至 {expanded} 次并继续执行\n"
                    f"- 回复 **继续** 保持当前上限继续执行\n"
                    f"- 或直接描述下一步您希望做什么。"
                    f"{LIMIT_REACHED_MARKER}"
                )
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

        # Web 渠道在请求开始时已保存用户消息，此处跳过避免重复
        user_message_saved = msg.metadata.get("user_message_saved") if msg.metadata else False

        # 分离图片和音频文件，只处理图片
        if msg.media:
            import base64
            user_images: list[str] = []
            for media_path in msg.media:
                # 只处理图片文件
                if not self._is_image_file(media_path):
                    continue
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
        
        if not user_message_saved:
            session.add_message("user", msg.content, **user_message_kwargs)
            self.sessions.save(session)
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

        # 兜底逻辑：如果主模型不支持视觉且配置了 subagent_model，但模型没有 spawn vision 子agent
        # 则使用 inline image recognition 作为兜底（只对图片）
        if image_files and not self._is_vision_model(self.model) and self.subagent_model and self.subagent_model != self.model:
            # 检查是否已经 spawn 了 vision 子agent
            spawned_vision = any(
                step.get("name") == "spawn" and "vision" in str(step.get("arguments", {}))
                for step in tool_steps
            )
            if not spawned_vision:
                # 模型没有 spawn vision 子agent，用 vision 子 agent 兜底
                logger.info("[Image] Model didn't spawn vision subagent, using vision subagent as fallback")
                await self._check_cancelled(getattr(msg, "session_key", f"{msg.channel}:{msg.chat_id}"))
                progress_cb = msg.metadata.get("progress_callback")
                if progress_cb:
                    try:
                        progress_cb({"type": "tool_start", "name": "image_recognition_fallback", "arguments": {"images": len(image_files)}})
                    except Exception:
                        pass
                session_key = getattr(msg, "session_key", f"{msg.channel}:{msg.chat_id}")
                channel, chat_id = (session_key.split(":", 1) + [session_key])[:2]
                img_desc = await self.subagents.run_vision_analysis(
                    task=msg.content.strip() or "请详细分析这些图片的内容。",
                    media=image_files, origin_channel=channel, origin_chat_id=chat_id,
                )
                if img_desc and img_desc.strip():
                    final_content = f"{final_content or ''}\n\n---\n\n[Vision 子 Agent 分析结果]\n{img_desc.strip()}"

                if progress_cb:
                    try:
                        progress_cb({"type": "tool_end", "name": "image_reccognition_fallback", "arguments": {}, "result": (img_desc or "")[:200]})
                    except Exception:
                        pass

        # 结束执行链路监控（主流程正常完成），确保节点持久化
        try:
            self._chain_monitor.end_chain(status="completed")
        except Exception as e:
            logger.error(f"[ExecutionChain] Failed to end chain: {e}")

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
        logger.info(f"[SystemMessage] Processing system message from sender={msg.sender_id}, chat_id={msg.chat_id}")

        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id

        logger.info(f"[SystemMessage] Parsed origin: channel={origin_channel}, chat_id={origin_chat_id}")

        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        self._reset_cancel_event(session_key)
        _preview = msg.content[:5000] + (f"... (共 {len(msg.content)} 字)" if len(msg.content) > 5000 else "")
        logger.info(f"[SystemMessage] Using session: {session_key}, message_preview=\n{_preview}")
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)
        if hasattr(self, "claude_code_manager") and self.claude_code_manager:
            self.claude_code_manager.set_context(origin_channel, origin_chat_id)

        # Get subagent results tool
        get_subagent_results_tool = self.tools.get("get_subagent_results")
        if get_subagent_results_tool:
            get_subagent_results_tool.set_context(origin_channel, origin_chat_id)

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

        # 按需加载模式：不再预加载 MCP
        # MCP 工具已作为懒加载代理注册
        
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
            
            await self._check_cancelled(session_key)
            
            if self.max_execution_time > 0:
                elapsed = time.monotonic() - loop_start
                if elapsed >= self.max_execution_time:
                    logger.info("System msg: max execution time %ds reached", self.max_execution_time)
                    break

            selected_tools = self._select_tools_for_message(msg.content)
            # 与主流程一致：LLM 调用期间每 2 秒轮询取消
            _LLM_CALL_TIMEOUT = 120
            _CANCEL_CHECK_INTERVAL = 2.0
            try:
                llm_task = asyncio.create_task(
                    self.provider.chat(
                        messages=messages,
                        tools=selected_tools,
                        model=self.model
                    )
                )
                loop_start_llm = time.monotonic()
                while not llm_task.done():
                    elapsed_llm = time.monotonic() - loop_start_llm
                    remaining = _LLM_CALL_TIMEOUT - elapsed_llm
                    if remaining <= 0:
                        llm_task.cancel()
                        try:
                            await llm_task
                        except asyncio.CancelledError:
                            pass
                        raise asyncio.TimeoutError()
                    wait_time = min(_CANCEL_CHECK_INTERVAL, remaining)
                    done, _ = await asyncio.wait(
                        [llm_task],
                        timeout=wait_time,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if llm_task in done:
                        break
                    if session_key in self._cancelled_sessions:
                        llm_task.cancel()
                        try:
                            await llm_task
                        except asyncio.CancelledError:
                            pass
                        self._cancelled_sessions.discard(session_key)
                        raise asyncio.CancelledError("Request cancelled by user")
                response = await llm_task
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                logger.warning("System msg: LLM call timed out")
                break
            _accumulate_usage(response.usage)

            # 记录 LLM 调用指标
            if self._status_service:
                try:
                    self._status_service.increment_llm_call()
                    if response.usage:
                        total = response.usage.get("total_tokens") or response.usage.get("tokens_used") or 0
                        if total:
                            self._status_service.update_token_usage(int(total))
                except Exception as e:
                    logger.debug(f"Failed to record LLM metrics: {e}")

            if response.has_tool_calls:
                loop_detected = False
                # 批内去重（与 _process_message 一致）
                tool_calls_deduped = self._deduplicate_tool_calls(response.tool_calls)
                tool_call_dicts = [
                    {
                        "id": str(tc.id) if tc.id is not None else "",
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in tool_calls_deduped
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )

                for tool_call in tool_calls_deduped:
                    call_key = (tool_call.name, json.dumps(tool_call.arguments, sort_keys=True))
                    if len(tool_steps) >= 1:
                        last_key = (tool_steps[-1]["name"], json.dumps(tool_steps[-1]["arguments"], sort_keys=True))
                        if call_key == last_key:
                            logger.info("System msg: loop detected %s", tool_call.name)
                            loop_detected = True
                            break
                    # spawn 专项：检查语义相似的重复
                    if tool_call.name == "spawn":
                        args = getattr(tool_call, "arguments", {}) or {}
                        if isinstance(args, dict) and self._is_duplicate_spawn(args, tool_steps):
                            logger.info("System msg: spawn for similar task already executed, skipping")
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
                # 记录 LLM 调用指标
                if self._status_service and synth.usage:
                    try:
                        self._status_service.increment_llm_call()
                        total = synth.usage.get("total_tokens") or synth.usage.get("tokens_used") or 0
                        if total:
                            self._status_service.update_token_usage(int(total))
                    except Exception as e:
                        logger.debug(f"Failed to record synthesis LLM metrics: {e}")
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
        # 立即保存用户消息，确保即使后续处理失败也不丢失
        self.sessions.save(session)
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
        
        # 结束执行链路监控
        try:
            self._chain_monitor.end_chain(status="completed")
        except Exception as e:
            logger.error(f"[ExecutionChain] Failed to end chain: {e}")

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
        self._reset_cancel_event(session_key)
        logger.info(f"Starting new request, cancel state reset for session {session_key}")
        
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
            await self._check_cancelled(session_key)
            response = await self._process_message(msg)
            return response.content if response else ""
        except asyncio.CancelledError:
            logger.info("Agent request was cancelled")
            return ""
        finally:
            # 异常/取消时确保结束执行链路并持久化节点（正常返回时 _process_message 已调用）
            try:
                self._chain_monitor.end_chain(status="completed")
            except Exception as e:
                logger.error(f"[ExecutionChain] Failed to end chain: {e}")
    
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

        desc = await self.subagents.run_vision_analysis(
            task="请详细分析这些图片的内容。",
            media=media,
            origin_channel=channel,
            origin_chat_id=chat_id,
        )
        if desc:
            if session:
                session.add_message("assistant", desc)
                self.sessions.save(session)
            return desc
        return "无法识别图片内容，请检查 vision 模板配置或 DashScope API Key。"
