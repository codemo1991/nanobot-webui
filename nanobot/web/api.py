"""Minimal HTTP API server for nanobot Web UI."""

from __future__ import annotations

import asyncio
import cgi
import io
import json
import mimetypes
import re
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.loader import convert_keys, ensure_initial_config, load_config, save_config
from nanobot.config.schema import Config, McpServerConfig
from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.services.system_status_service import SystemStatusService
from nanobot.session.manager import SessionManager
from nanobot.storage.status_repository import StatusRepository


def _ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _err(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": message, "details": details}}


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

        model = config.agents.defaults.model
        api_key = config.get_api_key(model)
        api_base = config.get_api_base(model)
        is_bedrock = model.startswith("bedrock/")
        if not api_key and not is_bedrock:
            logger.warning(
                "No API key configured. Web UI will start; configure providers.*.apiKey in ~/.nanobot/config.json "
                "or via the Config page to use chat."
            )

        provider = LiteLLMProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=config.agents.defaults.model,
        )

        self.agent = AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=config.workspace_path,
            model=config.agents.defaults.model,
            max_iterations=config.agents.defaults.max_tool_iterations,
            brave_api_key=config.tools.web.search.api_key or None,
            exec_config=config.tools.exec,
            filesystem_config=config.tools.filesystem,
        )
        self.sessions = self.agent.sessions
        
        # Initialize system status service
        data_dir = Path.home() / ".nanobot"
        status_repo = StatusRepository(data_dir / "chat.db")
        self.status_service = SystemStatusService(
            status_repo=status_repo,
            session_manager=self.sessions,
            workspace=config.workspace_path
        )
        self.status_service.initialize()
        
        # Initial gateway sync
        self._sync_gateway()

    def _reload_mcp(self) -> None:
        """Hot-reload MCP config so new/updated MCPs take effect without restart."""
        try:
            asyncio.run(self.agent.reload_mcp_config())
        except Exception as e:
            logger.warning(f"MCP reload failed: {e}", exc_info=True)

    def _reinit_agent_and_status(self, workspace_path: Path) -> None:
        """Reinitialize agent and status service with new workspace (hot reload)."""
        config = load_config()
        model = config.agents.defaults.model
        api_key = config.get_api_key(model)
        api_base = config.get_api_base(model)
        is_bedrock = model.startswith("bedrock/")
        if not api_key and not is_bedrock:
            logger.warning("No API key configured; agent will not be able to process chat until configured.")
        provider = LiteLLMProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=config.agents.defaults.model,
        )
        self.agent = AgentLoop(
            bus=self.agent.bus,
            provider=provider,
            workspace=workspace_path,
            model=config.agents.defaults.model,
            max_iterations=config.agents.defaults.max_tool_iterations,
            brave_api_key=config.tools.web.search.api_key or None,
            exec_config=config.tools.exec,
            filesystem_config=config.tools.filesystem,
        )
        self.sessions = self.agent.sessions
        data_dir = Path.home() / ".nanobot"
        status_repo = StatusRepository(data_dir / "chat.db")
        self.status_service = SystemStatusService(
            status_repo=status_repo,
            session_manager=self.sessions,
            workspace=workspace_path,
        )
        self.status_service.initialize()
        logger.info(f"Workspace hot-reloaded to: {workspace_path}")

    def switch_workspace(self, workspace_path: str) -> dict[str, Any]:
        """
        Switch workspace and hot-reload. Updates config and reinitializes agent/status.
        """
        path = Path(workspace_path).expanduser().resolve()
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        config = load_config()
        config.agents.defaults.workspace = str(path)
        save_config(config)
        self._reinit_agent_and_status(path)
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
        all_sessions = [s for s in self.sessions.list_sessions() if s["key"].startswith("web:")]
        total = len(all_sessions)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        items = all_sessions[start:end]
        return {
            "items": [
                {
                    "id": self.to_session_id(item["key"]),
                    "title": item.get("metadata", {}).get("title"),
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
        return self.sessions.delete(self.to_session_key(session_id))

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
            }
            for m in messages
        ]

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

    async def chat(self, session_id: str, content: str) -> dict[str, Any]:
        key = self.to_session_key(session_id)
        self.sessions.get_or_create(key)
        try:
            response = await self.agent.process_direct(
                content=content,
                session_key=key,
                channel="web",
                chat_id=session_id,
            )
        finally:
            # Close MCP connections before loop ends - avoids "different task" errors
            # when asyncio.run() tears down; connections are recreated next request
            if self.agent.mcp_loader:
                try:
                    await self.agent.reload_mcp_config()
                except BaseException:
                    pass  # Suppress any close errors (BaseExceptionGroup, etc.)
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
                }
                if assistant
                else None
            ),
        }

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
        }
        providers = []
        for provider_name in ['anthropic', 'openai', 'openrouter', 'deepseek', 'groq', 'zhipu', 'dashscope', 'gemini', 'vllm']:
            provider_config = getattr(config.providers, provider_name)
            if provider_config.api_key or provider_config.api_base:
                providers.append({
                    "id": provider_name,
                    "name": provider_display_names.get(provider_name, provider_name.capitalize()),
                    "type": provider_name,
                    "apiKey": "***" if provider_config.api_key else None,
                    "apiBase": provider_config.api_base,
                    "enabled": bool(provider_config.api_key),
                })
        
        # Create default model entry
        models = [{
            "id": "default",
            "name": config.agents.defaults.model,
            "providerId": config.agents.defaults.model.split('/')[0] if '/' in config.agents.defaults.model else "openai",
            "modelName": config.agents.defaults.model,
            "enabled": True,
            "isDefault": True,
            "parameters": {
                "temperature": config.agents.defaults.temperature,
                "maxTokens": config.agents.defaults.max_tokens,
            }
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

        return {
            "channels": channels,
            "providers": providers,
            "models": models,
            "mcps": [
                {
                    "id": m.id,
                    "name": m.name,
                    "transport": m.transport,
                    "command": m.command,
                    "args": m.args,
                    "url": m.url,
                    "enabled": m.enabled,
                }
                for m in config.mcps
            ],
            "skills": skills_data
        }

    def create_provider(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create/Enable a new AI provider configuration."""
        config = load_config()
        provider_type = data.get("type", "").lower()
        
        if not provider_type or provider_type not in ['anthropic', 'openai', 'openrouter', 'deepseek', 'groq', 'zhipu', 'dashscope', 'gemini', 'vllm']:
            raise ValueError("Invalid provider type")
        
        provider_config = getattr(config.providers, provider_type)
        provider_config.api_key = data.get("apiKey", "")
        provider_config.api_base = data.get("apiBase")
        
        from nanobot.config.loader import save_config
        save_config(config)
        
        return {
            "id": provider_type,
            "name": data.get("name", provider_type.capitalize()),
            "type": provider_type,
            "apiBase": provider_config.api_base,
            "apiKey": "***" if provider_config.api_key else None,
            "enabled": bool(provider_config.api_key),
        }

    def update_provider(self, provider_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update AI provider configuration."""
        config = load_config()
        
        if provider_id not in ['anthropic', 'openai', 'openrouter', 'deepseek', 'groq', 'zhipu', 'dashscope', 'gemini', 'vllm']:
            raise KeyError("Provider not found")
        
        provider_config = getattr(config.providers, provider_id)
        if "apiKey" in data:
            provider_config.api_key = data["apiKey"]
        if "apiBase" in data:
            provider_config.api_base = data["apiBase"]
        
        from nanobot.config.loader import save_config
        save_config(config)
        
        return {
            "id": provider_id,
            "name": data.get("name", provider_id.capitalize()),
            "type": provider_id,
            "apiBase": provider_config.api_base,
            "apiKey": "***" if provider_config.api_key else None,
            "enabled": bool(provider_config.api_key),
        }

    def delete_provider(self, provider_id: str) -> bool:
        """Disable AI provider configuration."""
        config = load_config()
        
        if provider_id not in ['anthropic', 'openai', 'openrouter', 'deepseek', 'groq', 'zhipu', 'dashscope', 'gemini', 'vllm']:
            return False
        
        provider_config = getattr(config.providers, provider_id)
        provider_config.api_key = ""
        provider_config.api_base = None

        save_config(config)
        return True

    def create_mcp(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new MCP server configuration."""
        config = load_config()
        mcp_id = data.get("id") or str(uuid4()).replace("-", "")[:12]
        if not re.match(r"^[a-zA-Z0-9._-]+$", mcp_id):
            raise ValueError("MCP id 只能包含字母、数字、点、下划线、连字符")
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
        )
        config.mcps.append(mcp)
        save_config(config)
        return {
            "id": mcp.id,
            "name": mcp.name,
            "transport": mcp.transport,
            "command": mcp.command,
            "args": mcp.args,
            "url": mcp.url,
            "enabled": mcp.enabled,
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
        save_config(config)
        return {"id": mcp.id, "name": mcp.name, "transport": mcp.transport, "command": mcp.command, "args": mcp.args, "url": mcp.url, "enabled": mcp.enabled}

    def delete_mcp(self, mcp_id: str) -> bool:
        """Delete MCP server configuration."""
        config = load_config()
        before = len(config.mcps)
        config.mcps = [m for m in config.mcps if m.id != mcp_id]
        if len(config.mcps) == before:
            return False
        save_config(config)
        return True

    def test_mcp(self, mcp_id: str) -> dict[str, Any]:
        """Test MCP connection. Returns {connected: bool, message: str}."""
        import subprocess
        import urllib.request

        config = load_config()
        mcp = next((m for m in config.mcps if m.id == mcp_id), None)
        if not mcp:
            raise KeyError(f"MCP 不存在: {mcp_id}")
        try:
            if mcp.transport in ("http", "sse", "streamable_http"):
                req = urllib.request.Request(mcp.url or "", method="GET")
                with urllib.request.urlopen(req, timeout=5) as _:
                    pass
                return {"connected": True, "message": "连接成功"}
            if mcp.transport == "stdio" and mcp.command:
                cmd = [mcp.command] + (mcp.args or [])
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
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
        return {"connected": False, "message": "未知 transport 类型"}

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
        """Create/update model configuration."""
        config = load_config()
        
        model_name = data.get("modelName", "")
        if not model_name:
            raise ValueError("modelName is required")
        
        config.agents.defaults.model = model_name
        if "parameters" in data:
            params = data["parameters"]
            if "temperature" in params:
                config.agents.defaults.temperature = params["temperature"]
            if "maxTokens" in params:
                config.agents.defaults.max_tokens = params["maxTokens"]
        
        from nanobot.config.loader import save_config
        save_config(config)

        # Hot reload: update running agent/provider without restart
        if hasattr(self.agent.provider, "update_config"):
            api_key = config.get_api_key(model_name)
            api_base = config.get_api_base(model_name)
            self.agent.provider.update_config(model_name, api_key, api_base)
        self.agent.update_model(model_name)
        
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
            }
        }

    def update_model(self, model_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update model configuration."""
        if data.get("isDefault") and not data.get("modelName"):
            config = load_config()
            model_name = config.agents.defaults.model if model_id == "default" else model_id
            data = {**data, "modelName": model_name}
        return self.create_model(data)

    def get_system_status(self) -> dict[str, Any]:
        """Get system status."""
        try:
            import platform
            from nanobot import __version__

            # Get status from SystemStatusService
            status = self.status_service.get_status()

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
                    "skills": status["skills"]
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
                    "skills": 0
                }
            }



    def get_logs(self, max_lines: int = 1000) -> list[str]:
        """Get system logs."""
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

    def export_config(self) -> dict[str, Any]:
        """Export system configuration."""
        config_path = Path.home() / ".nanobot" / "config.json"
        if not config_path.exists():
            return {}
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.exception(f"Failed to export config from {config_path}")
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

            # Configuration endpoints
            if path == "/api/v1/config":
                self._write_json(HTTPStatus.OK, _ok(app.get_config()))
                return

            if path == "/api/v1/channels":
                config_data = app.get_config()
                self._write_json(HTTPStatus.OK, _ok(config_data["channels"]))
                return

            if path == "/api/v1/providers":
                config_data = app.get_config()
                self._write_json(HTTPStatus.OK, _ok(config_data["providers"]))
                return

            if path == "/api/v1/models":
                config_data = app.get_config()
                self._write_json(HTTPStatus.OK, _ok(config_data["models"]))
                return

            if path == "/api/v1/mcps":
                config_data = app.get_config()
                self._write_json(HTTPStatus.OK, _ok(config_data["mcps"]))
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
        path, parts, _ = self._route()
        app = self.server.app

        if path == "/api/v1/system/workspace":
            body = self._read_json()
            workspace = (body.get("workspace") or "").strip()
            if not workspace:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "workspace 不能为空"))
                return
            try:
                data = app.switch_workspace(workspace)
                self._write_json(HTTPStatus.OK, _ok(data))
            except Exception as e:
                logger.exception("Workspace switch failed")
                self._write_json(HTTPStatus.BAD_REQUEST, _err("WORKSPACE_SWITCH_FAILED", str(e)))
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

        if path == "/api/v1/chat/sessions":
            body = self._read_json()
            title = body.get("title")
            data = app.create_session(title=title)
            self._write_json(HTTPStatus.CREATED, _ok(data))
            return

        # POST /api/v1/chat/sessions/{sessionId}/messages
        if len(parts) == 6 and parts[:4] == ["api", "v1", "chat", "sessions"] and parts[5] == "messages":
            body = self._read_json()
            content = (body.get("content") or "").strip()
            if not content:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "content 不能为空"))
                return
            session_id = parts[4]
            try:
                data = asyncio.run(app.chat(session_id=session_id, content=content))
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
            try:
                data = app.create_mcp(body)
                app._reload_mcp()
                self._write_json(HTTPStatus.CREATED, _ok(data))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # POST /api/v1/mcps/{mcpId}/test
        if len(parts) == 5 and parts[:3] == ["api", "v1", "mcps"] and parts[4] == "test":
            mcp_id = parts[3]
            try:
                data = app.test_mcp(mcp_id)
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("MCP_NOT_FOUND", "MCP 不存在"))
            return

        # POST /api/v1/models - Create model
        if path == "/api/v1/models":
            body = self._read_json()
            try:
                data = app.create_model(body)
                self._write_json(HTTPStatus.CREATED, _ok(data))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return

        # POST /api/v1/models/{modelId}/set-default
        if len(parts) == 5 and parts[:3] == ["api", "v1", "models"] and parts[4] == "set-default":
            model_id = parts[3]
            app.update_model(model_id, {"isDefault": True})
            self._write_json(HTTPStatus.OK, _ok({"success": True}))
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
            title = (body.get("title") or "").strip()
            if not title:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", "title 不能为空"))
                return
            session_id = parts[4]
            try:
                data = app.rename_session(session_id, title)
                self._write_json(HTTPStatus.OK, _ok(data))
            except KeyError:
                self._write_json(HTTPStatus.NOT_FOUND, _err("CHAT_SESSION_NOT_FOUND", "会话不存在"))
            return

        self._write_json(HTTPStatus.NOT_FOUND, _err("NOT_FOUND", f"Unknown path: {path}"))

    def do_DELETE(self) -> None:
        path, parts, _ = self._route()
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

        # DELETE /api/v1/mcps/{mcpId}
        if len(parts) == 4 and parts[:3] == ["api", "v1", "mcps"]:
            mcp_id = parts[3]
            deleted = app.delete_mcp(mcp_id)
            if deleted:
                app._reload_mcp()
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

        self._write_json(HTTPStatus.NOT_FOUND, _err("NOT_FOUND", f"Unknown path: {path}"))

    def do_PUT(self) -> None:
        path, parts, _ = self._route()
        app = self.server.app

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
            mcp_id = parts[3]
            body = self._read_json()
            try:
                data = app.update_mcp(mcp_id, body)
                app._reload_mcp()
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
                data = app.update_model(model_id, body)
                self._write_json(HTTPStatus.OK, _ok(data))
            except ValueError as e:
                self._write_json(HTTPStatus.BAD_REQUEST, _err("VALIDATION_ERROR", str(e)))
            return



class NanobotHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying app state."""

    def __init__(self, server_address: tuple[str, int], app: NanobotWebAPI, static_dir: Path | None = None):
        super().__init__(server_address, NanobotAPIHandler)
        self.app = app
        self.static_dir = static_dir


def run_server(host: str = "127.0.0.1", port: int = 6788, static_dir: Path | None = None) -> None:
    """Run the web API server."""
    app = NanobotWebAPI()
    
    # Determine static directory
    if static_dir is None:
        # Try to find built web-ui
        possible_locations = [
            Path(__file__).parent.parent.parent / "web-ui" / "dist",  # Development
            Path(__file__).parent / "static",  # Installed package
        ]
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
        logger.warning("No static files found. Run 'cd web-ui && npm install && npm run build' to build the frontend.")
    logger.info("Press Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("\nStopping web API server...")
    finally:
        app.shutdown()
        server.server_close()
