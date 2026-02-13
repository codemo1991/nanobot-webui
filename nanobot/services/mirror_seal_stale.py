"""Seal stale mirror sessions (non-today, active wu/bian). Used by CLI cron."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

from loguru import logger

from nanobot.services.mirror_service import MirrorService


async def run_mirror_analysis(
    llm_chat: Callable[..., Coroutine[Any, Any, Any]],
    model: str,
    sessions: Any,
    stype: str,
    key: str,
) -> dict[str, Any] | None:
    """Run LLM analysis on mirror session. Returns parsed key-value dict or None."""
    try:
        messages_raw = sessions.get_messages(key=key, limit=100)
        conv_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'AI'}: {m['content'][:500]}"
            for m in messages_raw[-20:]
        )
        from nanobot.agent.skills import BUILTIN_SKILLS_DIR

        prompt_file = "wu-prompts.md" if stype == "wu" else "bian-prompts.md"
        fallback_system = "你输出简洁的分析，每行格式为「标签: 内容」。"
        system_content = fallback_system
        mirror_skill_path = BUILTIN_SKILLS_DIR / "mirror-system" / "references" / prompt_file
        if mirror_skill_path.exists():
            import re
            content = mirror_skill_path.read_text(encoding="utf-8")
            match = re.search(r"```\n(.*?)\n```", content, re.DOTALL)
            if match:
                system_content = match.group(1).strip()

        prompt = f"对话内容：\n{conv_text}\n\n请按以下格式输出（每行一个）：\n"
        if stype == "wu":
            prompt += (
                "叙事结构: [第一人称/第三人称，过去/现在倾向等]\n"
                "防御机制: [如合理化、投射、否认等]\n"
                "潜意识关键词: [如应该、必须、没办法等]\n"
                "核心洞察: [一句概括]\n"
            )
        else:
            prompt += (
                "辩题/立场: [用户主要立场]\n"
                "矛盾/谬误: [检测到的认知失调或逻辑谬误]\n"
                "叙事结构: [第一人称/第三人称，过去/现在倾向等]\n"
                "防御机制: [如合理化、投射、否认等]\n"
                "潜意识关键词: [如应该、必须、没办法等]\n"
                "核心洞察: [一句概括]\n"
            )
        resp = await llm_chat(
            [
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt},
            ],
            model=model,
            max_tokens=800,
            temperature=0.3,
        )
        if not resp or not getattr(resp, "content", None):
            return None
        result = {}
        for line in (resp.content or "").strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                result[k.strip()] = v.strip()
        return result
    except Exception as e:
        logger.warning("Mirror LLM analysis failed: %s", e)
        return None


def seal_stale_sessions(
    workspace: Path,
    sessions: Any,
    llm_chat: Callable[..., Coroutine[Any, Any, Any]],
    model: str,
    dry_run: bool = False,
) -> int:
    """
    封存非当日、未封存的悟/辩会话。
    dry_run=True 时仅列出将被封存的会话，不实际执行。
    返回封存数量（dry_run 时为符合条件数量）。
    """
    import asyncio

    mirror = MirrorService(workspace=workspace, sessions_manager=sessions)
    today_str = date.today().isoformat()
    sealed_count = 0
    pending: list[tuple[str, str, str]] = []

    for stype in ("wu", "bian"):
        data = mirror.list_sessions(stype, page=1, page_size=500)
        for item in data["items"]:
            if item.get("status") != "active":
                continue
            created_at = item.get("createdAt", "")
            if isinstance(created_at, datetime):
                created_date = created_at.date().isoformat()
            else:
                created_date = created_at[:10] if created_at else ""
            if created_date >= today_str:
                continue
            session_id = item["id"]
            pending.append((stype, session_id, created_date))

    if dry_run:
        for stype, sid, created_date in pending:
            logger.info("Would seal: %s %s (created %s)", stype, sid, created_date)
        return len(pending)

    async def _seal_all() -> None:
        nonlocal sealed_count
        for stype, session_id, _ in pending:
            key = MirrorService._session_key(stype, session_id)
            llm_analysis = await run_mirror_analysis(llm_chat, model, sessions, stype, key)
            try:
                mirror.seal_session(session_id, llm_analysis=llm_analysis)
                sealed_count += 1
                logger.info("Sealed stale %s session %s", stype, session_id)
            except KeyError:
                pass

    asyncio.run(_seal_all())
    return sealed_count
