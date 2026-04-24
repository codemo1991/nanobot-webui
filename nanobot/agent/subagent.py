"""Subagent manager for background task execution."""

import asyncio
import base64
import concurrent.futures
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
from nanobot.utils.helpers import sanitize_args_for_log
from nanobot.agent.prompts import get_template, build_system_prompt, get_tools_for_template
from nanobot.config.subagent_summary_prompts_loader import (
    get_batch_system_prompt,
    get_batch_user_intro,
    get_single_task_system_prompt,
)


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
        claude_code_permission_mode: str = "auto",
        status_service: "SystemStatusService | None" = None,
        agent_template_manager: "AgentTemplateManager | None" = None,
        backend_registry: "BackendRegistry | None" = None,
        backend_resolver: "BackendResolver | None" = None,
        parent_tools_registry: "ToolRegistry | None" = None,
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
        self._claude_code_permission_mode = claude_code_permission_mode
        self._running_tasks: dict[str, tuple[str | None, Any]] = {}  # task_id -> (origin_key, task)
        self._session_tasks: dict[str, set[str]] = {}  # origin_key -> set[task_id]
        self._status_service = status_service
        self._agent_template_manager = agent_template_manager
        self._parent_tools_registry = parent_tools_registry

        # 并发控制
        self._max_concurrent_subagents = max_concurrent_subagents
        self._subagent_semaphore = asyncio.Semaphore(max_concurrent_subagents)
        # 按 session 取消标记（用于 _run_subagent 内检查，实现真正停止）
        self._session_cancelled: set[str] = set()
        # batch 聚合：batch_id -> set[task_id]，用于多子 agent 完成后统一汇总
        self._batch_tasks: dict[str, set[str]] = {}
        self._batch_lock = threading.Lock()
        # 工具缓存：按模板缓存，避免每次 spawn 都重新创建工具实例
        self._tools_cache: dict[str, Any] = {}

        # Backend registry and resolver
        from nanobot.agent.backend_registry import BackendRegistry as BR
        from nanobot.agent.backend_resolver import BackendResolver as BRes
        self._backend_registry = backend_registry if backend_registry is not None else BR()
        # 确保 voice、dashscope_vision 等自注册 backends 已加载
        import nanobot.agent.backends  # noqa: F401
        self._backend_resolver = backend_resolver if backend_resolver is not None else BRes(
            agent_template_manager, self._backend_registry
        )

        # 执行链路监控
        from nanobot.monitoring.execution_chain import ExecutionChainMonitor
        self._chain_monitor = ExecutionChainMonitor.get_instance()
        self._subagent_nodes: dict[str, str] = {}  # task_id -> node_id

        # 主事件循环引用，用于后台线程中线程安全地发布 inbound 消息
        self._main_loop: asyncio.AbstractEventLoop | None = None

    def set_main_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """设置主事件循环引用，用于后台线程中线程安全地发布消息。"""
        self._main_loop = loop

    def _publish_inbound_safe(self, msg: "InboundMessage") -> None:
        """
        线程安全地将消息发布到 inbound 队列。

        兼容两种调用场景：
        - 后台线程（spawn 子 agent）：通过 call_soon_threadsafe 唤醒主循环 I/O selector
        - 主事件循环：直接 put_nowait（由调用方的 await 上下文保证安全）

        注意：put_nowait 在队列满（maxsize=200）时会抛 QueueFull，
        此处静默记录日志以避免整个后台线程崩溃。
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        main_loop = self._main_loop

        def _safe_put(m: "InboundMessage") -> None:
            try:
                self.bus.inbound.put_nowait(m)
            except asyncio.QueueFull:
                logger.warning(
                    "[SubagentBus] inbound queue full, announce message dropped "
                    "(channel=%s, chat_id=%s)",
                    getattr(m, "channel", "?"), getattr(m, "chat_id", "?"),
                )

        if main_loop is not None and current_loop is not main_loop:
            # 后台线程路径：call_soon_threadsafe 写入 self-pipe，确保主循环从 I/O selector 中唤醒
            main_loop.call_soon_threadsafe(_safe_put, msg)
        else:
            # 主循环路径：直接同步 put（调用方应在协程中调用）
            _safe_put(msg)

    def _complete_subagent_node(self, task_id: str, status: str, result: str = None, error: str = None):
        """完成子 Agent 执行节点"""
        node_id = self._subagent_nodes.pop(task_id, None)
        if node_id and self._chain_monitor.get_current_chain():
            try:
                self._chain_monitor.get_current_chain().complete_node(
                    node_id,
                    result=result,
                    error=error
                )
                logger.info(f"[ExecutionChain] Completed subagent node: {node_id}, status: {status}")
            except Exception as e:
                logger.warning(f"[ExecutionChain] Failed to complete subagent node: {e}")

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


    def cancel_task(self, task_id: str) -> bool:
        """取消指定的子代理任务。调用 asyncio.Task.cancel() 实现真正停止。"""
        entry = self._running_tasks.get(task_id)
        if entry:
            orig_key, task = entry
            if task is not None and not task.done():
                logger.info(f"[SubagentManager] Cancelling task: {task_id}")
                task.cancel()
                if orig_key and hasattr(self, "_session_cancelled"):
                    self._session_cancelled.add(orig_key)
                logger.info(f"[SubagentManager] Task {task_id} cancelled")
                return True
            # vision-inline uses None as task (thread-based)
            if task is None:
                logger.info(f"[SubagentManager] Cancelling thread-based task: {task_id}")
                self._running_tasks.pop(task_id, None)
                if orig_key and hasattr(self, "_session_cancelled"):
                    self._session_cancelled.add(orig_key)
                return True
        return False

    def cancel_by_session(self, channel: str, chat_id: str) -> int:
        """取消指定 session 的所有子代理任务，返回取消的任务数量。"""
        origin_key = f"{channel}:{chat_id}"
        if hasattr(self, "_session_cancelled"):
            self._session_cancelled.add(origin_key)
        cancelled = 0
        for task_id, (orig_key, task) in list(self._running_tasks.items()):
            if orig_key == origin_key:
                if self.cancel_task(task_id):
                    cancelled += 1
        if cancelled > 0:
            logger.info(f"[SubagentManager] Cancelled {cancelled} tasks for session {origin_key}")
        return cancelled

    def cancel_all_tasks(self) -> int:
        """取消所有子代理任务，返回取消的任务数量"""
        count = 0
        for task_id in list(self._running_tasks.keys()):
            if self.cancel_task(task_id):
                count += 1
        return count

    async def _run_subagent_task(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        template: str,
        session_id: str | None,
        enable_memory: bool,
        media: list[str] | None,
        backend: str,
        batch_id: str | None,
        parent_span_id: str | None,
    ) -> None:
        """Wrapper around _run_subagent that handles cleanup and progress events."""
        origin_key = f"{origin['channel']}:{origin['chat_id']}"
        bus = SubagentProgressBus.get()
        cancelled_by_user = False
        try:
            await self._run_subagent(
                task_id, task, label, origin,
                template=template, session_id=session_id,
                enable_memory=enable_memory, media=media,
                backend=backend, batch_id=batch_id,
                parent_span_id=parent_span_id,
            )
            logger.info(f"[SubagentProgress] Subagent task {task_id} completed successfully")
            self._complete_subagent_node(task_id, status="completed", result="Task completed")
        except asyncio.CancelledError:
            cancelled_by_user = True
            logger.info(f"[SubagentProgress] Subagent task {task_id} was cancelled")
            self._complete_subagent_node(task_id, status="cancelled", result="任务已被用户取消。")
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "cancelled",
                "summary": "任务已被用户取消。",
                "result": "任务已被用户取消。",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            raise  # re-raise so asyncio marks the task as cancelled
        except Exception as e:
            logger.error(f"[SubagentProgress] Subagent task {task_id} failed: {e}")
            self._complete_subagent_node(task_id, status="failed", result=str(e))
            error_result = f"任务执行失败: {str(e)}"
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "error",
                "summary": error_result[:300],
                "result": error_result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            # Release semaphore and cleanup
            try:
                self._subagent_semaphore.release()
            except ValueError:
                pass
            self._running_tasks.pop(task_id, None)
            # Clean up _session_tasks to prevent memory leak
            self._session_tasks.setdefault(origin_key, set()).discard(task_id)
            if not self._session_tasks.get(origin_key):
                self._session_tasks.pop(origin_key, None)
            logger.info(f"[SubagentProgress] Semaphore released and task {task_id} removed from _running_tasks")
            # If no remaining subagents for this session, push stream_done
            remaining = sum(1 for ok, _ in self._running_tasks.values() if ok == origin_key)
            if remaining == 0 and not cancelled_by_user:
                try:
                    bus.push(origin_key, {"type": "stream_done"})
                    logger.info(f"[SubagentProgress] Pushed stream_done for origin_key: {origin_key}")
                except Exception as e:
                    logger.warning(f"[SubagentProgress] Failed to push stream_done: {e}")
            # Handle batch aggregation
            if batch_id:
                with self._batch_lock:
                    tasks = self._batch_tasks.get(batch_id, set())
                    tasks.discard(task_id)
                    if not tasks:
                        self._batch_tasks.pop(batch_id, None)
                        try:
                            bus.push(origin_key, {"type": "batch_complete", "batch_id": batch_id})
                        except Exception:
                            pass

    async def run_vision_analysis(
        self,
        task: str,
        media: list[str],
        origin_channel: str = "web",
        origin_chat_id: str = "direct",
        model: str | None = None,
        timeout: float = 120.0,
    ) -> str | None:
        """
        同步运行 vision 子 agent 进行图片分析，使用子 agent 配置（模板 model、backend、system_prompt）。
        供主 loop 在需要图片分析时调用，避免 inline recognition 固定单一解读方式。

        Args:
            model: 可选，外部指定的模型 ID（优先级最高）。
            timeout: 主线程等待结果的最大秒数。

        Returns:
            图片分析结果文本，失败时返回 None。
        """
        if not media:
            return None
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}
        origin_key = f"{origin_channel}:{origin_chat_id}"
        task_id = f"vision-inline-{uuid.uuid4().hex[:8]}"

        # 优先级：外部指定 model > vision 模板 model > subagent 默认 model
        effective_model = model or self.model
        if not model and self._agent_template_manager:
            tm = self._agent_template_manager.get_model_for_template("vision")
            if tm:
                effective_model = tm
                logger.info(f"[run_vision_analysis] Using vision template model: {effective_model}")
        if model:
            logger.info(f"[run_vision_analysis] Using explicit vision model: {effective_model}")

        backend = self._backend_resolver.resolve("vision", "native", media=media, model=effective_model)
        # 若 backend 为 native 但 model 非视觉模型，尝试用 dashscope_vision 兜底（需有 API key）
        if backend == "native" and "dashscope_vision" in self._backend_registry.list_available():
            def _is_vision(m: str) -> bool:
                return m and any(k in (m or "").lower() for k in ("vision", "vl", "qwen-vl", "gpt-4v", "gpt-4o"))
            if not _is_vision(effective_model):
                import os
                has_key = bool(os.environ.get("DASHSCOPE_API_KEY"))
                if not has_key:
                    try:
                        from nanobot.config.loader import load_config
                        has_key = bool((load_config().providers.dashscope.api_key or "").strip())
                    except Exception:
                        pass
                if has_key:
                    backend = "dashscope_vision"
                    effective_model = "qwen-vl-plus"
                    logger.info("[run_vision_analysis] Original model doesn't support vision, fallback to dashscope_vision (qwen-vl-plus)")
        logger.info(f"[run_vision_analysis] task_id={task_id}, backend={backend}, model={effective_model}")

        # 在独立线程中运行，避免被主请求取消/超时所中断
        loop = asyncio.get_running_loop()
        future: concurrent.futures.Future[str | None] = concurrent.futures.Future()
        self._running_tasks[task_id] = (origin_key, None)

        def _run_in_thread():
            sub_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(sub_loop)
            try:
                runner = self._backend_registry.get(backend)
                if runner and backend != "native":
                    sub_loop.run_until_complete(
                        runner(
                            task_id, task, task[:50], origin,
                            template="vision", batch_id=None, subagent_manager=self,
                            media=media, model=effective_model,
                        )
                    )
                else:
                    sub_loop.run_until_complete(
                        self._run_subagent(
                            task_id, task, task[:50], origin,
                            template="vision", media=media, backend="native",
                        )
                    )
                if self.sessions:
                    session = self.sessions.get_or_create(origin_key)
                    result = session.subagent_results.get(task_id, {}).get("result")
                    future.set_result(result)
                else:
                    future.set_result(None)
            except Exception as e:
                logger.error(f"[run_vision_analysis] Thread failed: {e}")
                future.set_exception(e)
            finally:
                self._running_tasks.pop(task_id, None)
                sub_loop.close()

        threading.Thread(target=_run_in_thread, daemon=True).start()

        try:
            return await asyncio.wait_for(
                asyncio.wrap_future(future, loop=loop),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[run_vision_analysis] Timeout after {timeout}s")
            self._running_tasks.pop(task_id, None)
            return None
        except asyncio.CancelledError:
            logger.info(f"[run_vision_analysis] Cancelled by parent")
            self._running_tasks.pop(task_id, None)
            raise

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

        resolved_backend = self._backend_resolver.resolve(template, backend, media=media)
        logger.info(f"Subagent [{task_id}] backend resolved: template={template}, requested={backend}, actual={resolved_backend}")

        # 创建子 Agent 执行节点
        subagent_node_id = None
        if self._chain_monitor.get_current_chain():
            try:
                subagent_node = self._chain_monitor.get_current_chain().create_node(
                    node_type='subagent',
                    name=template,
                    parent_node_id=None,  # 可以从调用栈获取父节点
                    arguments={'task': task, 'label': label, 'backend': resolved_backend}
                )
                subagent_node_id = subagent_node.node_id
                self._subagent_nodes[task_id] = subagent_node_id
                logger.info(f"[ExecutionChain] Created subagent node: {subagent_node_id}, template: {template}")
            except Exception as e:
                logger.warning(f"[ExecutionChain] Failed to create subagent node: {e}")
        logger.info(f"[SubagentProgress] Creating background task for subagent, origin_channel={origin_channel}, origin_chat_id={origin_chat_id}")

        if batch_id:
            with self._batch_lock:
                self._batch_tasks.setdefault(batch_id, set()).add(task_id)
            logger.info(f"[SubagentProgress] Added task {task_id} to batch {batch_id}")

        logger.info(f"[SubagentProgress] SubagentManager id: {id(self)}, _running_tasks before: {list(self._running_tasks.keys())}")

        # Capture parent span for tracing before starting task
        parent_span_id = None
        try:
            from nanobot.tracing.context import get_current_span_id
            parent_span_id = get_current_span_id()
        except Exception:
            pass  # Tracing not available, proceed without parent span

        origin_key = f"{origin_channel}:{origin_chat_id}"
        bg_task = asyncio.create_task(
            self._run_subagent_task(
                task_id=task_id,
                task=task,
                label=display_label,
                origin=origin,
                template=template,
                session_id=session_id,
                enable_memory=enable_memory,
                media=media,
                backend=resolved_backend,
                batch_id=batch_id,
                parent_span_id=parent_span_id,
            )
        )
        self._running_tasks[task_id] = (origin_key, bg_task)
        self._session_tasks.setdefault(origin_key, set()).add(task_id)

        # Yield control so the background task gets a chance to start immediately.
        # Without this, the caller's synchronous code may starve the new task.
        await asyncio.sleep(0)

        def _log_task_exception(t: asyncio.Task) -> None:
            exc = t.exception()
            if exc is not None:
                logger.error(f"[SubagentProgress] Task {task_id} raised unhandled exception: {exc}", exc_info=exc)
        bg_task.add_done_callback(_log_task_exception)

        logger.info(f"[SubagentProgress] SubagentManager id: {id(self)}, _running_tasks after: {list(self._running_tasks.keys())}")

        # 记录子Agent spawn次数
        if self._status_service:
            try:
                self._status_service.increment_subagent_spawn()
            except Exception:
                pass

        if not session_id or not self.sessions or not self.sessions.get(session_id):
            logger.info(f"Spawned subagent [{task_id}]: {display_label}")
            # 返回消息告诉主 agent 立即返回给用户，不要等待
            return f"✅ 后台任务已启动 (id: {task_id})：{display_label}。任务完成后将自动通知你，请稍候..."
        else:
            return f"继续子对话会话 {task_id}。任务完成后将自动通知你，请稍候..."

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

        timeout_sec = self._claude_code_manager.default_timeout

        # 优先使用系统配置（Web UI）中的 permission_mode，否则使用初始化时的配置
        perm_mode = self._claude_code_permission_mode
        if self._status_service:
            try:
                cc = self._status_service.get_concurrency_config()
                perm_mode = cc.get("claude_code_permission_mode") or perm_mode
            except Exception:
                pass

        async def _run_and_maybe_wait_late() -> dict[str, Any]:
            """在独立线程的事件循环中运行 run_task，避免 anyio / claude-agent-sdk 与主事件 loop 冲突。"""
            loop = asyncio.get_running_loop()
            future = loop.create_future()

            def _thread_target() -> None:
                # 清除 CLAUDECODE，防止 Claude Code CLI 检测到"嵌套会话"而拒绝启动
                #（"cannot be launched inside another Claude Code session"）
                os.environ.pop("CLAUDECODE", None)
                try:
                    result = asyncio.run(
                        self._claude_code_manager.run_task(
                            prompt=task,
                            workdir=None,
                            permission_mode=perm_mode,
                            enable_subagents=True,
                            timeout=timeout_sec * 2,
                            progress_callback=_progress_callback,
                        )
                    )
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(future.set_result, result)
                    else:
                        logger.warning(f"[SubagentProgress] Claude Code thread finished but loop closed for task {task_id}")
                except Exception as exc:
                    logger.error(f"[SubagentProgress] Claude Code thread error for task {task_id}: {exc}")
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(future.set_exception, exc)
                    else:
                        logger.warning(f"[SubagentProgress] Cannot propagate exception, loop closed for task {task_id}")

            thread = threading.Thread(target=_thread_target, daemon=True)
            thread.start()
            return await future

        async def _deliver_late_result(inner: asyncio.Task) -> None:
            """等待 shielded 任务完成，将最终结果推送到 bus，并处理 batch 聚合与非 web 渠道的 announce。"""
            try:
                result = await inner
                status = result.get("status", "unknown")
                output = result.get("output", "")
                logger.info(f"[SubagentProgress] Late result for task_id: {task_id}, status={status}")
                bus.push(origin_key, {
                    "type": "subagent_end",
                    "task_id": task_id,
                    "label": label,
                    "status": "ok" if status == "done" else "timeout" if status == "timeout" else "error",
                    "summary": (output or f"status={status}")[:300],
                    "result": output,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                if self.sessions:
                    main_session = self.sessions.get_or_create(f"{origin['channel']}:{origin['chat_id']}")
                    if main_session:
                        main_session.subagent_results[task_id] = {
                            "label": label,
                            "task": task,
                            "result": output or f"status={status}",
                            "status": "ok" if status == "done" else "timeout" if status == "timeout" else "error",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            **({"batch_id": batch_id} if batch_id else {}),
                        }
                        self.sessions.save(main_session)
                # Web 渠道：vision/voice 走 announce 让主 agent 注入综合；其余推 summary
                # 非 web：announce 给主 agent
                if template in ("vision", "voice") and origin.get("channel") == "web":
                    await self._announce_result(
                        task_id, label, task,
                        output or f"status={status}",
                        origin,
                        "ok" if status == "done" else "timeout" if status == "timeout" else "error",
                        template=template,
                    )
                elif origin.get("channel") == "web":
                    await self._generate_and_push_summary(task_id, label, task, output, origin, from_late_result=True)
                else:
                    await self._announce_result(
                        task_id, label, task,
                        output or f"status={status}",
                        origin,
                        "ok" if status == "done" else "timeout" if status == "timeout" else "error",
                        template=template,
                    )
                # batch 聚合：若为 batch 中最后一个完成，执行汇总
                is_last = self._is_last_in_batch(batch_id, task_id) if batch_id else True
                if is_last and batch_id:
                    with self._batch_lock:
                        batch_task_ids = self._batch_tasks.pop(batch_id, set())
                    if len(batch_task_ids) > 1:
                        await self._deliver_batch_complete(batch_id, batch_task_ids, origin)
            except asyncio.CancelledError:
                logger.debug(f"[SubagentProgress] Late result delivery cancelled for task_id: {task_id}")
            except Exception as e:
                logger.warning(f"[SubagentProgress] Late result delivery failed: {e}")

        try:
            try:
                logger.info(f"[SubagentProgress] Calling Claude Code Manager run_task for task_id: {task_id}")
                inner_task = asyncio.ensure_future(_run_and_maybe_wait_late())
                result = await asyncio.wait_for(asyncio.shield(inner_task), timeout=float(timeout_sec))
                logger.info(f"[SubagentProgress] Claude Code Manager run_task completed for task_id: {task_id}")
            except asyncio.TimeoutError:
                # 超时后任务受 shield 保护继续运行，等待完成后推送最终结果
                logger.info(f"[SubagentProgress] Task {task_id} timed out, waiting for late result...")
                partial = "⏳ 任务执行超时，正在后台继续运行，完成后将自动通知。"
                bus.push(origin_key, {
                    "type": "subagent_end",
                    "task_id": task_id,
                    "label": label,
                    "status": "timeout",
                    "summary": partial[:300],
                    "result": partial,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                if self.sessions:
                    main_session = self.sessions.get_or_create(f"{origin['channel']}:{origin['chat_id']}")
                    if main_session:
                        main_session.subagent_results[task_id] = {
                            "label": label,
                            "task": task,
                            "result": partial,
                            "status": "timeout",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            **({"batch_id": batch_id} if batch_id else {}),
                        }
                        self.sessions.save(main_session)
                # 超时分支不生成 summary，由 _deliver_late_result 在任务完成后统一处理
                await _deliver_late_result(inner_task)
                return

            status = result.get("status", "unknown")
            output = result.get("output", "")

            end_status_bus = "ok" if status == "done" else "timeout" if status == "timeout" else "error"
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": end_status_bus,
                "summary": (output or f"status={status}")[:300],
                "result": output,  # 完整结果供前端使用（含超时时的部分输出）
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
                        store_result = (
                            output
                            if (output and output.strip())
                            else f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒），任务可能仍在后台运行。"
                        )
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
                        # vision/voice 模板：web 渠道也走 _announce_result，
                        # 让主 agent 注入并综合（避免直接推前端造成割裂感）
                        if template in ("vision", "voice") and origin.get("channel") == "web":
                            await self._announce_result(task_id, label, task, output, origin, "ok", template=template)
                        elif status == "done":
                            if origin.get("channel") == "web":
                                await self._generate_and_push_summary(task_id, label, task, output, origin)
                            else:
                                await self._announce_result(task_id, label, task, output, origin, "ok", template=template)
                        elif status == "timeout":
                            if origin.get("channel") == "web":
                                await self._generate_and_push_summary(
                                    task_id, label, task,
                                    output or f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒）",
                                    origin,
                                )
                            else:
                                await self._announce_result(
                                    task_id, label, task,
                                    output or f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒），任务可能仍在后台运行。",
                                    origin, "timeout", template=template
                                )
                        elif origin.get("channel") != "web":
                            await self._announce_result(task_id, label, task, output or f"status={status}", origin, "error", template=template)
                else:
                    if template in ("vision", "voice") and origin.get("channel") == "web":
                        await self._announce_result(task_id, label, task, output, origin, "ok", template=template)
                    elif status == "done":
                        if origin.get("channel") == "web":
                            await self._generate_and_push_summary(task_id, label, task, output, origin)
                        else:
                            await self._announce_result(task_id, label, task, output, origin, "ok", template=template)
                    elif status == "timeout":
                        if origin.get("channel") == "web":
                            await self._generate_and_push_summary(
                                task_id, label, task,
                                output or f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒）",
                                origin,
                            )
                        else:
                            await self._announce_result(
                                task_id, label, task,
                                output or f"⏳ 任务执行超时（{self._claude_code_manager.default_timeout}秒），任务可能仍在后台运行。",
                                origin, "timeout", template=template
                            )
                    elif origin.get("channel") != "web":
                        await self._announce_result(task_id, label, task, output or f"status={status}", origin, "error", template=template)
        except Exception as exc:
            logger.error(f"Subagent [{task_id}] Claude Code backend failed: {exc}")
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "error",
                "summary": str(exc)[:300],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await self._announce_result(task_id, label, task, f"Error: {str(exc)}", origin, "error", template=template)

    async def _run_via_voice(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        template: str = "voice",
        batch_id: str | None = None,
        media: list[str] | None = None,
    ) -> None:
        """Voice backend: 直接调用 voice_transcribe 工具，绕过 LLM。"""
        origin_key = f"{origin['channel']}:{origin['chat_id']}"
        bus = SubagentProgressBus.get()

        bus.push(origin_key, {
            "type": "subagent_start",
            "task_id": task_id,
            "label": label,
            "backend": "voice",
            "task": task[:120],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        try:
            if origin_key in self._session_cancelled:
                self._session_cancelled.discard(origin_key)
                final_result = "任务已被用户取消。"
                end_status = "cancelled"
            else:
                audio_paths = []
                for path in media or []:
                    p = Path(path)
                    if not p.is_file():
                        continue
                    mime, _ = mimetypes.guess_type(path)
                    ext = p.suffix.lower()
                    is_audio = (mime and mime.startswith("audio/")) or ext in (".mp3", ".wav", ".ogg", ".m4a", ".opus", ".webm", ".aac")
                    if is_audio:
                        audio_paths.append(str(p.resolve()))
                if audio_paths:
                    tools = self._create_tools_for_template(template)
                    result = await tools.execute("voice_transcribe", {"file_path": audio_paths[0]})
                    if result and not str(result).strip().startswith("Error:"):
                        final_result = str(result).strip()
                    else:
                        final_result = str(result).strip() if result else "语音转写失败，请检查 DashScope API 配置。"
                else:
                    final_result = "未找到有效的音频文件。"
                end_status = "ok"

            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": end_status,
                "summary": final_result[:300],
                "result": final_result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if self.sessions:
                main_session = self.sessions.get_or_create(origin_key)
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
            is_last = self._is_last_in_batch(batch_id, task_id) if batch_id else True
            if is_last:
                if batch_id:
                    with self._batch_lock:
                        batch_task_ids = self._batch_tasks.pop(batch_id, set())
                    if len(batch_task_ids) > 1:
                        await self._deliver_batch_complete(batch_id, batch_task_ids, origin)
                    else:
                        # voice 模板：已在上面通过 _inject_voice_as_user_message 注入转写文本，
                        # 不再走 announce，避免触发第二次 LLM 处理导致重复回答
                        if template == "voice" and origin.get("channel") != "web":
                            logger.info(f"Subagent [{task_id}] voice result injected via _inject_voice_as_user_message, skipping announce")
                        # vision 模板：web 渠道走 _announce_result
                        elif template in ("vision", "voice") and origin.get("channel") == "web":
                            await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
                        elif origin.get("channel") == "web":
                            await self._generate_and_push_summary(task_id, label, task, final_result, origin)
                        else:
                            await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
                else:
                    # voice 模板：已在上面通过 _inject_voice_as_user_message 注入转写文本，
                    # 不再走 announce，避免触发第二次 LLM 处理导致重复回答
                    if template == "voice" and origin.get("channel") != "web":
                        logger.info(f"Subagent [{task_id}] voice result injected via _inject_voice_as_user_message, skipping announce")
                    # vision 模板：web 渠道走 _announce_result
                    elif template in ("vision", "voice") and origin.get("channel") == "web":
                        await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
                    elif origin.get("channel") == "web":
                        await self._generate_and_push_summary(task_id, label, task, final_result, origin)
                    else:
                        await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error(f"Subagent [{task_id}] Voice backend failed: {e}")
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "error",
                "summary": error_msg[:300],
                "result": error_msg,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if origin.get("channel") != "web":
                await self._announce_result(task_id, label, task, error_msg, origin, "error", template=template)

    async def _run_via_dashscope_vision(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        template: str = "",
        batch_id: str | None = None,
        media: list[str] | None = None,
        model: str = "",
    ) -> None:
        """DashScope vision backend: 直接调用 DashScope API，绕过 LiteLLM。"""
        origin_key = f"{origin['channel']}:{origin['chat_id']}"
        bus = SubagentProgressBus.get()

        bus.push(origin_key, {
            "type": "subagent_start",
            "task_id": task_id,
            "label": label,
            "backend": "dashscope_vision",
            "task": task[:120],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        try:
            if origin_key in self._session_cancelled:
                self._session_cancelled.discard(origin_key)
                final_result = "任务已被用户取消。"
                end_status = "cancelled"
            else:
                from nanobot.agent.prompts import build_system_prompt
                if self._agent_template_manager:
                    system_prompt = self._agent_template_manager.build_system_prompt(template, task, str(self.workspace))
                    if not system_prompt:
                        system_prompt = build_system_prompt(template, task, str(self.workspace))
                else:
                    system_prompt = build_system_prompt(template, task, str(self.workspace))
                messages: list[dict[str, Any]] = [
                    {"role": "system", "content": system_prompt},
                    self._build_user_message_with_media(task, media),
                ]
                ds_task = asyncio.create_task(self._dashscope_direct_call(messages, model))
                _DS_CALL_TIMEOUT = 60
                _CANCEL_CHECK_INTERVAL = 2.0
                cancelled_during_ds = False
                loop_start_ds = time.monotonic()
                while not ds_task.done():
                    elapsed = time.monotonic() - loop_start_ds
                    remaining = _DS_CALL_TIMEOUT - elapsed
                    if remaining <= 0:
                        ds_task.cancel()
                        try:
                            await ds_task
                        except asyncio.CancelledError:
                            pass
                        break
                    wait_time = min(_CANCEL_CHECK_INTERVAL, remaining)
                    done, _ = await asyncio.wait(
                        [ds_task],
                        timeout=wait_time,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if ds_task in done:
                        break
                    if origin_key in self._session_cancelled:
                        self._session_cancelled.discard(origin_key)
                        ds_task.cancel()
                        try:
                            await ds_task
                        except asyncio.CancelledError:
                            pass
                        cancelled_during_ds = True
                        break
                if cancelled_during_ds:
                    final_result = "任务已被用户取消。"
                    end_status = "cancelled"
                else:
                    try:
                        direct_result = await ds_task
                    except asyncio.CancelledError:
                        direct_result = None
                    if direct_result:
                        final_result = direct_result
                        end_status = "ok"
                    else:
                        final_result = "DashScope 图片识别失败，请检查 API key 和模型配置。"
                        end_status = "error"

            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": end_status,
                "summary": final_result[:300],
                "result": final_result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if self.sessions:
                main_session = self.sessions.get_or_create(origin_key)
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
            is_last = self._is_last_in_batch(batch_id, task_id) if batch_id else True
            if is_last:
                if batch_id:
                    with self._batch_lock:
                        batch_task_ids = self._batch_tasks.pop(batch_id, set())
                    if len(batch_task_ids) > 1:
                        await self._deliver_batch_complete(batch_id, batch_task_ids, origin)
                    else:
                        # DashScope vision 是 vision 专用后端：web 渠道也走 _announce_result
                        if origin.get("channel") == "web":
                            await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
                        else:
                            await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
                else:
                    if origin.get("channel") == "web":
                        await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
                    else:
                        await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error(f"Subagent [{task_id}] DashScope vision backend failed: {e}")
            bus.push(origin_key, {
                "type": "subagent_end",
                "task_id": task_id,
                "label": label,
                "status": "error",
                "summary": error_msg[:300],
                "result": error_msg,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if origin.get("channel") != "web":
                await self._announce_result(task_id, label, task, error_msg, origin, "error", template=template)

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
        parent_span_id: str | None = None,
    ) -> None:
        """Execute the subagent task and announce the result."""

        # Create subagent span for tracing
        from nanobot.tracing.spans import span as _span
        async with _span(
            "subagent.spawn",
            parent_id=parent_span_id,
            attrs={
                "subagent_id": task_id,
                "subagent_label": label,
                "template": template,
                "backend": backend,
                "memory_enabled": enable_memory,
            }
        ) as subagent_span:
            subagent_span.mark_subagent_span(task_id, label)
            await self._run_subagent_impl(
                task_id, task, label, origin,
                template=template, session_id=session_id,
                enable_memory=enable_memory, media=media,
                backend=backend, batch_id=batch_id,
                subagent_span=subagent_span,
            )

    async def _run_subagent_impl(
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
        subagent_span=None,
    ) -> None:
        """Internal implementation of subagent execution (traced)."""
        logger.info(f"Subagent [{task_id}] starting task: {label} (template: {template}, backend: {backend}, memory: {enable_memory}), manager id: {id(self)}")

        # Guard: provider must be available for native backend
        if self.provider is None:
            logger.error(f"Subagent [{task_id}] aborted: no LLM provider available")
            raise RuntimeError("No LLM provider available for subagent execution")

        # Determine the effective model: template's model or fallback to self.model
        effective_model = self.model
        if self._agent_template_manager and template:
            template_model = self._agent_template_manager.get_model_for_template(template)
            if template_model:
                effective_model = template_model
                logger.info(f"Subagent [{task_id}] using template '{template}' model: {effective_model}")
            else:
                logger.info(f"Subagent [{task_id}] using default model: {effective_model}")

        # 二次解析：native 时根据 media+model 可能路由到 dashscope_vision
        effective_backend = backend
        if backend == "native":
            effective_backend = self._backend_resolver.resolve(
                template, backend, media=media, model=effective_model
            )

        # 使用 BackendRegistry 路由到对应的 backend
        runner = self._backend_registry.get(effective_backend)
        if runner is not None and effective_backend != "native":
            logger.info(f"[SubagentProgress] Routing to {effective_backend} backend for task_id: {task_id}")
            await runner(
                task_id, task, label, origin,
                template=template, batch_id=batch_id, subagent_manager=self,
                media=media, model=effective_model,
            )
            logger.info(f"[SubagentProgress] {effective_backend} backend completed for task_id: {task_id}")
            return

        # effective_backend == "native" 或 runner 为 None 时，使用内置 native 路径
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
            # Mark span as cancelled before returning — __aexit__ would end with status="ok"
            if subagent_span is not None:
                subagent_span.end(status="cancelled")
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
            tools = self._create_tools_for_template(template).copy()
            from nanobot.agent.tools.persist_self_improvement import PersistSelfImprovementTool

            tools.register(
                PersistSelfImprovementTool(
                    workspace=str(self.workspace),
                    agent_id=task_id if enable_memory else None,
                )
            )

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

            max_iterations = 8
            iteration = 0
            final_result: str | None = None
            cancelled_by_user = False
            subagent_start_ts = time.monotonic()
            _SUBAGENT_TOTAL_TIMEOUT = 300.0

            # 使用 config 工具统一加载并注入模型 API Key（DashScope 等多级回退）
            from nanobot.config.model_api_key import ensure_model_api_key
            sa_key, sa_base = ensure_model_api_key(effective_model, provider=self.provider)

            # 视觉模型通常不支持 function calling，有图片时不传 tools
            # 语音子 agent 仅有音频时需保留 exec 等工具，故仅在有图片时禁用
            use_tools = None if (media and self._media_has_images(media)) else tools.get_definitions()

            while iteration < max_iterations:
                iteration += 1

                # 总超时检查
                elapsed_total = time.monotonic() - subagent_start_ts
                if elapsed_total > _SUBAGENT_TOTAL_TIMEOUT:
                    logger.warning(f"Subagent [{task_id}] total timeout ({_SUBAGENT_TOTAL_TIMEOUT}s)")
                    final_result = f"任务执行超时（已超过 {_SUBAGENT_TOTAL_TIMEOUT} 秒）。"
                    break

                # 推送迭代进度，让前端有反馈
                bus.push(origin_key, {
                    "type": "subagent_progress",
                    "task_id": task_id,
                    "label": label,
                    "content": f"第 {iteration}/{max_iterations} 轮思考中...",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

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

                    voice_direct_result = None
                    for tool_call in response.tool_calls:
                        args_str = json.dumps(sanitize_args_for_log(tool_call.arguments))
                        logger.debug(f"Subagent [{task_id}] executing: {tool_call.name} with arguments: {args_str}")
                        result = await tools.execute(tool_call.name, tool_call.arguments)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": result,
                        })
                        # voice 模板：voice_transcribe 成功返回后直接使用工具结果，不再调用 LLM（避免 LLM 输出模板占位符而非实际转写文本）
                        if template == "voice" and tool_call.name == "voice_transcribe" and result and not str(result).strip().startswith("Error:"):
                            voice_direct_result = str(result).strip()
                            break
                    if voice_direct_result is not None:
                        final_result = voice_direct_result
                        break
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
            # End subagent span with correct status
            if subagent_span is not None:
                subagent_span.end(status=end_status)
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
                        # vision/voice 模板：web 渠道也走 _announce_result，
                        # 让主 agent 注入并综合（避免直接推前端造成割裂感）
                        if template in ("vision", "voice") and origin.get("channel") == "web":
                            await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
                        else:
                            await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
                else:
                    if template in ("vision", "voice") and origin.get("channel") == "web":
                        await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)
                    else:
                        await self._announce_result(task_id, label, task, final_result, origin, "ok", template=template)

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error(f"Subagent [{task_id}] failed: {e}")
            end_status = "error"
            # Mark subagent span as errored
            if subagent_span is not None:
                subagent_span.set_attr("error", str(e)[:200])
                # Do NOT call subagent_span.end() here — __aexit__ of the
                # async with _span(...) context manager already ended the span
                # with status="error" and set error_type/error_msg attrs.
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

        # 获取用户原始提问用于判断输出语言
        user_question = ""
        if self.sessions:
            main_session = self.sessions.get_or_create(origin_key)
            if main_session and main_session.messages:
                for m in reversed(main_session.messages):
                    if m.get("role") == "user":
                        user_question = (m.get("content") or "").strip()
                        if user_question and user_question not in ("[图片]", "[语音]", "[文件]", "[空消息]", ""):
                            break
                        user_question = ""

        try:
            summary_prompt = get_batch_user_intro()
            if user_question:
                summary_prompt += f"用户原始提问：\n{user_question[:500]}\n\n---\n\n"
            summary_prompt += f"任务结果：\n{combined_preview}"
            messages = [
                {"role": "system", "content": get_batch_system_prompt()},
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

        # 优化后的 prompt：强调保持用户原始意图和输出语言一致性
        content += """

请基于用户原始意图，综合以上所有子任务结果进行回复：
1. 根据每个任务的「任务描述」来理解用户想要什么
2. 按「总体结论 → 各任务要点」结构，用 2-4 句话向用户汇报
3. 如果用户要求 mermaid 代码，就输出代码；如果要求描述，就提供描述
4. 不要逐条复述，要提炼关键结论
5. 不要提及 subagent、task_id 等技术细节
6. **语言一致性**：输出语言必须与「任务描述」的语言一致（任务描述是中文就输出中文，是英文就输出英文）"""

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=content,
        )
        self._publish_inbound_safe(msg)
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
        from_late_result: bool = False,
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

        # 获取用户原始提问用于判断输出语言
        user_question = ""
        if self.sessions:
            main_session = self.sessions.get_or_create(origin_key)
            if main_session and main_session.messages:
                for m in reversed(main_session.messages):
                    if m.get("role") == "user":
                        user_question = (m.get("content") or "").strip()
                        if user_question and user_question not in ("[图片]", "[语音]", "[文件]", "[空消息]", ""):
                            break
                        user_question = ""

        try:
            # 传入完整任务指令和结果，供 LLM 准确总结（限制 result 长度以防 token 溢出，保留足够上下文）
            result_for_prompt = result if len(result) <= 12000 else result[:12000] + "\n\n...(结果过长已截断，完整结果见下方)"
            summary_prompt = (
                f"任务名称：{label}\n\n"
                f"任务描述（子 agent 收到的完整指令）：\n{task}\n\n"
                f"执行结果：\n{result_for_prompt}"
            )
            if user_question:
                summary_prompt = f"用户原始提问：\n{user_question[:500]}\n\n---\n\n" + summary_prompt

            messages = [
                {"role": "system", "content": get_single_task_system_prompt()},
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
                task_id, label, origin_key, message_id, full_content, origin, bus,
                clear_buffer=not from_late_result,
            )

        except asyncio.TimeoutError:
            logger.warning(f"[SubagentSummary] LLM summary timed out for task {task_id}, using fallback")
            await self._push_summary_to_session_and_bus(
                task_id, label, origin_key, message_id, fallback_full, origin, bus,
                clear_buffer=not from_late_result,
            )
        except Exception as e:
            logger.warning(f"[SubagentSummary] Failed to generate summary for task {task_id}: {e}, using fallback")
            await self._push_summary_to_session_and_bus(
                task_id, label, origin_key, message_id, fallback_full, origin, bus,
                clear_buffer=not from_late_result,
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
        clear_buffer: bool = True,
    ) -> None:
        """将 summary 写入 session 并推送到 bus。clear_buffer=False 时保留缓冲供重连 replay（late result 场景）。"""
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

        if clear_buffer:
            # 裁剪为仅保留最后 5 个事件（subagent_end/summary/stream_done 等），
            # 供重连 replay，同时避免大量 progress 事件导致页面卡顿
            bus.trim_buffer(origin_key, keep_last=5)

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
        self._publish_inbound_safe(msg)
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
        # 内联 vision 分析（run_vision_analysis 调用）：结果由调用方直接使用，不需要 announce。
        # 若触发 announce 会导致主 agent 在原始回复之后再次生成一次相同回复（重复消息 Bug）。
        if task_id.startswith("vision-inline-"):
            logger.debug(f"[SubagentAnnounce] Skipping announce for inline vision task: {task_id}")
            return

        # voice 模板在非 web 渠道：转写结果作为用户指令注入，主 agent 直接执行
        if template == "voice" and origin.get("channel") != "web" and status == "ok":
            await self._inject_voice_as_user_message(origin, result)
            return

        # 检查结果是否已被主 agent 循环注入（通过检查 session 消息历史）。
        # 若已注入，说明主 agent 已经综合过此结果，不需要再次 announce（避免重复消息）。
        if self.sessions:
            origin_key = f"{origin['channel']}:{origin['chat_id']}"
            session = self.sessions.get(origin_key)
            if session and session.messages:
                for m in reversed(session.messages[-10:]):  # 只检查最近 10 条
                    content = m.get("content") or ""
                    if m.get("role") == "user" and f"### {label}" in content and content[:50] == "[子 Agent 已完成]":
                        logger.info(f"[SubagentAnnounce] Result for {task_id} already injected, skipping announce")
                        return

        status_text = "completed successfully" if status == "ok" else "failed"

        # 优化后的 announce content：强调保持用户原始意图
        result_preview = (result or "")[:800] + ("..." if len(result or "") > 800 else "")

        announce_content = f"""[子 agent 完成] {label}

**任务描述（用户原始意图）**：{task[:300]}{'...' if len(task) > 300 else ''}

**执行结果**：
{result_preview}

请基于用户原始意图进行回复：
- 如果用户要求 mermaid 代码，就输出代码块
- 如果用户要求描述内容，就提供描述
- 保持简洁（1-3 句），不要提及 subagent、task_id 等技术细节"""

        # Inject as system message to trigger main agent
        logger.info(f"[SubagentAnnounce] === ANNOUNCING RESULT ===")
        logger.info(f"[SubagentAnnounce] task_id: {task_id}, label: {label}, status: {status}, template: {template}")
        logger.info(f"[SubagentAnnounce] result_preview: {result[:100]}..." if len(result) > 100 else f"[SubagentAnnounce] result_preview: {result}")
        logger.info(f"[SubagentAnnounce] publishing to channel={origin['channel']}, chat_id={origin['chat_id']}")

        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )

        self._publish_inbound_safe(msg)
        logger.info(f"[SubagentAnnounce] Published to bus, origin={origin['channel']}:{origin['chat_id']}")

    def _create_tools_for_template(self, template: str) -> ToolRegistry:
        """Create and register tools based on template. Uses caching to avoid recreating tools for each spawn."""
        # 检查缓存
        if template in self._tools_cache:
            return self._tools_cache[template]

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

        # Fix: 从主 Agent 的工具注册表中复制 MCP 工具，让子 agent 也能调用 MCP
        if self._parent_tools_registry is not None:
            mcp_tools_copied = 0
            for name, tool in self._parent_tools_registry._tools.items():
                if name.startswith("mcp_") and not tools.has(name):
                    tools.register(tool)
                    mcp_tools_copied += 1
            if mcp_tools_copied:
                logger.info(f"[SubagentTools] Copied {mcp_tools_copied} MCP tools from parent registry for template '{template}'")

        # 缓存工具实例
        self._tools_cache[template] = tools
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
                logger.info(f"DashScope subagent image call completed (model: {model_name})")
                return content.strip()
            return None
        except Exception as e:
            logger.warning(f"DashScope subagent image call failed (model: {model_name}): {e}")
            return None

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
