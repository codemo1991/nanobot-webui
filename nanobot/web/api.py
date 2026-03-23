"""Minimal HTTP API server for nanobot Web UI."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
import cgi
import io
import json
import mimetypes
import os
import queue
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, unquote
from uuid import uuid4

from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.agentloop.db import connect_chat, connect_system, init_chat_schema, init_system_schema
from nanobot.agent.skills import BUILTIN_SKILLS_DIR
from nanobot.bus.queue import MessageBus
from nanobot.config.defaults import init_default_profiles, init_default_agent_config
from nanobot.config.loader import convert_keys, ensure_initial_config, get_config_repository, get_system_db_path, load_config, save_config
from nanobot.config.schema import Config, McpServerConfig
from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.providers.router import ModelRouter
from nanobot.services.mirror_service import MirrorService
from nanobot.services.system_status_service import SystemStatusService
from nanobot.session.manager import SessionManager
from nanobot.storage.status_repository import StatusRepository
from nanobot.storage import memory_repository

if TYPE_CHECKING:
    from nanobot.storage.config_repository import ConfigRepository


def _ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _err(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": message, "details": details}}


def _sync_smart_profile_default_model(repo: "ConfigRepository", model_id: str) -> None:
    """将 smart 场景的 model_chain 清空后仅设置为默认模型。"""
    smart_profile = repo.get_model_profile("smart")
    if smart_profile:
        repo.set_model_profile(
            profile_id="smart",
            name=smart_profile["name"],
            model_chain=model_id,
            description=smart_profile.get("description", ""),
            rules=smart_profile.get("rules", ""),
            enabled=smart_profile.get("enabled", True),
        )


class NanobotWebAPI:
    """State holder for web API handlers."""

    def __init__(self) -> None:
        import subprocess
        import sys
        
        self._subprocess = subprocess
        self._sys = sys
        self.gateway_process = None
        self.start_time = time.time()
        # Logging configured by setup_logging() in CLI main callback
        config = ensure_initial_config()

        # Initialize config repository and ensure default profiles exist
        repo = get_config_repository()
        init_default_profiles(repo)
        init_default_agent_config(repo)

        # Initialize ModelRouter - the new unified model resolution
        self.router = ModelRouter(repo)

        # Check if we have any usable models
        try:
            self.default_profile = repo.get_config_value("agent", "default_profile", "smart")
            handle = self.router.get(self.default_profile)
            logger.info(f"ModelRouter initialized with default profile: {self.default_profile}")
        except Exception as e:
            logger.warning(
                f"No usable model configuration found: {e}. "
                "Please configure providers and models via the Config page."
            )
            # Create a dummy router that will fail gracefully
            handle = None

        # 使用 ModelRouter 解析的模型，不再依赖 config.agents.defaults.model
        model = handle.model if handle else "anthropic/claude-opus-4-6"
        api_key = config.get_api_key(model)
        api_base = config.get_api_base(model)
        provider = LiteLLMProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=model,
        )

        # Initialize system status service first (needed by AgentLoop)
        # 优先使用 workspace 特定的数据库，否则使用默认数据库
        workspace_path = config.workspace_path

        # 启动时检测并创建 AgentLoop 微内核表（chat.db / system.db）
        try:
            chat_conn = connect_chat(workspace_path)
            init_chat_schema(chat_conn)
            chat_conn.close()
            sys_conn = connect_system()
            init_system_schema(sys_conn)
            sys_conn.close()
        except Exception as e:
            logger.warning("AgentLoop schema 初始化失败（可忽略，首次使用微内核时会重试）: %s", e)
        workspace_db_path = memory_repository.get_workspace_db_path(workspace_path)
        if workspace_db_path.exists():
            status_db_path = workspace_db_path
        else:
            status_db_path = memory_repository.get_default_db_path()
        data_dir = status_db_path.parent
        (data_dir / "media").mkdir(parents=True, exist_ok=True)
        # 确保 workspace/.nanobot/media 存在（web-ui 图片等文件统一存放于此）
        (workspace_path / ".nanobot" / "media").mkdir(parents=True, exist_ok=True)
        status_repo = StatusRepository(status_db_path)
        self.status_service = SystemStatusService(
            status_repo=status_repo,
            session_manager=None,  # Will be set after agent is created
            workspace=workspace_path
        )

        # Initialize AgentTemplateManager
        from nanobot.config.agent_templates import AgentTemplateManager
        self.agent_template_manager = AgentTemplateManager(workspace_path)

        # Pre-register API keys for custom models in templates
        self._register_template_model_keys(provider, config)
        # 启动时将所有已配置 provider 的 api_key 注入环境变量，供 profile 解析到的模型使用
        self._register_all_provider_keys(provider, config)

        self._workspace_path = workspace_path  # 用于 web-ui 图片等文件保存到 workspace/.nanobot/media
        self.agent = AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=config.workspace_path,
            model=model,
            subagent_model=getattr(config.agents.defaults, "subagent_model", "") or None,
            max_iterations=config.agents.defaults.max_tool_iterations,
            max_execution_time=getattr(config.agents.defaults, "max_execution_time", 600) or 0,
            brave_api_key=config.tools.web.search.api_key or None,
            exec_config=config.tools.exec,
            filesystem_config=config.tools.filesystem,
            claude_code_config=config.tools.claude_code,
            max_parallel_tool_calls=getattr(config.agents.defaults, "max_parallel_tool_calls", 5),
            enable_parallel_tools=getattr(config.agents.defaults, "enable_parallel_tools", True),
            thread_pool_size=getattr(config.agents.defaults, "thread_pool_size", 4),
            status_service=self.status_service,
            agent_template_manager=self.agent_template_manager,
            # 新架构：传入 ModelRouter
            router=self.router,
            default_profile=self.default_profile,
            # 微内核委托配置
            microkernel_escalation_enabled=getattr(config.agents.defaults, "microkernel_escalation_enabled", True),
            microkernel_escalation_threshold=getattr(config.agents.defaults, "microkernel_escalation_threshold", 10),
            microkernel_timeout_seconds=getattr(config.agents.defaults, "microkernel_timeout_seconds", 120.0),
            microkernel_threshold_simple=getattr(config.agents.defaults, "microkernel_threshold_simple", 15),
            microkernel_threshold_medium=getattr(config.agents.defaults, "microkernel_threshold_medium", 10),
            microkernel_threshold_complex=getattr(config.agents.defaults, "microkernel_threshold_complex", 5),
        )
        self.sessions = self.agent.sessions

        # Update status_service with session_manager now that it's available
        self.status_service.session_manager = self.sessions
        self.status_service.initialize()

        # 若主 Agent 提示词数据库无记录，则用默认内容初始化
        self._init_main_agent_prompt_if_needed(workspace_path)

        # Mirror service
        self.mirror = MirrorService(
            workspace=workspace_path,
            sessions_manager=self.sessions,
        )

        # 初始化仅用于发送的渠道客户端（供 cron 任务推送回复使用，不启动 inbound 监听）
        self._channel_senders: dict = {}
        if config.channels.feishu.enabled:
            from nanobot.channels.feishu import FeishuChannel
            from nanobot.bus.queue import MessageBus as _SenderBus
            _feishu_sender = FeishuChannel(config.channels.feishu, _SenderBus())
            _feishu_sender.setup_client()
            self._channel_senders["feishu"] = _feishu_sender

        # Cron service — 使用系统配置库 system.db（与 Config 同库）
        cron_db_path = get_system_db_path()
        from nanobot.cron.service import CronService

        async def cron_job_callback(job: dict):
            """执行定时任务：调用 LLM，若开启推送则发送到对应渠道。"""
            from nanobot.bus.events import OutboundMessage
            payload = job.get("payload", {})
            payload_kind = payload.get("kind", "")
            message = payload.get("message", "")
            deliver = payload.get("deliver", False)
            channel_name = (payload.get("channel") or "feishu").strip()
            to = (payload.get("to") or "").strip()
            job_id = job.get("id", "unknown")
            job_name = job.get("name", "unknown")

            # 处理系统任务
            if payload_kind == "system_event":
                return await self._handle_system_event_job(job, message)

            # 处理日历提醒任务
            if payload_kind == "calendar_reminder":
                event_id = payload.get("event_id")
                logger.info(f"日历提醒触发: {job_name} (event_id: {event_id})")

                if deliver and channel_name and to:
                    sender = self._channel_senders.get(channel_name)
                    if sender:
                        try:
                            await sender.send(OutboundMessage(
                                channel=channel_name,
                                chat_id=to,
                                content=message,
                            ))
                            logger.info(f"日历提醒已推送至 {channel_name}:{to}")
                        except Exception as _e:
                            logger.error(f"日历提醒推送失败: {_e}")
                    else:
                        logger.warning(
                            f"日历提醒: 渠道 '{channel_name}' 未配置或未启用，跳过推送"
                        )
                else:
                    logger.info(f"日历提醒未配置推送渠道: {job_name}")
                return message

            # 处理普通任务（调用 LLM）
            response = await self.agent.process_direct(
                message,
                session_key=f"cron:{job_id}",
                channel=channel_name,
                chat_id=to or "direct",
            )

            if deliver and to:
                sender = self._channel_senders.get(channel_name)
                if sender:
                    try:
                        await sender.send(OutboundMessage(
                            channel=channel_name,
                            chat_id=to,
                            content=response or "",
                        ))
                        logger.info(f"定时任务 '{job_name}' 已推送至 {channel_name}:{to}")
                    except Exception as _e:
                        logger.error(f"定时任务 '{job_name}' 推送失败: {_e}")
                else:
                    logger.warning(
                        f"定时任务 '{job_name}': 渠道 '{channel_name}' 未配置或未启用，跳过推送"
                    )
            return response

        self.cron_service = CronService(db_path=cron_db_path, on_job=cron_job_callback)

        # 确保系统默认任务存在
        self.cron_service.repository.ensure_system_jobs()

        # 初始化日历仓库
        from nanobot.storage.calendar_repository import get_calendar_repository
        self.calendar_repo = get_calendar_repository(workspace_path)

        # 初始化日历提醒服务
        from nanobot.services.calendar_reminder import CalendarReminderService
        self.calendar_reminder_service = CalendarReminderService(
            calendar_repo=self.calendar_repo,
            cron_service=self.cron_service,
        )

        # 初始化自动记忆整合服务
        from nanobot.services.auto_memory_integration import AutoMemoryIntegrationService
        self.auto_memory_integration = AutoMemoryIntegrationService(
            workspace=workspace_path,
            provider=provider,
            model=model,
            lookback_minutes=config.memory.lookback_minutes,
            max_messages=config.memory.max_messages,
        )

        # 初始化记忆维护服务
        from nanobot.services.memory_maintenance import MemoryMaintenanceService
        self.memory_maintenance = MemoryMaintenanceService(
            workspace=workspace_path,
            provider=provider,
            model=model,
            summarize_interval_min=config.memory.auto_integrate_interval_minutes,
            max_entries=config.memory.max_entries,
            max_chars=config.memory.max_chars,
        )

        # Initial gateway sync
        self._sync_gateway()

    def _reload_mcp(self) -> None:
        """Hot-reload MCP config so new/updated MCPs take effect without restart."""
        try:
            asyncio.run(self.agent.reload_mcp_config())
        except Exception as e:
            logger.warning(f"MCP reload failed: {e}", exc_info=True)

    async def _handle_system_event_job(self, job: dict, message: str):
        """处理系统事件任务"""
        job_id = job.get("id", "")
        job_name = job.get("name", "unknown")

        logger.info(f"系统任务 '{job_name}' 开始执行")

        try:
            if message == "auto_memory_integrate":
                # 检查自动记忆整合是否启用
                config = load_config()
                if not config.memory.auto_integrate_enabled:
                    logger.info("自动记忆整合已禁用，跳过执行")
                    return {"skipped": True, "reason": "disabled"}

                # 自动记忆整合任务
                if hasattr(self, 'auto_memory_integration') and self.auto_memory_integration:
                    await self.auto_memory_integration.integrate_now()
                    logger.info("自动记忆整合完成")
                else:
                    logger.warning("自动记忆整合服务未初始化")
                    return None

            elif message == "memory_maintenance":
                # 记忆维护任务
                if hasattr(self, 'memory_maintenance') and self.memory_maintenance:
                    await self.memory_maintenance._run_summarize_if_needed()
                    logger.info("记忆维护完成")
                else:
                    logger.warning("记忆维护服务未初始化")
                    return None

            else:
                # 非预定义系统事件：当作 agent 任务执行（用户误选 system_event 时仍可工作）
                if message and message.strip():
                    logger.info(f"系统事件 '{message[:50]}...' 作为 agent 任务执行")
                    payload = job.get("payload", {})
                    channel_name = (payload.get("channel") or "feishu").strip()
                    to = (payload.get("to") or "").strip()
                    deliver = payload.get("deliver", False)
                    response = await self.agent.process_direct(
                        message,
                        session_key=f"cron:{job_id}",
                        channel=channel_name,
                        chat_id=to or "direct",
                    )
                    if deliver and to:
                        sender = self._channel_senders.get(channel_name)
                        if sender:
                            try:
                                from nanobot.bus.events import OutboundMessage
                                await sender.send(OutboundMessage(
                                    channel=channel_name,
                                    chat_id=to,
                                    content=response or "",
                                ))
                                logger.info(f"系统任务 '{job_name}' 已推送至 {channel_name}:{to}")
                            except Exception as _e:
                                logger.error(f"系统任务推送失败: {_e}")
                    return response
                logger.warning(f"未知的系统事件类型且 message 为空: {message}")

            return None

        except Exception as e:
            logger.error(f"系统任务 '{job_name}' 执行失败: {e}")
            return None

    def _get_effective_model(self) -> str:
        """从 ModelRouter 解析当前生效的模型，不再使用 config.agents.defaults.model"""
        if hasattr(self, "router") and hasattr(self, "default_profile"):
            try:
                return self.router.get(self.default_profile).model
            except Exception:
                pass
        return "anthropic/claude-opus-4-6"

    def _get_stored_mcp_tools(self) -> dict[str, list[dict[str, Any]]]:
        """Read stored MCP tools from SQLite database, returning a dict mapping mcp_id -> tools list."""
        repo = get_config_repository()
        mcps = repo.get_all_mcps()
        return {m["id"]: m.get("tools", []) for m in mcps}

    def _reinit_agent_and_status(self, workspace_path: Path) -> None:
        """Reinitialize agent and status service with new workspace (hot reload)."""
        config = load_config()
        model = self._get_effective_model()
        api_key = config.get_api_key(model)
        api_base = config.get_api_base(model)
        is_bedrock = model.startswith("bedrock/")
        if not api_key and not is_bedrock:
            logger.warning("No API key configured; agent will not be able to process chat until configured.")
        provider = LiteLLMProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=model,
        )
        subagent_model_cfg = (getattr(config.agents.defaults, "subagent_model", "") or "").strip()
        if subagent_model_cfg:
            sa_key = config.get_api_key(subagent_model_cfg)
            if sa_key:
                provider.ensure_api_key_for_model(
                    subagent_model_cfg, sa_key, config.get_api_base(subagent_model_cfg)
                )
        # 使用 workspace 特定的数据库路径
        workspace_db_path = memory_repository.get_workspace_db_path(workspace_path)
        if workspace_db_path.exists():
            status_db_path = workspace_db_path
        else:
            status_db_path = memory_repository.get_default_db_path()
        # 确保 media 目录存在
        (status_db_path.parent / "media").mkdir(parents=True, exist_ok=True)
        status_repo = StatusRepository(status_db_path)
        # 先创建 status_service（此时 session_manager 暂时为 None）
        status_service = SystemStatusService(
            status_repo=status_repo,
            session_manager=None,
            workspace=workspace_path,
        )

        self.agent = AgentLoop(
            bus=self.agent.bus,
            provider=provider,
            workspace=workspace_path,
            model=model,
            subagent_model=getattr(config.agents.defaults, "subagent_model", "") or None,
            max_iterations=config.agents.defaults.max_tool_iterations,
            max_execution_time=getattr(config.agents.defaults, "max_execution_time", 600) or 0,
            brave_api_key=config.tools.web.search.api_key or None,
            exec_config=config.tools.exec,
            filesystem_config=config.tools.filesystem,
            claude_code_config=config.tools.claude_code,
            status_service=status_service,
            agent_template_manager=self.agent_template_manager,
            router=self.router,
            default_profile=self.default_profile,
            # 微内核委托配置
            microkernel_escalation_enabled=getattr(config.agents.defaults, "microkernel_escalation_enabled", True),
            microkernel_escalation_threshold=getattr(config.agents.defaults, "microkernel_escalation_threshold", 10),
            microkernel_timeout_seconds=getattr(config.agents.defaults, "microkernel_timeout_seconds", 120.0),
            microkernel_threshold_simple=getattr(config.agents.defaults, "microkernel_threshold_simple", 15),
            microkernel_threshold_medium=getattr(config.agents.defaults, "microkernel_threshold_medium", 10),
            microkernel_threshold_complex=getattr(config.agents.defaults, "microkernel_threshold_complex", 5),
        )
        self.sessions = self.agent.sessions

        # Update status_service with session_manager now that it's available
        status_service._session_manager = self.sessions
        self.status_service = status_service
        self.status_service.initialize()
        from nanobot.storage.calendar_repository import get_calendar_repository
        self.calendar_repo = get_calendar_repository(workspace_path)
        self._workspace_path = workspace_path
        self.mirror = MirrorService(
            workspace=workspace_path,
            sessions_manager=self.sessions,
        )
        logger.info(f"Workspace hot-reloaded to: {workspace_path}")

    def switch_workspace(
        self, workspace_path: str, copy_db: bool | None = None
    ) -> dict[str, Any]:
        """
        Switch workspace and hot-reload. Updates config and reinitializes agent/status.

        Args:
            workspace_path: 目标 workspace 路径
            copy_db: True 表示复制现有数据库，False 表示创建新的空数据库，None 表示需要用户选择

        Returns:
            如果需要用户选择，返回 {"needPrompt": True, "hasDefaultDb": bool}
            否则返回 {"workspace": str(path)}
        """
        path = Path(workspace_path).expanduser().resolve()
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)

        # 检查新 workspace 是否已有数据库
        has_workspace_db = memory_repository.workspace_db_exists(path)
        has_default_db = memory_repository.has_default_db()

        # 如果 workspace 已有数据库，直接切换
        if has_workspace_db:
            config = load_config()
            config.agents.defaults.workspace = str(path)
            save_config(config)
            self._reinit_agent_and_status(path)
            return {"workspace": str(path)}

        # 如果没有传递 copy_db 参数，需要提示用户选择
        if copy_db is None:
            return {
                "needPrompt": True,
                "hasDefaultDb": has_default_db,
                "workspace": str(path),
            }

        # 用户已选择，执行相应操作
        try:
            if copy_db and has_default_db:
                memory_repository.copy_db_to_workspace(path)
            else:
                memory_repository.create_empty_workspace_db(path)
        except Exception as e:
            logger.error(f"Failed to setup workspace database: {e}")
            raise ValueError(f"设置工作空间数据库失败: {e}")

        config = load_config()
        config.agents.defaults.workspace = str(path)
        save_config(config)
        self._reinit_agent_and_status(path, force_new_db=path)
        return {"workspace": str(path)}

    def import_config(self, config_data: dict[str, Any], reload_workspace: bool = True) -> dict[str, Any]:
        """
        Import configuration from dict. Validates, saves to config.json.
        Optionally hot-reloads workspace after import.
        """
        try:
            data = convert_keys(config_data)
            config = Config.model_validate(data)
            save_config(config)
            if reload_workspace:
                self._reinit_agent_and_status(config.workspace_path)
            return {"success": True, "workspace": str(config.workspace_path)}
        except Exception as e:
            logger.exception("Config import failed")
            raise ValueError(f"配置导入失败: {e}") from e

    def shutdown(self) -> None:
        """Cleanup resources."""
        if self.gateway_process:
            logger.info("Stopping gateway process...")
            self.gateway_process.terminate()
            try:
                self.gateway_process.wait(timeout=5)
            except self._subprocess.TimeoutExpired:
                self.gateway_process.kill()
            self.gateway_process = None

    def _register_template_model_keys(self, provider, config) -> None:
        """Pre-register API keys for custom models configured in templates."""
        try:
            custom_models = self.agent_template_manager.get_all_custom_models()
            for model in custom_models:
                api_key = config.get_api_key(model)
                api_base = config.get_api_base(model)
                if api_key:
                    provider.ensure_api_key_for_model(model, api_key, api_base)
                    logger.info(f"Registered API key for template model: {model}")
                else:
                    logger.warning(f"No API key found for template model: {model}")
        except Exception as e:
            logger.warning(f"Failed to register template model keys: {e}")

    def _register_all_provider_keys(self, provider, config) -> None:
        """将所有已配置 provider 的 api_key 注入环境变量，供 profile 解析到的模型使用。"""
        provider_ids = [
            "anthropic", "openai", "openrouter", "deepseek", "groq",
            "zhipu", "dashscope", "gemini", "vllm", "ollama", "minimax",
        ]
        for pid in provider_ids:
            pc = getattr(config.providers, pid, None)
            if pc and getattr(pc, "api_key", None) and hasattr(provider, "ensure_api_key_for_model"):
                provider.ensure_api_key_for_model(
                    f"{pid}/placeholder",
                    pc.api_key,
                    getattr(pc, "api_base", None),
                )

    def _sync_gateway(self, restart: bool = False) -> None:
        """Start, stop, or restart gateway based on channel configuration."""
        config = load_config()
        
        # Check if any channel is enabled
        any_channel_enabled = (
            config.channels.whatsapp.enabled or
            config.channels.telegram.enabled or
            config.channels.feishu.enabled or
            config.channels.discord.enabled or
            config.channels.qq.enabled or
            config.channels.dingtalk.enabled
        )
        
        # Stop if requested or if should be disabled
        if self.gateway_process and (restart or not any_channel_enabled):
            if self.gateway_process.poll() is None:
                logger.info(f"Stopping gateway process (restart={restart})...")
                self.gateway_process.terminate()
                try:
                    self.gateway_process.wait(timeout=5)
                except Exception as e:
                    logger.warning(f"Gateway terminate failed: {e}")
                    self.gateway_process.kill()
            self.gateway_process = None

        if any_channel_enabled:
            if self.gateway_process is None or self.gateway_process.poll() is not None:
                logger.info("Starting gateway process (channels enabled)...")
                try:
                    # Start nanobot gateway in a separate process
                    # Using the same python interpreter
                    logger.info(f"Gateway command: {self._sys.executable} -m nanobot gateway --port 18790")
                    self.gateway_process = self._subprocess.Popen(
                        [self._sys.executable, "-m", "nanobot", "gateway", "--port", "18790"],
                        # Inherit stdout/stderr for visibility
                        start_new_session=True 
                    )
                except Exception as e:
                    logger.error(f"Failed to start gateway: {e}")

    @staticmethod
    def to_session_id(key: str) -> str:
        return key.split(":", 1)[1] if key.startswith("web:") else key

    @staticmethod
    def to_session_key(session_id: str) -> str:
        return f"web:{session_id}"

    def list_sessions(self, page: int, page_size: int) -> dict[str, Any]:
        all_sessions = self.sessions.list_sessions(key_prefix="web:")
        total = len(all_sessions)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        items = all_sessions[start:end]
        return {
            "items": [
                {
                    "id": self.to_session_id(item["key"]),
                    "title": item.get("metadata", {}).get("title"),
                    "toolMode": item.get("metadata", {}).get("tool_mode", "auto"),
                    "selectedMcpServers": item.get("metadata", {}).get("selected_mcp_servers", []),
                    "createdAt": item["created_at"],
                    "updatedAt": item["updated_at"],
                    "lastMessageAt": item["updated_at"],
                    "messageCount": item["message_count"],
                    "status": "active",
                }
                for item in items
            ],
            "page": page,
            "pageSize": page_size,
            "total": total,
        }

    def create_session(self, title: str | None = None) -> dict[str, Any]:
        session_id = f"sess_{uuid4().hex[:12]}"
        key = self.to_session_key(session_id)
        session = self.sessions.get_or_create(key)
        if title:
            session.metadata["title"] = title
        self.sessions.save(session)
        return {
            "id": session_id,
            "title": session.metadata.get("title"),
            "createdAt": session.created_at.isoformat(),
            "updatedAt": session.updated_at.isoformat(),
            "lastMessageAt": session.updated_at.isoformat(),
            "messageCount": 0,
            "status": "active",
        }

    def delete_session(self, session_id: str) -> bool:
        # 先删除会话，如果成功则清理缓冲区
        from nanobot.agent.subagent_progress import SubagentProgressBus
        origin_key = f"web:{session_id}"
        result = self.sessions.delete(self.to_session_key(session_id))
        # 只有会话删除成功后才清理缓冲区，防止内存泄漏
        if result:
            SubagentProgressBus.get().clear_buffer(origin_key)
        return result

    def get_messages(self, session_id: str, before: int | None, limit: int) -> list[dict[str, Any]]:
        key = self.to_session_key(session_id)
        session = self.sessions.get(key)
        if session is None:
            raise KeyError("session not found")

        messages = self.sessions.get_messages(key=key, limit=limit, before_sequence=before)
        return [
            {
                "id": f"msg_{m['sequence']}",
                "sessionId": session_id,
                "role": m["role"],
                "content": m["content"],
                "createdAt": m["timestamp"],
                "sequence": m["sequence"],
                **({"toolSteps": m["tool_steps"]} if m.get("tool_steps") else {}),
                **(
                    {
                        "tokenUsage": {
                            "promptTokens": int(m["token_usage"].get("prompt_tokens", 0) or 0),
                            "completionTokens": int(m["token_usage"].get("completion_tokens", 0) or 0),
                            "totalTokens": int(m["token_usage"].get("total_tokens", 0) or 0),
                        }
                    }
                    if m.get("token_usage")
                    else {}
                ),
                **(
                    {"images": m["images"]}
                    if m.get("images")
                    else {}
                ),
            }
            for m in messages
        ]

    def get_session_token_summary(self, session_id: str) -> dict[str, int]:
        key = self.to_session_key(session_id)
        session = self.sessions.get(key)
        if session is None:
            raise KeyError("session not found")
        usage = self.sessions.get_session_token_usage(key)
        return {
            "promptTokens": int(usage.get("prompt_tokens", 0)),
            "completionTokens": int(usage.get("completion_tokens", 0)),
            "totalTokens": int(usage.get("total_tokens", 0)),
        }

    def reset_session_token_summary(self, session_id: str) -> dict[str, Any]:
        key = self.to_session_key(session_id)
        session = self.sessions.get(key)
        if session is None:
            raise KeyError("session not found")
        self.sessions.reset_session_token_usage(key)
        return {"reset": True, "scope": "session", "sessionId": session_id}

    def reset_global_token_summary(self) -> dict[str, Any]:
        self.sessions.reset_global_token_usage()
        return {"reset": True, "scope": "global"}

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        key = self.to_session_key(session_id)
        session = self.sessions.get(key)
        if session is None:
            raise KeyError("session not found")
        session.metadata["title"] = title
        self.sessions.save(session)
        return {
            "id": session_id,
            "title": title,
            "updatedAt": session.updated_at.isoformat(),
        }

    def update_session(
        self,
        session_id: str,
        tool_mode: str | None = None,
        selected_mcp_servers: list[str] | None = None,
    ) -> dict[str, Any]:
        key = self.to_session_key(session_id)
        session = self.sessions.get(key)
        if session is None:
            raise KeyError("session not found")
        if tool_mode is not None:
            session.metadata["tool_mode"] = tool_mode
        if selected_mcp_servers is not None:
            session.metadata["selected_mcp_servers"] = selected_mcp_servers
        self.sessions.save(session)
        return {
            "id": session_id,
            "toolMode": session.metadata.get("tool_mode", "auto"),
            "selectedMcpServers": session.metadata.get("selected_mcp_servers", []),
            "updatedAt": session.updated_at.isoformat(),
        }

    # ==================== Mirror Room Methods ====================

    def mirror_chat_stream(
        self, session_type: str, session_id: str, content: str
    ) -> tuple[queue.Queue[dict[str, Any]], threading.Thread]:
        """Run mirror chat with progress events."""
        evt_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        def on_progress(evt: dict[str, Any]) -> None:
            try:
                evt_queue.put(evt)
            except Exception:
                pass

        def run_agent() -> None:
            loop = None
            try:
                # 创建新的事件循环（避免与主线程冲突）
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    self._mirror_chat_with_progress(session_type, session_id, content, on_progress)
                )
                evt_queue.put({"type": "done", **result})
            except asyncio.CancelledError:
                evt_queue.put({"type": "error", "message": "cancelled"})
            except Exception as e:
                logger.exception("Mirror chat stream failed")
                evt_queue.put({"type": "error", "message": str(e)})
            finally:
                # 等待事件循环内未完成的任务（含微内核、子代理），避免 loop.close() 导致 Task was destroyed / coroutine ignored GeneratorExit
                if loop:
                    try:
                        current = asyncio.current_task()
                        pending = [t for t in asyncio.all_tasks(loop) if not t.done() and t is not current]
                        if pending:
                            logger.info(f"[MirrorChatStream] Waiting for {len(pending)} pending tasks before closing loop")
                            try:
                                loop.run_until_complete(asyncio.wait_for(
                                    asyncio.gather(*pending, return_exceptions=True),
                                    timeout=130,
                                ))
                            except asyncio.TimeoutError:
                                logger.warning("[MirrorChatStream] Pending tasks timed out after 130s, cancelling")
                                for t in pending:
                                    if not t.done():
                                        t.cancel()
                                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                        loop.close()
                    except Exception as e:
                        logger.debug("Loop close error (ignored): %s", e)

        thread = threading.Thread(target=run_agent, daemon=False)
        return evt_queue, thread

    async def _mirror_chat_with_progress(
        self,
        session_type: str,
        session_id: str,
        content: str,
        progress_callback: Any,
    ) -> dict[str, Any]:
        """Internal: run mirror chat with progress callback."""
        key = MirrorService._session_key(session_type, session_id)
        session = self.sessions.get_or_create(key)
        # 攻击强度注入：将 attack_level 传给 LLM，供辩模块调整追问风格
        attack_level = None
        if session_type == "bian" and session.metadata.get("attack_level"):
            attack_level = session.metadata["attack_level"]
        extra_metadata = {"attack_level": attack_level} if attack_level else None
        try:
            response = await self.agent.process_direct(
                content=content,
                session_key=key,
                channel="mirror",
                chat_id=session_id,
                progress_callback=progress_callback,
                extra_metadata=extra_metadata,
            )
        finally:
            if getattr(self.agent, "mcp_loader", None):
                try:
                    await self.agent.reload_mcp_config()
                except BaseException:
                    pass
        messages = self.sessions.get_messages(key=key, limit=2)
        assistant = next((m for m in reversed(messages) if m["role"] == "assistant"), None)
        return {
            "content": response,
            "assistantMessage": (
                {
                    "id": f"msg_{assistant['sequence']}",
                    "sessionId": session_id,
                    "role": assistant["role"],
                    "content": assistant["content"],
                    "createdAt": assistant["timestamp"],
                    "sequence": assistant["sequence"],
                    **({"toolSteps": assistant["tool_steps"]} if assistant.get("tool_steps") else {}),
                }
                if assistant
                else None
            ),
        }

    def _load_mirror_prompt(self, prompt_file: str, fallback: str) -> str:
        """从 mirror-system skill 加载 prompt，失败则用 fallback。"""
        try:
            mirror_skill_path = BUILTIN_SKILLS_DIR / "mirror-system" / "references" / prompt_file
            if mirror_skill_path.exists():
                content = mirror_skill_path.read_text(encoding="utf-8")
                # 提取 System Prompt 部分（在 ```之间）
                import re
                match = re.search(r'```\n(.*?)\n```', content, re.DOTALL)
                if match:
                    return match.group(1).strip()
            logger.debug(f"Mirror prompt file {prompt_file} not found, using fallback")
        except Exception as e:
            logger.warning(f"Failed to load mirror prompt {prompt_file}: {e}")
        return fallback

    async def _wu_first_reply(self, session_id: str) -> str:
        """悟首次回复：新建悟会话后，AI 自动给出三个悟命题或引导问题。"""
        key = MirrorService._session_key("wu", session_id)
        session = self.sessions.get(key)
        if session is None:
            raise KeyError("mirror wu session not found")
        # 已有消息则不再生成首次回复
        if session.messages:
            return ""
        trigger = "请开始悟道，给出三个悟命题或开放式引导问题，供用户选择；也可提示可直接说出此刻想聊的。"
        # 从 mirror-system/references/wu-prompts.md 加载 prompt
        fallback_system = (
            "你是一名悟道助手，擅长通过提问帮助用户探索内心叙事与潜意识。"
            "用户刚开启悟道会话，请直接给出三个悟命题或开放式引导问题，供用户选择。"
            "格式示例：1. 工作驱动力 2. 情绪表达 3. 人际关系。"
            "最后可加一句「也可直接说你此刻想聊的」。用简洁自然的中文，无需称呼寒暄。"
        )
        system_content = self._load_mirror_prompt("wu-prompts.md", fallback_system)
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": trigger},
        ]
        resp = await self.agent.provider.chat(
            messages=messages,
            model=self.agent.model,
            max_tokens=600,
            temperature=0.7,
        )
        content = (resp.content or "").strip()
        if not content:
            content = "你可以从以下方向开始：工作驱动力、情绪表达、人际关系。或者直接说你此刻想聊的。"
        session.add_message("user", trigger)
        # 立即保存用户消息，确保即使后续处理失败也不丢失
        self.sessions.save(session)
        session.add_message("assistant", content)
        self.sessions.save(session)
        return content

    def wu_first_reply_stream(
        self, session_id: str
    ) -> tuple[queue.Queue[dict[str, Any]], threading.Thread]:
        """流式返回悟首次回复。"""
        evt_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        def run() -> None:
            try:
                evt_queue.put({"type": "thinking"})
                result = asyncio.run(self._wu_first_reply(session_id))
                evt_queue.put({"type": "done", "content": result})
            except KeyError:
                evt_queue.put({"type": "error", "message": "mirror wu session not found"})
            except Exception as e:
                logger.exception("Wu first reply failed")
                evt_queue.put({"type": "error", "message": str(e)})

        thread = threading.Thread(target=run, daemon=False)
        return evt_queue, thread

    async def _bian_first_reply(self, session_id: str) -> str:
        """辩首次回复：有 topic 时开场该辩题，无 topic 时随机给出三个辩题供选。"""
        key = MirrorService._session_key("bian", session_id)
        session = self.sessions.get(key)
        if session is None:
            raise KeyError("mirror bian session not found")
        if session.messages:
            return ""
        topic = session.metadata.get("topic") or ""
        attack_level = session.metadata.get("attack_level") or "medium"
        if topic.strip():
            trigger = f"用户指定辩题：{topic.strip()}。请以辩论者身份开场，简要陈述该辩题的正反两面，并邀请用户选择立场开始辩论。用简洁自然的中文，无需寒暄。"
        else:
            trigger = (
                "用户未指定辩题。请随机给出三个适合认知压力测试的辩题，供用户选择。"
                "格式示例：1. 加班是奋斗还是剥削 2. 内卷有没有意义 3. 自由与责任的边界。"
                "用简洁自然的中文，最后可加「选一个开始，或直接提出你的辩题」。"
            )
        fallback_system = (
            "你是一名辩论助手，擅长通过追问暴露用户的认知偏误与双标。"
            "根据用户是否指定辩题，给出辩题或开场白。用简洁自然的中文。"
        )
        system_content = self._load_mirror_prompt("bian-prompts.md", fallback_system)
        if attack_level:
            desc_map = {"light": "友善追问", "medium": "适度施压", "heavy": "犀利戳穿"}
            system_content += f"\n\n本轮攻击强度：{attack_level}。请按{desc_map.get(attack_level, '适度施压')}的风格追问。"
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": trigger},
        ]
        resp = await self.agent.provider.chat(
            messages=messages,
            model=self.agent.model,
            max_tokens=600,
            temperature=0.7,
        )
        content = (resp.content or "").strip()
        if not content:
            content = "1. 加班是奋斗还是剥削 2. 内卷有没有意义 3. 自由与责任的边界。选一个开始，或直接提出你的辩题。"
        session.add_message("user", trigger)
        # 立即保存用户消息，确保即使后续处理失败也不丢失
        self.sessions.save(session)
        session.add_message("assistant", content)
        self.sessions.save(session)
        return content

    def bian_first_reply_stream(
        self, session_id: str
    ) -> tuple[queue.Queue[dict[str, Any]], threading.Thread]:
        """流式返回辩首次回复。"""
        evt_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        def run() -> None:
            try:
                evt_queue.put({"type": "thinking"})
                result = asyncio.run(self._bian_first_reply(session_id))
                evt_queue.put({"type": "done", "content": result})
            except KeyError:
                evt_queue.put({"type": "error", "message": "mirror bian session not found"})
            except Exception as e:
                logger.exception("Bian first reply failed")
                evt_queue.put({"type": "error", "message": str(e)})

        thread = threading.Thread(target=run, daemon=False)
        return evt_queue, thread

    async def _run_mirror_analysis(
        self, stype: str, key: str
    ) -> dict[str, Any] | None:
        """Run LLM analysis on mirror session for narrative/defense/insight."""
        try:
            messages_raw = self.sessions.get_messages(key=key, limit=100)
            conv_text = "\n".join(
                f"{'用户' if m['role'] == 'user' else 'AI'}: {m['content'][:500]}"
                for m in messages_raw[-20:]
            )
            # 从 mirror-system 加载封存分析 prompt（悟 或 辩）
            prompt_file = "wu-prompts.md" if stype == "wu" else "bian-prompts.md"
            fallback_system = "你输出简洁的分析，每行格式为「标签: 内容」。"
            system_content = self._load_mirror_prompt(prompt_file, fallback_system)
            if not system_content or system_content == fallback_system:
                system_content = "你是一位擅长叙事与心理分析的助手。根据以下悟道/辩论对话，提取分析结果。请以简洁中文回答，每条一行。"
            
            prompt = f"对话内容：\n{conv_text}\n\n请按以下格式输出（每行一个）：\n"
            if stype == "wu":
                prompt += (
                    "叙事结构: [第一人称/第三人称，过去/现在倾向等]\n"
                    "防御机制: [如合理化、投射、否认等]\n"
                    "潜意识关键词: [如应该、必须、没办法等]\n"
                    "核心洞察: [一句概括]\n"
                )
            else:  # bian
                prompt += (
                    "辩题/立场: [用户主要立场]\n"
                    "矛盾/谬误: [检测到的认知失调或逻辑谬误]\n"
                    "叙事结构: [第一人称/第三人称，过去/现在倾向等]\n"
                    "防御机制: [如合理化、投射、否认等]\n"
                    "潜意识关键词: [如应该、必须、没办法等]\n"
                    "核心洞察: [一句概括]\n"
                )
            msgs = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt},
            ]
            resp = await self.agent.provider.chat(
                messages=msgs,
                model=self.agent.model,
                max_tokens=800,
                temperature=0.3,
            )
            if not resp.content:
                return None
            result = {}
            for line in resp.content.strip().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    result[k.strip()] = v.strip()
            return result
        except Exception as e:
            logger.warning("Mirror LLM analysis failed: %s", e)
            return None

    async def _run_shang_analysis(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """根据赏记录的选择与归因，运行 LLM 分析（荣格类型、原型等）。"""
        try:
            topic = record.get("topic", "")
            choice = record.get("choice", "")
            desc_a = record.get("descriptionA", "")
            desc_b = record.get("descriptionB", "")
            attribution = record.get("attribution", "")
            system_content = self._load_mirror_prompt(
                "shang-prompts.md",
                "你是一位心理分析师，擅长通过审美偏好进行人格推断。",
            )
            if not system_content or len(system_content) < 50:
                system_content = (
                    "你是一位心理分析师，擅长通过审美偏好进行人格推断。"
                    "请根据用户的图像选择与归因，分析其荣格类型、大五倾向、原型特征。以简洁中文回答，每行格式为「标签: 内容」。"
                )
            prompt = f"""命题主题：{topic}
用户选择：{choice}（图A 或 图B）
图A特征：{desc_a}
图B特征：{desc_b}
用户归因：{attribution}

请按以下格式输出（每行一个）：
认知功能: [如 Intuition(N) + Thinking(T)]
类型代码: [如 NT型]
荣格解释: [一句话说明为什么]
主原型: [如 隐士 The Hermit]
次原型: [如 智者 The Sage]
大五线索: [开放性/外向性等简要描述]
"""
            msgs = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt},
            ]
            resp = await self.agent.provider.chat(
                messages=msgs,
                model=self.agent.model,
                max_tokens=600,
                temperature=0.3,
            )
            if not resp.content:
                return None
            result = {}
            for line in resp.content.strip().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    result[k.strip()] = v.strip()
            analysis: dict[str, Any] = {}
            if result.get("类型代码") or result.get("认知功能") or result.get("荣格解释"):
                analysis["jungType"] = {
                    "function": result.get("认知功能", ""),
                    "typeCode": result.get("类型代码", ""),
                    "description": result.get("荣格解释", ""),
                }
            if result.get("主原型") or result.get("次原型"):
                analysis["archetype"] = {
                    "primary": result.get("主原型", ""),
                    "secondary": result.get("次原型", ""),
                    "fear": "",
                    "need": "",
                }
            if result.get("大五线索"):
                analysis["bigFive"] = {"线索": result.get("大五线索", "")}
            return analysis if analysis else None
        except Exception as e:
            logger.warning("Shang LLM analysis failed: %s", e)
            return None

    async def generate_mirror_profile(self) -> dict[str, Any] | None:
        """融合悟/辩/赏数据生成镜画像，保存到 profile.json。"""
        import re
        fusion = self.mirror.get_fusion_data()
        if fusion["wu_count"] == 0 and fusion["bian_count"] == 0 and fusion["shang_count"] == 0:
            logger.warning("No mirror data to fuse")
            return None
        system_content = (
            "你是一位资深心理分析师，擅长综合多维数据进行人格画像。"
            "根据悟（叙事）、辩（认知压力）、赏（审美偏好）数据，归纳核心模式，生成综合画像。"
            "描述性、非诊断性；行动导向、非标签化。"
        )
        user_content = f"""# 数据来源

## 悟模块数据（语言层）
{fusion["wu_sessions_summary"][:6000]}
核心洞察：{fusion["wu_insights"]}

## 辩模块数据（行为层）
{fusion["bian_sessions_summary"][:6000]}
核心洞察：{fusion["bian_insights"]}

## 赏模块数据（直觉层）
{fusion["shang_records_summary"][:2000]}
核心洞察：{fusion["shang_insights"]}

----

# 请生成综合画像

请完成分析后，在回复末尾输出一个 JSON 代码块（用 ```json 包裹），格式如下：
```json
{{
  "bigFive": {{"openness": 80, "conscientiousness": 65, "extraversion": 30, "agreeableness": 50, "neuroticism": 60}},
  "jungArchetype": {{"primary": "隐士", "secondary": "智者"}},
  "drivers": [{{"need": "掌控感", "evidence": "来自数据的证据", "suggestion": "行动建议"}}],
  "conflicts": [{{"explicit": "显性表现", "implicit": "隐性表现", "type": "认知失调"}}],
  "suggestions": ["建议1", "建议2"],
  "mbti": {{
    "当前类型": "INFP",
    "历史类型分布": "INFP(65%), INTP(20%), ENFP(10%), 其他(5%)",
    "类型漂移": "过去3个月内在INFP-INTP边界波动",
    "维度": {{
      "EI": {{"倾向": "I", "得分": "72/28", "置信度": 75, "关键证据": ["对话中75%的话题关于内部思考", "选择独处活动的次数是社交的3倍"]}},
      "SN": {{"倾向": "N", "得分": "65/35", "置信度": 65, "关键证据": ["频繁使用比喻和抽象概念", "对未来可能性的讨论多于具体细节"]}},
      "TF": {{"倾向": "F", "得分": "58/42", "置信度": 58, "关键证据": ["决策时优先考虑人际关系", "使用感觉一词的频率是逻辑的2倍"]}},
      "JP": {{"倾向": "P", "得分": "55/45", "置信度": 55, "关键证据": ["对计划的执行有灵活调整", "对话中表现出对新选项的开放性"]}}
    }},
    "认知功能栈": {{
      "主导": {{"功能": "内倾情感 (Fi)", "强度": 85, "表现": "对话中频繁回归个人价值观和信念"}},
      "辅助": {{"功能": "外倾直觉 (Ne)", "强度": 72, "表现": "擅长发现事物之间的潜在联系"}},
      "第三": {{"功能": "内倾感觉 (Si)", "强度": 45, "表现": "偶尔依赖过去的经验做判断"}},
      "劣势": {{"功能": "外倾思维 (Te)", "强度": 25, "表现": "不擅长组织外部系统"}}
    }},
    "情境面具": [
      {{"情境": "工作场合", "显现类型": "ISTJ", "面具厚度": 70}},
      {{"情境": "亲密关系", "显现类型": "INFP", "面具厚度": 30}},
      {{"情境": "社交聚会", "显现类型": "ENFP", "面具厚度": 60}},
      {{"情境": "压力状态", "显现类型": "ISFP", "面具厚度": 75}}
    ],
    "成长建议": [
      {{"挑战": "每周制定并执行一个具体的计划", "练习": "将一个大目标分解为可执行的步骤", "预期": "6个月后Te强度从25提升至40"}},
      {{"挑战": "在决策中加入客观数据分析", "练习": "每次重大决定前写下3个客观理由", "预期": "减少情感决策导致的后悔率"}}
    ]
  }}
}}
```
大五分数为 0-100 整数。drivers、conflicts、suggestions 可为空数组。如果没有足够数据进行 MBTI 分析，mbti 字段可以省略或为 null。"""
        try:
            resp = await self.agent.provider.chat(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
                model=self.agent.model,
                max_tokens=2000,
                temperature=0.4,
            )
            if not resp.content:
                return None
            match = re.search(r"```json\s*([\s\S]*?)\s*```", resp.content)
            if match:
                profile = json.loads(match.group(1).strip())
            else:
                profile = self._parse_profile_from_text(resp.content)
            if not profile:
                return None
            self.mirror.save_profile(profile)
            return profile
        except Exception as e:
            logger.warning("Mirror profile generation failed: %s", e)
            return None

    def _parse_profile_from_text(self, text: str) -> dict[str, Any] | None:
        """从文本尝试解析 profile 结构（降级）。"""
        profile: dict[str, Any] = {
            "bigFive": {},
            "jungArchetype": {"primary": "-", "secondary": "-"},
            "drivers": [],
            "conflicts": [],
            "suggestions": [],
        }
        b5_keys = ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]
        for k in b5_keys:
            profile["bigFive"][k] = 50
        return profile

    def _save_images_to_temp(self, images: list[str]) -> list[str]:
        """
        将 base64 data URL 图片保存到当前 workspace 的 .nanobot/media 目录。
        与飞书等渠道一致，统一使用 workspace/.nanobot/media。
        调用方负责在处理完成后清理这些文件。
        """
        import base64
        import mimetypes
        import tempfile

        # 使用 workspace/.nanobot/media，与飞书等渠道一致
        workspace = getattr(self, "_workspace_path", None) or getattr(self.agent, "workspace", None)
        if workspace:
            media_dir = Path(workspace).expanduser().resolve() / ".nanobot" / "media"
        else:
            media_dir = Path.home() / ".nanobot" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        paths = []
        for data_url in images:
            try:
                if not data_url.startswith("data:"):
                    logger.warning(f"Invalid image data URL format: {data_url[:50]}...")
                    continue
                header, b64data = data_url.split(",", 1)
                mime = header.split(";")[0].split(":")[1]
                ext = mimetypes.guess_extension(mime) or ".jpg"
                if ext == ".jpe":
                    ext = ".jpg"
                
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        tmp = tempfile.NamedTemporaryFile(
                            suffix=ext, delete=False,
                            dir=media_dir
                        )
                        tmp.write(base64.b64decode(b64data))
                        tmp.close()
                        paths.append(tmp.name)
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            logger.warning(f"Retry saving image (attempt {attempt + 1}/{max_retries}): {e}")
                            continue
                        raise
            except Exception as e:
                logger.warning(f"Failed to save image after {max_retries} attempts: {e}")
        return paths

    def chat_stream(
        self, session_id: str, content: str, images: list[str] | None = None,
        tool_mode: str | None = None, selected_mcp_servers: list[str] | None = None,
    ) -> tuple[queue.Queue[dict[str, Any]], threading.Thread]:
        """
        Run chat with progress events. Returns (event_queue, thread).
        Caller reads from queue until {"type": "done"} or {"type": "error"}.
        """
        evt_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        media_paths = self._save_images_to_temp(images or [])

        # 立即保存用户消息到数据库，确保刷新界面时能看到提问（即使 nanobot 仍在回答中）
        key = self.to_session_key(session_id)
        session = self.sessions.get_or_create(key)
        user_kwargs: dict[str, Any] = {}
        if images:
            user_kwargs["images"] = images
        session.add_message("user", content or "[图片]", **user_kwargs)
        self.sessions.save(session)
        logger.info(f"[ChatStream] User message saved immediately for session {session_id}")

        from nanobot.web.chat_stream_bus import ChatStreamBus
        bus = ChatStreamBus.get()
        origin_key = f"web:{session_id}"

        def _put_evt(evt: dict[str, Any]) -> None:
            try:
                evt_queue.put(evt)
                bus.push(origin_key, evt)
            except Exception:
                pass

        def on_progress(evt: dict[str, Any]) -> None:
            _put_evt(evt)

        def run_agent() -> None:
            # 新对话开始，清空该 session 的缓冲，避免与旧事件混淆
            bus.clear_buffer(origin_key)
            # 首先发送一个开始事件，确认线程已启动
            try:
                _put_evt({"type": "start", "session_id": session_id})
            except Exception:
                pass

            logger.info(f"Chat stream thread running for session {session_id}, content: {content[:50] if content else ''}")
            loop = None
            try:
                # 创建新的事件循环（避免与主线程冲突）
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                _extra = {"user_message_saved": True}
                if tool_mode:
                    _extra["tool_mode"] = tool_mode
                if selected_mcp_servers:
                    _extra["selected_mcp_servers"] = selected_mcp_servers
                result = loop.run_until_complete(
                    self._chat_with_progress(
                        session_id, content, on_progress, media_paths,
                        extra_metadata=_extra,
                    )
                )
                logger.debug(f"Chat stream completed with result: {result}")
                _put_evt({"type": "done", **result})
            except asyncio.CancelledError:
                logger.debug("Chat stream cancelled")
                _put_evt({"type": "error", "message": "cancelled"})
            except Exception as e:
                logger.exception(f"Chat stream failed: {e}")
                _put_evt({"type": "error", "message": str(e)})
            finally:
                logger.debug("Chat stream thread ending")
                # 等待事件循环内未完成的任务（含微内核），避免 loop.close() 导致 Task was destroyed / coroutine ignored GeneratorExit
                if loop:
                    try:
                        current = asyncio.current_task()
                        pending = [t for t in asyncio.all_tasks(loop) if not t.done() and t is not current]
                        if pending:
                            logger.info(f"[ChatStream] Waiting for {len(pending)} pending tasks (incl. microkernel) before closing loop")
                            try:
                                loop.run_until_complete(asyncio.wait_for(
                                    asyncio.gather(*pending, return_exceptions=True),
                                    timeout=130,
                                ))
                            except asyncio.TimeoutError:
                                logger.warning("[ChatStream] Pending tasks timed out after 130s, cancelling")
                                for t in pending:
                                    if not t.done():
                                        t.cancel()
                                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                        loop.close()
                    except Exception as e:
                        logger.debug("Loop close error (ignored): %s", e)
                for p in media_paths:
                    try:
                        Path(p).unlink(missing_ok=True)
                    except Exception:
                        pass

        thread = threading.Thread(target=run_agent, daemon=False)
        return evt_queue, thread

    async def _chat_with_progress(
        self,
        session_id: str,
        content: str,
        progress_callback: Any,
        media: list[str] | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Internal: run chat with optional progress callback. Used by chat() and chat_stream."""
        key = self.to_session_key(session_id)
        self.sessions.get_or_create(key)
        try:
            response = await self.agent.process_direct(
                content=content,
                session_key=key,
                channel="web",
                chat_id=session_id,
                progress_callback=progress_callback,
                media=media or [],
                extra_metadata=extra_metadata,
            )
        finally:
            if getattr(self.agent, "mcp_loader", None):
                try:
                    await self.agent.reload_mcp_config()
                except BaseException:
                    pass
        messages = self.sessions.get_messages(key=key, limit=2)
        assistant = next((m for m in reversed(messages) if m["role"] == "assistant"), None)
        return {
            "content": response,
            "assistantMessage": (
                {
                    "id": f"msg_{assistant['sequence']}",
                    "sessionId": session_id,
                    "role": assistant["role"],
                    "content": assistant["content"],
                    "createdAt": assistant["timestamp"],
                    "sequence": assistant["sequence"],
                    **({"toolSteps": assistant["tool_steps"]} if assistant.get("tool_steps") else {}),
                    **(
                        {
                            "tokenUsage": {
                                "promptTokens": int(assistant["token_usage"].get("prompt_tokens", 0) or 0),
                                "completionTokens": int(assistant["token_usage"].get("completion_tokens", 0) or 0),
                                "totalTokens": int(assistant["token_usage"].get("total_tokens", 0) or 0),
                            }
                        }
                        if assistant.get("token_usage")
                        else {}
                    ),
                }
                if assistant
                else None
            ),
        }

    def chat_stream_resume(self, session_id: str) -> "queue.Queue[dict[str, Any]]":
        """
        订阅指定 session 的 Chat 流式事件（用于刷新/切换 tab 后重连 SSE）。
        返回的 Queue 会接收 start / tool_start / tool_end / done / error 等事件，
        支持 replay 已发生的事件。
        """
        from nanobot.web.chat_stream_bus import ChatStreamBus
        origin_key = f"web:{session_id}"
        return ChatStreamBus.get().subscribe(origin_key, replay=True)

    def subagent_progress_stream(self, session_id: str) -> "queue.Queue[dict[str, Any]]":
        """
        订阅指定 web session 的子 Agent 进度事件队列。

        origin_key = "web:{session_id}"（与 SpawnTool.set_context("web", session_id) 对应）。
        返回的 Queue 会持续接收 subagent_start / subagent_progress / subagent_end 事件，
        直到调用方手动取消订阅。
        """
        from nanobot.agent.subagent_progress import SubagentProgressBus
        origin_key = f"web:{session_id}"
        return SubagentProgressBus.get().subscribe(origin_key, replay=True)

    def unsubscribe_subagent_progress(
        self, session_id: str, q: "queue.Queue[dict[str, Any]]"
    ) -> None:
        """取消订阅子 Agent 进度队列。"""
        from nanobot.agent.subagent_progress import SubagentProgressBus
        SubagentProgressBus.get().unsubscribe(f"web:{session_id}", q)

    async def chat(
        self, session_id: str, content: str, images: list[str] | None = None,
        tool_mode: str | None = None, selected_mcp_servers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat. Reuses _chat_with_progress without callback."""
        media_paths = self._save_images_to_temp(images or [])
        extra: dict[str, Any] = {}
        if tool_mode:
            extra["tool_mode"] = tool_mode
        if selected_mcp_servers:
            extra["selected_mcp_servers"] = selected_mcp_servers
        try:
            return await self._chat_with_progress(
                session_id, content, progress_callback=None, media=media_paths, extra_metadata=extra or None,
            )
        finally:
            for p in media_paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass

    def get_config(self) -> dict[str, Any]:
        """Get all configuration data."""
        config = load_config()
        
        # Channels (IM) configuration
        channels = {
            "gateway": {
                "running": self.gateway_process is not None and self.gateway_process.poll() is None
            },
            "whatsapp": {
                "enabled": config.channels.whatsapp.enabled,
                "bridgeUrl": config.channels.whatsapp.bridge_url,
                "allowFrom": config.channels.whatsapp.allow_from,
            },
            "telegram": {
                "enabled": config.channels.telegram.enabled,
                "token": config.channels.telegram.token if config.channels.telegram.token else "",
                "allowFrom": config.channels.telegram.allow_from,
                "proxy": config.channels.telegram.proxy,
            },
            "feishu": {
                "enabled": config.channels.feishu.enabled,
                "appId": config.channels.feishu.app_id,
                "appSecret": "***" if config.channels.feishu.app_secret else "",
                "encryptKey": config.channels.feishu.encrypt_key,
                "verificationToken": config.channels.feishu.verification_token,
                "allowFrom": config.channels.feishu.allow_from,
            },
            "discord": {
                "enabled": config.channels.discord.enabled,
                "token": config.channels.discord.token if config.channels.discord.token else "",
                "allowFrom": config.channels.discord.allow_from,
            },
            "qq": {
                "enabled": config.channels.qq.enabled,
                "appId": config.channels.qq.app_id,
                "secret": "***" if config.channels.qq.secret else "",
                "allowFrom": config.channels.qq.allow_from,
            },
            "dingtalk": {
                "enabled": config.channels.dingtalk.enabled,
                "clientId": config.channels.dingtalk.client_id,
                "clientSecret": "***" if config.channels.dingtalk.client_secret else "",
                "allowFrom": config.channels.dingtalk.allow_from,
            },
        }
        
        # Providers (AI) configuration
        provider_display_names = {
            "anthropic": "Anthropic",
            "openai": "OpenAI",
            "openrouter": "OpenRouter",
            "deepseek": "DeepSeek",
            "groq": "Groq",
            "zhipu": "Zhipu (智谱)",
            "dashscope": "Qwen (通义)",
            "gemini": "Gemini",
            "vllm": "vLLM",
            "ollama": "Ollama",
            "minimax": "Minimax",
        }
        providers = []
        for provider_name in ['anthropic', 'openai', 'openrouter', 'deepseek', 'groq', 'zhipu', 'dashscope', 'gemini', 'vllm', 'ollama', 'minimax']:
            provider_config = getattr(config.providers, provider_name)
            if provider_config.api_key or provider_config.api_base:
                # Ollama 仅需 api_base 即可启用
                enabled = bool(provider_config.api_key) or (
                    provider_name == "ollama" and provider_config.api_base
                )
                providers.append({
                    "id": provider_name,
                    "name": provider_display_names.get(provider_name, provider_name.capitalize()),
                    "type": provider_name,
                    "apiKey": provider_config.api_key or None,  # 配置页需展示真实 key 以便编辑
                    "apiBase": provider_config.api_base,
                    "enabled": enabled,
                })
        
        # Create default model entry - 使用 ModelRouter 解析的实际模型
        effective_model = self._get_effective_model()
        models = [{
            "id": "default",
            "name": effective_model,
            "providerId": effective_model.split('/')[0] if '/' in effective_model else "openai",
            "modelName": effective_model,
            "enabled": True,
            "isDefault": True,
            "parameters": {
                "temperature": config.agents.defaults.temperature,
                "maxTokens": config.agents.defaults.max_tokens,
            },
            "qwenImageModel": (config.mirror.qwen_image_model or "").strip(),
            "subagentModel": (getattr(config.agents.defaults, "subagent_model", "") or "").strip(),
        }]
        
        # Load skills
        from nanobot.agent.skills import SkillsLoader
        skills_loader = SkillsLoader(config.workspace_path)
        skills_list = skills_loader.list_skills(filter_unavailable=False)
        
        skills_data = []
        for s in skills_list:
            # metadata is a dict from frontmatter
            meta = skills_loader.get_skill_metadata(s["name"]) or {}
            skills_data.append({
                "id": s["name"],
                "name": meta.get("name", s["name"]),
                "version": meta.get("version", "1.0.0"),
                "description": meta.get("description", "No description"),
                "enabled": True, # Skills are enabled by default if present
                "author": meta.get("author"),
                "tags": [t.strip() for t in meta.get("tags", "").split(",")] if meta.get("tags") else []
            })

        # Agent system config (max_tool_iterations, max_execution_time, microkernel)
        agent = {
            "maxToolIterations": config.agents.defaults.max_tool_iterations,
            "maxExecutionTime": getattr(config.agents.defaults, "max_execution_time", 600) or 0,
            "microkernelEscalationEnabled": getattr(
                config.agents.defaults, "microkernel_escalation_enabled", True
            ),
            "microkernelEscalationThreshold": getattr(
                config.agents.defaults, "microkernel_escalation_threshold", 10
            ),
        }

        return {
            "channels": channels,
            "providers": providers,
            "models": models,
            "agent": agent,
            "memory": {
                "auto_integrate_enabled": config.memory.auto_integrate_enabled,
                "auto_integrate_interval_minutes": config.memory.auto_integrate_interval_minutes,
                "lookback_minutes": config.memory.lookback_minutes,
                "max_messages": config.memory.max_messages,
                "max_entries": config.memory.max_entries,
                "max_chars": config.memory.max_chars,
                "read_max_entries": config.memory.read_max_entries,
                "read_max_chars": config.memory.read_max_chars,
            },
            "mcps": [
                {
                    "id": m.id,
                    "name": m.name,
                    "transport": m.transport,
                    "command": m.command,
                    "args": m.args,
                    "url": m.url,
                    "enabled": m.enabled,
                    "env": m.env,
                    "headers": m.headers,
                    "tools": self._get_stored_mcp_tools().get(m.id, []),
                    "scope": m.scope or [],
                }
                for m in config.mcps
            ],
            "skills": skills_data
        }

    def create_provider(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create/Enable a new AI provider configuration."""
        config = load_config()
        provider_type = data.get("type", "").lower()
        
        if not provider_type or provider_type not in ['anthropic', 'openai', 'openrouter', 'deepseek', 'groq', 'zhipu', 'dashscope', 'gemini', 'vllm', 'ollama', 'minimax']:
            raise ValueError("Invalid provider type")
        
        provider_config = getattr(config.providers, provider_type)
        provider_config.api_key = data.get("apiKey", "")
        provider_config.api_base = data.get("apiBase")
        
        from nanobot.config.loader import save_config
        save_config(config)
        
        # 立即更新该 provider 的环境变量，供后续 chat 使用
        if provider_config.api_key and hasattr(self.agent.provider, "ensure_api_key_for_model"):
            self.agent.provider.ensure_api_key_for_model(
                f"{provider_type}/placeholder",
                provider_config.api_key,
                provider_config.api_base,
            )
        
        enabled = bool(provider_config.api_key) or (
            provider_type == "ollama" and provider_config.api_base
        )
        return {
            "id": provider_type,
            "name": data.get("name", provider_type.capitalize()),
            "type": provider_type,
            "apiBase": provider_config.api_base,
            "apiKey": provider_config.api_key or None,
            "enabled": enabled,
        }

    def update_provider(self, provider_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update AI provider configuration."""
        config = load_config()
        
        if provider_id not in ['anthropic', 'openai', 'openrouter', 'deepseek', 'groq', 'zhipu', 'dashscope', 'gemini', 'vllm', 'ollama', 'minimax']:
            raise KeyError("Provider not found")
        
        provider_config = getattr(config.providers, provider_id)
        if "apiKey" in data and data["apiKey"] != "***":
            # 占位符 "***" 不覆盖；空串或新值则更新
            provider_config.api_key = data["apiKey"]
        if "apiBase" in data:
            provider_config.api_base = data["apiBase"]
        
        from nanobot.config.loader import save_config
        save_config(config)
        
        # 始终更新该 provider 的环境变量（MINIMAX_API_KEY 等），供 LiteLLM 使用
        # 否则当使用 profile 解析到 minimax 时，env 中无 key 会导致 401
        if provider_config.api_key and hasattr(self.agent.provider, "ensure_api_key_for_model"):
            self.agent.provider.ensure_api_key_for_model(
                f"{provider_id}/placeholder",
                provider_config.api_key,
                provider_config.api_base,
            )
        # 若当前默认模型使用此 provider，热更新 agent 的 provider 实例配置
        model_name = self._get_effective_model()
        if model_name and model_name.split("/")[0] == provider_id:
            if hasattr(self.agent.provider, "update_config"):
                api_key = config.get_api_key(model_name)
                api_base = config.get_api_base(model_name)
                self.agent.provider.update_config(model_name, api_key, api_base)
        
        enabled = bool(provider_config.api_key) or (
            provider_id == "ollama" and provider_config.api_base
        )
        return {
            "id": provider_id,
            "name": data.get("name", provider_id.capitalize()),
            "type": provider_id,
            "apiBase": provider_config.api_base,
            "apiKey": provider_config.api_key or None,
            "enabled": enabled,
        }

    def delete_provider(self, provider_id: str) -> bool:
        """Disable AI provider configuration."""
        config = load_config()
        
        if provider_id not in ['anthropic', 'openai', 'openrouter', 'deepseek', 'groq', 'zhipu', 'dashscope', 'gemini', 'vllm', 'ollama', 'minimax']:
            return False
        
        provider_config = getattr(config.providers, provider_id)
        provider_config.api_key = ""
        provider_config.api_base = None

        save_config(config)
        return True

    def create_mcp(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new MCP server configuration.

        Supports two formats:
        1. Flat format: {"id": "...", "name": "...", "transport": "...", ...}
        2. Standard MCP config format: {"mcpServers": {"name": {"type": "...", "url": "..."}}}
        """
        config = load_config()

        # Handle standard MCP config format (e.g., from Claude Desktop)
        if "mcpServers" in data:
            mcp_servers = data["mcpServers"]
            if not isinstance(mcp_servers, dict) or not mcp_servers:
                raise ValueError("mcpServers 必须是非空对象")

            results = []
            for server_name, server_config in mcp_servers.items():
                if not isinstance(server_config, dict):
                    raise ValueError(f"MCP server '{server_name}' 配置必须是对象")

                # Convert standard format to internal format
                # Standard uses "type", we use "transport"
                transport = server_config.get("type", "stdio").lower()
                # Convert hyphen to underscore (e.g., "streamable-http" -> "streamable_http")
                transport = transport.replace("-", "_")

                # Generate ID from name (or use existing name as ID)
                mcp_id = server_name.strip()
                # Sanitize ID: keep only allowed chars
                mcp_id = re.sub(r'[^a-zA-Z0-9._-]', '_', mcp_id)
                if not mcp_id:
                    mcp_id = str(uuid4()).replace("-", "")[:12]

                # Ensure unique ID
                existing_ids = {m.id for m in config.mcps}
                original_id = mcp_id
                counter = 1
                while mcp_id in existing_ids:
                    mcp_id = f"{original_id}_{counter}"
                    counter += 1

                # Build internal format
                internal_data = {
                    "id": mcp_id,
                    "name": server_name.strip() or mcp_id,
                    "transport": transport,
                    "command": server_config.get("command"),
                    "args": server_config.get("args", []),
                    "url": server_config.get("url"),
                    "enabled": server_config.get("enabled", not server_config.get("disabled", False)),
                    "env": server_config.get("env", {}),
                    "headers": server_config.get("headers", {}),
                }

                result = self._create_mcp_internal(config, internal_data)
                results.append(result)

            return results[0] if len(results) == 1 else {"servers": results}

        # Handle flat format (original)
        return self._create_mcp_internal(config, data)

    def _create_mcp_internal(self, config, data: dict[str, Any]) -> dict[str, Any]:
        """Internal method to create a single MCP server configuration."""
        mcp_id = data.get("id") or ""
        # If ID is empty or contains non-ASCII, generate a UUID
        if not mcp_id or not re.match(r"^[a-zA-Z0-9._-]+$", mcp_id):
            mcp_id = str(uuid4()).replace("-", "")[:12]
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("name 不能为空")
        transport = (data.get("transport") or "stdio").lower()
        if transport not in ("stdio", "http", "sse", "streamable_http"):
            raise ValueError("transport 必须为 stdio、http、sse 或 streamable_http")
        if transport == "stdio" and not data.get("command"):
            raise ValueError("stdio 模式需要 command")
        if transport in ("http", "sse", "streamable_http") and not data.get("url"):
            raise ValueError("http/sse/streamable_http 模式需要 url")
        existing_ids = {m.id for m in config.mcps}
        if mcp_id in existing_ids:
            raise ValueError(f"MCP id 已存在: {mcp_id}")
        mcp = McpServerConfig(
            id=mcp_id,
            name=name,
            transport=transport,
            command=data.get("command"),
            args=data.get("args") or [],
            url=data.get("url"),
            enabled=data.get("enabled", True),
            env=data.get("env") or {},
            headers=data.get("headers") or {},
            scope=data.get("scope") or [],
        )
        config.mcps.append(mcp)
        save_config(config)
        # Also persist to database (env stored via set_mcp)
        repo = get_config_repository()
        repo.set_mcp(
            mcp_id=mcp.id,
            name=mcp.name,
            transport=mcp.transport,
            command=mcp.command,
            args=mcp.args,
            url=mcp.url,
            enabled=mcp.enabled,
            env=mcp.env,
            headers=mcp.headers,
            scope=mcp.scope,
        )
        return {
            "id": mcp.id,
            "name": mcp.name,
            "transport": mcp.transport,
            "command": mcp.command,
            "args": mcp.args,
            "url": mcp.url,
            "enabled": mcp.enabled,
            "env": mcp.env,
            "headers": mcp.headers,
            "scope": mcp.scope or [],
            "tools": mcp.tools or [],
        }

    def update_mcp(self, mcp_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update MCP server configuration."""
        config = load_config()
        mcp = next((m for m in config.mcps if m.id == mcp_id), None)
        if not mcp:
            raise KeyError(f"MCP 不存在: {mcp_id}")
        if "name" in data and data["name"] is not None:
            mcp.name = str(data["name"]).strip() or mcp.name
        if "transport" in data and data["transport"]:
            t = str(data["transport"]).lower()
            if t in ("stdio", "http", "sse", "streamable_http"):
                mcp.transport = t
        if "command" in data:
            mcp.command = data["command"] or None
        if "args" in data:
            mcp.args = list(data["args"]) if data["args"] else []
        if "url" in data:
            mcp.url = data["url"] or None
        if "enabled" in data:
            mcp.enabled = bool(data["enabled"])
        if "env" in data:
            mcp.env = dict(data["env"]) if data["env"] else {}
        if "headers" in data:
            mcp.headers = dict(data["headers"]) if data["headers"] else {}
        if "scope" in data:
            mcp.scope = list(data["scope"]) if data["scope"] else []
        save_config(config)
        # Persist scope and tools to SQLite (scope changes otherwise lost on reload)
        repo = get_config_repository()
        repo.set_mcp(
            mcp_id=mcp.id,
            name=mcp.name,
            transport=mcp.transport,
            command=mcp.command,
            args=mcp.args,
            url=mcp.url,
            enabled=mcp.enabled,
            env=mcp.env,
            headers=mcp.headers,
            scope=mcp.scope,
            tools=mcp.tools or [],
        )
        return {"id": mcp.id, "name": mcp.name, "transport": mcp.transport, "command": mcp.command, "args": mcp.args, "url": mcp.url, "enabled": mcp.enabled, "env": mcp.env, "headers": mcp.headers, "scope": mcp.scope or [], "tools": mcp.tools or []}

    def delete_mcp(self, mcp_id: str) -> bool:
        """Delete MCP server configuration."""
        # 清理 ID：去除前后空格
        mcp_id_clean = mcp_id.strip()
        logger.info(f"Deleting MCP: '{mcp_id}' (cleaned: '{mcp_id_clean}')")

        config = load_config()

        # 记录当前所有 MCP ID 用于调试
        available_ids = [m.id.strip() if m.id else "" for m in config.mcps]
        logger.info(f"Available MCPs: {available_ids}")

        before = len(config.mcps)

        # 更宽松的匹配：清理空格后进行匹配
        config.mcps = [m for m in config.mcps if (m.id or "").strip() != mcp_id_clean]

        if len(config.mcps) == before:
            logger.warning(f"MCP not found after cleanup: '{mcp_id_clean}'")
            # 尝试大小写不敏感匹配
            mcp_id_lower = mcp_id_clean.lower()
            config.mcps = [m for m in config.mcps if (m.id or "").strip().lower() != mcp_id_lower]
            if len(config.mcps) == before:
                logger.error(f"MCP '{mcp_id_clean}' not found in configuration")
                return False
            else:
                logger.info(f"MCP deleted using case-insensitive match: '{mcp_id_clean}'")
        else:
            logger.info(f"MCP deleted: '{mcp_id_clean}'")

        # Also delete from database to ensure it's truly removed
        repo = get_config_repository()
        try:
            repo.delete_mcp(mcp_id_clean)
            logger.debug(f"MCP deleted from database: '{mcp_id_clean}'")
        except Exception as e:
            logger.warning(f"Failed to delete MCP from database: {e}")

        # 保存配置并检查结果
        try:
            save_config(config)
            logger.info(f"Config saved successfully after deleting MCP: '{mcp_id_clean}'")
        except Exception as e:
            logger.error(f"Failed to save config after deleting MCP '{mcp_id_clean}': {e}")
            raise

        logger.info(f"MCP deleted successfully: '{mcp_id_clean}'")
        return True

    async def get_mcps_with_tools(self) -> list[dict[str, Any]]:
        """
        Get MCP list from config, discover tools for each server (connects, lists, disconnects).
        Returns MCP configs enriched with tools information.
        """
        import asyncio
        from nanobot.config.loader import load_config
        from nanobot.mcp.loader import McpToolLoader, _safe_id

        config = load_config()
        mcps = getattr(config, "mcps", None) or []
        if not mcps:
            return []

        workspace = config.workspace_path
        loader = McpToolLoader(mcps, workspace)

        results = []
        for mcp_cfg in mcps:
            mcp_dict = {
                "id": getattr(mcp_cfg, "id", "") or "",
                "name": getattr(mcp_cfg, "name", "") or "",
                "transport": getattr(mcp_cfg, "transport", "stdio") or "stdio",
                "command": getattr(mcp_cfg, "command", None),
                "args": getattr(mcp_cfg, "args", None) or [],
                "url": getattr(mcp_cfg, "url", None),
                "env": dict(getattr(mcp_cfg, "env", None) or {}),
                "headers": dict(getattr(mcp_cfg, "headers", None) or {}),
                "enabled": getattr(mcp_cfg, "enabled", True),
                "scope": list(getattr(mcp_cfg, "scope", None) or []),
                "tools": [],
            }

            # Try to discover tools (connect, list, disconnect)
            server_id = mcp_dict["id"] or mcp_dict["name"]
            try:
                result = await asyncio.wait_for(
                    loader.connect_lazy(server_id, timeout=10.0),
                    timeout=15.0,
                )
                if result:
                    session, tools = result
                    mcp_dict["tools"] = [
                        {
                            "name": t.name,
                            "description": getattr(t, "description", "") or "",
                        }
                        for t in tools
                    ]
                    try:
                        await session.__aexit__(None, None, None)
                    except BaseException:
                        pass

                    # Persist discovered tools to SQLite
                    if mcp_dict["tools"]:
                        try:
                            repo = get_config_repository()
                            repo.set_mcp(
                                mcp_id=mcp_cfg.id,
                                name=mcp_dict["name"],
                                transport=mcp_dict["transport"],
                                command=mcp_dict["command"],
                                args=mcp_dict["args"],
                                url=mcp_dict["url"],
                                enabled=mcp_dict["enabled"],
                                env=mcp_dict["env"],
                                headers=mcp_dict["headers"],
                                scope=mcp_dict["scope"],
                                tools=mcp_dict["tools"],
                            )
                        except Exception as e:
                            logger.warning(f"Failed to persist tools for {mcp_cfg.id}: {e}")
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug(f"MCP {server_id}: tool discovery skipped: {e}")

            results.append(mcp_dict)

        return results

    async def discover_mcp_tools(self, mcp_id: str) -> list[dict[str, Any]]:
        """
        Connect to a single MCP server and discover its tools.
        Returns list of tool schemas [{name, description, parameters}, ...].
        Saves discovered tools to database for persistence.
        """
        import asyncio
        from nanobot.config.loader import load_config
        from nanobot.mcp.loader import McpToolLoader, _safe_id
        from nanobot.config.loader import get_config_repository

        config = load_config()
        mcp_cfg = next((m for m in config.mcps if m.id == mcp_id), None)
        if not mcp_cfg:
            raise KeyError(f"MCP 不存在: {mcp_id}")

        workspace = config.workspace_path
        loader = McpToolLoader([mcp_cfg], workspace)
        server_id = mcp_cfg.id or _safe_id(mcp_cfg.name)

        discovered_tools: list[dict[str, Any]] = []
        try:
            result = await asyncio.wait_for(
                loader.connect_lazy(server_id, timeout=10.0),
                timeout=15.0,
            )
            if result:
                _, tools = result
                discovered_tools = [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.inputSchema or {"type": "object", "properties": {}},
                    }
                    for t in tools
                ]
        except asyncio.TimeoutError:
            logger.warning(f"MCP {server_id}: tool discovery timeout")
        except Exception as e:
            logger.warning(f"MCP {server_id}: tool discovery failed: {e}")

        # Save discovered tools to database (even if empty, to mark as "attempted")
        try:
            repo = get_config_repository()
            existing = repo.get_mcp(mcp_id)
            if existing:
                repo.set_mcp(
                    mcp_id=mcp_id,
                    name=existing.get("name", ""),
                    transport=existing.get("transport", "stdio"),
                    command=existing.get("command"),
                    args=existing.get("args"),
                    url=existing.get("url"),
                    enabled=existing.get("enabled", True),
                    env=existing.get("env"),
                    headers=existing.get("headers"),
                    scope=existing.get("scope"),
                    tools=discovered_tools,
                )
                logger.debug(f"[MCP] Saved {len(discovered_tools)} tools for {mcp_id}")
        except Exception as e:
            logger.warning(f"[MCP] Failed to save tools for {mcp_id}: {e}")

        return discovered_tools

    def test_mcp(self, mcp_id: str) -> dict[str, Any]:
        """Test MCP connection. Returns {connected: bool, message: str}."""
        import subprocess
        import urllib.request
        import urllib.error
        import json

        config = load_config()
        mcp = next((m for m in config.mcps if m.id == mcp_id), None)
        if not mcp:
            raise KeyError(f"MCP 不存在: {mcp_id}")

        # 根据 transport 类型动态选择测试方式
        transport = mcp.transport
        url = mcp.url
        command = mcp.command

        # 通用 HTTP headers
        headers = {}
        if mcp.headers:
            headers = dict(mcp.headers)
        if "Authorization" not in headers and mcp.env and "ANTHROPIC_API_KEY" in mcp.env:
            # 如果环境变量中有 API key，添加到 headers
            headers["Authorization"] = f"Bearer {mcp.env['ANTHROPIC_API_KEY']}"

        def try_http_request(req_method: str, req_data: bytes | None = None, timeout: int = 10) -> tuple[bool, str]:
            """尝试 HTTP 请求"""
            try:
                req = urllib.request.Request(
                    url,
                    data=req_data,
                    headers=headers,
                    method=req_method
                )
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    result = response.read().decode("utf-8")
                    return True, f"连接成功: {result[:200]}"
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                # 某些 MCP 服务器可能返回 405/406 但仍然可用
                if e.code in (405, 406, 400):
                    return True, f"服务器响应 {e.code}，但连接可达"
                return False, f"HTTP {e.code}: {e.reason} - {body[:200]}"
            except urllib.error.URLError as e:
                return False, f"连接失败: {e.reason}"
            except Exception as e:
                return False, f"错误: {str(e)}"

        try:
            if transport == "streamable_http":
                # streamable_http 需要发送 JSON-RPC 初始化请求
                init_request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "nanobot",
                            "version": "1.0.0"
                        }
                    }
                }
                req_data = json.dumps(init_request).encode("utf-8")
                req_headers = dict(headers)
                req_headers["Content-Type"] = "application/json"
                req_headers["Accept"] = "application/json, text/event-stream"

                try:
                    req = urllib.request.Request(url, data=req_data, headers=req_headers, method="POST")
                    with urllib.request.urlopen(req, timeout=10) as response:
                        result = response.read().decode("utf-8")
                        return {"connected": True, "message": f"连接成功: {result[:200]}"}
                except urllib.error.HTTPError as e:
                    if e.code in (405, 406):
                        # 尝试 GET 请求看服务器是否可达
                        connected, msg = try_http_request("GET", None, 5)
                        if connected:
                            return {"connected": True, "message": f"POST 返回 {e.code}，但 GET 成功: {msg}"}
                    body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                    return {"connected": False, "message": f"HTTP {e.code}: {body[:200]}"}
                except Exception as e:
                    return {"connected": False, "message": f"连接错误: {str(e)}"}

            elif transport == "sse":
                # SSE 使用 GET 请求，期望 event-stream 响应
                connected, msg = try_http_request("GET", None, 5)
                return {"connected": connected, "message": msg}

            elif transport == "http":
                # HTTP 使用 GET 请求
                connected, msg = try_http_request("GET", None, 5)
                return {"connected": connected, "message": msg}

            elif transport == "stdio":
                if not command:
                    return {"connected": False, "message": "stdio transport 需要 command 参数"}
                cmd = [command] + (mcp.args or [])
                # Windows: npx/npm 等是 .cmd 批处理，Popen(shell=False) 无法直接执行，需用 cmd /c 包装
                if os.name == "nt" and not os.path.dirname(command):
                    cmd = ["cmd", "/c", command] + (mcp.args or [])
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        env={**os.environ, **mcp.env} if mcp.env else None,
                    )
                except FileNotFoundError:
                    return {"connected": False, "message": (
                        f"找不到可执行文件 '{command}'。请确保：1) 已安装并在 PATH 中；"
                        "2) Windows 下可尝试使用完整路径（如 npx.cmd、docker.exe）；"
                        "3) 若用 Docker，请确认 Docker Desktop 已启动且 PATH 正确。"
                    )}
                except OSError as e:
                    if getattr(e, "errno", None) == 2:  # WinError 2 / ENOENT
                        return {"connected": False, "message": f"找不到可执行文件 '{command}'。请检查 PATH 或使用完整路径。"}
                    raise
                import time
                time.sleep(1.5)
                if proc.poll() is not None:
                    err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
                    return {"connected": False, "message": err or f"进程退出码 {proc.returncode}"}
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return {"connected": True, "message": "进程启动成功"}

        except Exception as e:
            return {"connected": False, "message": str(e)}

    def update_agent_config(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update agent system config (max_tool_iterations, max_execution_time, microkernel). Hot-updates running agent."""
        config = load_config()
        defaults = config.agents.defaults
        if "maxToolIterations" in data and data["maxToolIterations"] is not None:
            v = int(data["maxToolIterations"])
            defaults.max_tool_iterations = max(1, min(v, 200))
        if "maxExecutionTime" in data and data["maxExecutionTime"] is not None:
            v = int(data["maxExecutionTime"])
            defaults.max_execution_time = max(0, v)
        if "microkernelEscalationEnabled" in data and data["microkernelEscalationEnabled"] is not None:
            defaults.microkernel_escalation_enabled = bool(data["microkernelEscalationEnabled"])
        if "microkernelEscalationThreshold" in data and data["microkernelEscalationThreshold"] is not None:
            v = int(data["microkernelEscalationThreshold"])
            defaults.microkernel_escalation_threshold = max(1, min(v, 50))
        save_config(config)
        self.agent.update_agent_params(
            max_iterations=defaults.max_tool_iterations,
            max_execution_time=defaults.max_execution_time,
            microkernel_escalation_enabled=defaults.microkernel_escalation_enabled,
            microkernel_escalation_threshold=defaults.microkernel_escalation_threshold,
        )
        return {
            "maxToolIterations": defaults.max_tool_iterations,
            "maxExecutionTime": defaults.max_execution_time,
            "microkernelEscalationEnabled": defaults.microkernel_escalation_enabled,
            "microkernelEscalationThreshold": defaults.microkernel_escalation_threshold,
        }

    def get_concurrency_config(self) -> dict[str, Any]:
        """Get concurrency configuration from database."""
        try:
            config = self.status_service.get_concurrency_config()
            return config
        except Exception as e:
            logger.exception("Failed to get concurrency config")
            return {
                "max_parallel_tool_calls": 5,
                "max_concurrent_subagents": 10,
                "enable_parallel_tools": True,
                "thread_pool_size": 4,
                "enable_subagent_parallel": True,
                "claude_code_max_concurrent": 3,
                "claude_code_permission_mode": "auto",
            }

    def update_concurrency_config(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update concurrency configuration and persist to database."""
        config_map = {
            "maxParallelToolCalls": "max_parallel_tool_calls",
            "maxConcurrentSubagents": "max_concurrent_subagents",
            "enableParallelTools": "enable_parallel_tools",
            "threadPoolSize": "thread_pool_size",
            "enableSubagentParallel": "enable_subagent_parallel",
            "claudeCodeMaxConcurrent": "claude_code_max_concurrent",
            "claudeCodePermissionMode": "claude_code_permission_mode",
            "enableSmartParallel": "enable_smart_parallel",
            "smartParallelModel": "smart_parallel_model",
        }

        config = {}
        for web_key, db_key in config_map.items():
            if web_key in data and data[web_key] is not None:
                if web_key in ("enableParallelTools", "enableSubagentParallel", "enableSmartParallel"):
                    config[db_key] = bool(data[web_key])
                elif web_key in ("smartParallelModel", "claudeCodePermissionMode"):
                    config[db_key] = str(data[web_key])
                else:
                    config[db_key] = int(data[web_key])

        # 保存到数据库
        self.status_service.set_concurrency_config(config)

        # 更新运行中的 agent 配置
        if self.agent:
            if "maxParallelToolCalls" in data and data["maxParallelToolCalls"] is not None:
                self.agent._max_parallel_tool_calls = int(data["maxParallelToolCalls"])
            if "enableParallelTools" in data and data["enableParallelTools"] is not None:
                self.agent._enable_parallel_tools = bool(data["enableParallelTools"])
            if "threadPoolSize" in data and data["threadPoolSize"] is not None:
                self.agent._thread_pool_size = int(data["threadPoolSize"])
            if "enableSmartParallel" in data and data["enableSmartParallel"] is not None:
                self.agent._enable_smart_parallel = bool(data["enableSmartParallel"])
            if "smartParallelModel" in data and data["smartParallelModel"] is not None:
                self.agent._smart_parallel_model = str(data["smartParallelModel"])

        return self.get_concurrency_config()

    def get_metrics(self) -> dict[str, Any]:
        """Get monitoring metrics from database."""
        try:
            return self.status_service.get_metrics()
        except Exception as e:
            logger.exception("Failed to get metrics")
            return {}

    def reset_metrics(self) -> None:
        """Reset all monitoring metrics."""
        try:
            self.status_service.reset_metrics()
        except Exception as e:
            logger.exception("Failed to reset metrics")

    def update_memory_config(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update memory system configuration. Hot-updates running memory services."""
        config = load_config()
        memory = config.memory

        if "auto_integrate_enabled" in data and data["auto_integrate_enabled"] is not None:
            memory.auto_integrate_enabled = bool(data["auto_integrate_enabled"])
        if "auto_integrate_interval_minutes" in data and data["auto_integrate_interval_minutes"] is not None:
            memory.auto_integrate_interval_minutes = max(1, int(data["auto_integrate_interval_minutes"]))
        if "lookback_minutes" in data and data["lookback_minutes"] is not None:
            memory.lookback_minutes = max(1, int(data["lookback_minutes"]))
        if "max_messages" in data and data["max_messages"] is not None:
            memory.max_messages = max(1, int(data["max_messages"]))
        if "max_entries" in data and data["max_entries"] is not None:
            memory.max_entries = max(10, int(data["max_entries"]))
        if "max_chars" in data and data["max_chars"] is not None:
            memory.max_chars = max(1024, int(data["max_chars"]))
        if "read_max_entries" in data and data["read_max_entries"] is not None:
            memory.read_max_entries = max(1, int(data["read_max_entries"]))
        if "read_max_chars" in data and data["read_max_chars"] is not None:
            memory.read_max_chars = max(1024, int(data["read_max_chars"]))

        save_config(config)

        # Hot-update running memory services
        self._update_memory_services()

        return {
            "auto_integrate_enabled": memory.auto_integrate_enabled,
            "auto_integrate_interval_minutes": memory.auto_integrate_interval_minutes,
            "lookback_minutes": memory.lookback_minutes,
            "max_messages": memory.max_messages,
            "max_entries": memory.max_entries,
            "max_chars": memory.max_chars,
            "read_max_entries": memory.read_max_entries,
            "read_max_chars": memory.read_max_chars,
        }

    def _update_memory_services(self) -> None:
        """Hot-update running memory services with new config."""
        config = load_config()

        # Update MemoryMaintenanceService if running
        if hasattr(self, 'memory_maintenance') and self.memory_maintenance:
            self.memory_maintenance.tick_interval_min = config.memory.auto_integrate_interval_minutes
            self.memory_maintenance.summarize_interval_min = config.memory.auto_integrate_interval_minutes
            self.memory_maintenance._max_entries = config.memory.max_entries
            self.memory_maintenance._max_chars = config.memory.max_chars
            logger.info("MemoryMaintenanceService hot-updated with new config")

        # Update AutoMemoryIntegrationService if running
        if hasattr(self, 'auto_memory_integration') and self.auto_memory_integration:
            self.auto_memory_integration.lookback_minutes = config.memory.lookback_minutes
            self.auto_memory_integration.max_messages = config.memory.max_messages
            logger.info("AutoMemoryIntegrationService hot-updated with new config")

        # Update cron job intervals to match memory config
        try:
            from nanobot.storage.cron_repository import CronRepository
            integrate_interval = config.memory.auto_integrate_interval_minutes * 60
            maintenance_interval = config.memory.auto_integrate_interval_minutes * 60  # Use same interval for now

            self.cron_service.update_job(
                job_id=CronRepository.SYSTEM_MEMORY_INTEGRATE,
                trigger_interval_seconds=integrate_interval,
            )
            self.cron_service.update_job(
                job_id=CronRepository.SYSTEM_MEMORY_MAINTENANCE,
                trigger_interval_seconds=maintenance_interval,
            )
            logger.info(f"Cron jobs intervals updated: integrate={integrate_interval}s, maintenance={maintenance_interval}s")
        except Exception as e:
            logger.warning(f"Failed to update cron job intervals: {e}")

    def update_channels(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update IM channels configuration."""
        config = load_config()
        
        # WhatsApp
        if "whatsapp" in data:
            wa = data["whatsapp"]
            config.channels.whatsapp.enabled = wa.get("enabled", config.channels.whatsapp.enabled)
            config.channels.whatsapp.bridge_url = wa.get("bridgeUrl", config.channels.whatsapp.bridge_url)
            config.channels.whatsapp.allow_from = wa.get("allowFrom", config.channels.whatsapp.allow_from)

        # Telegram
        if "telegram" in data:
            tg = data["telegram"]
            config.channels.telegram.enabled = tg.get("enabled", config.channels.telegram.enabled)
            if "token" in tg:
                config.channels.telegram.token = tg["token"]
            config.channels.telegram.allow_from = tg.get("allowFrom", config.channels.telegram.allow_from)
            if "proxy" in tg:
                config.channels.telegram.proxy = tg["proxy"]

        # Feishu
        if "feishu" in data:
            fe = data["feishu"]
            config.channels.feishu.enabled = fe.get("enabled", config.channels.feishu.enabled)
            if "appId" in fe:
                config.channels.feishu.app_id = fe["appId"]
            if "appSecret" in fe:
                config.channels.feishu.app_secret = fe["appSecret"]
            if "encryptKey" in fe:
                config.channels.feishu.encrypt_key = fe["encryptKey"]
            if "verificationToken" in fe:
                config.channels.feishu.verification_token = fe["verificationToken"]
            config.channels.feishu.allow_from = fe.get("allowFrom", config.channels.feishu.allow_from)

        if "discord" in data:
            dc = data["discord"]
            config.channels.discord.enabled = dc.get("enabled", config.channels.discord.enabled)
            if "token" in dc:
                config.channels.discord.token = dc["token"]
            config.channels.discord.allow_from = dc.get("allowFrom", config.channels.discord.allow_from)

        if "qq" in data:
            qq = data["qq"]
            config.channels.qq.enabled = qq.get("enabled", config.channels.qq.enabled)
            if "appId" in qq:
                config.channels.qq.app_id = qq["appId"]
            if "secret" in qq:
                config.channels.qq.secret = qq["secret"]
            config.channels.qq.allow_from = qq.get("allowFrom", config.channels.qq.allow_from)

        if "dingtalk" in data:
            dt = data["dingtalk"]
            config.channels.dingtalk.enabled = dt.get("enabled", config.channels.dingtalk.enabled)
            if "clientId" in dt:
                config.channels.dingtalk.client_id = dt["clientId"]
            if "clientSecret" in dt:
                config.channels.dingtalk.client_secret = dt["clientSecret"]
            config.channels.dingtalk.allow_from = dt.get("allowFrom", config.channels.dingtalk.allow_from)

        from nanobot.config.loader import save_config
        save_config(config)
        
        # Sync gateway state
        self._sync_gateway(restart=True)
        
        return self.get_config()["channels"]

    def create_model(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create/update model configuration (legacy API，新配置请用 config_repository)."""
        config = load_config()
        
        model_name = data.get("modelName", "")
        if not model_name:
            raise ValueError("modelName is required")
        
        if "parameters" in data:
            params = data["parameters"]
            if "temperature" in params:
                config.agents.defaults.temperature = params["temperature"]
            if "maxTokens" in params:
                config.agents.defaults.max_tokens = params["maxTokens"]
        if "qwenImageModel" in data:
            config.mirror.qwen_image_model = (data["qwenImageModel"] or "").strip()
        
        subagent_model = (data.get("subagentModel") or "").strip()
        config.agents.defaults.subagent_model = subagent_model

        from nanobot.config.loader import save_config
        save_config(config)

        # Hot reload: update running agent/provider without restart
        if hasattr(self.agent.provider, "update_config"):
            api_key = config.get_api_key(model_name)
            api_base = config.get_api_base(model_name)
            self.agent.provider.update_config(model_name, api_key, api_base)
            if subagent_model and hasattr(self.agent.provider, "ensure_api_key_for_model"):
                sa_key = config.get_api_key(subagent_model)
                sa_base = config.get_api_base(subagent_model)
                self.agent.provider.ensure_api_key_for_model(subagent_model, sa_key, sa_base)
        self.agent.update_model(model_name)
        if hasattr(self.agent, "update_subagent_model"):
            self.agent.update_subagent_model(subagent_model)
        
        return {
            "id": "default",
            "name": model_name,
            "channelId": model_name.split('/')[0] if '/' in model_name else "openai",
            "modelName": model_name,
            "enabled": True,
            "isDefault": True,
            "parameters": {
                "temperature": config.agents.defaults.temperature,
                "maxTokens": config.agents.defaults.max_tokens,
            },
            "qwenImageModel": config.mirror.qwen_image_model,
            "subagentModel": subagent_model,
        }

    def update_model(self, model_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update model configuration."""
        if data.get("isDefault") and not data.get("modelName"):
            model_name = self._get_effective_model() if model_id == "default" else model_id
            data = {**data, "modelName": model_name}
        return self.create_model(data)

    # ==================== Calendar ====================

    def get_calendar_events(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get calendar events within a time range."""
        return self.calendar_repo.get_events(start_time=start_time, end_time=end_time)

    def create_calendar_event(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new calendar event."""
        event = self.calendar_repo.create_event(data)

        # 解析 reminders_json 为 reminders 数组
        reminders_json = event.get("reminders_json")
        if reminders_json:
            import json
            try:
                event["reminders"] = json.loads(reminders_json)
            except Exception:
                event["reminders"] = []

        # 创建日历提醒任务
        if event.get("reminders"):
            self.calendar_reminder_service.create_reminder_jobs(event)
        return event

    def update_calendar_event(self, event_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update an existing calendar event."""
        event = self.calendar_repo.update_event(event_id, data)

        # 解析 reminders_json 为 reminders 数组
        if event:
            reminders_json = event.get("reminders_json")
            if reminders_json:
                import json
                try:
                    event["reminders"] = json.loads(reminders_json)
                except Exception:
                    event["reminders"] = []

            # 更新日历提醒任务
            if event.get("reminders"):
                self.calendar_reminder_service.update_reminder_jobs(event)
            else:
                # 如果事件没有提醒配置，删除旧的提醒任务
                self.calendar_reminder_service.delete_reminder_jobs(event_id)
        return event

    def delete_calendar_event(self, event_id: str) -> bool:
        """Delete a calendar event."""
        # 先删除关联的提醒任务
        self.calendar_reminder_service.delete_reminder_jobs(event_id)
        return self.calendar_repo.delete_event(event_id)

    def get_calendar_settings(self) -> dict[str, Any]:
        """Get calendar settings."""
        return self.calendar_repo.get_settings()

    def get_enabled_channels(self) -> list[dict[str, str]]:
        """获取已启用的渠道列表，供前端下拉选择"""
        from nanobot.config.loader import load_config
        channels = []
        config = load_config()
        if config.channels.feishu.enabled:
            channels.append({"id": "feishu", "name": "飞书"})
        if config.channels.whatsapp.enabled:
            channels.append({"id": "whatsapp", "name": "WhatsApp"})
        if config.channels.telegram.enabled:
            channels.append({"id": "telegram", "name": "Telegram"})
        if config.channels.discord.enabled:
            channels.append({"id": "discord", "name": "Discord"})
        if config.channels.qq.enabled:
            channels.append({"id": "qq", "name": "QQ"})
        if config.channels.dingtalk.enabled:
            channels.append({"id": "dingtalk", "name": "钉钉"})
        return channels

    def get_calendar_jobs(self) -> list[dict[str, Any]]:
        """获取日历相关的 cron jobs"""
        return self.calendar_reminder_service.get_calendar_jobs()

    # ========== Agent Template API ==========

    def list_agent_templates(self) -> list[dict[str, Any]]:
        """获取所有 Agent 模板列表"""
        templates = self.agent_template_manager.list_templates()
        return [
            {
                "name": t.name,
                "description": t.description,
                "tools": t.tools,
                "rules": t.rules,
                "system_prompt": t.system_prompt,
                "skills": getattr(t, "skills", []) or [],
                "model": t.model,
                "is_system": t.is_system,
                "is_builtin": t.is_builtin,
                "is_editable": t.is_editable,
                "is_deletable": t.is_deletable,
                "enabled": t.enabled,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
            for t in templates
        ]

    def get_agent_template(self, name: str) -> dict[str, Any] | None:
        """获取单个 Agent 模板详情"""
        template = self.agent_template_manager.get_template(name)
        if not template:
            return None
        return {
            "name": template.name,
            "description": template.description,
            "tools": template.tools,
            "rules": template.rules,
            "system_prompt": template.system_prompt,
            "skills": getattr(template, "skills", []) or [],
            "model": template.model,
            "is_system": template.is_system,
            "is_builtin": template.is_builtin,
            "is_editable": template.is_editable,
            "is_deletable": template.is_deletable,
            "enabled": template.enabled,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
        }

    def create_agent_template(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建新的 Agent 模板"""
        from nanobot.config.agent_templates import AgentTemplateConfig

        config = AgentTemplateConfig(
            name=data["name"],
            description=data.get("description", ""),
            tools=data.get("tools", []),
            rules=data.get("rules", []),
            system_prompt=data.get("system_prompt", ""),
            skills=data.get("skills", []),
        )
        created = self.agent_template_manager.create_template(config)
        return {"name": created.name, "success": True}

    def update_agent_template(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        """更新 Agent 模板"""
        updated = self.agent_template_manager.update_template(name, data)
        if not updated:
            raise KeyError(name)
        return {"name": updated.name, "success": True}

    def delete_agent_template(self, name: str) -> dict[str, Any]:
        """删除 Agent 模板"""
        success = self.agent_template_manager.delete_template(name)
        if not success:
            raise KeyError(name)
        return {"name": name, "success": True}

    def import_agent_templates(self, content: str, on_conflict: str = "skip") -> dict[str, Any]:
        """从 YAML 导入 Agent 模板"""
        result = self.agent_template_manager.import_from_yaml(content, on_conflict)
        return result

    def export_agent_templates(self, names: list[str] | None = None) -> str:
        """导出 Agent 模板为 YAML"""
        return self.agent_template_manager.export_to_yaml(names)

    def get_valid_tools(self) -> list[dict[str, str]]:
        """获取有效的工具列表（包含名称和描述）"""
        from nanobot.config.builtin_templates_data import VALID_TOOLS

        # 工具描述映射
        tool_descriptions = {
            "read_file": "读取文件内容",
            "write_file": "创建或写入文件",
            "edit_file": "编辑现有文件",
            "list_dir": "列出目录内容",
            "exec": "执行shell命令",
            "web_search": "搜索网页信息",
            "web_fetch": "获取网页内容",
        }

        return [
            {"name": tool, "description": tool_descriptions.get(tool, "")}
            for tool in VALID_TOOLS
        ]

    def reload_agent_templates(self) -> dict[str, Any]:
        """热重载 Agent 模板"""
        success = self.agent_template_manager.reload()
        # Re-register API keys for custom models after reload
        if success:
            try:
                config = load_config()
                self._register_template_model_keys(self.agent.provider, config)
            except Exception as e:
                logger.warning(f"Failed to re-register template model keys on reload: {e}")
        return {"success": success}

    # ========== 主 Agent System Prompt API ==========

    def get_main_agent_prompt(self) -> dict[str, Any]:
        """获取主 Agent 系统提示词配置（Identity 部分）。"""
        from nanobot.storage import memory_repository
        from nanobot.storage.main_agent_prompt_repository import MainAgentPromptRepository

        workspace_path = str(self.agent.workspace.expanduser().resolve())
        db_path = memory_repository.MemoryRepository.get_workspace_db_path(self.agent.workspace)
        if not db_path.exists():
            db_path = memory_repository.MemoryRepository.get_default_db_path()
        repo = MainAgentPromptRepository(db_path)
        row = repo.get(workspace_path)
        if row:
            return {"identity_content": row.get("identity_content", ""), "updated_at": row.get("updated_at", "")}
        return {"identity_content": "", "updated_at": ""}

    def update_main_agent_prompt(self, identity_content: str) -> dict[str, Any]:
        """更新主 Agent 系统提示词配置。"""
        from nanobot.storage import memory_repository
        from nanobot.storage.main_agent_prompt_repository import MainAgentPromptRepository

        workspace_path = str(self.agent.workspace.expanduser().resolve())
        db_path = memory_repository.MemoryRepository.get_workspace_db_path(self.agent.workspace)
        if not db_path.exists():
            db_path = memory_repository.MemoryRepository.get_default_db_path()
        repo = MainAgentPromptRepository(db_path)
        result = repo.upsert(workspace_path, identity_content or "")
        return {"identity_content": result["identity_content"], "updated_at": result["updated_at"]}

    def reset_main_agent_prompt(self) -> dict[str, Any]:
        """恢复主 Agent 系统提示词为默认。"""
        from nanobot.storage import memory_repository
        from nanobot.storage.main_agent_prompt_repository import MainAgentPromptRepository

        workspace_path = str(self.agent.workspace.expanduser().resolve())
        db_path = memory_repository.MemoryRepository.get_workspace_db_path(self.agent.workspace)
        if not db_path.exists():
            db_path = memory_repository.MemoryRepository.get_default_db_path()
        repo = MainAgentPromptRepository(db_path)
        repo.reset(workspace_path)
        return {"success": True}

    def _init_main_agent_prompt_if_needed(self, workspace_path: Path) -> None:
        """若主 Agent 提示词数据库无记录，则用默认内容初始化。"""
        from nanobot.agent.context import DEFAULT_IDENTITY_CONTENT
        from nanobot.storage.main_agent_prompt_repository import MainAgentPromptRepository

        workspace_str = str(workspace_path.expanduser().resolve())
        db_path = memory_repository.MemoryRepository.get_workspace_db_path(workspace_path)
        if not db_path.exists():
            db_path = memory_repository.MemoryRepository.get_default_db_path()
        repo = MainAgentPromptRepository(db_path)
        row = repo.get(workspace_str)
        if row is None or not (row.get("identity_content") or "").strip():
            repo.upsert(workspace_str, DEFAULT_IDENTITY_CONTENT)
            logger.info(f"主 Agent 提示词已初始化默认内容: workspace={workspace_str}")

    def update_calendar_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update calendar settings."""
        return self.calendar_repo.update_settings(data)

    def get_system_status(self) -> dict[str, Any]:
        """Get system status."""
        try:
            import platform
            from nanobot import __version__

            # Get status from SystemStatusService
            status = self.status_service.get_status()
            token_usage = self.sessions.get_global_token_usage()

            # Merge with existing gateway, web, and environment info
            return {
                "gateway": {
                    "running": self.gateway_process is not None and self.gateway_process.poll() is None,
                    "pid": self.gateway_process.pid if self.gateway_process else None,
                    "port": 18790
                },
                "web": {
                    "version": __version__,
                    "uptime": status["uptime"],
                    "workspace": str(self.agent.workspace)
                },
                "environment": {
                    "python": platform.python_version(),
                    "platform": f"{platform.system()} {platform.release()} ({platform.machine()})"
                },
                "stats": {
                    "sessions": status["sessions"],
                    "skills": status["skills"],
                    "tokens": {
                        "promptTokens": int(token_usage.get("prompt_tokens", 0)),
                        "completionTokens": int(token_usage.get("completion_tokens", 0)),
                        "totalTokens": int(token_usage.get("total_tokens", 0)),
                    },
                }
            }
        except Exception as e:
            logger.exception("Failed to get system status")
            # Return default values on error
            import platform
            from nanobot import __version__

            return {
                "gateway": {
                    "running": False,
                    "pid": None,
                    "port": 18790
                },
                "web": {
                    "version": __version__,
                    "uptime": 0,
                    "workspace": str(self.agent.workspace)
                },
                "environment": {
                    "python": platform.python_version(),
                    "platform": f"{platform.system()} {platform.release()} ({platform.machine()})"
                },
                "stats": {
                    "sessions": 0,
                    "skills": 0,
                    "tokens": {
                        "promptTokens": 0,
                        "completionTokens": 0,
                        "totalTokens": 0,
                    },
                }
            }



    def get_logs(self, max_lines: int = 1000) -> list[str]:
        """Get system logs. 优先从内存缓冲读取，避免打开文件导致轮换 rename 失败。"""
        from nanobot.logging_config import get_buffered_logs

        lines = get_buffered_logs(max_lines=max_lines)
        if lines:
            return lines
        # 缓冲为空时（如冷启动）回退到读文件
        log_file = Path.home() / ".nanobot" / "nanobot.log"
        if not log_file.exists():
            return []
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.readlines()
                return [line.strip() for line in content[-max_lines:]]
        except Exception as e:
            logger.exception("Failed to read logs")
            return [f"Error reading logs: {e}"]

    # ========== Cron API ==========

    def list_cron_jobs(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        """List all cron jobs."""
        return self.cron_service.list_jobs(include_disabled=include_disabled)

    def create_cron_job(
        self,
        name: str,
        trigger_type: str,
        trigger_date_ms: int | None = None,
        trigger_interval_seconds: int | None = None,
        trigger_cron_expr: str | None = None,
        trigger_tz: str | None = None,
        payload_kind: str = "agent_turn",
        payload_message: str = "",
        payload_deliver: bool = False,
        payload_channel: str | None = None,
        payload_to: str | None = None,
        delete_after_run: bool = False,
    ) -> dict[str, Any]:
        """Create a new cron job."""
        return self.cron_service.add_job(
            name=name,
            trigger_type=trigger_type,
            trigger_date_ms=trigger_date_ms,
            trigger_interval_seconds=trigger_interval_seconds,
            trigger_cron_expr=trigger_cron_expr,
            trigger_tz=trigger_tz,
            payload_kind=payload_kind,
            payload_message=payload_message,
            payload_deliver=payload_deliver,
            payload_channel=payload_channel,
            payload_to=payload_to,
            delete_after_run=delete_after_run,
        )

    def update_cron_job(
        self,
        job_id: str,
        name: str | None = None,
        enabled: bool | None = None,
        trigger_type: str | None = None,
        trigger_date_ms: int | None = None,
        trigger_interval_seconds: int | None = None,
        trigger_cron_expr: str | None = None,
        trigger_tz: str | None = None,
        payload_kind: str | None = None,
        payload_message: str | None = None,
        payload_deliver: bool | None = None,
        payload_channel: str | None = None,
        payload_to: str | None = None,
        delete_after_run: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update a cron job."""
        return self.cron_service.update_job(
            job_id=job_id,
            name=name,
            enabled=enabled,
            trigger_type=trigger_type,
            trigger_date_ms=trigger_date_ms,
            trigger_interval_seconds=trigger_interval_seconds,
            trigger_cron_expr=trigger_cron_expr,
            trigger_tz=trigger_tz,
            payload_kind=payload_kind,
            payload_message=payload_message,
            payload_deliver=payload_deliver,
            payload_channel=payload_channel,
            payload_to=payload_to,
            delete_after_run=delete_after_run,
        )

    def delete_cron_job(self, job_id: str) -> bool:
        """Delete a cron job."""
        # 检查是否为系统任务
        job = self.cron_service.get_job(job_id)
        if job and job.get("is_system"):
            raise ValueError("系统任务无法删除")
        return self.cron_service.remove_job(job_id)

    async def run_cron_job(self, job_id: str, force: bool = False) -> bool:
        """Manually run a cron job."""
        return await self.cron_service.run_job(job_id, force=force)

    def get_cron_status(self) -> dict:
        """Get cron service status."""
        return self.cron_service.status()

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    def get_calendar_events(self, start_time: str | None = None, end_time: str | None = None) -> list[dict[str, Any]]:
        return self.calendar_repo.get_events(start_time=start_time, end_time=end_time)

    def create_calendar_event(self, data: dict[str, Any]) -> dict[str, Any]:
        event = self.calendar_repo.create_event(data)

        # 解析 reminders_json 为 reminders 数组
        reminders_json = event.get("reminders_json")
        if reminders_json:
            import json
            try:
                event["reminders"] = json.loads(reminders_json)
            except Exception:
                event["reminders"] = []

        # 创建日历提醒任务
        if event.get("reminders"):
            self.calendar_reminder_service.create_reminder_jobs(event)
        return event

    def update_calendar_event(self, event_id: str, data: dict[str, Any]) -> dict[str, Any]:
        result = self.calendar_repo.update_event(event_id, data)
        if result is None:
            raise KeyError(event_id)

        # 解析 reminders_json 为 reminders 数组
        reminders_json = result.get("reminders_json")
        if reminders_json:
            import json
            try:
                result["reminders"] = json.loads(reminders_json)
            except Exception:
                result["reminders"] = []

        # 更新日历提醒任务
        if result.get("reminders"):
            self.calendar_reminder_service.update_reminder_jobs(result)
        else:
            self.calendar_reminder_service.delete_reminder_jobs(event_id)
        return result

    def delete_calendar_event(self, event_id: str) -> bool:
        # 先删除关联的提醒任务
        self.calendar_reminder_service.delete_reminder_jobs(event_id)
        return self.calendar_repo.delete_event(event_id)

    def get_calendar_settings(self) -> dict[str, Any]:
        return self.calendar_repo.get_settings()

    def get_enabled_channels(self) -> list[dict[str, str]]:
        """获取已启用的渠道列表，供前端下拉选择"""
        from nanobot.config.loader import load_config
        channels = []
        config = load_config()
        if config.channels.feishu.enabled:
            channels.append({"id": "feishu", "name": "飞书"})
        if config.channels.whatsapp.enabled:
            channels.append({"id": "whatsapp", "name": "WhatsApp"})
        if config.channels.telegram.enabled:
            channels.append({"id": "telegram", "name": "Telegram"})
        if config.channels.discord.enabled:
            channels.append({"id": "discord", "name": "Discord"})
        if config.channels.qq.enabled:
            channels.append({"id": "qq", "name": "QQ"})
        if config.channels.dingtalk.enabled:
            channels.append({"id": "dingtalk", "name": "钉钉"})
        return channels

    def get_calendar_jobs(self) -> list[dict[str, Any]]:
        """获取日历相关的 cron jobs"""
        return self.calendar_reminder_service.get_calendar_jobs()

    def update_calendar_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.calendar_repo.update_settings(data)

    def export_config(self) -> dict[str, Any]:
        """Export system configuration from SQLite."""
        try:
            repo = get_config_repository()
            return repo.load_full_config()
        except Exception as e:
            logger.exception(f"Failed to export config")
            return {}

    def upload_skill(self, form: cgi.FieldStorage) -> dict[str, Any]:
        """
        Upload a custom skill from folder (multiple files) to workspace/skills/.
        
        Form fields: "path" + "file" paired lists (from webkitdirectory).
        """
        config = load_config()
        workspace = config.workspace_path
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        def _list(field: Any) -> list:
            if field is None:
                return []
            return field if isinstance(field, list) else [field]

        def safe_skill_name(name: str) -> bool:
            """Validate skill folder name: alphanumeric, hyphen, underscore only."""
            return bool(re.match(r"^[a-zA-Z0-9_-]+$", name))

        def safe_path(rel_path: str) -> bool:
            """Check no path traversal."""
            return ".." not in rel_path and not Path(rel_path).is_absolute()

        skill_name: str | None = None
        has_skill_md = False

        if "path" in form and "file" in form:
            paths = _list(form["path"])
            files = _list(form["file"])
            if len(paths) != len(files):
                raise ValueError("路径与文件数量不匹配")
            files_map: dict[str, bytes] = {}
            for p, f in zip(paths, files):
                path = p.value if hasattr(p, "value") else str(p)
                path = path.replace("\\", "/").strip()
                if not path or not safe_path(path):
                    continue
                if hasattr(f, "file"):
                    files_map[path] = f.file.read()
                elif hasattr(f, "value"):
                    files_map[path] = f.value if isinstance(f.value, bytes) else f.value.encode("utf-8")

            if not files_map:
                raise ValueError("未收到有效文件")

            first_key = min(files_map.keys())
            skill_name = first_key.split("/")[0]
            if not safe_skill_name(skill_name):
                raise ValueError(f"技能名称无效: {skill_name}")

            for rel_path, content in files_map.items():
                clean = rel_path.replace("\\", "/")
                if ".." in clean:
                    continue
                if clean == f"{skill_name}/SKILL.md":
                    has_skill_md = True
                sub = clean[len(skill_name) + 1 :] if clean.startswith(skill_name + "/") else clean
                if not sub:
                    continue
                dest = skills_dir / skill_name / sub
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)

        else:
            raise ValueError("请选择技能文件夹")

        if not skill_name:
            raise ValueError("无法识别技能名称")
        if not has_skill_md and not (skills_dir / skill_name / "SKILL.md").exists():
            raise ValueError("技能必须包含 SKILL.md 文件")

        # Return the new skill info for UI
        from nanobot.agent.skills import SkillsLoader
        loader = SkillsLoader(workspace)
        meta = loader.get_skill_metadata(skill_name) or {}
        return {
            "id": skill_name,
            "name": meta.get("name", skill_name),
            "version": meta.get("version", "1.0.0"),
            "description": meta.get("description", "No description"),
            "enabled": True,
            "author": meta.get("author"),
            "tags": [t.strip() for t in meta.get("tags", "").split(",")] if meta.get("tags") else [],
        }




class NanobotAPIHandler(BaseHTTPRequestHandler):
    """HTTP handler for nanobot API endpoints."""

    server: "NanobotHTTPServer"

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,PATCH,OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _handle_chat_stream_resume(
        self, app: "NanobotWebAPI", session_id: str
    ) -> None:
        """
        重连 Chat SSE 流。当用户刷新或切换 tab 后，可由此端点继续接收推送结果。
        """
        logger.info(f"[ChatStream] SSE resume connection for session: {session_id}")
        evt_queue = app.chat_stream_resume(session_id)

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        idle_timeout = 60  # 60 秒无事件则关闭（replay 后若流已结束会很快收到 done）
        heartbeat_interval = 30
        last_event = time.time()
        last_heartbeat = time.time()

        try:
            while True:
                try:
                    evt = evt_queue.get(timeout=0.5)
                    last_event = time.time()
                except queue.Empty:
                    now = time.time()
                    if now - last_event >= idle_timeout:
                        try:
                            self.wfile.write(b'data: {"type":"timeout"}\n\n')
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                        break
                    if now - last_heartbeat >= heartbeat_interval:
                        try:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                            last_heartbeat = now
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            break
                    continue
                try:
                    payload = json.dumps(evt, ensure_ascii=False)
                except (TypeError, ValueError):
                    continue
                try:
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
                if evt.get("type") in ("done", "error"):
                    break
        except Exception as e:
            logger.warning("Chat stream resume error: %s", e)

    def _handle_chat_stream(
        self, app: "NanobotWebAPI", session_id: str, content: str, images: list[str] | None = None,
        tool_mode: str | None = None, selected_mcp_servers: list[str] | None = None,
    ) -> None:
        """Stream chat progress via SSE. Resilient to client disconnect and worker errors."""
        logger.info(f"Starting chat stream for session {session_id}")
        evt_queue, thread = app.chat_stream(session_id, content, images, tool_mode=tool_mode, selected_mcp_servers=selected_mcp_servers)
        logger.info(f"Chat stream thread created: {thread}")
        thread.start()
        logger.info(f"Chat stream thread started: {thread.is_alive()}")

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        heartbeat_interval = 30  # 心跳间隔（秒）
        last_heartbeat = time.time()

        try:
            loop_count = 0
            while True:
                loop_count += 1
                evt = None
                try:
                    evt = evt_queue.get(timeout=0.5)
                    if isinstance(evt, dict) and evt.get('type'):
                        logger.debug(f"Got event: {evt.get('type')}")
                except queue.Empty:
                    now = time.time()
                    if not thread.is_alive():
                        # 线程已结束，尝试非阻塞获取可能存在的最后一事件（如 done）
                        # 避免 evt_queue.empty() 竞态导致 done 事件丢失（多轮对话场景）
                        try:
                            evt = evt_queue.get_nowait()
                        except queue.Empty:
                            break
                    if evt is None:
                        if now - last_heartbeat >= heartbeat_interval:
                            try:
                                self.wfile.write(b": heartbeat\n\n")
                                self.wfile.flush()
                                last_heartbeat = now
                                logger.debug("SSE heartbeat sent")
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                logger.debug("Client disconnected, stopping stream")
                                break
                        continue
                try:
                    payload = json.dumps(evt, ensure_ascii=False)
                except (TypeError, ValueError) as e:
                    logger.warning("SSE event not JSON-serializable: %s", e)
                    continue
                line = f"data: {payload}\n\n"
                try:
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    logger.debug("Client disconnected during stream")
                    break
                if evt.get("type") in ("done", "error"):
                    break
            thread.join(timeout=1.0)
        except Exception as e:
            logger.warning("Chat stream write error: %s", e)
        finally:
            if thread.is_alive():
                logger.warning("Chat stream thread still running after response end, attempting to cancel...")
                # 尝试取消线程（通过设置取消标志）
                # 注意：线程可能无法被强制终止，这是最后的警告
                thread.join(timeout=0.5)
                if thread.is_alive():
                    logger.error("Chat stream thread failed to terminate, possible resource leak")

    def _handle_subagent_progress_stream(
        self, app: "NanobotWebAPI", session_id: str
    ) -> None:
        """
        以 SSE 形式持续推送子 Agent 进度事件。

        订阅 SubagentProgressBus 的 "web:{session_id}" origin_key，
        将事件实时流给前端；20 分钟无事件后自动关闭并发送 {"type": "timeout"}。
        late result 场景下 SDK 可能超时后继续运行，延长空闲超时以便用户能收到最终结果。
        """
        origin_key = f"web:{session_id}"
        logger.info(f"[SubagentProgress] SSE connection established for session: {session_id}, origin_key: {origin_key}")
        evt_queue = app.subagent_progress_stream(session_id)

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        idle_timeout = 1200  # 20 分钟无事件自动关闭（覆盖 Claude Code SDK 超时后 late result 的等待期）
        heartbeat_interval = 30
        last_event = time.time()
        last_heartbeat = time.time()

        try:
            while True:
                try:
                    evt = evt_queue.get(timeout=0.5)
                    last_event = time.time()
                except queue.Empty:
                    now = time.time()
                    if now - last_event >= idle_timeout:
                        try:
                            self.wfile.write(b'data: {"type":"timeout"}\n\n')
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                        break
                    if now - last_heartbeat >= heartbeat_interval:
                        try:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                            last_heartbeat = now
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            break
                    continue

                try:
                    payload = json.dumps(evt, ensure_ascii=False)
                except (TypeError, ValueError) as e:
                    logger.warning(f"[SubagentProgress] Failed to serialize event: {e}, event: {evt}")
                    continue

                try:
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    logger.debug(f"[SubagentProgress] Sent event to frontend: {evt.get('type')}, task_id: {evt.get('task_id')}")
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    logger.warning(f"[SubagentProgress] Connection lost for session {session_id}, event not sent: {evt.get('type')}, task_id: {evt.get('task_id')}, error: {e}")
                    break
                if evt.get("type") == "stream_done":
                    logger.info(f"[SubagentProgress] All subagents finished for session {session_id}, closing SSE")
                    break
        finally:
            app.unsubscribe_subagent_progress(session_id, evt_queue)
            # 注意：不再自动清除缓冲区，保留事件供后续重连时 replay
            # 缓冲区会在会话真正结束时通过 clear_buffer API 手动清除
            logger.info(f"[SubagentProgress] SSE connection closed for session: {session_id}, buffer preserved for replay")

    def _handle_mirror_chat_stream(
        self, app: "NanobotWebAPI", session_type: str, session_id: str, content: str
    ) -> None:
        """Stream mirror chat progress via SSE."""
        evt_queue, thread = app.mirror_chat_stream(session_type, session_id, content)
        thread.start()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        heartbeat_interval = 30  # 心跳间隔（秒）
        last_heartbeat = time.time()

        try:
            loop_count = 0
            while True:
                loop_count += 1
                evt = None
                try:
                    evt = evt_queue.get(timeout=0.5)
                    if isinstance(evt, dict) and evt.get('type'):
                        logger.debug(f"Got event: {evt.get('type')}")
                except queue.Empty:
                    now = time.time()
                    if not thread.is_alive():
                        try:
                            evt = evt_queue.get_nowait()
                        except queue.Empty:
                            break
                    if evt is None:
                        if now - last_heartbeat >= heartbeat_interval:
                            try:
                                self.wfile.write(b": heartbeat\n\n")
                                self.wfile.flush()
                                last_heartbeat = now
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                logger.debug("Client disconnected, stopping stream")
                                break
                        continue
                try:
                    payload = json.dumps(evt, ensure_ascii=False)
                except (TypeError, ValueError) as e:
                    logger.warning(f"[ChatStream] Failed to serialize event: {e}")
                    continue
                line = f"data: {payload}\n\n"
                try:
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
                if evt.get("type") in ("done", "error"):
                    break
            thread.join(timeout=1.0)
        except Exception:
            pass

    def _serve_static(self, file_path: Path) -> None:
        """Serve a static file."""
        try:
            if not file_path.exists() or not file_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            mime_type, _ = mimetypes.guess_type(str(file_path))
            if mime_type is None:
                mime_type = "application/octet-stream"

            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            logger.exception(f"Error serving static file {file_path}")
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)

    def _route(self) -> tuple[str, list[str], dict[str, list[str]]]:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        return parsed.path, parts, parse_qs(parsed.query)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("web-api " + fmt % args)

    def do_OPTIONS(self) -> None:
        self._write_json(HTTPStatus.OK, _ok({"ok": True}))

    def do_GET(self) -> None:
        path, parts, query = self._route()
        
        # API routes
        if path.startswith("/api/"):
            app = self.server.app

            if path == "/api/v1/health":
                self._write_json(HTTPStatus.OK, _ok({"status": "ok"}))
                return

            # 执行链路监控 API
            if path == "/api/v1/monitoring/chains":
                # 查询链路列表
                session_key = query.get("sessionKey", [None])[0]
                status = query.get("status", [None])[0]
                limit = int(query.get("limit", ["100"])[0])
                limit = max(1, min(limit, 500))
                try:
                    from nanobot.monitoring.execution_chain import ExecutionChainMonitor
                    monitor = ExecutionChainMonitor.get_instance()
                    chains = monitor.query_chains(
                        session_key=session_key,
                        status=status,
                        limit=limit
                    )
                    self._write_json(HTTPStatus.OK, _ok(chains))
                except Exception as e:
                    logger.exception("Failed to query execution chains")
                    self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("CHAIN_QUERY_FAILED", str(e)))
                return

            # 获取链路详情
            if len(parts) == 5 and parts[:4] == ["api", "v1", "monitoring", "chains"]:
                chain_id = parts[4]
                try:
                    from nanobot.monitoring.execution_chain import ExecutionChainMonitor
                    monitor = ExecutionChainMonitor.get_instance()
                    detail = monitor.get_chain_detail(chain_id)
                    if detail:
                        self._write_json(HTTPStatus.OK, _ok(detail))
                    else:
                        self._write_json(HTTPStatus.NOT_FOUND, _err("CHAIN_NOT_FOUND", "链路不存在"))
                except Exception as e:
                    logger.exception("Failed to get chain detail")
                    self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("CHAIN_DETAIL_FAILED", str(e)))
                return

            if path == "/api/v1/chat/sessions":
                page = int(query.get("page", ["1"])[0])
                page_size = int(query.get("pageSize", ["20"])[0])
                page = max(1, page)
                page_size = max(1, min(page_size, 100))
                self._write_json(HTTPStatus.OK, _ok(app.list_sessions(page, page_size)))
                return

            # GET /api/v1/chat/sessions/{sessionId}/messages
            if len(parts) == 6 and parts[:4] == ["api", "v1", "chat", "sessions"] and parts[5] == "messages":
                session_id = parts[4]
                before_raw = query.get("before", [None])[0]
                before = int(before_raw) if before_raw else None
                limit = int(query.get("limit", ["50"])[0])
                limit = max(1, min(limit, 200))
                try:
                    self._write_json(HTTPStatus.OK, _ok(app.get_messages(session_id, before, limit)))
                except KeyError:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("CHAT_SESSION_NOT_FOUND", "会话不存在"))
                return

            # GET /api/v1/chat/sessions/{sessionId}/stream  (SSE 重连)
            if len(parts) == 6 and parts[:4] == ["api", "v1", "chat", "sessions"] and parts[5] == "stream":
                session_id = parts[4]
                try:
                    self._handle_chat_stream_resume(app, session_id)
                except Exception as exc:
                    logger.exception("Chat stream resume failed")
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        _err("CHAT_STREAM_RESUME_FAILED", "Chat 流重连失败", str(exc)),
                    )
                return

            # GET /api/v1/chat/sessions/{sessionId}/subagent-progress  (SSE)
            if len(parts) == 6 and parts[:4] == ["api", "v1", "chat", "sessions"] and parts[5] == "subagent-progress":
                session_id = parts[4]
                try:
                    self._handle_subagent_progress_stream(app, session_id)
                except Exception as exc:
                    logger.exception("Subagent progress stream failed")
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        _err("SUBAGENT_PROGRESS_FAILED", "子 Agent 进度流失败", str(exc)),
                    )
                return

            # GET /api/v1/chat/sessions/{sessionId}/token-summary
            if len(parts) == 6 and parts[:4] == ["api", "v1", "chat", "sessions"] and parts[5] == "token-summary":
                session_id = parts[4]
                try:
                    self._write_json(HTTPStatus.OK, _ok(app.get_session_token_summary(session_id)))
                except KeyError:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("CHAT_SESSION_NOT_FOUND", "会话不存在"))
                return

            # Configuration endpoints
            if path == "/api/v1/config":
                self._write_json(HTTPStatus.OK, _ok(app.get_config()))
                return

            if path == "/api/v1/config/memory":
                config_data = app.get_config()
                self._write_json(HTTPStatus.OK, _ok(config_data["memory"]))
                return

            if path == "/api/v1/config/concurrency":
                self._write_json(HTTPStatus.OK, _ok(app.get_concurrency_config()))
                return

            if path == "/api/v1/config/metrics":
                self._write_json(HTTPStatus.OK, _ok(app.get_metrics()))
                return

            if path == "/api/v1/config/channels":
                config_data = app.get_config()
                self._write_json(HTTPStatus.OK, _ok(config_data["channels"]))
                return

            if path == "/api/v1/providers":
                config_data = app.get_config()
                self._write_json(HTTPStatus.OK, _ok(config_data["providers"]))
                return

            if path == "/api/v1/models":
                # New model router API - return detailed model info
                from nanobot.config.loader import get_config_repository
                repo = get_config_repository()
                models = repo.get_all_models()
                # Convert snake_case to camelCase for frontend compatibility
                models_camel = [{
                    "id": m["id"],
                    "providerId": m["provider_id"],
                    "name": m["name"],
                    "litellmId": m["litellm_id"],
                    "aliases": m["aliases"],
                    "capabilities": m["capabilities"],
                    "contextWindow": m["context_window"],
                    "costRank": m["cost_rank"],
                    "qualityRank": m["quality_rank"],
                    "enabled": m["enabled"],
                    "isDefault": m["is_default"],
                } for m in models]
                self._write_json(HTTPStatus.OK, _ok(models_camel))
                return

            # GET /api/v1/model-profiles - Get model profiles
            if path == "/api/v1/model-profiles":
                from nanobot.config.loader import get_config_repository
                repo = get_config_repository()
                profiles = repo.get_all_model_profiles()
                # 转为 camelCase 供前端展示（前端期望 modelChain 而非 model_chain）
                profiles_camel = [{
                    "id": p["id"],
                    "name": p["name"],
                    "description": p["description"],
                    "modelChain": p["model_chain"],
                    "rules": p.get("rules", ""),
                    "enabled": p["enabled"],
                } for p in profiles]
                self._write_json(HTTPStatus.OK, _ok(profiles_camel))
                return

            # GET /api/v1/providers/{providerId}/discover - 仅查询可用模型（不保存），供添加模型时下拉选择
            if len(parts) == 5 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "providers" and parts[4] == "discover":
                provider_id = parts[3]
                from nanobot.providers.discovery import ModelDiscoveryService
                from nanobot.config.loader import get_config_repository
                repo = get_config_repository()
                discovery = ModelDiscoveryService(repo)
                import asyncio
                try:
                    models = asyncio.run(discovery.discover_for_provider(provider_id))
                    self._write_json(HTTPStatus.OK, _ok([{
                        "id": m.id,
                        "name": m.name,
                        "litellmId": m.litellm_id,
                        "aliases": m.aliases,
                        "capabilities": m.capabilities,
                        "contextWindow": m.context_window,
                    } for m in models]))
                except Exception as e:
                    logger.exception(f"Failed to discover models for {provider_id}")
                    self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("DISCOVERY_FAILED", str(e)))
                return

            if path == "/api/v1/mcps":
                config_data = app.get_config()
                self._write_json(HTTPStatus.OK, _ok(config_data["mcps"]))
                return

            # GET /api/v1/mcps/with-tools - 返回 MCP 列表（含发现到的工具详情）
            if path == "/api/v1/mcps/with-tools":
                try:
                    import asyncio
                    mcps = asyncio.run(app.get_mcps_with_tools())
                    self._write_json(HTTPStatus.OK, _ok(mcps))
                except Exception as e:
                    logger.exception("Failed to get MCPs with tools")
                    self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("MCP_TOOLS_FAILED", str(e)))
                return

            # Calendar endpoints
            if path == "/api/v1/calendar/events":
                start_time = query.get("start", [None])[0]
                end_time = query.get("end", [None])[0]
                events = app.get_calendar_events(start_time=start_time, end_time=end_time)
                self._write_json(HTTPStatus.OK, _ok(events))
                return

            if path == "/api/v1/calendar/settings":
                settings = app.get_calendar_settings()
                self._write_json(HTTPStatus.OK, _ok(settings))
                return

            # GET /api/v1/channels - 获取已启用的渠道列表
            if path == "/api/v1/channels":
                channels = app.get_enabled_channels()
                self._write_json(HTTPStatus.OK, _ok(channels))
                return

            # GET /api/v1/calendar/jobs - 获取日历相关的 cron jobs
            if path == "/api/v1/calendar/jobs":
                jobs = app.get_calendar_jobs()
                self._write_json(HTTPStatus.OK, _ok(jobs))
                return

            if path == "/api/v1/system/status":
                try:
                    self._write_json(HTTPStatus.OK, _ok(app.get_system_status()))
                except Exception as e:
                    logger.error(f"Error getting system status: {e}")
                    self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("SYSTEM_STATUS_ERROR", "获取系统状态失败", str(e)))
                return

            if path == "/api/v1/system/logs":
                self._write_json(HTTPStatus.OK, _ok({"lines": app.get_logs()}))
                return
            
            if path == "/api/v1/system/config/export":
                data = app.export_config()
                json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Disposition", 'attachment; filename="config.json"')
                self.send_header("Content-Length", str(len(json_bytes)))
                self.end_headers()
                self.wfile.write(json_bytes)
                return

            if path == "/api/v1/skills/installed":
                config_data = app.get_config()
                self._write_json(HTTPStatus.OK, _ok(config_data["skills"]))
                return

            # ==================== Cron GET ====================

            # GET /api/v1/cron/status
            if path == "/api/v1/cron/status":
                status = app.get_cron_status()
                self._write_json(HTTPStatus.OK, _ok(status))
                return

            # GET /api/v1/cron/jobs
            if path == "/api/v1/cron/jobs":
                include_disabled = query.get("includeDisabled", ["false"])[0].lower() == "true"
                jobs = app.list_cron_jobs(include_disabled=include_disabled)
                self._write_json(HTTPStatus.OK, _ok({"jobs": jobs}))
                return

            # GET /api/v1/cron/jobs/{jobId}
            if len(parts) == 5 and parts[:3] == ["api", "v1", "cron"] and parts[3] == "jobs":
                job_id = parts[4]
                job = app.cron_service.get_job(job_id)
                if job:
                    self._write_json(HTTPStatus.OK, _ok(job))
                else:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("CRON_JOB_NOT_FOUND", "定时任务不存在"))
                return

            # ==================== Claude Code Tasks GET ====================

            # GET /api/v1/tasks?page=1&pageSize=20&status=all - List all Claude Code tasks with pagination
            if path == "/api/v1/tasks":
                try:
                    page = int(query.get("page", ["1"])[0])
                    page_size = int(query.get("pageSize", ["20"])[0])
                    status = query.get("status", ["all"])[0]
                    # Validate status parameter
                    valid_statuses = ("all", "running", "done", "error", "timeout", "cancelled")
                    if status not in valid_statuses:
                        status = "all"
                    tasks = app.agent.claude_code_manager.get_all_tasks(
                        page=page,
                        page_size=page_size,
                        status=status,
                    )
                    self._write_json(HTTPStatus.OK, _ok(tasks))
                except Exception as e:
                    logger.error(f"Error getting Claude Code tasks: {e}")
                    self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("TASKS_ERROR", "获取任务列表失败", str(e)))
                return

            # GET /api/v1/tasks/{taskId} - Get single task details
            if len(parts) == 4 and parts[:2] == ["api", "v1"] and parts[2] == "tasks":
                task_id = parts[3]
                try:
                    task = app.agent.claude_code_manager.get_task(task_id)
                    if task:
                        self._write_json(HTTPStatus.OK, _ok(task))
                    else:
                        self._write_json(HTTPStatus.NOT_FOUND, _err("TASK_NOT_FOUND", "任务不存在"))
                except Exception as e:
                    logger.error(f"Error getting Claude Code task {task_id}: {e}")
                    self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("TASK_ERROR", "获取任务详情失败", str(e)))
                return

            # GET /api/v1/tasks/{taskId}/status - Get task status for polling
            if len(parts) == 5 and parts[:2] == ["api", "v1"] and parts[2] == "tasks" and parts[4] == "status":
                task_id = parts[3]
                try:
                    task = app.agent.claude_code_manager.get_task(task_id)
                    if task:
                        # Return lightweight status response
                        status_response = {
                            "taskId": task["task_id"],
                            "status": task["status"],
                            "prompt": task.get("prompt", "")[:100],  # Truncate for brevity
                            "startTime": task.get("start_time"),
                            "endTime": task.get("end_time"),
                            "result": task.get("result")[:500] if task.get("result") else None,  # Truncate
                        }
                        self._write_json(HTTPStatus.OK, _ok(status_response))
                    else:
                        self._write_json(HTTPStatus.NOT_FOUND, _err("TASK_NOT_FOUND", "任务不存在"))
                except Exception as e:
                    logger.error(f"Error getting task status {task_id}: {e}")
                    self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("TASK_STATUS_ERROR", "获取任务状态失败", str(e)))
                return

            # ==================== Calendar GET ====================

            # GET /api/v1/calendar/events
            if path == "/api/v1/calendar/events":
                self._write_json(HTTPStatus.OK, _ok(app.get_calendar_events()))
                return

            # GET /api/v1/calendar/settings
            if path == "/api/v1/calendar/settings":
                self._write_json(HTTPStatus.OK, _ok(app.get_calendar_settings()))
                return

            # GET /api/v1/channels - 获取已启用的渠道列表
            if path == "/api/v1/channels":
                self._write_json(HTTPStatus.OK, _ok(app.get_enabled_channels()))
                return

            # GET /api/v1/calendar/jobs - 获取日历相关的 cron jobs
            if path == "/api/v1/calendar/jobs":
                self._write_json(HTTPStatus.OK, _ok(app.get_calendar_jobs()))
                return

            # ==================== Agent Template GET ====================

            # GET /api/v1/agent-templates
            if path == "/api/v1/agent-templates":
                self._write_json(HTTPStatus.OK, _ok(app.list_agent_templates()))
                return

            # GET /api/v1/agent-templates/{name}
            if len(parts) == 4 and parts[:3] == ["api", "v1", "agent-templates"]:
                template_name = parts[3]
                template = app.get_agent_template(template_name)
                if template:
                    self._write_json(HTTPStatus.OK, _ok(template))
                else:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("AGENT_TEMPLATE_NOT_FOUND", "Agent模板不存在"))
                return

            # GET /api/v1/agent-templates/tools/valid
            if path == "/api/v1/agent-templates/tools/valid":
                self._write_json(HTTPStatus.OK, _ok(app.get_valid_tools()))
                return

            # GET /api/v1/main-agent-prompt - 主 Agent 系统提示词
            if path == "/api/v1/main-agent-prompt":
                self._write_json(HTTPStatus.OK, _ok(app.get_main_agent_prompt()))
                return

            # ==================== Mirror Room GET ====================

            # GET /api/v1/mirror/profile
            if path == "/api/v1/mirror/profile":
                profile = app.mirror.get_profile()
                self._write_json(HTTPStatus.OK, _ok(profile))
                return

            # GET /api/v1/mirror/sessions?type=wu&page=1&pageSize=20
            if path == "/api/v1/mirror/sessions":
                stype = query.get("type", ["wu"])[0]
                page = int(query.get("page", ["1"])[0])
                page_size = int(query.get("pageSize", ["20"])[0])
                data = app.mirror.list_sessions(stype, page, page_size)
                self._write_json(HTTPStatus.OK, _ok(data))
                return

            # GET /api/v1/mirror/sessions/{sessionId}/messages
            if (
                len(parts) == 6
                and parts[:3] == ["api", "v1", "mirror"]
                and parts[3] == "sessions"
                and parts[5] == "messages"
            ):
                session_id = parts[4]
                limit = int(query.get("limit", ["50"])[0])
                type_param = query.get("type", [None])[0]
                types_to_try = [type_param] if type_param in ("wu", "bian") else ("wu", "bian")
                for stype in types_to_try:
                    try:
                        msgs = app.mirror.get_messages(session_id, stype, limit)
                        self._write_json(HTTPStatus.OK, _ok(msgs))
                        return
                    except KeyError:
                        continue
                self._write_json(HTTPStatus.NOT_FOUND, _err("MIRROR_SESSION_NOT_FOUND", "镜室会话不存在"))
                return

            # GET /api/v1/mirror/shang/today
            if path == "/api/v1/mirror/shang/today":
                data = app.mirror.get_shang_today()
                self._write_json(HTTPStatus.OK, _ok(data))
                return

            # GET /api/v1/mirror/shang/records
            if path == "/api/v1/mirror/shang/records":
                page = int(query.get("page", ["1"])[0])
                page_size = int(query.get("pageSize", ["20"])[0])
                data = app.mirror.get_shang_records(page, page_size)
                self._write_json(HTTPStatus.OK, _ok(data))
                return

            # GET /api/v1/mirror/shang/image?recordId=xxx&slot=A
            if path == "/api/v1/mirror/shang/image":
                record_id = query.get("recordId", [None])[0]
                slot = query.get("slot", [None])[0]
                # 校验 recordId 格式，防止 path traversal（仅接受 shang_xxxxxxxx）
                valid_record = record_id and re.match(r"^shang_[0-9a-f]{8}$", str(record_id).lower())
                if valid_record and slot in ("A", "B"):
                    from nanobot.config.loader import load_config
                    cfg = load_config()
                    img_path = cfg.workspace_path / "mirror" / "shang" / "images" / f"{record_id}_{slot}.png"
                    try:
                        rp = img_path.resolve()
                        images_dir = (cfg.workspace_path / "mirror" / "shang" / "images").resolve()
                        rp.relative_to(images_dir)  # 确保在 images 目录内
                        if rp.is_file():
                            size = rp.stat().st_size
                            if size <= 10 * 1024 * 1024:  # 限制 10MB
                                self.send_response(HTTPStatus.OK)
                                self.send_header("Content-Type", "image/png")
                                self.send_header("Cache-Control", "max-age=86400")
                                self.end_headers()
                                self.wfile.write(rp.read_bytes())
                                return
                    except (OSError, ValueError):
                        pass
                self._write_json(HTTPStatus.NOT_FOUND, _err("NOT_FOUND", "图片不存在"))
                return

            self._write_json(HTTPStatus.NOT_FOUND, _err("NOT_FOUND", f"Unknown path: {path}"))
            return

        # Static file serving
        static_dir = self.server.static_dir
        if static_dir and static_dir.exists():
            # Serve index.html for SPA routes
            if path == "/" or not Path(static_dir / path.lstrip("/")).exists():
                index_file = static_dir / "index.html"
                if index_file.exists():
                    self._serve_static(index_file)
                    return
            else:
                # Serve requested static file
                file_path = static_dir / path.lstrip("/")
                if file_path.exists() and file_path.is_file():
                    self._serve_static(file_path)
                    return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path, parts, query = self._route()
        app = self.server.app

        if path == "/api/v1/system/workspace":
            body = self._read_json()
            workspace = (body.get("workspace") or "").strip()
            if not workspace:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "workspace 不能为空"))
                return
            # copy_db 可以是 true、false 或 undefined
            copy_db = body.get("copy_db")
            try:
                data = app.switch_workspace(workspace, copy_db)
                self._write_json(HTTPStatus.OK, _ok(data))
            except Exception as e:
                logger.exception("Workspace switch failed")
                self._write_json(HTTPStatus.BAD_REQUEST, _err("WORKSPACE_SWITCH_FAILED", str(e)))
            return

        if path == "/api/v1/system/restart":
            from nanobot.agent.tools.self_update import RESTART_EXIT_CODE
            logger.info("Restart requested via API")
            self._write_json(HTTPStatus.OK, _ok({"message": "Restarting..."}))
            import os
            threading.Timer(1.5, lambda: os._exit(RESTART_EXIT_CODE)).start()
            return

        # POST /api/v1/main-agent-prompt/reset - 恢复主 Agent 系统提示词为默认（先匹配更具体的路径）
        if path == "/api/v1/main-agent-prompt/reset":
            try:
                data = app.reset_main_agent_prompt()
                self._write_json(HTTPStatus.OK, _ok(data))
            except Exception as e:
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("RESET_FAILED", str(e)))
            return

        # POST /api/v1/main-agent-prompt - 更新主 Agent 系统提示词（保存）
        if path == "/api/v1/main-agent-prompt":
            body = self._read_json() or {}
            try:
                data = app.update_main_agent_prompt(body.get("identity_content", ""))
                self._write_json(HTTPStatus.OK, _ok(data))
            except Exception as e:
                logger.exception("Failed to update main agent prompt")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("UPDATE_FAILED", str(e)))
            return

        if path == "/api/v1/system/config/import":
            body = self._read_json()
            config_data = body.get("config") or body
            reload_workspace = body.get("reloadWorkspace", True)
            if not config_data or not isinstance(config_data, dict):
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "config 不能为空"))
                return
            try:
                data = app.import_config(config_data, reload_workspace=reload_workspace)
                self._write_json(HTTPStatus.OK, _ok(data))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("IMPORT_FAILED", str(e)))
            except Exception as e:
                logger.exception("Config import failed")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("IMPORT_FAILED", str(e)))
            return

        # ==================== Cron POST ====================

        # POST /api/v1/cron/jobs
        if path == "/api/v1/cron/jobs":
            body = self._read_json()
            name = (body.get("name") or "").strip()
            if not name:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "name 不能为空"))
                return

            trigger_type = body.get("triggerType")
            if trigger_type not in ("at", "every", "cron"):
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "triggerType 必须是 at, every 或 cron"))
                return

            # Validate trigger params based on type
            if trigger_type == "at" and not body.get("triggerDateMs"):
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "at 触发器需要 triggerDateMs"))
                return
            if trigger_type == "every" and not body.get("triggerIntervalSeconds"):
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "every 触发器需要 triggerIntervalSeconds"))
                return
            if trigger_type == "cron" and not body.get("triggerCronExpr"):
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "cron 触发器需要 triggerCronExpr"))
                return

            try:
                job = app.create_cron_job(
                    name=name,
                    trigger_type=trigger_type,
                    trigger_date_ms=body.get("triggerDateMs"),
                    trigger_interval_seconds=body.get("triggerIntervalSeconds"),
                    trigger_cron_expr=body.get("triggerCronExpr"),
                    trigger_tz=body.get("triggerTz"),
                    payload_kind=body.get("payloadKind", "agent_turn"),
                    payload_message=body.get("payloadMessage", ""),
                    payload_deliver=body.get("payloadDeliver", False),
                    payload_channel=body.get("payloadChannel"),
                    payload_to=body.get("payloadTo"),
                    delete_after_run=body.get("deleteAfterRun", False),
                )
                self._write_json(HTTPStatus.CREATED, _ok(job))
            except Exception as e:
                logger.exception("Failed to create cron job")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("CRON_JOB_CREATE_FAILED", str(e)))
            return

        # POST /api/v1/cron/jobs/{jobId}/run
        if len(parts) == 6 and parts[:3] == ["api", "v1", "cron"] and parts[3] == "jobs" and parts[5] == "run":
            job_id = parts[4]
            force = query.get("force", ["false"])[0].lower() == "true"
            try:
                success = asyncio.run(app.run_cron_job(job_id, force=force))
                if success:
                    self._write_json(HTTPStatus.OK, _ok({"success": True}))
                else:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("CRON_JOB_NOT_FOUND", "定时任务不存在"))
            except Exception as e:
                logger.exception("Failed to run cron job")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("CRON_JOB_RUN_FAILED", str(e)))
            return

        # ==================== Claude Code Tasks POST ====================

        # POST /api/v1/tasks/{taskId}/cancel - Cancel a running task (native subagent or Claude Code)
        if len(parts) == 5 and parts[:2] == ["api", "v1"] and parts[2] == "tasks" and parts[4] == "cancel":
            task_id = parts[3]
            try:
                success = False
                # 优先尝试 native subagent（spawn 工具派发的任务）
                if hasattr(app.agent, "subagents") and app.agent.subagents:
                    success = app.agent.subagents.cancel_task(task_id)
                # 若未命中，再尝试 Claude Code 任务
                if not success and hasattr(app.agent, "claude_code_manager") and app.agent.claude_code_manager:
                    success = app.agent.claude_code_manager.cancel_task(task_id)
                if success:
                    self._write_json(HTTPStatus.OK, _ok({"cancelled": True}))
                else:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("TASK_NOT_FOUND", "任务不存在或已完成"))
            except Exception as e:
                logger.error(f"Error cancelling task {task_id}: {e}")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("TASK_CANCEL_FAILED", "取消任务失败", str(e)))
            return

        if path == "/api/v1/chat/sessions":
            body = self._read_json()
            title = body.get("title")
            data = app.create_session(title=title)
            self._write_json(HTTPStatus.CREATED, _ok(data))
            return

        # POST /api/v1/chat/sessions/{sessionId}/messages (with optional ?stream=1 for SSE)
        if len(parts) == 6 and parts[:4] == ["api", "v1", "chat", "sessions"] and parts[5] == "messages":
            body = self._read_json()
            content = (body.get("content") or "").strip()
            images: list[str] = body.get("images") or []
            if not content and not images:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "content 或 images 不能为空"))
                return
            session_id = parts[4]
            use_stream = query.get("stream", [None])[0] == "1"
            tool_mode = body.get("tool_mode")
            selected_mcp_servers = body.get("selected_mcp_servers")

            if use_stream:
                try:
                    self._handle_chat_stream(app, session_id, content, images, tool_mode=tool_mode, selected_mcp_servers=selected_mcp_servers)
                except Exception as exc:
                    logger.exception("Chat stream failed")
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        _err("CHAT_STREAM_FAILED", "流式处理失败", str(exc)),
                    )
                return

            try:
                data = asyncio.run(app.chat(session_id=session_id, content=content, images=images, tool_mode=tool_mode, selected_mcp_servers=selected_mcp_servers))
                self._write_json(HTTPStatus.OK, _ok(data))
            except RuntimeError as exc:
                # MCP streamable_http + anyio: "cancel scope in different task" can occur
                # during asyncio.run() shutdown (modelcontextprotocol/python-sdk#521).
                # Chat usually completed; rebuild response from session.
                msg = str(exc)
                if "cancel scope" in msg and "different task" in msg:
                    logger.debug("MCP shutdown noise (expected): %s", msg[:80])
                    key = app.to_session_key(session_id)
                    messages = app.sessions.get_messages(key=key, limit=2)
                    assistant = next((m for m in reversed(messages) if m["role"] == "assistant"), None)
                    data = {
                        "content": assistant["content"] if assistant else "",
                        "assistantMessage": (
                            {
                                "id": f"msg_{assistant['sequence']}",
                                "sessionId": session_id,
                                "role": assistant["role"],
                                "content": assistant["content"],
                                "createdAt": assistant["timestamp"],
                                "sequence": assistant["sequence"],
                                **({"toolSteps": assistant["tool_steps"]} if assistant.get("tool_steps") else {}),
                                **(
                                    {
                                        "tokenUsage": {
                                            "promptTokens": int(assistant["token_usage"].get("prompt_tokens", 0) or 0),
                                            "completionTokens": int(assistant["token_usage"].get("completion_tokens", 0) or 0),
                                            "totalTokens": int(assistant["token_usage"].get("total_tokens", 0) or 0),
                                        }
                                    }
                                    if assistant.get("token_usage")
                                    else {}
                                ),
                            }
                            if assistant
                            else None
                        ),
                    }
                    self._write_json(HTTPStatus.OK, _ok(data))
                else:
                    raise
            except Exception as exc:
                logger.exception("Chat request failed")
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _err("CHAT_FAILED", "处理消息失败", str(exc)),
                )
            return

        # POST /api/v1/chat/stop - Stop the current running agent
        if path == "/api/v1/chat/stop":
            try:
                # 从请求体获取 session 信息
                channel = "web"  # 默认
                session_id = None
                try:
                    body_data = self._read_json()
                    if body_data:
                        channel = body_data.get("channel", "web")
                        session_id = body_data.get("sessionId")
                except (json.JSONDecodeError, TypeError):
                    pass
                app.agent.cancel_current_request(channel=channel, session_id=session_id)
                self._write_json(HTTPStatus.OK, _ok({"stopped": True}))
            except Exception as e:
                logger.exception("Stop request failed")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("STOP_FAILED", str(e)))
            return

        # POST /api/v1/chat/sessions/{sessionId}/token-summary/reset
        if len(parts) == 7 and parts[:4] == ["api", "v1", "chat", "sessions"] and parts[5] == "token-summary" and parts[6] == "reset":
            session_id = parts[4]
            try:
                data = app.reset_session_token_summary(session_id)
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("CHAT_SESSION_NOT_FOUND", "会话不存在"))
            return

        # POST /api/v1/system/token-summary/reset
        if path == "/api/v1/system/token-summary/reset":
            data = app.reset_global_token_summary()
            self._write_json(HTTPStatus.OK, _ok(data))
            return

        # POST /api/v1/providers - Create provider
        if path == "/api/v1/providers":
            body = self._read_json()
            try:
                data = app.create_provider(body)
                self._write_json(HTTPStatus.CREATED, _ok(data))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # POST /api/v1/mcps - Create MCP
        if path == "/api/v1/mcps":
            body = self._read_json()
            logger.debug(f"Create MCP request body: {body}")
            try:
                data = app.create_mcp(body)
                self._write_json(HTTPStatus.CREATED, _ok(data))
            except ValueError as e:
                logger.warning(f"Create MCP validation error: {e}, body: {body}")
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            except Exception as e:
                logger.exception(f"Create MCP error: {e}, body: {body}")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("CREATE_MCP_ERROR", str(e)))
            return

        # POST /api/v1/calendar/events - Create calendar event
        if path == "/api/v1/calendar/events":
            body = self._read_json()
            try:
                data = app.create_calendar_event(body)
                self._write_json(HTTPStatus.CREATED, _ok(data))
            except Exception as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("CALENDAR_ERROR", str(e)))
            return

        # POST /api/v1/mcps/{mcpId}/test
        if len(parts) == 5 and parts[:3] == ["api", "v1", "mcps"] and parts[4] == "test":
            mcp_id = unquote(parts[3])  # URL 解码
            try:
                data = app.test_mcp(mcp_id)
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("MCP_NOT_FOUND", "MCP 不存在"))
            return

        # POST /api/v1/mcps/{mcpId}/discover
        if len(parts) == 5 and parts[:3] == ["api", "v1", "mcps"] and parts[4] == "discover":
            mcp_id = unquote(parts[3])  # URL 解码
            try:
                tools = asyncio.run(app.discover_mcp_tools(mcp_id))
                self._write_json(HTTPStatus.OK, _ok({"tools": tools}))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("MCP_NOT_FOUND", "MCP 不存在"))
            except Exception as e:
                logger.exception(f"MCP discover error: {e}")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("MCP_DISCOVER_ERROR", str(e)))
            return

        # POST /api/v1/models - Create model manually
        if path == "/api/v1/models":
            body = self._read_json()
            try:
                from nanobot.config.loader import get_config_repository
                repo = get_config_repository()
                model_id = body["id"]
                is_default = body.get("isDefault", False)
                if is_default:
                    repo.clear_default_for_all_models_except(model_id)
                    repo.set_config_value("agent", "default_profile", model_id)
                    _sync_smart_profile_default_model(repo, model_id)
                repo.set_model(
                    model_id=model_id,
                    provider_id=body["providerId"],
                    name=body["name"],
                    litellm_id=body["litellmId"],
                    aliases=body.get("aliases", ""),
                    capabilities=body.get("capabilities", ""),
                    context_window=body.get("contextWindow", 128000),
                    cost_rank=body.get("costRank"),
                    quality_rank=body.get("qualityRank"),
                    enabled=body.get("enabled", True),
                    is_default=is_default,
                )
                self._write_json(HTTPStatus.CREATED, _ok({"success": True}))
            except Exception as e:
                logger.exception("Failed to create model")
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # POST /api/v1/model-profiles - Create profile
        if path == "/api/v1/model-profiles":
            body = self._read_json()
            try:
                from nanobot.config.loader import get_config_repository
                repo = get_config_repository()
                repo.set_model_profile(
                    profile_id=body["id"],
                    name=body["name"],
                    description=body.get("description", ""),
                    model_chain=body["modelChain"],
                    rules=body.get("rules", ""),
                    enabled=body.get("enabled", True),
                )
                # Clear router cache
                if hasattr(app, 'router'):
                    app.router.clear_cache()
                self._write_json(HTTPStatus.CREATED, _ok({"success": True}))
            except Exception as e:
                logger.exception("Failed to create profile")
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # POST /api/v1/models/{modelId}/set-default
        if len(parts) == 5 and parts[:3] == ["api", "v1", "models"] and parts[4] == "set-default":
            model_id = parts[3]
            from nanobot.config.loader import get_config_repository
            repo = get_config_repository()
            model = repo.get_model(model_id)
            if model:
                # 全局唯一默认：先清除所有模型的默认状态
                repo.clear_default_for_all_models_except(model_id)
                repo.set_model(
                    model_id=model_id,
                    provider_id=model["provider_id"],
                    name=model["name"],
                    litellm_id=model["litellm_id"],
                    aliases=model.get("aliases", ""),
                    capabilities=model.get("capabilities", ""),
                    context_window=model.get("context_window", 128000),
                    cost_rank=model.get("cost_rank"),
                    quality_rank=model.get("quality_rank"),
                    enabled=model.get("enabled", True),
                    is_default=True,
                )
                # 同步 agent 的 default_profile，使 router 实际使用该模型（否则会继续用 smart 等 profile）
                repo.set_config_value("agent", "default_profile", model_id)
                # 同步 smart 场景的 model_chain，将默认模型置于首位
                _sync_smart_profile_default_model(repo, model_id)
                if hasattr(app, "router") and hasattr(app.router, "clear_cache"):
                    app.router.clear_cache()
                # 热更新 agent 的模型和 provider 的 api_key
                if hasattr(app, "agent") and app.agent:
                    from nanobot.config.loader import load_config
                    cfg = load_config()
                    if hasattr(app.agent.provider, "update_config"):
                        api_key = cfg.get_api_key(model["litellm_id"])
                        api_base = cfg.get_api_base(model["litellm_id"])
                        app.agent.provider.update_config(model["litellm_id"], api_key, api_base)
                    if hasattr(app.agent, "update_model"):
                        app.agent.update_model(model["litellm_id"])
            self._write_json(HTTPStatus.OK, _ok({"success": True}))
            return

        # ==================== Mirror Room POST ====================

        # POST /api/v1/mirror/sessions - Create mirror session
        if path == "/api/v1/mirror/sessions":
            body = self._read_json()
            stype = body.get("type", "wu")
            attack_level = body.get("attackLevel")
            topic = body.get("topic")
            try:
                data = app.mirror.create_session(stype, attack_level=attack_level, topic=topic)
                self._write_json(HTTPStatus.CREATED, _ok(data))
            except Exception as e:
                logger.exception("Mirror session create failed")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("MIRROR_CREATE_FAILED", str(e)))
            return

        # POST /api/v1/mirror/sessions/{sessionId}/wu-first-reply (stream=1)
        if (
            len(parts) == 6
            and parts[:3] == ["api", "v1", "mirror"]
            and parts[3] == "sessions"
            and parts[5] == "wu-first-reply"
        ):
            session_id = parts[4]
            use_stream = query.get("stream", [None])[0] == "1"
            key_wu = MirrorService._session_key("wu", session_id)
            if app.sessions.get(key_wu) is None:
                self._write_json(HTTPStatus.NOT_FOUND, _err("MIRROR_SESSION_NOT_FOUND", "悟会话不存在"))
                return
            if use_stream:
                try:
                    evt_queue, thread = app.wu_first_reply_stream(session_id)
                    thread.start()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    heartbeat_interval = 30  # 心跳间隔（秒）
                    last_heartbeat = time.time()
                    try:
                        while True:
                            evt = None
                            try:
                                evt = evt_queue.get(timeout=0.5)
                            except queue.Empty:
                                now = time.time()
                                if not thread.is_alive():
                                    try:
                                        evt = evt_queue.get_nowait()
                                    except queue.Empty:
                                        break
                                if evt is None:
                                    if now - last_heartbeat >= heartbeat_interval:
                                        try:
                                            self.wfile.write(b": heartbeat\n\n")
                                            self.wfile.flush()
                                            last_heartbeat = now
                                        except (BrokenPipeError, ConnectionResetError, OSError):
                                            break
                                    continue
                            if evt is None:
                                continue
                            payload = json.dumps(evt, ensure_ascii=False)
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                            self.wfile.flush()
                            if evt.get("type") in ("done", "error"):
                                break
                        thread.join(timeout=1.0)
                    except Exception:
                        pass
                except Exception as exc:
                    logger.exception("Wu first reply stream failed")
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        _err("WU_FIRST_REPLY_FAILED", "悟首次回复失败", str(exc)),
                    )
            else:
                try:
                    content = asyncio.run(app._wu_first_reply(session_id))
                    self._write_json(HTTPStatus.OK, _ok({"content": content}))
                except KeyError:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("MIRROR_SESSION_NOT_FOUND", "悟会话不存在"))
                except Exception as exc:
                    logger.exception("Wu first reply failed")
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        _err("WU_FIRST_REPLY_FAILED", "悟首次回复失败", str(exc)),
                    )
            return

        # POST /api/v1/mirror/sessions/{sessionId}/bian-first-reply (stream=1)
        if (
            len(parts) == 6
            and parts[:3] == ["api", "v1", "mirror"]
            and parts[3] == "sessions"
            and parts[5] == "bian-first-reply"
        ):
            session_id = parts[4]
            use_stream = query.get("stream", [None])[0] == "1"
            key_bian = MirrorService._session_key("bian", session_id)
            if app.sessions.get(key_bian) is None:
                self._write_json(HTTPStatus.NOT_FOUND, _err("MIRROR_SESSION_NOT_FOUND", "辩会话不存在"))
                return
            if use_stream:
                try:
                    evt_queue, thread = app.bian_first_reply_stream(session_id)
                    thread.start()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    heartbeat_interval = 30  # 心跳间隔（秒）
                    last_heartbeat = time.time()
                    try:
                        while True:
                            evt = None
                            try:
                                evt = evt_queue.get(timeout=0.5)
                            except queue.Empty:
                                now = time.time()
                                if not thread.is_alive():
                                    try:
                                        evt = evt_queue.get_nowait()
                                    except queue.Empty:
                                        break
                                if evt is None:
                                    if now - last_heartbeat >= heartbeat_interval:
                                        try:
                                            self.wfile.write(b": heartbeat\n\n")
                                            self.wfile.flush()
                                            last_heartbeat = now
                                        except (BrokenPipeError, ConnectionResetError, OSError):
                                            break
                                    continue
                            if evt is None:
                                continue
                            payload = json.dumps(evt, ensure_ascii=False)
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                            self.wfile.flush()
                            if evt.get("type") in ("done", "error"):
                                break
                        thread.join(timeout=1.0)
                    except Exception:
                        pass
                except Exception as exc:
                    logger.exception("Bian first reply stream failed")
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        _err("BIAN_FIRST_REPLY_FAILED", "辩首次回复失败", str(exc)),
                    )
            else:
                try:
                    content = asyncio.run(app._bian_first_reply(session_id))
                    self._write_json(HTTPStatus.OK, _ok({"content": content}))
                except KeyError:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("MIRROR_SESSION_NOT_FOUND", "辩会话不存在"))
                except Exception as exc:
                    logger.exception("Bian first reply failed")
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        _err("BIAN_FIRST_REPLY_FAILED", "辩首次回复失败", str(exc)),
                    )
            return

        # POST /api/v1/mirror/sessions/{sessionId}/messages (with optional ?stream=1)
        if (
            len(parts) == 6
            and parts[:3] == ["api", "v1", "mirror"]
            and parts[3] == "sessions"
            and parts[5] == "messages"
        ):
            body = self._read_json()
            content = (body.get("content") or "").strip()
            if not content:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "content 不能为空"))
                return
            session_id = parts[4]
            use_stream = query.get("stream", [None])[0] == "1"

            # Session type: prefer from body, else detect by iterating
            stype = body.get("type") if body.get("type") in ("wu", "bian") else None
            if not stype:
                for t in ("wu", "bian"):
                    key = MirrorService._session_key(t, session_id)
                    if app.sessions.get(key) is not None:
                        stype = t
                        break
                if not stype:
                    stype = "wu"

            if use_stream:
                try:
                    self._handle_mirror_chat_stream(app, stype, session_id, content)
                except Exception as exc:
                    logger.exception("Mirror chat stream failed")
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        _err("MIRROR_STREAM_FAILED", "镜室流式处理失败", str(exc)),
                    )
                return

            try:
                data = asyncio.run(
                    app._mirror_chat_with_progress(stype, session_id, content, progress_callback=None)
                )
                self._write_json(HTTPStatus.OK, _ok(data))
            except Exception as exc:
                logger.exception("Mirror chat failed")
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _err("MIRROR_CHAT_FAILED", "镜室消息处理失败", str(exc)),
                )
            return

        # POST /api/v1/mirror/sessions/{sessionId}/seal
        if (
            len(parts) == 6
            and parts[:3] == ["api", "v1", "mirror"]
            and parts[3] == "sessions"
            and parts[5] == "seal"
        ):
            session_id = parts[4]
            try:
                llm_analysis = None
                analysis_ok = False
                for stype in ("wu", "bian"):
                    key = MirrorService._session_key(stype, session_id)
                    if app.sessions.get(key) is not None:
                        try:
                            llm_analysis = asyncio.run(
                                app._run_mirror_analysis(stype, key)
                            )
                            analysis_ok = llm_analysis is not None and bool(llm_analysis)
                        except Exception as e:
                            logger.warning("Mirror analysis LLM call failed: %s", e)
                        break
                data = app.mirror.seal_session(session_id, llm_analysis=llm_analysis)
                data["analysisStatus"] = "success" if analysis_ok else "failed"
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("MIRROR_SESSION_NOT_FOUND", "镜室会话不存在"))
            return

        # POST /api/v1/mirror/sessions/{sessionId}/retry-analysis
        if (
            len(parts) == 6
            and parts[:3] == ["api", "v1", "mirror"]
            and parts[3] == "sessions"
            and parts[5] == "retry-analysis"
        ):
            session_id = parts[4]
            try:
                analysis_ok = False
                for stype in ("wu", "bian"):
                    key = MirrorService._session_key(stype, session_id)
                    session = app.sessions.get(key)
                    if session is not None and session.metadata.get("status") == "sealed":
                        try:
                            llm_analysis = asyncio.run(app._run_mirror_analysis(stype, key))
                            if llm_analysis:
                                insight = llm_analysis.get("核心洞察") or llm_analysis.get("core_insight")
                                if insight:
                                    session.metadata["insight"] = str(insight)[:100]
                                    app.sessions.save(session)
                                    analysis_ok = True
                        except Exception as e:
                            logger.warning("Mirror retry analysis failed: %s", e)
                        formatted = app.mirror._format_session_obj(session, stype, session_id)
                        formatted["analysisStatus"] = "success" if analysis_ok else "failed"
                        self._write_json(HTTPStatus.OK, _ok(formatted))
                        return
                self._write_json(HTTPStatus.NOT_FOUND, _err("MIRROR_SESSION_NOT_FOUND", "会话不存在或未封存"))
            except Exception as e:
                logger.exception("Retry analysis failed")
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _err("RETRY_ANALYSIS_FAILED", str(e)),
                )
            return

        # POST /api/v1/mirror/profile/generate
        if path == "/api/v1/mirror/profile/generate":
            try:
                profile = asyncio.run(app.generate_mirror_profile())
                if profile is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        _err("NO_DATA", "悟/辩/赏均无数据，无法生成画像"),
                    )
                else:
                    self._write_json(HTTPStatus.OK, _ok(profile))
            except Exception as e:
                logger.exception("Mirror profile generate failed")
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _err("PROFILE_GENERATE_FAILED", str(e)),
                )
            return

        # POST /api/v1/mirror/shang/start
        if path == "/api/v1/mirror/shang/start":
            try:
                cfg = load_config()
                qwen_img = (cfg.mirror.qwen_image_model or "").strip()
                dashscope_key = (cfg.providers.dashscope.api_key or "").strip() or None
                api_key = dashscope_key if qwen_img else None
                data = app.mirror.start_shang(
                    dashscope_api_key=api_key,
                    qwen_image_model=qwen_img or "",
                )
                self._write_json(HTTPStatus.CREATED, _ok(data))
            except Exception as e:
                logger.exception("Shang start failed")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("SHANG_START_FAILED", str(e)))
            return

        # POST /api/v1/mirror/shang/{recordId}/regenerate-images
        if (
            len(parts) == 6
            and parts[:3] == ["api", "v1", "mirror"]
            and parts[3] == "shang"
            and parts[5] == "regenerate-images"
        ):
            record_id = parts[4]
            try:
                cfg = load_config()
                qwen_img = (cfg.mirror.qwen_image_model or "").strip()
                dashscope_key = (cfg.providers.dashscope.api_key or "").strip() or None
                api_key = dashscope_key if qwen_img else None
                data = app.mirror.regenerate_shang_images(
                    record_id,
                    dashscope_api_key=api_key,
                    qwen_image_model=qwen_img or "",
                )
                if data is None:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        _err("SHANG_REGENERATE_FAILED", "记录不存在或已提交，无法重新生成"),
                    )
                else:
                    self._write_json(HTTPStatus.OK, _ok(data))
            except Exception as e:
                logger.exception("Shang regenerate images failed")
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _err("SHANG_REGENERATE_FAILED", str(e)),
                )
            return

        # POST /api/v1/mirror/shang/{recordId}/choose
        if (
            len(parts) == 6
            and parts[:3] == ["api", "v1", "mirror"]
            and parts[3] == "shang"
            and parts[5] == "choose"
        ):
            record_id = parts[4]
            body = self._read_json()
            choice = body.get("choice")
            attribution = (body.get("attribution") or "").strip()
            if choice not in ("A", "B"):
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "choice 必须是 A 或 B"))
                return
            # attribution 可为空，用户可直接点「已赏」提交
            try:
                data = app.mirror.submit_shang_choice(record_id, choice, attribution)
                # 异步运行 LLM 分析并更新记录
                try:
                    analysis = asyncio.run(app._run_shang_analysis(data))
                    if analysis:
                        updated = app.mirror.update_shang_analysis(record_id, analysis)
                        if updated:
                            data = updated
                except Exception as e:
                    logger.warning("Shang analysis failed (non-blocking): %s", e)
                try:
                    app.mirror.write_shang_record_to_memory(data)
                except Exception as e:
                    logger.warning("Shang write to memory failed: %s", e)
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("SHANG_RECORD_NOT_FOUND", "赏记录不存在"))
            return

        # POST /api/v1/skills/upload
        if path == "/api/v1/skills/upload":
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data"):
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "需要 multipart/form-data"))
                return
            try:
                env = {
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                    "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                }
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    environ=env,
                    keep_blank_values=True,
                    encoding="utf-8",
                )
                data = app.upload_skill(form)
                self._write_json(HTTPStatus.CREATED, _ok(data))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            except Exception as e:
                logger.exception("Skill upload failed")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("UPLOAD_FAILED", str(e)))
            return

        self._write_json(HTTPStatus.NOT_FOUND, _err("NOT_FOUND", f"Unknown path: {path}"))

    def do_PATCH(self) -> None:
        path, parts, _ = self._route()
        app = self.server.app

        # PATCH /api/v1/chat/sessions/{sessionId}
        if len(parts) == 5 and parts[:4] == ["api", "v1", "chat", "sessions"]:
            body = self._read_json()
            session_id = parts[4]
            # MCP settings update (tool_mode, selected_mcp_servers)
            if "tool_mode" in body or "selected_mcp_servers" in body:
                try:
                    data = app.update_session(
                        session_id,
                        tool_mode=body.get("tool_mode"),
                        selected_mcp_servers=body.get("selected_mcp_servers"),
                    )
                    self._write_json(HTTPStatus.OK, _ok(data))
                except KeyError:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("CHAT_SESSION_NOT_FOUND", "会话不存在"))
                return
            # Title update (existing behavior)
            title = (body.get("title") or "").strip()
            if not title:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "title 不能为空"))
                return
            try:
                data = app.rename_session(session_id, title)
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("CHAT_SESSION_NOT_FOUND", "会话不存在"))
            return

        # PATCH /api/v1/mirror/sessions/{sessionId}
        if len(parts) == 5 and parts[:3] == ["api", "v1", "mirror"] and parts[3] == "sessions":
            body = self._read_json()
            title = (body.get("title") or "").strip()
            if not title:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "title 不能为空"))
                return
            session_id = parts[4]
            data = app.mirror.update_session_title(session_id, title)
            if data is None:
                self._write_json(HTTPStatus.NOT_FOUND, _err("MIRROR_SESSION_NOT_FOUND", "镜室会话不存在"))
            else:
                self._write_json(HTTPStatus.OK, _ok(data))
            return

        # ==================== Cron PATCH ====================

        # PATCH /api/v1/cron/jobs/{jobId}
        if len(parts) == 5 and parts[:3] == ["api", "v1", "cron"] and parts[3] == "jobs":
            job_id = parts[4]
            body = self._read_json()
            try:
                job = app.update_cron_job(
                    job_id=job_id,
                    name=body.get("name"),
                    enabled=body.get("enabled"),
                    trigger_type=body.get("triggerType"),
                    trigger_date_ms=body.get("triggerDateMs"),
                    trigger_interval_seconds=body.get("triggerIntervalSeconds"),
                    trigger_cron_expr=body.get("triggerCronExpr"),
                    trigger_tz=body.get("triggerTz"),
                    payload_kind=body.get("payloadKind"),
                    payload_message=body.get("payloadMessage"),
                    payload_deliver=body.get("payloadDeliver"),
                    payload_channel=body.get("payloadChannel"),
                    payload_to=body.get("payloadTo"),
                    delete_after_run=body.get("deleteAfterRun"),
                )
                if job:
                    self._write_json(HTTPStatus.OK, _ok(job))
                else:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("CRON_JOB_NOT_FOUND", "定时任务不存在"))
            except Exception as e:
                logger.exception("Failed to update cron job")
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("CRON_JOB_UPDATE_FAILED", str(e)))
            return

        # PATCH /api/v1/calendar/events/{eventId}
        if len(parts) == 5 and parts[:3] == ["api", "v1", "calendar"] and parts[3] == "events" and len(parts) == 5:
            body = self._read_json()
            event_id = parts[4]
            try:
                data = app.update_calendar_event(event_id, body)
                if data:
                    self._write_json(HTTPStatus.OK, _ok(data))
                else:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("CALENDAR_EVENT_NOT_FOUND", "日历事件不存在"))
            except Exception as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("CALENDAR_ERROR", str(e)))
            return

        # PATCH /api/v1/calendar/settings
        if path == "/api/v1/calendar/settings":
            body = self._read_json()
            try:
                data = app.update_calendar_settings(body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except Exception as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("CALENDAR_ERROR", str(e)))
            return

        # ==================== Agent Template POST/PATCH ====================

        # POST /api/v1/agent-templates - 创建新模板
        if path == "/api/v1/agent-templates":
            body = self._read_json()
            try:
                data = app.create_agent_template(body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("AGENT_TEMPLATE_ERROR", str(e)))
            return

        # POST /api/v1/agent-templates/import - 导入模板
        if path == "/api/v1/agent-templates/import":
            body = self._read_json()
            try:
                on_conflict = body.get("on_conflict", "skip")
                result = app.import_agent_templates(body.get("content", ""), on_conflict)
                self._write_json(HTTPStatus.OK, _ok(result))
            except Exception as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("IMPORT_ERROR", str(e)))
            return

        # POST /api/v1/agent-templates/export - 导出模板
        if path == "/api/v1/agent-templates/export":
            body = self._read_json() or {}
            try:
                yaml_content = app.export_agent_templates(body.get("names"))
                self._write_json(HTTPStatus.OK, _ok({"content": yaml_content}))
            except Exception as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("EXPORT_ERROR", str(e)))
            return

        # POST /api/v1/agent-templates/reload - 热重载
        if path == "/api/v1/agent-templates/reload":
            try:
                data = app.reload_agent_templates()
                self._write_json(HTTPStatus.OK, _ok(data))
            except Exception as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("RELOAD_ERROR", str(e)))
            return

        # PATCH /api/v1/agent-templates/{name} - 更新模板
        if len(parts) == 4 and parts[:3] == ["api", "v1", "agent-templates"]:
            template_name = parts[3]
            body = self._read_json()
            try:
                data = app.update_agent_template(template_name, body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("AGENT_TEMPLATE_NOT_FOUND", "Agent模板不存在"))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("AGENT_TEMPLATE_ERROR", str(e)))
            return

        self._write_json(HTTPStatus.NOT_FOUND, _err("NOT_FOUND", f"Unknown path: {path}"))

    def do_DELETE(self) -> None:
        path, parts, query = self._route()
        app = self.server.app

        # DELETE /api/v1/chat/sessions/{sessionId}
        if len(parts) == 5 and parts[:4] == ["api", "v1", "chat", "sessions"]:
            session_id = parts[4]
            deleted = app.delete_session(session_id)
            if deleted:
                self._write_json(HTTPStatus.OK, _ok({"deleted": True}))
            else:
                self._write_json(HTTPStatus.NOT_FOUND, _err("CHAT_SESSION_NOT_FOUND", "会话不存在"))
            return

        # DELETE /api/v1/calendar/events/{eventId}
        if len(parts) == 5 and parts[:3] == ["api", "v1", "calendar", "events"]:
            event_id = parts[4]
            deleted = app.delete_calendar_event(event_id)
            if deleted:
                self._write_json(HTTPStatus.OK, _ok({"deleted": True}))
            else:
                self._write_json(HTTPStatus.NOT_FOUND, _err("CALENDAR_EVENT_NOT_FOUND", "日历事件不存在"))
            return

        # DELETE /api/v1/agent-templates/{name}
        if len(parts) == 4 and parts[:3] == ["api", "v1", "agent-templates"]:
            template_name = parts[3]
            try:
                data = app.delete_agent_template(template_name)
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("AGENT_TEMPLATE_NOT_FOUND", "Agent模板不存在"))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("AGENT_TEMPLATE_ERROR", str(e)))
            return

        # DELETE /api/v1/mcps/{mcpId}
        if len(parts) == 4 and parts[:3] == ["api", "v1", "mcps"]:
            mcp_id = unquote(parts[3])  # URL 解码
            deleted = app.delete_mcp(mcp_id)
            if deleted:
                self._write_json(HTTPStatus.OK, _ok({"deleted": True}))
            else:
                self._write_json(HTTPStatus.NOT_FOUND, _err("MCP_NOT_FOUND", "MCP 不存在"))
            return

        # DELETE /api/v1/providers/{providerId}
        if len(parts) == 4 and parts[:3] == ["api", "v1", "providers"]:
            provider_id = parts[3]
            deleted = app.delete_provider(provider_id)
            if deleted:
                self._write_json(HTTPStatus.OK, _ok({"deleted": True}))
            else:
                self._write_json(HTTPStatus.NOT_FOUND, _err("PROVIDER_NOT_FOUND", "Provider 不存在"))
            return

        # DELETE /api/v1/models/{modelId}
        if len(parts) == 4 and parts[:3] == ["api", "v1", "models"]:
            model_id = parts[3]
            from nanobot.config.loader import get_config_repository
            repo = get_config_repository()
            deleted = repo.delete_model(model_id)
            if deleted:
                self._write_json(HTTPStatus.OK, _ok({"deleted": True}))
            else:
                self._write_json(HTTPStatus.NOT_FOUND, _err("MODEL_NOT_FOUND", "模型不存在"))
            return

        # DELETE /api/v1/model-profiles/{profileId}
        if len(parts) == 4 and parts[:3] == ["api", "v1", "model-profiles"]:
            profile_id = parts[3]
            from nanobot.config.loader import get_config_repository
            repo = get_config_repository()
            # Prevent deletion of system profiles
            if profile_id in ["smart", "fast", "coding", "summarize"]:
                self._write_json(HTTPStatus.FORBIDDEN, _err("CANNOT_DELETE_SYSTEM_PROFILE", "不能删除系统预设场景"))
                return
            deleted = repo.delete_model_profile(profile_id)
            if deleted:
                if hasattr(app, 'router'):
                    app.router.clear_cache()
                self._write_json(HTTPStatus.OK, _ok({"deleted": True}))
            else:
                self._write_json(HTTPStatus.NOT_FOUND, _err("PROFILE_NOT_FOUND", "场景配置不存在"))
            return

        # ==================== Cron DELETE ====================

        # DELETE /api/v1/cron/jobs/{jobId}
        if len(parts) == 5 and parts[:3] == ["api", "v1", "cron"] and parts[3] == "jobs":
            job_id = parts[4]
            try:
                deleted = app.delete_cron_job(job_id)
                if deleted:
                    self._write_json(HTTPStatus.OK, _ok({"deleted": True}))
                else:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("CRON_JOB_NOT_FOUND", "定时任务不存在"))
            except ValueError as e:
                self._write_json(HTTPStatus.FORBIDDEN, _err("CANNOT_DELETE_SYSTEM_JOB", str(e)))
            return

        # DELETE /api/v1/mirror/sessions/{sessionId}?type=wu|bian
        if (
            len(parts) == 5
            and parts[:3] == ["api", "v1", "mirror"]
            and parts[3] == "sessions"
        ):
            session_id = parts[4]
            stype = (query.get("type") or [None])[0]
            if stype not in ("wu", "bian"):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    _err("VALIDATION_ERROR", "type 必须为 wu 或 bian"),
                )
                return
            deleted = app.mirror.delete_session(stype, session_id)
            if deleted:
                self._write_json(HTTPStatus.OK, _ok({"deleted": True}))
            else:
                self._write_json(
                    HTTPStatus.NOT_FOUND,
                    _err("MIRROR_SESSION_NOT_FOUND", "镜室会话不存在"),
                )
            return

        # DELETE /api/v1/mirror/shang/records/{recordId}
        if (
            len(parts) == 6
            and parts[:3] == ["api", "v1", "mirror"]
            and parts[3] == "shang"
            and parts[4] == "records"
        ):
            record_id = parts[5]
            deleted = app.mirror.delete_shang_record(record_id)
            if deleted:
                self._write_json(HTTPStatus.OK, _ok({"deleted": True}))
            else:
                self._write_json(
                    HTTPStatus.NOT_FOUND,
                    _err("SHANG_RECORD_NOT_FOUND", "赏记录不存在"),
                )
            return

        self._write_json(HTTPStatus.NOT_FOUND, _err("NOT_FOUND", f"Unknown path: {path}"))

    def do_PUT(self) -> None:
        path, parts, _ = self._route()
        app = self.server.app

        # PUT /api/v1/config/agent
        if path == "/api/v1/config/agent":
            body = self._read_json()
            try:
                data = app.update_agent_config(body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except (ValueError, TypeError) as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # PUT /api/v1/config/concurrency
        if path == "/api/v1/config/concurrency":
            body = self._read_json()
            try:
                data = app.update_concurrency_config(body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except (ValueError, TypeError) as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # POST /api/v1/config/metrics/reset
        if path == "/api/v1/config/metrics/reset" and method == "POST":
            try:
                app.reset_metrics()
                self._write_json(HTTPStatus.OK, _ok({"message": "Metrics reset successfully"}))
            except Exception as e:
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("RESET_FAILED", str(e)))
            return

        # PUT /api/v1/main-agent-prompt - 更新主 Agent 系统提示词
        if path == "/api/v1/main-agent-prompt":
            body = self._read_json() or {}
            try:
                data = app.update_main_agent_prompt(body.get("identity_content", ""))
                self._write_json(HTTPStatus.OK, _ok(data))
            except Exception as e:
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, _err("UPDATE_FAILED", str(e)))
            return

        # PUT /api/v1/config/memory
        if path == "/api/v1/config/memory":
            body = self._read_json()
            try:
                data = app.update_memory_config(body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except (ValueError, TypeError) as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # PUT /api/v1/config/channels
        if path == "/api/v1/config/channels":
            body = self._read_json()
            try:
                data = app.update_channels(body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except (ValueError, TypeError) as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # PUT /api/v1/channels
        if path == "/api/v1/channels":
            body = self._read_json()
            try:
                data = app.update_channels(body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # PUT /api/v1/providers/{providerId}
        if len(parts) == 4 and parts[:3] == ["api", "v1", "providers"]:
            provider_id = parts[3]
            body = self._read_json()
            try:
                data = app.update_provider(provider_id, body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError as e:
                self._write_json(HTTPStatus.NOT_FOUND, _err("PROVIDER_NOT_FOUND", str(e)))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # PUT /api/v1/mcps/{mcpId}
        if len(parts) == 4 and parts[:3] == ["api", "v1", "mcps"]:
            mcp_id = unquote(parts[3])  # URL 解码
            body = self._read_json()
            try:
                data = app.update_mcp(mcp_id, body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("MCP_NOT_FOUND", "MCP 不存在"))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # PUT /api/v1/models/{modelId}
        if len(parts) == 4 and parts[:3] == ["api", "v1", "models"]:
            model_id = parts[3]
            body = self._read_json()
            try:
                from nanobot.config.loader import get_config_repository
                repo = get_config_repository()
                existing = repo.get_model(model_id)
                if not existing:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("MODEL_NOT_FOUND", "模型不存在"))
                    return
                is_default = body.get("isDefault", existing.get("is_default", False))
                if is_default:
                    repo.clear_default_for_all_models_except(model_id)
                    repo.set_config_value("agent", "default_profile", model_id)
                    _sync_smart_profile_default_model(repo, model_id)
                repo.set_model(
                    model_id=model_id,
                    provider_id=body.get("providerId", existing["provider_id"]),
                    name=body.get("name", existing["name"]),
                    litellm_id=body.get("litellmId", existing["litellm_id"]),
                    aliases=body.get("aliases", existing.get("aliases", "")),
                    capabilities=body.get("capabilities", existing.get("capabilities", "")),
                    context_window=body.get("contextWindow", existing.get("context_window", 128000)),
                    cost_rank=body.get("costRank", existing.get("cost_rank")),
                    quality_rank=body.get("qualityRank", existing.get("quality_rank")),
                    enabled=body.get("enabled", existing.get("enabled", True)),
                    is_default=is_default,
                )
                self._write_json(HTTPStatus.OK, _ok({"success": True}))
            except Exception as e:
                logger.exception("Failed to update model")
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # PUT /api/v1/model-profiles/{profileId}
        if len(parts) == 4 and parts[:3] == ["api", "v1", "model-profiles"]:
            profile_id = parts[3]
            body = self._read_json()
            try:
                from nanobot.config.loader import get_config_repository
                repo = get_config_repository()
                existing = repo.get_model_profile(profile_id)
                if not existing:
                    self._write_json(HTTPStatus.NOT_FOUND, _err("PROFILE_NOT_FOUND", "场景配置不存在"))
                    return
                repo.set_model_profile(
                    profile_id=profile_id,
                    name=body.get("name", existing["name"]),
                    description=body.get("description", existing.get("description", "")),
                    model_chain=body.get("modelChain", existing["model_chain"]),
                    rules=body.get("rules", existing.get("rules", "")),
                    enabled=body.get("enabled", existing.get("enabled", True)),
                )
                # Clear router cache
                if hasattr(app, 'router'):
                    app.router.clear_cache()
                self._write_json(HTTPStatus.OK, _ok({"success": True}))
            except Exception as e:
                logger.exception("Failed to update profile")
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return



class NanobotHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying app state."""

    def __init__(self, server_address: tuple[str, int], app: NanobotWebAPI, static_dir: Path | None = None):
        super().__init__(server_address, NanobotAPIHandler)
        self.app = app
        self.static_dir = static_dir


def _run_cron_in_thread(app: "NanobotWebAPI") -> tuple["threading.Thread", asyncio.AbstractEventLoop | None]:
    """在独立线程中运行 cron 服务，保持 event loop 常驻，避免 add_job 时 Event loop is closed。"""
    import threading
    import time

    loop_ref: list[asyncio.AbstractEventLoop] = []

    def _thread_target() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_ref.append(loop)
        try:
            loop.run_until_complete(app.cron_service.start())
            loop.run_forever()
        except Exception as e:
            logger.warning(f"Cron thread error: {e}")
        finally:
            try:
                app.cron_service.stop()
            except Exception:
                pass
            loop.close()

    t = threading.Thread(target=_thread_target, daemon=True)
    t.start()
    for _ in range(50):
        if loop_ref:
            break
        time.sleep(0.1)
    return t, loop_ref[0] if loop_ref else None


def run_server(host: str = "127.0.0.1", port: int = 6788, static_dir: Path | None = None) -> None:
    """Run the web API server."""
    import threading
    app = NanobotWebAPI()

    # 在独立线程中启动 cron 服务，保持 event loop 常驻
    cron_thread: threading.Thread | None = None
    cron_loop: asyncio.AbstractEventLoop | None = None
    try:
        cron_thread, cron_loop = _run_cron_in_thread(app)
    except Exception as e:
        logger.warning(f"Failed to start cron service: {e}")

    # 在独立线程中启动 MCP 工具加载（等待完成，最多30秒）
    mcp_init_done = threading.Event()
    mcp_error: list[str] = []

    def _mcp_init_thread_target() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(app.agent._init_mcp_loader())
        except Exception as e:
            mcp_error.append(str(e))
        finally:
            loop.close()
            mcp_init_done.set()

    mcp_init_thread = threading.Thread(target=_mcp_init_thread_target, daemon=True)
    mcp_init_thread.start()
    mcp_init_thread.join(timeout=30)
    if mcp_init_done.is_set():
        logger.info(f"[WebAPI] MCP tools loaded successfully, {len([n for n in app.agent.tools.tool_names if n.startswith('mcp_')])} MCP tools available")
    elif mcp_error:
        logger.warning(f"[WebAPI] MCP tools failed to load: {mcp_error[0]}")
    else:
        logger.warning("[WebAPI] MCP tools timed out after 30s, will load lazily on first message")

    # Determine static directory
    if static_dir is None:
        # Try to find built web-ui
        import os
        env_static = os.environ.get("NANOBOT_STATIC_DIR", "").strip()
        possible_locations = [
            Path(env_static) if env_static else None,
            Path(__file__).parent.parent.parent / "web-ui" / "dist",  # Development
            Path(__file__).parent / "static",  # Installed package
        ]
        possible_locations = [p for p in possible_locations if p is not None]
        for loc in possible_locations:
            if loc.exists() and (loc / "index.html").exists():
                static_dir = loc
                break
    
    # Try to bind to the requested port, with fallback
    original_port = port
    max_attempts = 10
    server = None
    
    for attempt in range(max_attempts):
        try:
            server = NanobotHTTPServer((host, port), app, static_dir=static_dir)
            break
        except OSError as e:
            if attempt < max_attempts - 1:
                logger.warning(f"Port {port} unavailable (attempt {attempt + 1}/{max_attempts}): {e}")
                port += 1
            else:
                logger.error(f"Failed to bind to any port from {original_port} to {port}")
                logger.error("Try specifying a different port with --port, e.g.: nanobot web-ui --port 8080")
                raise RuntimeError(f"Could not bind to any port in range {original_port}-{port}") from e
    
    if server is None:
        raise RuntimeError("Failed to create server")
    
    actual_port = server.server_address[1]
    logger.info(f"Web API running at http://{host}:{actual_port}")
    if actual_port != original_port:
        logger.info(f"Note: Using port {actual_port} instead of {original_port} (port was busy)")
    logger.info("API endpoints: /api/v1/health, /api/v1/chat/sessions")
    if static_dir and static_dir.exists():
        logger.info(f"Serving static files from: {static_dir}")
        logger.info(f"Open http://{host}:{actual_port} in your browser")
    else:
        logger.info("No static files found (frontend not built). Run 'cd web-ui && npm install && npm run build' to build the frontend.")
    logger.info("Press Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("\nStopping web API server...")
    finally:
        # Stop cron service（停止 loop 以结束 cron 线程）
        if cron_loop and cron_loop.is_running():
            cron_loop.call_soon_threadsafe(cron_loop.stop)
        try:
            app.cron_service.stop()
        except Exception as e:
            logger.warning(f"Error stopping cron service: {e}")
        if cron_thread and cron_thread.is_alive():
            cron_thread.join(timeout=3)
        app.shutdown()
        server.server_close()
