"""Tool to get subagent execution results."""

from typing import Any, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager


class GetSubagentResultsTool:
    """
    Get the execution results of previous subagent tasks in the current session.

    Use this tool when the user asks about subagent results, such as:
    - "查看子agent执行结果"
    - "之前后台任务的结果是什么"
    - "subagent的任务完成了吗"
    """

    def __init__(self, sessions: "SessionManager", channel: str = "cli", chat_id: str = "direct"):
        self._sessions = sessions
        self._channel = channel
        self._chat_id = chat_id

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the channel and chat ID for the tool."""
        self._channel = channel
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "get_subagent_results"

    @property
    def description(self) -> str:
        return (
            "Get the execution results of previous subagent tasks in the current session. "
            "Use this when the user asks about subagent results, task status, or wants to see what background tasks have completed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Optional specific task ID to get result for. If not provided, returns all results.",
                },
            },
            "required": [],
        }

    async def execute(self, task_id: str | None = None, **kwargs: Any) -> str:
        """Get subagent results from the session."""
        session_key = f"{self._channel}:{self._chat_id}"
        logger.info(f"[GetSubagentResults] Querying for session_key: {session_key}, task_id: {task_id}")

        session = self._sessions.get(session_key)

        if not session:
            logger.warning(f"[GetSubagentResults] No session found for key: {session_key}")
            return "No session found. There are no subagent results available."

        results = session.subagent_results
        logger.info(f"[GetSubagentResults] Found {len(results)} results in session {session_key}")

        if not results:
            return "No subagent results found in this session."

        if task_id:
            if task_id in results:
                r = results[task_id]
                return f"""Subagent Task: {r['label']}
Status: {r['status']}
Task: {r['task']}

Result:
{r['result']}

Timestamp: {r['timestamp']}"""
            else:
                return f"Task ID '{task_id}' not found. Available task IDs: {', '.join(results.keys())}"

        # Return all results
        output = f"Found {len(results)} subagent result(s):\n\n"
        for tid, r in results.items():
            status_icon = "✅" if r['status'] == 'ok' else "❌" if r['status'] == 'error' else "⏳"
            output += f"{status_icon} [{tid}] {r['label']} - {r['status']}\n"
            output += f"   Task: {r['task'][:80]}...\n"
            output += f"   Result: {r['result'][:100]}...\n\n"

        return output
