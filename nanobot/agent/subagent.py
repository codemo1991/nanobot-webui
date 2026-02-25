"""Subagent manager for background task execution."""

import asyncio
import base64
import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

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
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

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
            # 未达到上限，直接获取信号量（非阻塞）
            self._subagent_semaphore.acquire()

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

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin, template, session_id, enable_memory, media)
        )
        self._running_tasks[task_id] = bg_task

        # 任务完成后释放信号量
        def release_semaphore(t):
            self._subagent_semaphore.release()
            self._running_tasks.pop(task_id, None)

        bg_task.add_done_callback(release_semaphore)

        if not session_id or not self.sessions or not self.sessions.get(session_id):
            logger.info(f"Spawned subagent [{task_id}]: {display_label}")
            return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."
        else:
            return f"Continuing subagent session [{task_id}]. I'll notify you when it completes."

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
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info(f"Subagent [{task_id}] starting task: {label} (template: {template}, memory: {enable_memory})")

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
            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error(f"Subagent [{task_id}] failed: {e}")
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
