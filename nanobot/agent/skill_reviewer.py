"""Skill reviewer: background agent for silent skill review after conversations."""

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---- Prompts ----

_SKILL_REVIEW_SYSTEM_PROMPT = """You are a skill reviewer. Your job is to analyze the conversation above and decide whether any reusable skills should be created, updated, or deleted.

Focus on:
- Was a non-trivial approach used to complete a task that required trial and error?
- Did the agent discover a better method mid-task and change course?
- Was there a reusable workflow worth capturing for future similar tasks?
- Is any existing skill now outdated or wrong based on what was learned?

If a relevant skill already exists, UPDATE it (use action='patch' or action='edit').
Otherwise, CREATE a new skill if the approach is reusable (action='create').
If a skill is no longer useful, DELETE it (action='delete').

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
            "触发时机：完成复杂任务、修复错误、发现可复用工作流。\n"
            "动作：create（新建）/ edit（全文替换）/ delete（删除）/ patch（局部替换）/ write_file（辅助文件）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "edit", "delete", "patch", "write_file"],
                    "description": "操作类型"
                },
                "name": {
                    "type": "string",
                    "description": "Skill 名称（小写、字母数字、-_. ，最大64字符）"
                },
                "content": {
                    "type": "string",
                    "description": "SKILL.md 内容（create/edit 必须包含 YAML frontmatter）"
                },
                "file_path": {
                    "type": "string",
                    "description": "辅助文件路径（write_file 时必填）"
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


def spawn_skill_review(
    provider,
    model: str,
    workspace: Path,
    context: Any,
    messages: list[dict[str, Any]],
) -> None:
    """Spawn a daemon thread to run skill review.

    Args:
        provider: LLM provider instance (must have .chat() method)
        model: Model name string
        workspace: Workspace Path
        context: ContextBuilder instance (for refresh_cache after write)
        messages: Conversation history snapshot (list of message dicts)
    """
    from nanobot.agent.tools.skill_manager import SkillManagerTool

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        reviewer = None
        try:
            reviewer = _SkillReviewer(
                provider=provider,
                model=model,
                workspace=workspace,
                context=context,
                messages=messages,
            )
            loop.run_until_complete(reviewer.run())
        except Exception as e:
            logger.debug("[SkillReviewer] Review failed: %s", e)
        finally:
            if reviewer is not None:
                try:
                    reviewer.close()
                except Exception:
                    pass
            try:
                loop.close()
            except Exception:
                pass

    t = threading.Thread(target=_run, daemon=True, name="skill-reviewer")
    t.start()


class _SkillReviewer:
    """Lightweight background reviewer that calls LLM with skill_manage tool."""

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

    def _build_review_messages(self) -> list[dict[str, Any]]:
        """Build messages for the review LLM call."""
        return [
            {"role": "system", "content": _SKILL_REVIEW_SYSTEM_PROMPT},
            *self.messages,
            {"role": "user", "content": _SKILL_REVIEW_USER_PROMPT},
        ]

    async def run(self) -> None:
        """Run one review cycle: call LLM, execute skill_manage if needed."""
        from nanobot.agent.tools.skill_manager import SkillManagerTool

        messages = self._build_review_messages()
        max_iterations = 3

        for _ in range(max_iterations):
            # Call LLM
            try:
                response = self.provider.chat(
                    model=self.model,
                    messages=messages,
                    tools=[_SKILL_MANAGE_TOOL_DEF],
                    stream=False,
                )
            except Exception as e:
                logger.debug("[SkillReviewer] LLM call failed: %s", e)
                break

            # Check for tool calls
            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                # No tool calls — check if model said "Nothing to save"
                content = getattr(response, "content", "") or ""
                logger.debug("[SkillReviewer] Review done, no skill action: %s", content[:100])
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
                try:
                    result_str = await self._skill_tool.execute(**args)
                    result = json.loads(result_str)
                    if result.get("success"):
                        msg = result.get("message", "")
                        logger.info("[SkillReviewer] skill_manage %s '%s' succeeded: %s", action, name, msg)
                        # Refresh skill cache so next main-agent call picks up new/updated skill
                        if self.context is not None:
                            try:
                                self.context.skills.refresh_cache()
                                self.context.invalidate_skill_snapshot()
                            except Exception:
                                pass
                    else:
                        logger.debug("[SkillReviewer] skill_manage %s '%s' failed: %s", action, name, result.get("error", ""))
                except Exception as e:
                    logger.debug("[SkillReviewer] skill_manage execution error: %s", e)

            # After processing skill calls, do NOT call LLM again
            # Single-shot review is sufficient
            break

    def _get_tc_name(self, tc) -> str:
        """Extract tool name from tool call object (handles OpenAI/Anthropic format)."""
        if isinstance(tc, dict):
            fn = tc.get("function", {})
            if isinstance(fn, dict):
                return fn.get("name", "")
            return tc.get("name", "")
        return getattr(tc, "name", "") or getattr(tc, "function", {}).get("name", "")

    def _get_tc_args(self, tc) -> dict:
        """Extract arguments from tool call object."""
        if isinstance(tc, dict):
            fn = tc.get("function", {})
            if isinstance(fn, dict):
                raw = fn.get("arguments", "{}")
            else:
                raw = tc.get("arguments", "{}")
        else:
            raw = getattr(tc, "arguments", "{}")
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            return {}

    def close(self) -> None:
        """Clean up resources."""
        try:
            if hasattr(self.provider, "close"):
                self.provider.close()
        except Exception:
            pass
