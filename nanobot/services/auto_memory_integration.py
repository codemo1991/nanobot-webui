"""
自动记忆整合服务：从对话历史中自动提取长期记忆。

功能：
- 定时从 chat_messages 获取消息
- 使用 LLM 提取值得长期记住的信息
- 写入 memory_entries 表

触发方式：
- 通过 Cron 任务定时触发（推荐）
- 调用 integrate_now() 立即执行
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.storage.memory_repository import get_memory_repository


# LLM Prompt
AUTO_INTEGRATE_PROMPT = """你是一个记忆提取助手。从以下对话记录中提取值得长期记住的信息。

## 任务
从对话中提取对未来交互有价值的长期信息。

## 提取规则
1. 保留：
   - 用户偏好和习惯 (如喜欢 Markdown、喜欢简洁回答)
   - 重要决定和承诺 (如"以后用 Claude 模型")
   - 项目状态和进展 (如"项目已上线")
   - 关键事实和知识 (如"API 密钥在 .env 文件中")
   - 重要人物和关系 (如"张三是技术负责人")

2. 忽略：
   - 日常问候
   - 一次性问答
   - 临时任务请求
   - 不重要的闲聊

## 输出格式
每条一行，格式：- [提取的事实]
如果无内容，输出：无

## 对话记录
{chat_history}

## 输出
"""


class AutoMemoryIntegrationService:
    """自动记忆整合服务"""

    def __init__(
        self,
        workspace: Path,
        provider: Any,
        model: str | None = None,
        lookback_minutes: int = 60,
        max_messages: int = 100,
    ):
        """
        初始化自动记忆整合服务。

        Args:
            workspace: Workspace 路径
            provider: LLM Provider
            model: LLM 模型名称
            lookback_minutes: 每次回溯的时间窗口（分钟）
            max_messages: 每次最多处理的消息数
        """
        self.workspace = Path(workspace).resolve()
        self.provider = provider
        self.model = model or (
            provider.get_default_model() if hasattr(provider, "get_default_model") else "anthropic/claude-sonnet-4-5"
        )
        self.lookback_minutes = lookback_minutes
        self.max_messages = max_messages
        self._repo = get_memory_repository(self.workspace)

        # 缓存 SessionManager 实例
        self._session_manager = None

    @property
    def session_manager(self):
        """获取 SessionManager 实例（延迟初始化，缓存复用）"""
        if self._session_manager is None:
            from nanobot.session.manager import SessionManager
            self._session_manager = SessionManager(self.workspace)
        return self._session_manager

    async def integrate_now(self) -> dict[str, Any]:
        """
        立即执行一次记忆整合。

        Returns:
            整合结果 {messages_processed: int, memories_extracted: int}
        """
        logger.info("开始执行自动记忆整合...")

        # 1. 计算时间窗口
        now = datetime.now()
        since = now - timedelta(minutes=self.lookback_minutes)
        since_timestamp = since.isoformat()

        # 2. 获取最近的聊天消息（使用缓存的 SessionManager）
        try:
            messages = self.session_manager.get_recent_messages(
                since_timestamp=since_timestamp,
                limit=self.max_messages,
                exclude_subagent=True,
            )
        except Exception as e:
            logger.error(f"获取聊天消息失败: {e}")
            return {"messages_processed": 0, "memories_extracted": 0, "error": str(e)}

        if not messages:
            logger.info("没有新的聊天消息需要处理")
            return {"messages_processed": 0, "memories_extracted": 0}

        logger.info(f"获取到 {len(messages)} 条消息")

        # 3. 格式化对话历史
        chat_history = self._format_chat_history(messages)

        # 4. 调用 LLM 提取长期记忆
        try:
            extracted = await self._extract_memories(chat_history)
        except Exception as e:
            logger.error(f"LLM 提取失败: {e}")
            return {"messages_processed": len(messages), "memories_extracted": 0, "error": str(e)}

        if not extracted or extracted == "无":
            logger.info("没有提取到需要长期记忆的信息")
            return {"messages_processed": len(messages), "memories_extracted": 0}

        # 5. 解析提取结果
        entries = self._parse_extracted_memories(extracted, now)
        if not entries:
            logger.warning("无法解析 LLM 输出的记忆")
            return {"messages_processed": len(messages), "memories_extracted": 0}

        # 6. 写入长期记忆（带去重）
        written_count = self._write_memories_with_dedup(entries)

        logger.info(f"自动记忆整合完成：处理 {len(messages)} 条消息，写入 {written_count} 条记忆")

        return {
            "messages_processed": len(messages),
            "memories_extracted": len(entries),
            "memories_written": written_count,
        }

    def _format_chat_history(self, messages: list[dict[str, Any]]) -> str:
        """格式化对话历史为文本"""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # 截断过长的内容
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    async def _extract_memories(self, chat_history: str) -> str:
        """调用 LLM 提取长期记忆"""
        from nanobot.providers.litellm_provider import LiteLLMProvider

        messages = [
            {"role": "system", "content": AUTO_INTEGRATE_PROMPT},
            {"role": "user", "content": chat_history},
        ]

        # 如果 provider 已经有 chat 方法，直接使用
        if hasattr(self.provider, "chat"):
            resp = await self.provider.chat(messages=messages, model=self.model, max_tokens=4096)
            return (resp.content or "").strip()

        # 否则尝试使用 LiteLLMProvider
        provider = LiteLLMProvider(
            api_key=self.provider.api_key if hasattr(self.provider, "api_key") else None,
            api_base=self.provider.api_base if hasattr(self.provider, "api_base") else None,
            default_model=self.model,
        )
        resp = await provider.chat(messages=messages, model=self.model, max_tokens=4096)
        return (resp.content or "").strip()

    def _parse_extracted_memories(self, text: str, now: datetime) -> list[tuple[str, str]]:
        """解析 LLM 输出的记忆为 (日期, 内容) 元组列表"""
        entries = []
        entry_date = now.strftime("%Y-%m-%d")

        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("-"):
                # 去掉开头的 "- "
                content = line[1:].strip()
                if content and content != "无":
                    entries.append((entry_date, content))

        return entries

    def _write_memories_with_dedup(self, entries: list[tuple[str, str]]) -> int:
        """写入记忆（带简单去重）"""
        # 获取现有的自动记忆
        existing = self._repo.get_memories_for_summarize(
            agent_id=None,
            scope="global",
        )

        # 简单去重：检查内容是否已存在
        existing_contents = [content for _, content in existing]
        new_entries = []

        for entry_date, content in entries:
            # 简单检查：内容是否被现有记忆包含
            is_duplicate = any(content in existing for existing in existing_contents)
            if not is_duplicate:
                new_entries.append((entry_date, content))

        if not new_entries:
            return 0

        # 写入新记忆
        for entry_date, content in new_entries:
            self._repo.append_memory(
                content=content,
                agent_id=None,
                scope="global",
                source_type="auto_integrate",
                source_id=None,
            )

        return len(new_entries)
