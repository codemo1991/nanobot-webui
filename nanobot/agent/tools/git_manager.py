"""Git Manager skill handler for direct git operations."""

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger


# Keywords that trigger git-manager skill
GIT_MANAGER_KEYWORDS = {
    # Push related (pull first to avoid conflicts, then push)
    "推送代码到github": "pull_and_push",
    "推送到github": "pull_and_push",
    "推送代码": "pull_and_push",
    "push到github": "pull_and_push",
    "git push": "pull_and_push",
    # Pull and restart related
    "更新并重启": "pull_and_restart",
    "更新并重启服务": "pull_and_restart",
    "拉取并重启": "pull_and_restart",
    "更新代码": "pull",
    "拉取代码": "pull",
    "git pull": "pull",
    # Restart
    "重启服务": "restart",
    "重启nanobot": "restart",
    # Evolve (commit + push + restart)
    "自进化": "evolve",
    "自我更新": "evolve",
}


class GitManagerHandler:
    """Handler for git-manager skill - executes git operations directly without LLM."""

    def __init__(self, agent_loop: "AgentLoop"):
        self.agent_loop = agent_loop

    async def handle(self, message: str) -> str | None:
        """
        Check if message matches git-manager keywords and execute directly.

        Args:
            message: User message content

        Returns:
            Result message if handled, None if not a git-manager command
        """
        message_lower = message.lower().strip()

        # Check for exact phrase matches first
        action = None

        # Check exact phrases (higher priority)
        for keyword, action_name in GIT_MANAGER_KEYWORDS.items():
            if keyword in message_lower:
                action = action_name
                logger.info(f"Git-manager: matched keyword '{keyword}' -> action '{action}'")
                break

        if not action:
            # No match, let LLM handle it
            return None

        # Execute the git operation directly
        try:
            result = await self._execute_git_action(action, message)
            return result
        except Exception as e:
            logger.exception(f"Git-manager: error executing action '{action}'")
            return f"❌ 执行git操作失败: {str(e)}"

    async def _execute_git_action(self, action: str, message: str) -> str:
        """
        Execute the git action using SelfUpdateTool.

        Args:
            action: Action to perform (push, pull, pull_and_restart, restart, evolve)
            message: Original message (for extracting commit message if needed)

        Returns:
            Result message
        """
        # Get the SelfUpdateTool from the agent's tool registry
        tool = self.agent_loop.tools.get("self_update")
        if not tool:
            return "❌ git-manager: SelfUpdateTool 未注册"

        # Prepare arguments based on action
        args: dict[str, Any] = {"action": action}

        # For commit actions, try to extract commit message from the message
        if action in ("commit_and_push", "evolve"):
            commit_message = self._extract_commit_message(message)
            if commit_message:
                args["commit_message"] = commit_message

        # Execute the tool
        result = await tool.execute(**args)
        return result

    def _extract_commit_message(self, message: str) -> str | None:
        """
        Extract commit message from user message.

        Args:
            message: Original user message

        Returns:
            Commit message if found, None otherwise
        """
        # Common patterns:
        # - "推送代码到github: message"
        # - "推送代码 -m 'message'"
        # - "推送代码，提交信息: xxx"

        message_lower = message.lower()

        # Check for commit message after colon
        if ":" in message:
            parts = message.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                return parts[1].strip()

        # Check for -m flag
        if "-m" in message_lower:
            idx = message_lower.find("-m")
            remaining = message[idx + 2:].strip()
            # Handle quoted message
            if remaining.startswith("'") or remaining.startswith('"'):
                quote_char = remaining[0]
                end_idx = remaining[1:].find(quote_char)
                if end_idx > 0:
                    return remaining[1:end_idx + 1]
            # Unquoted - take until space or end
            return remaining.strip()

        # Default commit message based on action
        if "推送" in message:
            return "更新并推送代码"
        elif "自进化" in message or "自我更新" in message:
            return "自进化更新"

        return None


def create_git_manager_handler(agent_loop: "AgentLoop") -> GitManagerHandler:
    """Factory function to create a GitManagerHandler."""
    return GitManagerHandler(agent_loop)
