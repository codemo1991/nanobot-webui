"""
Memory maintenance service: 定时总结 MEMORY.md、每日合并昨日 date.md。

- 每 60 分钟检查：若 MEMORY > 80 条 或 > 25KB，则调用 LLM 总结、去重、重写
- 每日 00:05：处理昨日 YYYY-MM-DD.md，提取重要信息追加到 MEMORY.md
"""

import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.memory import (
    MEMORY_READ_MAX_CHARS,
    MEMORY_READ_MAX_ENTRIES,
    MemoryStore,
    entries_to_text_preserve_dates,
    parse_memory_entries_with_dates,
    truncate_entries_to_limit,
)


async def _call_llm_summarize(provider: Any, model: str, text: str, prompt: str) -> str:
    """调用 LLM 进行总结/提取。"""
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text},
    ]
    resp = await provider.chat(messages=messages, model=model, max_tokens=4096)
    return (resp.content or "").strip()


MEMORY_SUMMARIZE_PROMPT = """你是一个记忆整理助手。将以下长期记忆条目进行总结、去重、合并相似内容。
要求：
1. 保留所有重要信息，合并表述相似或重复的条目
2. 每条输出格式为：- [YYYY-MM-DD HH:MM] 内容（保留最早日期）
3. 精简表述，去除冗余；内容中避免使用 # 开头的行，以免被误解析
4. 输出直接可写入 MEMORY.md 的 Markdown，不要额外说明"""

DAILY_EXTRACT_PROMPT = """你是一个记忆提取助手。从以下当日笔记中提取值得长期记住的信息。
提取：用户偏好、重要决定、项目信息、习惯设定等跨天仍有价值的内容。
忽略：临时待办、当天会议安排、一次性任务等。
每条输出格式：- [YYYY-MM-DD] 内容（内容中避免 # 开头的行）
若无值得长期记忆的内容，输出空。输出直接可追加到 MEMORY.md，不要额外说明"""


class MemoryMaintenanceService:
    """记忆维护服务：定时总结与每日合并。"""

    def __init__(
        self,
        workspace: Path,
        provider: Any,
        model: str | None = None,
        tick_interval_min: int = 5,
        summarize_interval_min: int = 60,
    ):
        self.workspace = Path(workspace).resolve()
        self.memory = MemoryStore(self.workspace)
        self.provider = provider
        self.model = model or (
            provider.get_default_model() if hasattr(provider, "get_default_model") else "anthropic/claude-sonnet-4-5"
        )
        self.tick_interval_min = tick_interval_min
        self.summarize_interval_min = summarize_interval_min
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_daily_run_date: datetime.date | None = None
        self._last_summarize_run: float = 0.0

    async def start(self) -> None:
        """启动维护服务。"""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        try:
            await self._tick()
        except Exception as e:
            logger.warning("Memory maintenance initial tick failed: {}", e)
        logger.info(
            "Memory maintenance started (tick {} min, summarize {} min)",
            self.tick_interval_min,
            self.summarize_interval_min,
        )

    def stop(self) -> None:
        """停止维护服务。"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        """主循环：定期 tick。"""
        while self._running:
            try:
                await asyncio.sleep(self.tick_interval_min * 60)
                if not self._running:
                    break
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Memory maintenance tick failed: {}", e)

    async def _tick(self) -> None:
        """单次 tick：每日 00:05 合并昨日；每 N 分钟检查总结。"""
        now = datetime.now()
        # 每日 00:05 执行昨日合并
        if now.hour == 0 and 5 <= now.minute < 5 + self.tick_interval_min:
            today = now.date()
            if self._last_daily_run_date != today:
                await self._run_daily_merge()
                self._last_daily_run_date = today
        # 每 summarize_interval_min 检查 MEMORY 是否需总结
        elapsed = time.monotonic() - self._last_summarize_run
        if self._last_summarize_run == 0 or elapsed >= self.summarize_interval_min * 60:
            await self._run_summarize_if_needed()
            self._last_summarize_run = time.monotonic()

    async def _run_summarize_if_needed(self) -> None:
        """若 MEMORY 超过阈值，执行 LLM 总结。"""
        raw = self.memory.read_long_term()
        if not raw or not raw.strip():
            return
        entries = parse_memory_entries_with_dates(raw)
        n = len(entries)
        total_chars = sum(len(d) + len(c) + 20 for d, c in entries)
        if n <= MEMORY_READ_MAX_ENTRIES and total_chars <= MEMORY_READ_MAX_CHARS:
            return
        logger.info("Memory over limit ({} entries, {} chars), summarizing...", n, total_chars)
        try:
            summarized = await _call_llm_summarize(self.provider, self.model, raw, MEMORY_SUMMARIZE_PROMPT)
            if not summarized:
                return
            new_entries = parse_memory_entries_with_dates(summarized)
            if not new_entries and "# Long-term Memory" in summarized:
                new_entries = parse_memory_entries_with_dates(summarized.replace("# Long-term Memory", "").strip())
            new_entries = truncate_entries_to_limit(new_entries)
            self.memory.write_long_term(entries_to_text_preserve_dates(new_entries))
            logger.info("Memory summarized: {} -> {} entries", n, len(new_entries))
        except Exception as e:
            logger.exception("Memory summarization failed: {}", e)

    async def _run_daily_merge(self) -> None:
        """处理昨日 date.md，提取重要信息追加到 MEMORY。"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        daily_file = self.memory.memory_dir / f"{yesterday}.md"
        if not daily_file.exists():
            return
        content = daily_file.read_text(encoding="utf-8")
        if not content.strip():
            return
        logger.info("Daily merge: extracting from {}", daily_file.name)
        try:
            extracted = await _call_llm_summarize(self.provider, self.model, content, DAILY_EXTRACT_PROMPT)
            if not extracted or not extracted.strip():
                return
            new_entries = parse_memory_entries_with_dates(extracted)
            if not new_entries:
                for line in extracted.split("\n"):
                    line = line.strip()
                    if line.startswith("- [") and "]" in line:
                        end = line.index("]", 3)
                        date_part = line[3:end].strip()
                        new_entries.append((date_part, line[end + 1 :].strip()))
            if new_entries:
                self.memory.append_entries_with_limit(new_entries)
            logger.info("Daily merge: added {} entries from {}", len(new_entries), daily_file.name)
        except Exception as e:
            logger.exception("Daily merge failed: {}", e)
