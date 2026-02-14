"""Memory system for persistent agent memory."""

import re
from pathlib import Path
from datetime import datetime
from typing import Any

from nanobot.utils.helpers import ensure_dir, today_date, estimate_tokens, truncate_to_token_limit
from nanobot.storage.memory_repository import (
    get_memory_repository,
    parse_memory_entries_with_dates as _parse_entries,
    entries_to_text_preserve_dates as _entries_to_text,
)

# 写入限制：文件最多条目数与字符数
MEMORY_MAX_ENTRIES = 100
MEMORY_MAX_CHARS = 30 * 1024  # 30KB

# 读取限制：全量读取阈值，超出则首尾截断
MEMORY_READ_MAX_ENTRIES = 80
MEMORY_READ_MAX_CHARS = 25 * 1024  # 25KB
MEMORY_READ_KEEP_HEAD = 30  # 截断时保留最旧条数
MEMORY_READ_KEEP_TAIL = 50  # 截断时保留最新条数


def _entry_size(d: str, c: str) -> int:
    return len(d) + len(c) + 20


def truncate_entries_to_limit(
    entries_with_dates: list[tuple[str, str]],
    max_entries: int = MEMORY_MAX_ENTRIES,
    max_chars: int = MEMORY_MAX_CHARS,
) -> list[tuple[str, str]]:
    """超出限制时丢弃最旧条目。不修改输入列表。"""
    entries = list(entries_with_dates)
    while len(entries) > max_entries:
        entries.pop(0)
    total = sum(_entry_size(d, c) for d, c in entries)
    while entries and total > max_chars:
        d, c = entries.pop(0)
        total -= _entry_size(d, c)
    return entries


def entries_to_text_preserve_dates(entries_with_dates: list[tuple[str, str]]) -> str:
    """将 (date, content) 列表格式化为 MEMORY.md 内容，保留原日期"""
    return _entries_to_text(entries_with_dates)


def parse_memory_entries_with_dates(text: str) -> list[tuple[str, str]]:
    """解析为 (date_str, content) 元组列表"""
    return _parse_entries(text)


class MemoryStore:
    """
    Memory system for the agent.

    Supports daily notes (memory/YYYY-MM-DD.md) and long-term memory (MEMORY.md).
    Supports agent-specific memory isolation via agent_id parameter.
    
    Note: This class now uses SQLite as the underlying storage while maintaining
    backward compatibility with the original file-based API.
    """

    DEFAULT_MEMORY_DIR = "memory"
    AGENT_MEMORY_DIR = "agents"

    def __init__(self, workspace: Path, agent_id: str | None = None):
        self.workspace = workspace
        self.agent_id = agent_id
        self._repo = get_memory_repository()

        # Keep directory paths for backward compatibility (some code may check these)
        if agent_id:
            self.memory_dir = ensure_dir(workspace / self.AGENT_MEMORY_DIR / agent_id / self.DEFAULT_MEMORY_DIR)
            self.memory_file = self.memory_dir / "MEMORY.md"
        else:
            self.memory_dir = ensure_dir(workspace / self.DEFAULT_MEMORY_DIR)
            self.memory_file = self.memory_dir / "MEMORY.md"

    @classmethod
    def for_agent(cls, workspace: Path, agent_id: str) -> "MemoryStore":
        """Create a MemoryStore for a specific agent."""
        return cls(workspace=workspace, agent_id=agent_id)

    @classmethod
    def global_memory(cls, workspace: Path) -> "MemoryStore":
        """Create a MemoryStore for global memory (no agent isolation)."""
        return cls(workspace=workspace, agent_id=None)

    def is_agent_memory(self) -> bool:
        """Check if this is agent-specific memory."""
        return self.agent_id is not None

    def get_today_file(self) -> Path:
        """Get path to today's memory file (for backward compatibility)."""
        return self.memory_dir / f"{today_date()}.md"

    def read_today(self) -> str:
        """Read today's memory notes."""
        note = self._repo.get_daily_note(
            note_date=today_date(),
            agent_id=self.agent_id,
            scope="global"
        )
        if note:
            return f"# {today_date()}\n\n{note}"
        return ""

    def append_today(self, content: str) -> None:
        """Append content to today's memory notes."""
        # Strip header if present
        if content.startswith(f"# {today_date()}"):
            content = content.split("\n", 2)[-1]
        self._repo.append_daily_note(
            content=content,
            note_date=today_date(),
            agent_id=self.agent_id,
            scope="global"
        )

    def read_long_term(self) -> str:
        """Read long-term memory (MEMORY.md format)."""
        entries = self._repo.get_memories_for_summarize(
            agent_id=self.agent_id,
            scope="global"
        )
        return entries_to_text_preserve_dates(entries)

    def write_long_term(self, content: str) -> None:
        """Write to long-term memory (replaces all entries)."""
        entries = parse_memory_entries_with_dates(content)
        self._repo.replace_memories(
            entries=entries,
            agent_id=self.agent_id,
            scope="global"
        )

    def append_long_term_with_limit(self, content: str) -> None:
        """
        追加新条目到 MEMORY.md，超出限制时丢弃最旧条目。
        限制：MEMORY_MAX_ENTRIES 条、MEMORY_MAX_CHARS 字符。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.append_entries_with_limit([(now, content.strip())])

    def append_entries_with_limit(self, new_entries: list[tuple[str, str]]) -> None:
        """批量追加 (date_str, content) 条目，超出限制时丢弃最旧。一次读写，用于每日合并。"""
        if not new_entries:
            return

        # Get existing entries
        existing_entries = self._repo.get_memories_for_summarize(
            agent_id=self.agent_id,
            scope="global"
        )

        # Merge and truncate
        all_entries = list(existing_entries)
        all_entries.extend((d, c.strip()) for d, c in new_entries if c and c.strip())
        all_entries = truncate_entries_to_limit(all_entries)

        # Replace all entries
        self._repo.replace_memories(
            entries=all_entries,
            agent_id=self.agent_id,
            scope="global"
        )

    def get_recent_memories(self, days: int = 7) -> str:
        """
        Get memories from the last N days.

        Args:
            days: Number of days to look back.

        Returns:
            Combined memory content.
        """
        from datetime import timedelta

        memories = []
        today = datetime.now().date()

        for i in range(days):
            date = today - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            note = self._repo.get_daily_note(
                note_date=date_str,
                agent_id=self.agent_id,
                scope="global"
            )
            if note:
                memories.append(f"# {date_str}\n\n{note}")

        return "\n\n---\n\n".join(memories)

    def list_memory_files(self) -> list[Path]:
        """List all memory files sorted by date (newest first) - for backward compatibility."""
        # This method is kept for backward compatibility but returns empty list
        # since we no longer use files
        return []

    def get_memory_context(self, max_tokens: int | None = None) -> str:
        """
        Get memory context for the agent.
        
        Args:
            max_tokens: Optional maximum tokens for the context. If specified, will truncate to fit.
        
        Returns:
            Memory context string.
        - 若 MEMORY 条数≤80 且≤25KB：全量读取
        - 否则：取前30条（最旧）+ 后50条（最新），兼顾首尾
        """
        parts = []

        entries = self._repo.get_memories_for_summarize(
            agent_id=self.agent_id,
            scope="global"
        )

        if entries:
            n = len(entries)
            total_chars = sum(len(d) + len(c) + 20 for d, c in entries)

            if n <= MEMORY_READ_MAX_ENTRIES and total_chars <= MEMORY_READ_MAX_CHARS:
                long_term = entries_to_text_preserve_dates(entries)
            else:
                head = entries[:MEMORY_READ_KEEP_HEAD]
                tail_start = max(MEMORY_READ_KEEP_HEAD, n - MEMORY_READ_KEEP_TAIL)
                merged = head + entries[tail_start:]
                long_term = entries_to_text_preserve_dates(merged)

            parts.append("## Long-term Memory\n" + long_term)

        today_note = self._repo.get_daily_note(
            note_date=today_date(),
            agent_id=self.agent_id,
            scope="global"
        )
        if today_note:
            parts.append("## Today's Notes\n# " + today_date() + "\n\n" + today_note)

        result = "\n\n".join(parts) if parts else ""
        
        if max_tokens and estimate_tokens(result) > max_tokens:
            result = truncate_to_token_limit(result, max_tokens)
        
        return result

    # ========== New methods for direct repository access ==========

    def get_repository(self) -> Any:
        """Get the underlying memory repository for advanced operations."""
        return self._repo

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search memories using full-text search."""
        return self._repo.search_memories(
            query=query,
            scope="global",
            limit=limit
        )
