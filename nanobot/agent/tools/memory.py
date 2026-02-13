"""Remember tool for reliably saving important information to long-term memory."""

from pathlib import Path

from nanobot.agent.tools.base import Tool
from nanobot.agent.memory import MemoryStore


class RememberTool(Tool):
    """
    将重要信息追加到长期记忆 (memory/MEMORY.md)。
    当用户说"记住"、"请记住"、"remember" 等时，必须调用此工具，而不是仅用文字回应。
    """

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace).resolve()
        self.memory = MemoryStore(self.workspace)

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "将用户要求记住的重要信息追加到长期记忆文件 memory/MEMORY.md。"
            "当用户说「记住」「请记住」「remember」或类似表达时，必须调用此工具保存信息，"
            "然后回复用户确认已记住。不要仅用文字说「记住了」而不调用本工具。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的内容，用简洁清晰的文字描述（如：用户每天的行动项在 /TODO 目录下）",
                }
            },
            "required": ["content"],
        }

    async def execute(self, content: str, **kwargs) -> str:
        if not content or not content.strip():
            return "Error: 记忆内容不能为空"
        self.memory.append_long_term_with_limit(content.strip())
        return f"已记住：{content.strip()}"
