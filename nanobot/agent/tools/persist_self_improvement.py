"""Persist self-improvement conclusions to SQLite (scope=self_improve)."""

from pathlib import Path

from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.base import Tool
from nanobot.storage.memory_repository import SELF_IMPROVE_CONTENT_MAX_CHARS


class PersistSelfImprovementTool(Tool):
    """
    将自我改进流程中的可复用结论写入工作区 SQLite（memory_entries，scope=self_improve）。
    与 remember（global 长期记忆）分离，避免被记忆总结任务整表替换。
    """

    def __init__(self, workspace: Path | str, agent_id: str | None = None):
        self.workspace = Path(workspace).resolve()
        self.memory = (
            MemoryStore.for_agent(self.workspace, agent_id)
            if agent_id
            else MemoryStore(self.workspace)
        )

    @property
    def name(self) -> str:
        return "persist_self_improvement"

    @property
    def description(self) -> str:
        return (
            "【仅在自我改进/复盘流程末尾使用】把可复用结论写入工作区 SQLite（memory_entries，scope=self_improve），"
            "主 Agent 的 Memory 上下文会显示；只改 JSON/SKILL 文件不会自动入库，必须调用本工具。"
            "去重键 (source_id, source_type)，与 episodic、pat-…、Evolution 标记一致。"
            "source_type：self_improve_episode | self_improve_pattern | self_improve_correction。"
            "日常闲聊或非回顾任务不要调用。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "写给主 Agent 看的简短摘要（自然语言）；"
                        f"最长 {SELF_IMPROVE_CONTENT_MAX_CHARS} 字符"
                    ),
                },
                "source_type": {
                    "type": "string",
                    "enum": [
                        "self_improve_episode",
                        "self_improve_pattern",
                        "self_improve_correction",
                    ],
                    "description": "条目类型：情景 / 模式 / 纠错",
                },
                "source_id": {
                    "type": "string",
                    "description": "与技能目录 memory 或 SKILL 标记一致的 id，例如 ep-2025-03-26-001、pat-xxx",
                },
            },
            "required": ["content", "source_type", "source_id"],
        }

    async def execute(self, content: str, source_type: str, source_id: str, **kwargs) -> str:
        try:
            repo = self.memory.get_repository()
            mid = repo.upsert_self_improve_memory(
                content=content,
                source_type=source_type,
                source_id=source_id,
                agent_id=self.memory.agent_id,
            )
        except ValueError as e:
            return f"错误：{e}"
        return f"已写入 self_improve 记忆（id={mid}），source_id={source_id.strip()}"

