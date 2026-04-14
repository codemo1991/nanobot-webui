"""Skill reviewer: background agent for silent skill review after conversations."""

import asyncio
import json
from loguru import logger
from pathlib import Path
from typing import Any

from nanobot.agent.tools.skill_manager import SkillManagerTool

# ---- Prompts ----

_SKILL_REVIEW_SYSTEM_PROMPT = """You are a skill reviewer. Your job is to analyze the conversation above and decide whether any reusable skills should be created, updated, or deleted.

Design principles (follow Hermes-Agent style):
- Prefer MANY small, focused skills over one giant skill. Each skill should capture ONE specific type of task or workflow.
- Examples of good granularity: 'dingtalk-meeting-management', 'github-pr-workflow', 'colleague-directory-lookup'. Do NOT combine unrelated workflows into a single skill.
- Use category subdirectories when it helps organization: e.g. 'productivity/dingtalk-meeting-management', 'devops/docker-management'.
- A skill can include supporting files (references, templates, scripts, assets) via write_file if the workflow needs them.

Focus on:
- Was a non-trivial approach used to complete a task that required trial and error?
- Did the agent discover a better method mid-task and change course?
- Was there a reusable workflow worth capturing for future similar tasks?
- Is any existing skill now outdated or wrong based on what was learned?

If a relevant skill already exists, UPDATE it (use action='patch' or action='edit').
Otherwise, CREATE a new skill if the approach is reusable (action='create').
If a skill is no longer useful, DELETE it (action='delete').

When creating or editing a skill, the content MUST include a YAML frontmatter with:
- name: the skill name (lowercase letters, numbers, hyphens, dots, underscores; max 64 chars per segment). Supports category/skill-name paths.
- description: a concise summary (max 1024 characters)

Use the skill_manage tool for all actions. Only call it if there is something genuinely worth saving or updating.
If nothing is worth saving, simply respond "Nothing to save." without calling any tool."""

_SKILL_REVIEW_USER_PROMPT = "Please review the conversation above for reusable skills."

# ---- Tool definition ----

_SKILL_MANAGE_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "skill_manage",
        "description": (
            "创建、编辑、删除工作区内的 Skill（存储在 workspace/skills/）。\n"
            "设计原则：优先生成多个小而精的 skill，每个只负责一类具体任务；支持 category/skill-name 分类目录。\n"
            "动作：create（新建）/ edit（全文替换）/ delete（删除）/ patch（局部替换）/ write_file（写辅助文件）/ remove_file（删辅助文件）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "edit", "delete", "patch", "write_file", "remove_file"],
                    "description": "操作类型"
                },
                "name": {
                    "type": "string",
                    "description": "Skill 名称（支持 category/skill-name 目录形式；每段只能包含小写、字母数字、-_. ；最大64字符）"
                },
                "content": {
                    "type": "string",
                    "description": "SKILL.md 内容（create/edit 必须包含 YAML frontmatter；description 不超过1024字符）"
                },
                "file_path": {
                    "type": "string",
                    "description": "辅助文件路径（write_file/remove_file 时必填，如 'references/example.md'）"
                },
                "file_content": {
                    "type": "string",
                    "description": "辅助文件内容（write_file 时必填）"
                },
                "old_string": {
                    "type": "string",
                    "description": "patch 的目标字符串"
                },
                "new_string": {
                    "type": "string",
                    "description": "patch 的替换字符串"
                },
            },
            "required": ["action", "name"]
        },
    },
}


async def run_skill_review(
    provider,
    model: str,
    workspace: Path,
    context: Any,
    messages: list[dict[str, Any]],
) -> None:
    """Run skill review as a background coroutine on the main event loop.

    Args:
        provider: LLM provider instance (must have .chat() method)
        model: Model name string
        workspace: Workspace Path
        context: ContextBuilder instance (for refresh_cache after write)
        messages: Conversation history snapshot (list of message dicts)
    """
    logger.debug("[SkillReviewer] Background review started, messages count={}", len(messages))
    reviewer = None
    try:
        reviewer = _SkillReviewer(
            provider=provider,
            model=model,
            workspace=workspace,
            context=context,
            messages=messages,
        )
        await reviewer.run()
    except Exception as e:
        logger.warning("[SkillReviewer] Review crashed: {}", e)
    finally:
        # NOTE: Do NOT close the provider here — it is typically a shared instance
        # from the router/main loop, and closing it would break all future LLM calls.
        pass


class _SkillReviewer:
    """Lightweight background reviewer that calls LLM with skill_manage tool."""

    # Limits for balancing performance vs accuracy
    _MAX_TOOL_STEPS_PER_MSG = 8
    _MAX_TOOL_ARGS_LEN = 200
    _MAX_TOOL_RESULT_LEN = 400
    _MAX_ASSISTANT_MSGS_TO_ENRICH = 10

    def __init__(
        self,
        provider,
        model: str,
        workspace: Path,
        context: Any,
        messages: list[dict[str, Any]],
    ):
        self.provider = provider
        self.model = model
        self.workspace = workspace
        self.context = context
        self.messages = messages
        self._skill_tool = SkillManagerTool(workspace=workspace, context=None)

    @classmethod
    def _format_tool_steps(cls, tool_steps: list[dict[str, Any]]) -> str:
        """Format tool steps into a concise, readable summary."""
        lines: list[str] = []
        total = len(tool_steps)
        for idx, step in enumerate(tool_steps[:cls._MAX_TOOL_STEPS_PER_MSG], start=1):
            name = step.get("name", "unknown")
            args = step.get("arguments", {})
            try:
                args_str = json.dumps(args, ensure_ascii=False)
            except Exception:
                args_str = str(args)
            if len(args_str) > cls._MAX_TOOL_ARGS_LEN:
                args_str = args_str[: cls._MAX_TOOL_ARGS_LEN] + "..."
            result = str(step.get("result", ""))
            if len(result) > cls._MAX_TOOL_RESULT_LEN:
                result = result[: cls._MAX_TOOL_RESULT_LEN] + "..."
            lines.append(f"{idx}. {name}(args: {args_str}) → result: {result}")
        omitted = total - cls._MAX_TOOL_STEPS_PER_MSG
        if omitted > 0:
            lines.append(f"({omitted} more tool call(s) omitted)")
        return "\n".join(lines)

    @classmethod
    def _enrich_messages(cls, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Inject formatted tool-step summaries into assistant messages for review."""
        enriched: list[dict[str, Any]] = []
        enriched_count = 0
        for msg in messages:
            if not isinstance(msg, dict):
                enriched.append(msg)
                continue
            role = msg.get("role")
            tool_steps = msg.get("tool_steps")
            if role == "assistant" and isinstance(tool_steps, list) and tool_steps:
                if enriched_count < cls._MAX_ASSISTANT_MSGS_TO_ENRICH:
                    summary = cls._format_tool_steps(tool_steps)
                    content = str(msg.get("content", ""))
                    # Append tool summary in a clear separator block
                    new_content = (
                        f"{content}\n\n"
                        f"--- Tool Execution Summary ---\n"
                        f"{summary}\n"
                        f"--- End Tool Summary ---"
                    )
                    new_msg = {**msg, "content": new_content}
                    enriched.append(new_msg)
                    enriched_count += 1
                    continue
            enriched.append(msg)
        return enriched

    def _build_review_messages(self) -> list[dict[str, Any]]:
        """Build messages for the review LLM call."""
        enriched = self._enrich_messages(self.messages)
        return [
            {"role": "system", "content": _SKILL_REVIEW_SYSTEM_PROMPT},
            *enriched,
            {"role": "user", "content": _SKILL_REVIEW_USER_PROMPT},
        ]

    async def run(self) -> None:
        """Run one review cycle: call LLM, execute skill_manage if needed."""
        messages = self._build_review_messages()
        max_iterations = 3

        for _ in range(max_iterations):
            # Call LLM
            try:
                response = await self.provider.chat(
                    model=self.model,
                    messages=messages,
                    tools=[_SKILL_MANAGE_TOOL_DEF],
                )
            except Exception as e:
                logger.warning(f"[SkillReviewer] LLM call failed: {e}")
                break

            # Check for tool calls
            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                # No tool calls — check if model said "Nothing to save"
                content = getattr(response, "content", "") or ""
                logger.info(f"[SkillReviewer] Review done, no skill action: {content[:100]}")
                break

            skill_calls = [tc for tc in tool_calls if self._get_tc_name(tc) == "skill_manage"]
            if not skill_calls:
                # Model called other tools, ignore
                break

            # Execute skill_manage calls
            for tc in skill_calls:
                args = self._get_tc_args(tc)
                action = args.get("action", "")
                name = args.get("name", "?")
                if not action or not name:
                    logger.warning(f"[SkillReviewer] Skipping skill_manage: missing required args {args}")
                    continue
                try:
                    result_str = await self._skill_tool.execute(**args)
                    result = json.loads(result_str)
                    if result.get("success"):
                        msg = result.get("message", "")
                        logger.info(f"[SkillReviewer] skill_manage {action} '{name}' succeeded: {msg}")
                        # Refresh skill cache so next main-agent call picks up new/updated skill
                        if self.context is not None:
                            try:
                                self.context.skills.refresh_cache()
                                self.context.invalidate_skill_snapshot()
                            except Exception:
                                pass
                    else:
                        logger.warning(f"[SkillReviewer] skill_manage {action} '{name}' rejected: {result.get('error', '')}")
                except Exception as e:
                    logger.warning(f"[SkillReviewer] skill_manage execution error: {e}")

            # After processing skill calls, do NOT call LLM again
            # Single-shot review is sufficient
            break

    def _get_tc_name(self, tc) -> str:
        """Extract tool name from tool call object (handles OpenAI/Anthropic format)."""
        if isinstance(tc, dict):
            fn = tc.get("function", {}) or {}
            if isinstance(fn, dict):
                return fn.get("name", "")
            return tc.get("name", "")
        return getattr(tc, "name", "") or ""

    def _get_tc_args(self, tc) -> dict:
        """Extract arguments from tool call object."""
        if isinstance(tc, dict):
            fn = tc.get("function", {}) or {}
            if isinstance(fn, dict):
                raw = fn.get("arguments")
            else:
                raw = tc.get("arguments")
        else:
            raw = getattr(tc, "arguments", None)
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return {}

    def close(self) -> None:
        """Clean up resources."""
        try:
            if hasattr(self.provider, "close"):
                self.provider.close()
        except Exception:
            pass
