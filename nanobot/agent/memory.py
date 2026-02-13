"""Memory system for persistent agent memory."""

import re
from pathlib import Path
from datetime import datetime

from nanobot.utils.helpers import ensure_dir, today_date

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
    if not entries_with_dates:
        return "# Long-term Memory\n\n"
    lines = ["# Long-term Memory"]
    for date_part, content in entries_with_dates:
        lines.append(f"\n- [{date_part}] {content}")
    return "\n".join(lines) + "\n"


def parse_memory_entries_with_dates(text: str) -> list[tuple[str, str]]:
    """解析为 (date_str, content) 元组列表"""
    if not text or not text.strip():
        return []
    result = []
    for m in re.finditer(r"^\s*-\s*\[([\d\-:\s]+)\]\s*(.+?)(?=\n\s*-\s*\[|\n#|\Z)", text, re.DOTALL | re.MULTILINE):
        result.append((m.group(1).strip(), m.group(2).strip()))
    if not result:
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("- [") and "]" in line:
                end = line.index("]", 3)
                date_part = line[3:end].strip()
                content = line[end + 1 :].strip()
                result.append((date_part, content))
    return result


class MemoryStore:
    """
    Memory system for the agent.

    Supports daily notes (memory/YYYY-MM-DD.md) and long-term memory (MEMORY.md).
    Supports agent-specific memory isolation via agent_id parameter.
    """

    DEFAULT_MEMORY_DIR = "memory"
    AGENT_MEMORY_DIR = "agents"

    def __init__(self, workspace: Path, agent_id: str | None = None):
        self.workspace = workspace
        self.agent_id = agent_id

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
        """Get path to today's memory file."""
        return self.memory_dir / f"{today_date()}.md"
    
    def read_today(self) -> str:
        """Read today's memory notes."""
        today_file = self.get_today_file()
        if today_file.exists():
            return today_file.read_text(encoding="utf-8")
        return ""
    
    def append_today(self, content: str) -> None:
        """Append content to today's memory notes."""
        today_file = self.get_today_file()
        
        if today_file.exists():
            existing = today_file.read_text(encoding="utf-8")
            content = existing + "\n" + content
        else:
            # Add header for new day
            header = f"# {today_date()}\n\n"
            content = header + content
        
        today_file.write_text(content, encoding="utf-8")
    
    def read_long_term(self) -> str:
        """Read long-term memory (MEMORY.md)."""
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""
    
    def write_long_term(self, content: str) -> None:
        """Write to long-term memory (MEMORY.md)."""
        self.memory_file.write_text(content, encoding="utf-8")

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
        existing = self.read_long_term()
        entries = parse_memory_entries_with_dates(existing)
        entries.extend((d, c.strip()) for d, c in new_entries if c and c.strip())
        entries = truncate_entries_to_limit(entries)
        self.write_long_term(entries_to_text_preserve_dates(entries))
    
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
            file_path = self.memory_dir / f"{date_str}.md"
            
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                memories.append(content)
        
        return "\n\n---\n\n".join(memories)
    
    def list_memory_files(self) -> list[Path]:
        """List all memory files sorted by date (newest first)."""
        if not self.memory_dir.exists():
            return []
        
        files = list(self.memory_dir.glob("????-??-??.md"))
        return sorted(files, reverse=True)
    
    def get_memory_context(self) -> str:
        """
        Get memory context for the agent.
        - 若 MEMORY 条数≤80 且≤25KB：全量读取
        - 否则：取前30条（最旧）+ 后50条（最新），兼顾首尾
        """
        parts = []
        long_term_raw = self.read_long_term()
        if long_term_raw:
            entries = parse_memory_entries_with_dates(long_term_raw)
            n = len(entries)
            total_chars = sum(len(d) + len(c) + 20 for d, c in entries)
            if n <= MEMORY_READ_MAX_ENTRIES and total_chars <= MEMORY_READ_MAX_CHARS:
                long_term = long_term_raw
            else:
                head = entries[:MEMORY_READ_KEEP_HEAD]
                tail_start = max(MEMORY_READ_KEEP_HEAD, n - MEMORY_READ_KEEP_TAIL)
                merged = head + entries[tail_start:]
                long_term = entries_to_text_preserve_dates(merged)
            parts.append("## Long-term Memory\n" + long_term)
        today = self.read_today()
        if today:
            parts.append("## Today's Notes\n" + today)
        return "\n\n".join(parts) if parts else ""
