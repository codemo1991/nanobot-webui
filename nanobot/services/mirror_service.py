"""Mirror Room (镜室) service: manages wu/bian sessions, shang records, and profile data."""

from __future__ import annotations

import json
from datetime import datetime, date
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger


class MirrorService:
    """Handles all mirror-room data operations."""

    def __init__(self, workspace: str | Path, sessions_manager: Any) -> None:
        self.workspace = Path(workspace)
        self.sessions = sessions_manager
        self._mirror_dir = self.workspace / "mirror"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create mirror directory structure if not exists."""
        for sub in ["wu", "bian", "shang", "snapshots"]:
            (self._mirror_dir / sub).mkdir(parents=True, exist_ok=True)

    def update_workspace(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)
        self._mirror_dir = self.workspace / "mirror"
        self._ensure_dirs()

    # ---------- Session key helpers ----------

    @staticmethod
    def _session_prefix(session_type: str) -> str:
        return f"mirror-{session_type}:"

    @staticmethod
    def _session_key(session_type: str, session_id: str) -> str:
        return f"mirror-{session_type}:{session_id}"

    @staticmethod
    def _parse_session_key(key: str) -> tuple[str, str]:
        """Returns (type, session_id) from a key like 'mirror-wu:sess_abc123'."""
        prefix, sid = key.split(":", 1)
        stype = prefix.replace("mirror-", "")
        return stype, sid

    # ---------- Mirror Sessions (悟/辩) ----------

    def list_sessions(
        self, session_type: str, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        """List mirror sessions of a given type."""
        prefix = self._session_prefix(session_type)
        all_sessions = self.sessions.list_sessions(key_prefix=prefix)
        total = len(all_sessions)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        items = all_sessions[start:end]
        return {
            "items": [self._format_session(s) for s in items],
            "total": total,
        }

    def _format_session(self, s: dict[str, Any]) -> dict[str, Any]:
        stype, sid = self._parse_session_key(s["key"])
        meta = s.get("metadata", {})
        return {
            "id": sid,
            "type": stype,
            "title": meta.get("title"),
            "status": meta.get("status", "active"),
            "createdAt": s["created_at"],
            "updatedAt": s["updated_at"],
            "sealedAt": meta.get("sealed_at"),
            "messageCount": s["message_count"],
            "attackLevel": meta.get("attack_level"),
            "topic": meta.get("topic"),
            "insight": meta.get("insight"),
        }

    def create_session(
        self,
        session_type: str,
        attack_level: str | None = None,
        topic: str | None = None,
    ) -> dict[str, Any]:
        """Create a new mirror session."""
        session_id = f"sess_{uuid4().hex[:12]}"
        key = self._session_key(session_type, session_id)
        session = self.sessions.get_or_create(key)
        session.metadata["status"] = "active"
        session.metadata["session_type"] = session_type
        if attack_level:
            session.metadata["attack_level"] = attack_level
        if topic:
            session.metadata["topic"] = topic
        self.sessions.save(session)
        return self._format_session_obj(session, session_type, session_id)

    def _format_session_obj(self, session: Any, stype: str, sid: str) -> dict[str, Any]:
        return {
            "id": sid,
            "type": stype,
            "title": session.metadata.get("title"),
            "status": session.metadata.get("status", "active"),
            "createdAt": session.created_at.isoformat(),
            "updatedAt": session.updated_at.isoformat(),
            "sealedAt": session.metadata.get("sealed_at"),
            "messageCount": len(session.history) if hasattr(session, "history") else 0,
            "attackLevel": session.metadata.get("attack_level"),
            "topic": session.metadata.get("topic"),
            "insight": session.metadata.get("insight"),
        }

    def get_messages(self, session_id: str, session_type: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get messages for a mirror session."""
        key = self._session_key(session_type, session_id)
        session = self.sessions.get(key)
        if session is None:
            raise KeyError("mirror session not found")
        messages = self.sessions.get_messages(key=key, limit=limit)
        return [
            {
                "id": f"msg_{m['sequence']}",
                "sessionId": session_id,
                "role": m["role"],
                "content": m["content"],
                "createdAt": m["timestamp"],
                "sequence": m["sequence"],
                **({"toolSteps": m["tool_steps"]} if m.get("tool_steps") else {}),
            }
            for m in messages
        ]

    def seal_session(
        self, session_id: str, llm_analysis: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Seal a mirror session (make it read-only) and trigger analysis."""
        for stype in ("wu", "bian"):
            key = self._session_key(stype, session_id)
            session = self.sessions.get(key)
            if session is not None:
                session.metadata["status"] = "sealed"
                session.metadata["sealed_at"] = datetime.now().isoformat()
                self.sessions.save(session)
                self._write_session_analysis(stype, session_id, key, llm_analysis)
                return self._format_session_obj(session, stype, session_id)
        raise KeyError("mirror session not found")

    def _write_session_analysis(
        self,
        stype: str,
        session_id: str,
        key: str,
        llm_analysis: dict[str, Any] | None = None,
    ) -> None:
        """Write session analysis to daily MD file and MEMORY.md."""
        try:
            messages = self.sessions.get_messages(key=key, limit=200)
            today_str = date.today().strftime("%Y-%m-%d")
            now_str = datetime.now().strftime("%H:%M")

            conversation_lines = []
            for m in messages:
                role = "用户" if m["role"] == "user" else "AI"
                conversation_lines.append(f"**{role}**: {m['content'][:200]}")

            extra = ""
            if llm_analysis:
                extra = "\n".join(
                    f"**{k}**: {v}" for k, v in llm_analysis.items() if v
                )
                if extra:
                    extra = "\n\n" + extra + "\n"

            analysis_block = f"""
### {stype.upper()}分析 #{session_id[-6:]} ({now_str})
**会话ID**: {session_id}
**对话切片**:
{chr(10).join(conversation_lines[:10])}
{"..." if len(conversation_lines) > 10 else ""}
{extra}
---
"""
            # Write to daily file
            daily_file = self._mirror_dir / stype / f"{today_str}.md"
            if daily_file.exists():
                existing = daily_file.read_text(encoding="utf-8")
            else:
                existing = f"## {today_str}\n\n"
            daily_file.write_text(existing + analysis_block, encoding="utf-8")

            # 若有 LLM 分析，写入核心洞察供侧栏展示
            if llm_analysis:
                insight = llm_analysis.get("核心洞察") or llm_analysis.get("core_insight")
                if insight:
                    session = self.sessions.get(key)
                    if session:
                        session.metadata["insight"] = str(insight)[:100]
                        self.sessions.save(session)

            # Append to MEMORY.md
            memory_file = self._mirror_dir / stype / "MEMORY.md"
            if memory_file.exists():
                mem_content = memory_file.read_text(encoding="utf-8")
            else:
                mem_content = f"# {stype.upper()} 总档案\n\n"
            summary_line = f"- [{today_str} {now_str}] 会话 {session_id[-6:]} - {len(messages)} 条消息\n"
            memory_file.write_text(mem_content + summary_line, encoding="utf-8")

            logger.info(f"Mirror analysis written for {stype} session {session_id}")
        except Exception as e:
            logger.error(f"Failed to write mirror analysis: {e}")

    # ---------- Shang (赏) ----------

    def get_shang_today(self) -> dict[str, Any]:
        """Check if today's shang has been completed."""
        today_str = date.today().strftime("%Y-%m-%d")
        records = self._load_shang_records()
        today_records = [r for r in records if r.get("date") == today_str]
        if today_records:
            latest = today_records[-1]
            return {"done": latest.get("status") == "done", "record": latest}
        return {"done": False, "record": None}

    def get_shang_records(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Get paginated shang records."""
        records = self._load_shang_records()
        records.sort(key=lambda r: r.get("date", ""), reverse=True)
        total = len(records)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        return {"items": records[start:end], "total": total}

    def start_shang(self) -> dict[str, Any]:
        """Start a new shang session for today."""
        today_str = date.today().strftime("%Y-%m-%d")
        record_id = f"shang_{uuid4().hex[:8]}"

        # Generate topic and descriptions (placeholder - will be filled by LLM later)
        topics = [
            "内在力量", "孤独与连接", "秩序与混沌", "自由与束缚",
            "创造与毁灭", "光明与阴影", "旅程与归宿", "真实与面具"
        ]
        import random
        topic = random.choice(topics)

        record = {
            "id": record_id,
            "date": today_str,
            "topic": topic,
            "imageA": None,
            "imageB": None,
            "descriptionA": f"「{topic}」的第一种诠释 - 温暖而明亮的表达",
            "descriptionB": f"「{topic}」的第二种诠释 - 深沉而内敛的表达",
            "choice": None,
            "attribution": "",
            "analysis": None,
            "status": "choosing",
        }

        self._save_shang_record(record)
        return record

    def submit_shang_choice(
        self, record_id: str, choice: str, attribution: str
    ) -> dict[str, Any]:
        """Submit A/B choice and attribution for a shang record."""
        records = self._load_shang_records()
        for i, r in enumerate(records):
            if r["id"] == record_id:
                r["choice"] = choice
                r["attribution"] = attribution
                r["status"] = "done"
                r["analysis"] = {
                    "jungType": None,
                    "bigFive": None,
                    "archetype": None,
                    "crossValidation": None,
                }
                records[i] = r
                self._save_all_shang_records(records)
                return r
        raise KeyError("shang record not found")

    def _load_shang_records(self) -> list[dict[str, Any]]:
        """Load all shang records from JSON files."""
        shang_dir = self._mirror_dir / "shang"
        records = []
        index_file = shang_dir / "records.json"
        if index_file.exists():
            try:
                records = json.loads(index_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                records = []
        return records

    def _save_shang_record(self, record: dict[str, Any]) -> None:
        """Add a single shang record."""
        records = self._load_shang_records()
        records.append(record)
        self._save_all_shang_records(records)

    def _save_all_shang_records(self, records: list[dict[str, Any]]) -> None:
        index_file = self._mirror_dir / "shang" / "records.json"
        index_file.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- Profile (吾) ----------

    def get_profile(self) -> dict[str, Any] | None:
        """Get the current mirror profile from profile.json or latest snapshot."""
        profile_file = self._mirror_dir / "profile.json"
        if profile_file.exists():
            try:
                return json.loads(profile_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                pass
        # 降级：从 snapshots/ 取最新快照
        snap_dir = self._mirror_dir / "snapshots"
        if snap_dir.exists():
            snaps = sorted(snap_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for snap in snaps:
                try:
                    return json.loads(snap.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, IOError):
                    continue
        return None
