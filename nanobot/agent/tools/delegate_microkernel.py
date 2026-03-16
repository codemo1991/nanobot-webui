"""将复杂任务委托给微内核编排执行。"""

from typing import Any, Callable, Coroutine

from nanobot.agent.tools.base import Tool


class DelegateMicrokernelTool(Tool):
    """将复杂任务委托给微内核编排执行。委托后立即返回，微内核在后台执行，完成后会通知用户。"""

    def __init__(self, delegate_fn: Callable[..., Coroutine[Any, Any, str]]):
        """
        Args:
            delegate_fn: 异步委托回调，签名为 (goal, attempted_steps, initial_artifacts, channel, chat_id) -> str
        """
        self._delegate_fn = delegate_fn
        self._channel = "cli"
        self._chat_id = "direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        """设置当前会话上下文。"""
        self._channel = channel
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "delegate_to_microkernel"

    @property
    def description(self) -> str:
        return (
            "将复杂任务委托给微内核编排执行。适用于：多步链式任务、需要并行检索+起草+批评的任务、"
            "主 Agent 已尝试多次仍未完成的任务。委托后立即返回，微内核在后台执行，完成后会通知用户。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "用户目标的完整描述",
                },
                "attempted_steps": {
                    "type": "array",
                    "description": "主 Agent 已执行的步骤摘要（可选）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "result_preview": {"type": "string"},
                        },
                    },
                },
                "initial_artifacts": {
                    "type": "object",
                    "description": "主 Agent 已产出的结果（可选），如 doc_content_v1、search_result_v1 等",
                },
            },
            "required": ["goal"],
        }

    async def execute(
        self,
        goal: str,
        attempted_steps: list[dict] | None = None,
        initial_artifacts: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        if not goal or not str(goal).strip():
            return "Error: goal 不能为空"
        trace_id = await self._delegate_fn(
            goal=str(goal).strip(),
            attempted_steps=attempted_steps or [],
            initial_artifacts=initial_artifacts or {},
            channel=self._channel,
            chat_id=self._chat_id,
        )
        return f"✅ 复杂任务已提交 (trace_id: {trace_id})，执行完成后将通知你。"
