"""Subagent manager for background task execution."""

import asyncio
import base64
import json
import mimetypes
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.claude_code.manager import ClaudeCodeManager
    from nanobot.services.system_status_service import SystemStatusService

# Forward declaration to avoid circular imports
SystemStatusService = "SystemStatusService"

from nanobot.agent.subagent_progress import SubagentProgressBus
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, ListDirTool, EditFileTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.prompts import get_template, build_system_prompt, get_tools_for_template


class SubagentManager:
    """
    Manages background subagent execution.

    Subagents are lightweight agent instances that run in the background
    to handle specific tasks. They share the same LLM provider but have
    isolated context and a focused system prompt.

    Supports session persistence for multi-turn conversations.
    When template="coder" and a ClaudeCodeManager is provided, the backend
    can be auto-selected between Claude Code CLI and the native LLM runner.
    """

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        sessions: "SessionManager | None" = None,
        max_concurrent_subagents: int = 10,
        claude_code_manager: "ClaudeCodeManager | None" = None,
        status_service: "SystemStatusService | None" = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        from nanobot.session.manager import SessionManager
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.sessions = sessions
        self._claude_code_manager = claude_code_manager
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._status_service = status_service

        # 并发控制
        self._max_concurrent_subagents = max_concurrent_subagents
        self._subagent_semaphore = asyncio.Semaphore(max_concurrent_subagents)

    def _build_user_message_with_media(self, text: str, media: list[str] | None = None) -> dict[str, Any]:
        """Build user message with optional base64-encoded images."""
        if not media:
            return {"role": "user", "content": text}
        
        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        
        if not images:
            return {"role": "user", "content": text}
        
        return {"role": "user", "content": images + [{"type": "text", "text": text}]}

    def _resolve_backend(self, template: str, backend: str) -> str:
        """
        决定实际使用的执行后端。

        仅当 template=="coder" 时才有 claude_code 后端可选；
        其他模板始终使用 native LLM。

        Args:
            template: 子 Agent 模板名称
            backend: 用户指定的后端偏好（"auto" / "native" / "claude_code"）

        Returns:
            "claude_code" 或 "native"
        """
        if template != "coder":
            return "native"
        if backend == "native":
            return "native"
        if backend == "claude_code":
            if self._claude_code_manager and self._claude_code_manager.check_claude_available():
                return "claude_code"
            logger.warning("Claude Code CLI 不可用，回退到 native LLM 后端")
            return "native"
        # auto：优先 Claude Code CLI，不可用时降级
        if self._claude_code_manager and self._claude_code_manager.check_claude_available():
            return "claude_code"
        return "native"

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        template: str = "minimal",
        session_id: str | None = None,
        enable_memory: bool = False,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        media: list[str] | None = None,
        backend: str = "auto",
    ) -> str:
        """
        Spawn a subagent to execute a task in the background.

        Args:
            task: The task for the subagent to complete.
            label: Optional human-readable label for the task.
            template: The subagent template to use (minimal/coder/researcher/analyst).
            session_id: Optional session ID to continue an existing conversation.
            enable_memory: Whether to enable agent-specific memory for this subagent.
            origin_channel: The channel to announce results to.
            origin_chat_id: The chat ID to announce results to.
            media: Optional list of local file paths for images to include.

        Returns:
            Status message indicating the subagent was started.
        """
        # 检查当前并发数并等待信号量
        current_running = len(self._running_tasks)
        if current_running >= self._max_concurrent_subagents:
            logger.info(f"Subagent concurrency limit reached ({self._max_concurrent_subagents}), waiting for available slot...")
            # 等待信号量，如果超时则返回错误
            try:
                async with asyncio.timeout(60):  # 最多等待60秒
                    await self._subagent_semaphore.acquire()
            except asyncio.TimeoutError:
                return f"Error: Too many concurrent subagents ({current_running}). Please wait and try again."
        else:
            # 未达到上限，直接等待获取信号量
            await self._subagent_semaphore.acquire()

        template_obj = get_template(template)

        if session_id and self.sessions:
            existing_session = self.sessions.get(session_id)
            if existing_session:
                display_label = label or f"Continue session {session_id}"
                task_id = session_id
                logger.info(f"Continuing existing subagent session [{task_id}]: {display_label}")
            else:
                task_id = session_id
                display_label = label or task[:30] + ("..." if len(task) > 30 else "")
                logger.info(f"Starting new subagent with session [{task_id}]: {display_label}")
        else:
            task_id = str(uuid.uuid4())[:8]
            display_label = label or task[:30] + ("..." if len(task) > 30 else "")

        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
        }

        resolved_backend = self._resolve_backend(template, backend)
        logger.info(f"Subagent [{task_id}] backend resolved: template={template}, requested={backend}, actual={resolved_backend}")
        logger.info(f"[SubagentProgress] Creating background task for subagent, origin_channel={origin_channel}, origin_chat_id={origin_chat_id}")

        logger.info(f"[SubagentProgress] SubagentManager id: {id(self)}, _running_tasks before: {list(self._running_tasks.keys())}")

        # 在独立线程中运行子代理任务，避免被主请求的事件循环关闭取消
        import threading

        # 保存信号量和任务引用，用于在线程完成后清理
        semaphore = self._subagent_semaphore
        running_tasks_ref = self._running_tasks

        def run_in_thread():
            """在独立线程的事件循环中运行子代理任务"""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bus = SubagentProgressBus.get()
            origin_key = f"{origin['channel']}:{origin['chat_id']}"
            try:
                loop.run_until_complete(
                    self._run_subagent(task_id, task, display_label, origin, template, session_id, enable_memory, media, resolved_backend)
                )
                logger.info(f"[SubagentProgress] Thread-based subagent task {task_id} completed successfully")
            except Exception as e:
                logger.error(f"[SubagentProgress] Thread-based subagent task {task_id} failed: {e}")
                # 确保即使失败也发送 subagent_end 事件
                try:
                    bus.push(origin_key, {
                        "type": "subagent_end",
                        "task_id": task_id,
                        "label": display_label,
                        "status": "error",
                        "summary": f"任务执行失败: {str(e)[:200]}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    logger.info(f"[SubagentProgress] Pushed subagent_end event for failed task {task_id}")
                except Exception as push_err:
                    logger.error(f"[SubagentProgress] Failed to push subagent_end for failed task: {push_err}")
            finally:
                # 释放信号量并清理
                semaphore.release()
                running_tasks_ref.pop(task_id, None)
                logger.info(f"[SubagentProgress] Semaphore released and task {task_id} removed from _running_tasks")
                loop.close()

        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()

        # 由于任务在线程中运行，不需要 asyncio Task 追踪
        # 直接添加到 _running_tasks 表示任务正在运行
        self._running_tasks[task_id] = None  # 使用 None 表示线程任务
        logger.info(f"[SubagentProgress] SubagentManager id: {id(self)}, _running_tasks after (thread-based): {list(self._running_tasks.keys())}")

        # 记录子Agent spawn次数
        if self._status_service:
            try:
                self._status_service.increment_subagent_spawn()
            except Exception:
                pass

        # 对于线程方式运行的任务，不需要 asyncio task 回调
        # 信号量释放和清理由线程内部处理

        # 在返回前检查任务状态
        logger.info(f"[SubagentProgress] Before return: thread-based task {task_id} started")

        if not session_id or not self.sessions or not self.sessions.get(session_id):
            logger.info(f"Spawned subagent [{task_id}]: {display_label}")
            return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."
        else:
            return f"Continuing subagent session [{task_id}]. I'll notify you when it completes."

    async def _run_via_claude_code(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
    ) -> None:
        """
        使用 Claude Code Agent SDK 后端在后台执行编码任务。

        通过 SubagentProgressBus 将进度事件广播给所有订阅者（Web SSE / 飞书卡片等），
        完成后通过 _announce_result 发布最终通知。
        """
        assert self._claude_code_manager is not None
        logger.info(f"Subagent [{task_id}] running via Claude Code Agent SDK backend (background)")

        origin_key = f"{origin['channel']}:{origin['chat_id']}"
        logger.info(f"[SubagentProgress] Subscribing to origin_key: {origin_key}")
        bus = SubagentProgressBus.get()

        bus.push(origin_key, {
            "type": "subagent_start",
            "task_id": task_id,
            "label": label,
            "backend": "claude_code",
            "task": task[:120],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[SubagentProgress] Pushed subagent_start event for task_id: {task_id}")

        # 简单的去重机制，避免相同内容反复发送
        last_progress_content = None
        last_progress_time = 0

        def _progress_callback(payload: dict) -> None:
            """将 SDK 流式事件通过 SubagentProgressBus 广播给所有订阅者。"""
            nonlocal last_progress_content, last_progress_time
            try:
                subtype = payload.get("subtype", "")
                content = payload.get("content", "")
                if not subtype:
                    return

                # 去重：如果内容和上次相同且时间间隔小于2秒，跳过
                current_time = time.time()
                if content == last_progress_content and (current_time - last_progress_time) < 2:
                    return
                last_progress_content = content
                last_progress_time = current_time

                bus.push(origin_key, {
                    "type": "subagent_progress",
                    "task_id": task_id,
                    "label": label,
                    "subtype": subtype,
                    "content": content,
                    "tool_name": payload.get("tool_name"),
                    "subagent_type": payload.get("subagent_type"),
                })
                logger.debug(f"[SubagentProgress] Pushed subagent_progress event: subtype={subtype}, content={content[:80]}")
            except Exception as e:
                logger.error(f"[SubagentProgress] Error in progress callback: {e}")

        try:
            logger.info(f"[SubagentProgress] Calling Claude Code Manager run_task for task_id: {task_id}, manager id: {id(self)}")
            logger.info(f"[SubagentProgress] _running_tasks before run_task: {list(self._running_tasks.keys())}")
            result = await self._claude_code_manager.run_task(
                prompt=task,
                workdir=None,
                permission_mode="auto",
                enable_subagents=True,
                timeout=self._claude_code_manager.default_timeout,
                progress_callback=_progress_callback,
            )
            logger.info(f"[SubagentProgress] Claude Code Manager run_task completed for task_id: {task_id}")
            status = result.get("status", "unknown")
            output = result.get("output", "")

            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "ok" if status == "done" else "error",
                "summary": (output or f"status={status}")[:300],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.info(f"[SubagentProgress] Pushed subagent_end event: status={status}, task_id={task_id}")

            if status == "done":
                await self._announce_result(task_id, label, task, output, origin, "ok")
            elif status == "timeout":
                # 超时不等于失败，任务可能仍在运行
                bus.push(origin_key, {
                    "type": "subagent_end",
                    "task_id": task_id,
                    "label": label,
                    "status": "timeout",
                    "summary": "任务执行超时，任务可能仍在后台运行，请稍后查询状态",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                await self._announce_result(
                    task_id, label, task,
                    f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒），任务可能仍在后台运行。\n\n请稍后再查询任务状态或等待结果通知。",
                    origin, "timeout"
                )
            else:
                await self._announce_result(task_id, label, task, output or f"status={status}", origin, "error")
        except Exception as e:
            logger.error(f"Subagent [{task_id}] Claude Code backend failed: {e}")
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "error",
                "summary": str(e)[:300],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await self._announce_result(task_id, label, task, f"Error: {str(e)}", origin, "error")

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        template: str = "minimal",
        session_id: str | None = None,
        enable_memory: bool = False,
        media: list[str] | None = None,
        backend: str = "native",
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info(f"Subagent [{task_id}] starting task: {label} (template: {template}, backend: {backend}, memory: {enable_memory}), manager id: {id(self)}")

        # 路由到 Claude Code CLI 后端
        if backend == "claude_code":
            logger.info(f"[SubagentProgress] Routing to Claude Code backend for task_id: {task_id}")
            await self._run_via_claude_code(task_id, task, label, origin)
            logger.info(f"[SubagentProgress] Claude Code backend completed for task_id: {task_id}")
            return

        origin_key = f"{origin['channel']}:{origin['chat_id']}"
        bus = SubagentProgressBus.get()

        bus.push(origin_key, {
            "type": "subagent_start",
            "task_id": task_id,
            "label": label,
            "backend": "native",
            "task": task[:120],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        try:
            tools = self._create_tools_for_template(template)

            from nanobot.agent.memory import MemoryStore
            if enable_memory:
                agent_memory = MemoryStore.for_agent(self.workspace, task_id)
            else:
                agent_memory = MemoryStore.global_memory(self.workspace)

            memory_context = agent_memory.get_memory_context() if enable_memory else ""

            system_prompt = build_system_prompt(template, task, str(self.workspace))
            if memory_context:
                system_prompt += f"\n\n## Agent Memory\n\n{memory_context}"

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
            ]

            if session_id and self.sessions:
                existing = self.sessions.get(session_id)
                if existing:
                    history = existing.get_history(max_messages=50)
                    messages.extend(history)
                else:
                    messages.append(self._build_user_message_with_media(task, media))
            else:
                messages.append(self._build_user_message_with_media(task, media))

            max_iterations = 15
            iteration = 0
            final_result: str | None = None
            # 视觉模型通常不支持 function calling，有图片时不传 tools
            use_tools = None if media else tools.get_definitions()

            # DashScope 模型有图片时绕过 LiteLLM (Bug #16007: LiteLLM 会丢弃 image_url)
            if media and use_tools is None:
                model_lower = self.model.lower()
                if any(k in model_lower for k in ("dashscope", "qwen")):
                    direct_result = await self._dashscope_direct_call(messages, self.model)
                    if direct_result:
                        final_result = direct_result
                    else:
                        final_result = "DashScope 图片识别失败，请检查 API key 和模型配置。"
                    # 跳过正常 loop
                    max_iterations = 0

            while iteration < max_iterations:
                iteration += 1

                response = await self.provider.chat(
                    messages=messages,
                    tools=use_tools,
                    model=self.model,
                )

                if response.has_tool_calls:
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": tool_call_dicts,
                    })

                    for tool_call in response.tool_calls:
                        args_str = json.dumps(tool_call.arguments)
                        logger.debug(f"Subagent [{task_id}] executing: {tool_call.name} with arguments: {args_str}")
                        result = await tools.execute(tool_call.name, tool_call.arguments)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": result,
                        })
                else:
                    final_result = response.content
                    break

            if final_result is None:
                final_result = "Task completed but no final response was generated."

            if enable_memory and agent_memory:
                agent_memory.append_today(
                    f"Task: {task}\nResult: {final_result[:500]}..." if len(final_result) > 500 else f"Task: {task}\nResult: {final_result}"
                )

            if session_id and self.sessions:
                subagent_session = self.sessions.get_or_create(f"subagent:{session_id}")
                for msg in messages:
                    if msg.get("role") in ("user", "assistant"):
                        subagent_session.add_message(
                            role=msg["role"],
                            content=msg.get("content", ""),
                            tool_steps=msg.get("tool_steps"),
                        )
                self.sessions.save(subagent_session)

            logger.info(f"Subagent [{task_id}] completed successfully")
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "ok",
                "summary": final_result[:300],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error(f"Subagent [{task_id}] failed: {e}")
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "error",
                "summary": error_msg[:300],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await self._announce_result(task_id, label, task, error_msg, origin, "error")
    
    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"
        
        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""
        
        # Inject as system message to trigger main agent
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )
        
        await self.bus.publish_inbound(msg)
        logger.debug(f"Subagent [{task_id}] announced result to {origin['channel']}:{origin['chat_id']}")

    def _create_tools_for_template(self, template: str) -> ToolRegistry:
        """Create and register tools based on template."""
        tools = ToolRegistry()
        tool_names = get_tools_for_template(template)

        if "read_file" in tool_names:
            tools.register(ReadFileTool(workspace=str(self.workspace)))
        if "write_file" in tool_names:
            tools.register(WriteFileTool(workspace=str(self.workspace)))
        if "edit_file" in tool_names:
            tools.register(EditFileTool(workspace=str(self.workspace)))
        if "list_dir" in tool_names:
            tools.register(ListDirTool(workspace=str(self.workspace)))
        if "exec" in tool_names:
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            ))
        if "web_search" in tool_names:
            tools.register(WebSearchTool(api_key=self.brave_api_key))
        if "web_fetch" in tool_names:
            tools.register(WebFetchTool())

        return tools

    async def _dashscope_direct_call(
        self,
        messages: list[dict[str, Any]],
        model: str,
    ) -> str | None:
        """
        直接调用 DashScope OpenAI 兼容 API，绕过 LiteLLM。
        LiteLLM Bug #16007: DashScope 适配层会丢弃 image_url 内容。
        """
        import os
        import httpx

        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not api_key:
            try:
                from nanobot.config.loader import load_config
                cfg = load_config()
                api_key = (cfg.providers.dashscope.api_key or "").strip()
            except Exception:
                pass
        if not api_key:
            logger.warning("DashScope API key not found for subagent image recognition")
            return None

        model_name = model.split("/", 1)[1] if "/" in model else model
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"model": model_name, "messages": messages}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content and content.strip():
                logger.info("DashScope subagent image call completed (model: %s)", model_name)
                return content.strip()
            return None
        except Exception as e:
            logger.warning("DashScope subagent image call failed (model: %s): %s", model_name, e)
            return None

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
