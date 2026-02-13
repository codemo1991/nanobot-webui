"""Subagent manager for background task execution."""

import asyncio
import json
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

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        template: str = "minimal",
        session_id: str | None = None,
        enable_memory: bool = False,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
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

        Returns:
            Status message indicating the subagent was started.
        """
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
            self._run_subagent(task_id, task, display_label, origin, template, session_id, enable_memory)
        )
        self._running_tasks[task_id] = bg_task

        bg_task.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))

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
                    messages.append({"role": "user", "content": task})
            else:
                messages.append({"role": "user", "content": task})

            max_iterations = 15
            iteration = 0
            final_result: str | None = None

            while iteration < max_iterations:
                iteration += 1

                response = await self.provider.chat(
                    messages=messages,
                    tools=tools.get_definitions(),
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

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
