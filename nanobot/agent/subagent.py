"""Subagent manager for background task execution."""

import asyncio
import base64
import json
import mimetypes
import threading
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
        main_model: str | None = None,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        sessions: "SessionManager | None" = None,
        max_concurrent_subagents: int = 10,
        claude_code_manager: "ClaudeCodeManager | None" = None,
        status_service: "SystemStatusService | None" = None,
        agent_template_manager: "AgentTemplateManager | None" = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        from nanobot.session.manager import SessionManager
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        # 主模型，用于 summary 等纯文本 LLM 调用（避免使用 vision 模型的 API key）
        self._main_model = main_model or self.model
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.sessions = sessions
        self._claude_code_manager = claude_code_manager
        self._running_tasks: dict[str, tuple[str | None, Any]] = {}  # task_id -> (origin_key, task)
        self._status_service = status_service
        self._agent_template_manager = agent_template_manager

        # 并发控制
        self._max_concurrent_subagents = max_concurrent_subagents
        self._subagent_semaphore = asyncio.Semaphore(max_concurrent_subagents)
        # 按 session 取消标记（用于 _run_subagent 内检查，实现真正停止）
        self._session_cancelled: set[str] = set()
        # batch 聚合：batch_id -> set[task_id]，用于多子 agent 完成后统一汇总
        self._batch_tasks: dict[str, set[str]] = {}
        self._batch_lock = threading.Lock()

    def _media_has_images(self, media: list[str]) -> bool:
        """检查 media 中是否包含图片（仅图片时需禁用 tools，语音子 agent 需保留 exec 等工具）。"""
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if mime and mime.startswith("image/"):
                return True
            if not mime and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                return True
        return False

    def _build_user_message_with_media(self, text: str, media: list[str] | None = None) -> dict[str, Any]:
        """Build user message with optional media (images or audio files)."""
        if not media:
            return {"role": "user", "content": text}

        images = []
        audio_files = []

        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file():
                continue

            # 检查文件类型（支持 mime 类型或扩展名）
            is_image = mime and mime.startswith("image/")
            is_audio = mime and mime.startswith("audio/")
            # 如果 mime 无法识别，检查扩展名
            if not mime:
                ext = p.suffix.lower()
                is_image = ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')
                is_audio = ext in ('.mp3', '.wav', '.ogg', '.m4a', '.opus', '.webm', '.aac')

            if is_image:
                # 图片：编码为 base64
                b64 = base64.b64encode(p.read_bytes()).decode()
                images.append({"type": "image_url", "image_url": {"url": f"data:{mime or 'image/jpeg'};base64,{b64}"}})
            elif is_audio:
                # 音频：记录文件路径供子 agent 读取
                audio_files.append(str(p))

        # 构建内容：如果有音频文件，在文本中附加路径信息
        content_text = text
        if audio_files:
            audio_info = "\n\n[Attached Audio Files]\n" + "\n".join(f"- {f}" for f in audio_files)
            content_text = text + audio_info

        if not images:
            return {"role": "user", "content": content_text}

        return {"role": "user", "content": images + [{"type": "text", "text": content_text}]}

    def _resolve_backend(self, template: str, backend: str) -> str:
        """
        决定实际使用的执行后端。

        当 template 包含 "coder" 或 "claude" 时（如 "coder", "claude-coder"），
        可以使用 claude_code 后端；其他模板始终使用 native LLM。

        Args:
            template: 子 Agent 模板名称
            backend: 用户指定的后端偏好（"auto" / "native" / "claude_code"）

        Returns:
            "claude_code" 或 "native"
        """
        # 判断模板是否支持 Claude Code 后端
        supports_claude_code = "coder" in template.lower() or "claude" in template.lower()

        if not supports_claude_code:
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

    def cancel_task(self, task_id: str) -> bool:
        """取消指定的子代理任务。仅从追踪列表移除，不释放信号量（由任务线程在 finally 中释放，避免双重释放）。"""
        if task_id in self._running_tasks:
            logger.info(f"[SubagentManager] Cancelling task: {task_id}")
            orig_key, _ = self._running_tasks.get(task_id, (None, None))
            self._running_tasks.pop(task_id, None)
            if orig_key and hasattr(self, "_session_cancelled"):
                self._session_cancelled.add(orig_key)
            logger.info(f"[SubagentManager] Task {task_id} cancelled (thread will release semaphore on exit)")
            return True
        return False

    def cancel_by_session(self, channel: str, chat_id: str) -> int:
        """取消指定 session 的所有子代理任务，返回取消的任务数量。仅移除追踪，不释放信号量。"""
        origin_key = f"{channel}:{chat_id}"
        if hasattr(self, "_session_cancelled"):
            self._session_cancelled.add(origin_key)
        cancelled = 0
        for task_id, (orig_key, _) in list(self._running_tasks.items()):
            if orig_key == origin_key:
                logger.info(f"[SubagentManager] Cancelling task {task_id} for session {origin_key}")
                self._running_tasks.pop(task_id, None)
                # 不在此处 release，避免双重释放
                cancelled += 1
        if cancelled > 0:
            logger.info(f"[SubagentManager] Cancelled {cancelled} tasks for session {origin_key}")
        return cancelled

    def cancel_all_tasks(self) -> int:
        """取消所有子代理任务，返回取消的任务数量"""
        count = len(self._running_tasks)
        for task_id in list(self._running_tasks.keys()):
            self.cancel_task(task_id)
        return count

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
        batch_id: str | None = None,
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

        if batch_id:
            with self._batch_lock:
                self._batch_tasks.setdefault(batch_id, set()).add(task_id)
            logger.info(f"[SubagentProgress] Added task {task_id} to batch {batch_id}")

        logger.info(f"[SubagentProgress] SubagentManager id: {id(self)}, _running_tasks before: {list(self._running_tasks.keys())}")

        # 在独立线程中运行子代理任务，避免被主请求的事件循环关闭取消
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
                    self._run_subagent(
                        task_id, task, display_label, origin,
                        template=template, session_id=session_id,
                        enable_memory=enable_memory, media=media,
                        backend=resolved_backend, batch_id=batch_id,
                    )
                )
                logger.info(f"[SubagentProgress] Thread-based subagent task {task_id} completed successfully")
            except Exception as e:
                logger.error(f"[SubagentProgress] Thread-based subagent task {task_id} failed: {e}")
                # 确保即使失败也发送 subagent_end 事件
                try:
                    error_result = f"任务执行失败: {str(e)}"
                    bus.push(origin_key, {
                        "type": "subagent_end",
                        "task_id": task_id,
                        "label": display_label,
                        "status": "error",
                        "summary": error_result[:300],
                        "result": error_result,  # 完整错误信息供前端使用
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

        # 由于任务在线程中运行，存储 origin_key 以便后续按 session 取消
        origin_key = f"{origin_channel}:{origin_chat_id}"
        self._running_tasks[task_id] = (origin_key, None)  # 存储 (origin_key, 占位)
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
        template: str = "",
        batch_id: str | None = None,
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
                "result": output,  # 完整结果供前端使用
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.info(f"[SubagentProgress] Pushed subagent_end event: status={status}, task_id={task_id}")

            # 存储到 session（供 get_subagent_results 和 batch 汇总使用）
            end_status = "ok" if status == "done" else "timeout" if status == "timeout" else "error"
            if self.sessions:
                main_session_key = f"{origin['channel']}:{origin['chat_id']}"
                main_session = self.sessions.get_or_create(main_session_key)
                if main_session:
                    if status == "timeout":
                        store_result = f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒），任务可能仍在后台运行。"
                    else:
                        store_result = output or f"status={status}"
                    main_session.subagent_results[task_id] = {
                        "label": label,
                        "task": task,
                        "result": store_result,
                        "status": end_status,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        **({"batch_id": batch_id} if batch_id else {}),
                    }
                    self.sessions.save(main_session)
                    logger.info(f"[SubagentProgress] Stored Claude Code result for task {task_id}, status={end_status}")

            # batch 聚合或单独交付
            is_last = self._is_last_in_batch(batch_id, task_id) if batch_id else True
            if is_last:
                if batch_id:
                    with self._batch_lock:
                        batch_task_ids = self._batch_tasks.pop(batch_id, set())
                    if len(batch_task_ids) > 1:
                        await self._deliver_batch_complete(batch_id, batch_task_ids, origin)
                    else:
                        if status == "done":
                            if origin.get("channel") == "web":
                                await self._generate_and_push_summary(task_id, label, task, output, origin)
                            else:
                                await self._announce_result(task_id, label, task, output, origin, "ok", template=template)
                        elif status == "timeout" and origin.get("channel") != "web":
                            bus.push(origin_key, {
                                "type": "subagent_end",
                                "task_id": task_id, "label": label, "status": "timeout",
                                "summary": "任务执行超时，任务可能仍在后台运行，请稍后查询状态",
                                "result": f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒），任务可能仍在后台运行。",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                            await self._announce_result(
                                task_id, label, task,
                                f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒），任务可能仍在后台运行。",
                                origin, "timeout", template=template
                            )
                        elif status != "done" and origin.get("channel") != "web":
                            await self._announce_result(task_id, label, task, output or f"status={status}", origin, "error", template=template)
                else:
                    if status == "done":
                        if origin.get("channel") == "web":
                            await self._generate_and_push_summary(task_id, label, task, output, origin)
                        else:
                            await self._announce_result(task_id, label, task, output, origin, "ok", template=template)
                    elif status == "timeout" and origin.get("channel") != "web":
                        bus.push(origin_key, {
                            "type": "subagent_end",
                            "task_id": task_id, "label": label, "status": "timeout",
                            "summary": "任务执行超时，任务可能仍在后台运行，请稍后查询状态",
                            "result": f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒），任务可能仍在后台运行。",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                        await self._announce_result(
                            task_id, label, task,
                            f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒），任务可能仍在后台运行。",
                            origin, "timeout", template=template
                        )
                    elif status != "done" and origin.get("channel") != "web":
                        await self._announce_result(task_id, label, task, output or f"status={status}", origin, "error", template=template)
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
            await self._announce_result(task_id, label, task, f"Error: {str(e)}", origin, "error", template=template)

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
        batch_id: str | None = None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info(f"Subagent [{task_id}] starting task: {label} (template: {template}, backend: {backend}, memory: {enable_memory}), manager id: {id(self)}")

        # Determine the effective model: template's model or fallback to self.model
        effective_model = self.model
        if self._agent_template_manager and template:
            template_model = self._agent_template_manager.get_model_for_template(template)
            if template_model:
                effective_model = template_model
                logger.info(f"Subagent [{task_id}] using template '{template}' model: {effective_model}")
            else:
                logger.info(f"Subagent [{task_id}] using default model: {effective_model}")

        # 路由到 Claude Code CLI 后端
        if backend == "claude_code":
            logger.info(f"[SubagentProgress] Routing to Claude Code backend for task_id: {task_id}")
            await self._run_via_claude_code(task_id, task, label, origin, template=template, batch_id=batch_id)
            logger.info(f"[SubagentProgress] Claude Code backend completed for task_id: {task_id}")
            return

        origin_key = f"{origin['channel']}:{origin['chat_id']}"
        # 启动前检查是否已被取消
        if origin_key in self._session_cancelled:
            self._session_cancelled.discard(origin_key)
            logger.info(f"Subagent [{task_id}] cancelled before start (session {origin_key})")
            bus = SubagentProgressBus.get()
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "cancelled",
                "summary": "任务已被用户取消。",
                "result": "任务已被用户取消。",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

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

            # 设置 ClaudeCodeTool 的 context（如果有）
            claude_code_tool = tools.get("claude_code")
            if claude_code_tool:
                claude_code_tool.set_context(origin["channel"], origin["chat_id"])

            from nanobot.agent.memory import MemoryStore
            if enable_memory:
                agent_memory = MemoryStore.for_agent(self.workspace, task_id)
            else:
                agent_memory = MemoryStore.global_memory(self.workspace)

            memory_context = agent_memory.get_memory_context() if enable_memory else ""

            # 优先使用 AgentTemplateManager（如果可用）
            if self._agent_template_manager:
                system_prompt = self._agent_template_manager.build_system_prompt(
                    template, task, str(self.workspace)
                )
                # 如果模板不存在，回退到 prompts.py
                if not system_prompt:
                    system_prompt = build_system_prompt(template, task, str(self.workspace))
            else:
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
            cancelled_by_user = False
            # 子 agent 可能使用与主模型不同的模型，显式传入 api_key/api_base 避免 401（不依赖 env）
            sa_key: str | None = None
            sa_base: str | None = None
            try:
                from nanobot.config.loader import load_config
                cfg = load_config()
                sa_key = cfg.get_api_key(effective_model)
                sa_base = cfg.get_api_base(effective_model)
                if sa_key and hasattr(self.provider, "ensure_api_key_for_model"):
                    self.provider.ensure_api_key_for_model(
                        effective_model, sa_key, sa_base
                    )
                if not sa_key and any(k in effective_model.lower() for k in ("dashscope", "qwen")):
                    logger.warning(
                        f"子 agent 模型 {effective_model} 需要 DashScope API Key，"
                        "请在配置页 Provider 中填写 Qwen（通义）的 apiKey"
                    )
            except Exception as e:
                logger.debug(f"Subagent pre-register model key: {e}")

            # 视觉模型通常不支持 function calling，有图片时不传 tools
            # 语音子 agent 仅有音频时需保留 exec 等工具，故仅在有图片时禁用
            use_tools = None if (media and self._media_has_images(media)) else tools.get_definitions()

            # DashScope 模型有图片时绕过 LiteLLM (Bug #16007: LiteLLM 会丢弃 image_url)
            if media and self._media_has_images(media) and use_tools is None:
                model_lower = effective_model.lower()
                if any(k in model_lower for k in ("dashscope", "qwen")):
                    direct_result = await self._dashscope_direct_call(messages, effective_model)
                    if direct_result:
                        final_result = direct_result
                    else:
                        final_result = "DashScope 图片识别失败，请检查 API key 和模型配置。"
                    # 跳过正常 loop
                    max_iterations = 0

            while iteration < max_iterations:
                iteration += 1
                # 检查是否被用户取消
                if origin_key in self._session_cancelled:
                    self._session_cancelled.discard(origin_key)
                    logger.info(f"Subagent [{task_id}] cancelled by user (session {origin_key})")
                    final_result = "任务已被用户取消。"
                    cancelled_by_user = True
                    break

                # LLM 调用期间每 2 秒轮询取消，与主 Agent 一致
                _LLM_CALL_TIMEOUT = 120
                _CANCEL_CHECK_INTERVAL = 2.0
                cancelled_during_llm = False
                try:
                    chat_kwargs: dict[str, Any] = {
                        "messages": messages,
                        "tools": use_tools,
                        "model": effective_model,
                    }
                    if sa_key:
                        chat_kwargs["api_key"] = sa_key
                        if sa_base:
                            chat_kwargs["api_base"] = sa_base
                    llm_task = asyncio.create_task(self.provider.chat(**chat_kwargs))
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
                        if origin_key in self._session_cancelled:
                            self._session_cancelled.discard(origin_key)
                            llm_task.cancel()
                            try:
                                await llm_task
                            except asyncio.CancelledError:
                                pass
                            cancelled_during_llm = True
                            break
                    if cancelled_during_llm:
                        final_result = "任务已被用户取消。"
                        cancelled_by_user = True
                        break
                    response = await llm_task
                except asyncio.TimeoutError:
                    logger.warning(f"Subagent [{task_id}] LLM call timed out")
                    final_result = "LLM 调用超时。"
                    break

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
            end_status = "cancelled" if cancelled_by_user else "ok"
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": end_status,
                "summary": final_result[:300],
                "result": final_result,  # 完整结果供前端使用
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # 将子agent结果存入session，供主agent后续查询
            if self.sessions:
                main_session_key = f"{origin['channel']}:{origin['chat_id']}"
                logger.info(f"[SubagentProgress] Trying to store result, session_key: {main_session_key}, origin: {origin}")
                # 使用 get_or_create 而不是 get，确保session存在（即使主线程还没保存）
                main_session = self.sessions.get_or_create(main_session_key)
                logger.info(f"[SubagentProgress] Session obtained: {main_session is not None}, key: {main_session.key if main_session else 'N/A'}")
                if main_session:
                    main_session.subagent_results[task_id] = {
                        "label": label,
                        "task": task,
                        "result": final_result,
                        "status": end_status,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        **({"batch_id": batch_id} if batch_id else {}),
                    }
                    self.sessions.save(main_session)
                    logger.info(f"[SubagentProgress] Stored result for task {task_id} in session {main_session_key}")
                    logger.info(f"[SubagentProgress] Stored data: label={label}, status={end_status}, result_len={len(final_result)}")
                    logger.info(f"[SubagentProgress] Session messages count: {len(main_session.messages)}")
                    # 验证存储
                    verify_session = self.sessions.get(main_session_key)
                    if verify_session and task_id in verify_session.subagent_results:
                        logger.info(f"[SubagentProgress] Verified: task {task_id} exists in session, result length: {len(verify_session.subagent_results[task_id].get('result', ''))}")
                    else:
                        logger.warning(f"[SubagentProgress] Verification failed: task {task_id} not found in session {main_session_key}")
                else:
                    logger.warning(f"[SubagentProgress] Failed to get session for key: {main_session_key}")
            else:
                logger.warning(f"[SubagentProgress] sessions is None, cannot store result")

            # batch 聚合：最后一个完成时做汇总；否则不单独 push
            logger.info(f"[SubagentProgress] origin channel: {origin.get('channel')}, task_id: {task_id}, batch_id: {batch_id}")
            is_last = self._is_last_in_batch(batch_id, task_id) if batch_id else True
            if is_last:
                if batch_id:
                    with self._batch_lock:
                        batch_task_ids = self._batch_tasks.pop(batch_id, set())
                    if len(batch_task_ids) > 1:
                        await self._deliver_batch_complete(batch_id, batch_task_ids, origin)
                    else:
                        if origin.get("channel") == "web":
                            await self._generate_and_push_summary(task_id, label, task, final_result, origin)
                        else:
                            await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
                else:
                    if origin.get("channel") == "web":
                        await self._generate_and_push_summary(task_id, label, task, final_result, origin)
                    else:
                        await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error(f"Subagent [{task_id}] failed: {e}")
            end_status = "error"
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "error",
                "summary": error_msg[:300],
                "result": error_msg,  # 完整错误信息供前端使用
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # 异常时也存储结果
            if self.sessions:
                main_session_key = f"{origin['channel']}:{origin['chat_id']}"
                logger.info(f"[SubagentProgress] Exception: trying to store error result, session_key: {main_session_key}")
                main_session = self.sessions.get_or_create(main_session_key)
                if main_session:
                    main_session.subagent_results[task_id] = {
                        "label": label,
                        "task": task,
                        "result": error_msg,
                        "status": end_status,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        **({"batch_id": batch_id} if batch_id else {}),
                    }
                    self.sessions.save(main_session)
                    logger.info(f"[SubagentProgress] Stored error result for task {task_id}")

            # batch 聚合：最后一个完成时做汇总
            is_last = self._is_last_in_batch(batch_id, task_id) if batch_id else True
            if is_last:
                if batch_id:
                    with self._batch_lock:
                        batch_task_ids = self._batch_tasks.pop(batch_id, set())
                    if len(batch_task_ids) > 1:
                        await self._deliver_batch_complete(batch_id, batch_task_ids, origin)
                    else:
                        if origin.get("channel") != "web":
                            await self._announce_result(task_id, label, task, error_msg, origin, "error", template=template)
                else:
                    if origin.get("channel") != "web":
                        await self._announce_result(task_id, label, task, error_msg, origin, "error", template=template)
    
    def _is_last_in_batch(self, batch_id: str | None, task_id: str) -> bool:
        """判断当前 task 是否为该 batch 中最后一个完成的（不含已移除的）。"""
        if not batch_id:
            return True
        with self._batch_lock:
            batch_task_ids = self._batch_tasks.get(batch_id, set())
            still_running = {t for t in batch_task_ids if t in self._running_tasks}
        return still_running == {task_id}

    async def _deliver_batch_complete(
        self,
        batch_id: str,
        batch_task_ids: set[str],
        origin: dict[str, str],
    ) -> None:
        """batch 内所有任务完成，进行汇总并交付。"""
        main_session_key = f"{origin['channel']}:{origin['chat_id']}"
        if not self.sessions:
            logger.warning("[SubagentBatch] No sessions, cannot deliver batch")
            return
        main_session = self.sessions.get_or_create(main_session_key)
        batch_results = [
            (tid, main_session.subagent_results[tid])
            for tid in batch_task_ids
            if tid in main_session.subagent_results and main_session.subagent_results[tid].get("batch_id") == batch_id
        ]
        if not batch_results:
            logger.warning(f"[SubagentBatch] No results found for batch {batch_id}")
            return
        if origin.get("channel") == "web":
            await self._generate_batch_summary(batch_id, batch_results, origin)
        else:
            await self._announce_batch_result(batch_results, origin)

    async def _generate_batch_summary(
        self,
        batch_id: str,
        batch_results: list[tuple[str, dict[str, Any]]],
        origin: dict[str, str],
    ) -> None:
        """Web 渠道：对 batch 内多条结果做一次 LLM 综合汇总并推送。包含完整任务指令和结果，不截断。"""
        origin_key = f"{origin['channel']}:{origin['chat_id']}"
        bus = SubagentProgressBus.get()
        message_id = f"msg_subagent_batch_{batch_id}"

        # 完整内容：每条含任务指令 + 完整结果
        full_parts = []
        preview_parts = []
        for tid, r in batch_results:
            label = r.get("label", tid)
            task = r.get("task", "")
            result = r.get("result") or ""
            status = r.get("status", "ok")
            block = f"[{label}] ({status})\n任务指令：{task}\n\n结果：\n{result}"
            full_parts.append(block)
            # 用于 LLM 的预览（单条截断以防 token 溢出，但保留足够上下文）
            preview_block = f"[{label}] ({status})\n任务：{task[:500]}{'...' if len(task) > 500 else ''}\n结果：{result[:2000]}{'...' if len(result) > 2000 else ''}"
            preview_parts.append(preview_block)
        combined_preview = "\n\n---\n\n".join(preview_parts)
        full_detail = "\n\n---\n\n".join(full_parts)

        try:
            summary_prompt = (
                "以下是多个子任务的执行结果（含任务指令与结果），请用 2-4 句话综合总结，自然回复用户。"
                "不要逐条复述，要提炼关键结论。不要提 subagent、task_id 等技术细节。\n\n"
                f"{combined_preview}"
            )
            messages = [
                {"role": "system", "content": "你是协调子任务的主 agent，负责综合多子任务结果并向用户汇报。"},
                {"role": "user", "content": summary_prompt},
            ]
            response = await asyncio.wait_for(
                self.provider.chat(
                    messages=messages,
                    tools=None,
                    model=self._main_model,
                    max_tokens=400,
                    temperature=0.5,
                ),
                timeout=15.0,
            )
            brief = (response.content or "").strip()
            if not brief:
                raise ValueError("Empty summary from LLM")
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[SubagentBatch] LLM summary failed for batch {batch_id}: {e}, using fallback")
            brief = "各子任务执行完成，结果如下："

        llm_summary = f"{brief}\n\n---\n\n**各任务详情**：\n\n{full_detail}"

        if self.sessions:
            main_session = self.sessions.get_or_create(origin_key)
            if main_session:
                main_session.add_message(
                    role="assistant",
                    content=llm_summary,
                    source="subagent_batch_summary",
                    batch_id=batch_id,
                )
                self.sessions.save(main_session)

        batch_task_ids_list = [tid for tid, _ in batch_results]
        bus.push(origin_key, {
            "type": "subagent_summary",
            "task_id": batch_id,
            "task_ids": batch_task_ids_list,
            "label": "batch",
            "llm_summary": llm_summary,
            "message_id": message_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[SubagentBatch] Pushed batch summary for {batch_id}")

    async def _announce_batch_result(
        self,
        batch_results: list[tuple[str, dict[str, Any]]],
        origin: dict[str, str],
    ) -> None:
        """非 Web 渠道：将 batch 内所有结果合并为一条 system 消息，触发主 agent 综合。包含完整任务指令和结果。"""
        parts = []
        for tid, r in batch_results:
            label = r.get("label", tid)
            task = r.get("task", "")
            result = r.get("result") or ""
            status = r.get("status", "ok")
            status_text = "completed successfully" if status == "ok" else "failed"
            parts.append(f"[Subagent '{label}' {status_text}]\nTask: {task}\nResult:\n{result}")
        content = "\n\n---\n\n".join(parts)
        content += "\n\nSummarize the above subagent results naturally for the user in 2-4 sentences. Do not mention technical details."
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=content,
        )
        await self.bus.publish_inbound(msg)
        logger.debug(f"[SubagentBatch] Announced batch result to {origin['channel']}:{origin['chat_id']}")

    def _build_full_summary_content(self, label: str, task: str, result: str, brief_summary: str) -> str:
        """
        构建完整 summary 内容：简要总结 + 任务指令 + 完整结果。
        确保主 agent 和用户获得完整上下文，不截断。
        """
        return (
            f"{brief_summary}\n\n---\n\n"
            f"**任务指令**：\n{task}\n\n"
            f"**完整结果**：\n{result}"
        )

    async def _generate_and_push_summary(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
    ) -> None:
        """
        Web 渠道专用：子 agent 成功完成后异步生成 LLM 总结并推送 subagent_summary 事件。
        使用主模型（纯文本），失败时降级为结果预览。返回内容包含任务指令和完整结果，不截断。
        """
        origin_key = f"{origin['channel']}:{origin['chat_id']}"
        bus = SubagentProgressBus.get()
        message_id = f"msg_subagent_{task_id}"
        fallback_brief = f"任务「{label}」已完成。"
        fallback_full = self._build_full_summary_content(label, task, result, fallback_brief)

        try:
            # 传入完整任务指令和结果，供 LLM 准确总结（限制 result 长度以防 token 溢出，保留足够上下文）
            result_for_prompt = result if len(result) <= 12000 else result[:12000] + "\n\n...(结果过长已截断，完整结果见下方)"
            summary_prompt = (
                f"任务名称：{label}\n\n"
                f"任务描述（子 agent 收到的完整指令）：\n{task}\n\n"
                f"执行结果：\n{result_for_prompt}"
            )
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一个助手，用 1–2 句话自然地向用户总结后台任务的执行结果。"
                        "语言简洁口语化，不要提 subagent、task_id 等技术细节，不要使用 Markdown 标题。"
                        "总结时要结合「任务描述」和「执行结果」两方面，确保准确。"
                    ),
                },
                {"role": "user", "content": summary_prompt},
            ]
            # 使用主模型做纯文本 summary，避免 vision 模型 (如 qwen-vl-plus) 的 API key 要求
            response = await asyncio.wait_for(
                self.provider.chat(
                    messages=messages,
                    tools=None,
                    model=self._main_model,
                    max_tokens=300,
                    temperature=0.5,
                ),
                timeout=15.0,
            )
            brief = (response.content or "").strip()
            if not brief:
                brief = fallback_brief

            full_content = self._build_full_summary_content(label, task, result, brief)

            await self._push_summary_to_session_and_bus(
                task_id, label, origin_key, message_id, full_content, origin, bus
            )

        except asyncio.TimeoutError:
            logger.warning(f"[SubagentSummary] LLM summary timed out for task {task_id}, using fallback")
            await self._push_summary_to_session_and_bus(
                task_id, label, origin_key, message_id, fallback_full, origin, bus
            )
        except Exception as e:
            logger.warning(f"[SubagentSummary] Failed to generate summary for task {task_id}: {e}, using fallback")
            await self._push_summary_to_session_and_bus(
                task_id, label, origin_key, message_id, fallback_full, origin, bus
            )

    async def _push_summary_to_session_and_bus(
        self,
        task_id: str,
        label: str,
        origin_key: str,
        message_id: str,
        llm_summary: str,
        origin: dict[str, str],
        bus: SubagentProgressBus,
    ) -> None:
        """将 summary 写入 session 并推送到 bus。"""
        if self.sessions:
            main_session = self.sessions.get_or_create(origin_key)
            if main_session:
                main_session.add_message(
                    role="assistant",
                    content=llm_summary,
                    source="subagent_summary",
                    task_id=task_id,
                )
                self.sessions.save(main_session)
                logger.info(f"[SubagentSummary] Saved summary to session for task {task_id}")

        bus.push(origin_key, {
            "type": "subagent_summary",
            "task_id": task_id,
            "task_ids": [task_id],
            "label": label,
            "llm_summary": llm_summary,
            "message_id": message_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[SubagentSummary] Pushed subagent_summary for task {task_id}")

    async def _inject_voice_as_user_message(self, origin: dict[str, str], transcribed_text: str) -> None:
        """
        将语音转写结果作为用户指令注入，让主 agent 直接执行。
        用于飞书/Telegram 等渠道：语音触发 → 子 agent 转写 → 转写文本作为新用户消息，主 agent 自然回应。
        """
        msg = InboundMessage(
            channel=origin["channel"],
            sender_id="voice",
            chat_id=origin["chat_id"],
            content=transcribed_text.strip() or "[语音转写为空]",
        )
        await self.bus.publish_inbound(msg)
        logger.info(f"[Voice] Injected transcription as user message to {origin['channel']}:{origin['chat_id']}")

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
        template: str = "",
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        # voice 模板在非 web 渠道：转写结果作为用户指令注入，主 agent 直接执行，不走 summary
        if template == "voice" and origin.get("channel") != "web" and status == "ok":
            await self._inject_voice_as_user_message(origin, result)
            return

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

        # 优先使用 AgentTemplateManager（如果可用）
        if self._agent_template_manager:
            tool_names = self._agent_template_manager.get_tools_for_template(template)
            # 如果模板不存在，回退到 prompts.py
            if not tool_names:
                tool_names = get_tools_for_template(template)
        else:
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
        if "voice_transcribe" in tool_names:
            from nanobot.agent.tools.voice_transcribe import VoiceTranscribeTool
            tools.register(VoiceTranscribeTool())

        # Claude Code tool - 需要 ClaudeCodeManager
        if "claude_code" in tool_names and self._claude_code_manager:
            from nanobot.agent.tools.claude_code import ClaudeCodeTool
            tools.register(ClaudeCodeTool(manager=self._claude_code_manager))

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
