"""Mirror Room (镜室) service: manages wu/bian sessions, shang records, and profile data."""

from __future__ import annotations

import json
from datetime import datetime, date
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from nanobot.storage.memory_repository import get_memory_repository


class MirrorService:
    """Handles all mirror-room data operations."""

    def __init__(self, workspace: str | Path, sessions_manager: Any) -> None:
        self.workspace = Path(workspace)
        self.sessions = sessions_manager
        self._mirror_dir = self.workspace / "mirror"
        # 使用 workspace 特定的数据库
        self._repo = get_memory_repository(self.workspace)
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create mirror directory structure if not exists."""
        # Keep directory structure for images and profile files
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

    def update_session_title(self, session_id: str, title: str) -> dict[str, Any] | None:
        """更新悟/辩会话标题。返回更新后的 session 或 None。"""
        for stype in ("wu", "bian"):
            key = self._session_key(stype, session_id)
            session = self.sessions.get(key)
            if session is not None:
                session.metadata["title"] = title
                session.updated_at = datetime.now()
                self.sessions.save(session)
                return self._format_session_obj(session, stype, session_id)
        return None

    def delete_session(self, session_type: str, session_id: str) -> bool:
        """删除悟/辩会话。"""
        key = self._session_key(session_type, session_id)
        return self.sessions.delete(key)

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
        """Write session analysis to SQLite (daily notes and memory)."""
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
            # Write to daily notes in SQLite
            self._repo.append_daily_note(
                content=analysis_block,
                note_date=today_str,
                scope=f"mirror-{stype}",
            )

            # 若有 LLM 分析，写入核心洞察供侧栏展示
            if llm_analysis:
                insight = llm_analysis.get("核心洞察") or llm_analysis.get("core_insight")
                if insight:
                    session = self.sessions.get(key)
                    if session:
                        session.metadata["insight"] = str(insight)[:100]
                        self.sessions.save(session)

            # Append to memory in SQLite
            summary_line = f"会话 {session_id[-6:]} - {len(messages)} 条消息"
            self._repo.append_memory(
                content=summary_line,
                scope=f"mirror-{stype}",
                source_type="session_analysis",
                source_id=session_id,
            )

            logger.info(f"Mirror analysis written for {stype} session {session_id}")
        except Exception as e:
            logger.error(f"Failed to write mirror analysis: {e}")

    # ---------- Shang (赏) ----------

    def get_shang_today(self) -> dict[str, Any]:
        """Check if today's shang has been completed."""
        today_str = date.today().strftime("%Y-%m-%d")
        records = self._repo.list_shang_records(page=1, page_size=1000)
        items = records.get("items", [])
        today_records = [r for r in items if r.get("date") == today_str]
        if today_records:
            latest = today_records[-1]
            return {"done": latest.get("status") == "done", "record": latest}
        return {"done": False, "record": None}

    def get_shang_records(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Get paginated shang records."""
        return self._repo.list_shang_records(page=page, page_size=page_size)

    def start_shang(
        self,
        dashscope_api_key: str | None = None,
        qwen_image_model: str = "qwen-image-plus",
    ) -> dict[str, Any]:
        """Start a new shang session for today. If dashscope_api_key provided, generate A/B images via Qwen-Image."""
        today_str = date.today().strftime("%Y-%m-%d")
        record_id = f"shang_{uuid4().hex[:8]}"

        topics = [
            "内在力量", "孤独与连接", "秩序与混沌", "自由与束缚",
            "创造与毁灭", "光明与阴影", "旅程与归宿", "真实与面具"
        ]
        import random
        topic = random.choice(topics)

        description_a = f"「{topic}」的第一种诠释 - 温暖而明亮的表达"
        description_b = f"「{topic}」的第二种诠释 - 深沉而内敛的表达"

        image_a_url: str | None = None
        image_b_url: str | None = None
        if dashscope_api_key and qwen_image_model:
            try:
                from nanobot.providers.dashscope_image import (
                    generate_image,
                    get_shang_prompts_for_topic,
                    download_and_save_image,
                )
                prompt_a, prompt_b = get_shang_prompts_for_topic(topic)
                model = qwen_image_model
                url_a = generate_image(prompt_a, dashscope_api_key, model=model)
                url_b = generate_image(prompt_b, dashscope_api_key, model=model)
                images_dir = self._mirror_dir / "shang" / "images"
                images_dir.mkdir(parents=True, exist_ok=True)
                if url_a:
                    if download_and_save_image(url_a, str(images_dir / f"{record_id}_A.png")):
                        image_a_url = f"/api/v1/mirror/shang/image?recordId={record_id}&slot=A"
                    else:
                        image_a_url = url_a
                else:
                    image_a_url = None
                if url_b:
                    if download_and_save_image(url_b, str(images_dir / f"{record_id}_B.png")):
                        image_b_url = f"/api/v1/mirror/shang/image?recordId={record_id}&slot=B"
                    else:
                        image_b_url = url_b
                else:
                    image_b_url = None
            except Exception as e:
                logger.warning("Qwen-Image generation failed: %s", e)

        record = {
            "id": record_id,
            "date": today_str,
            "topic": topic,
            "imageA": image_a_url,
            "imageB": image_b_url,
            "descriptionA": description_a,
            "descriptionB": description_b,
            "choice": None,
            "attribution": "",
            "analysis": None,
            "status": "choosing",
        }

        self._repo.save_shang_record(record)
        return record

    def regenerate_shang_images(
        self,
        record_id: str,
        dashscope_api_key: str | None = None,
        qwen_image_model: str = "qwen-image-plus",
    ) -> dict[str, Any] | None:
        """重新生成指定 record 的 A/B 图片。仅 status=choosing 时可用。返回更新后的 record 或 None。"""
        record = self._repo.get_shang_record(record_id)
        if record is None or record.get("status") != "choosing":
            return None

        topic = record.get("topic", "内在力量")
        image_a_url: str | None = None
        image_b_url: str | None = None

        if dashscope_api_key and qwen_image_model:
            try:
                from nanobot.providers.dashscope_image import (
                    generate_image,
                    get_shang_prompts_for_topic,
                    download_and_save_image,
                )
                prompt_a, prompt_b = get_shang_prompts_for_topic(topic)
                model = qwen_image_model
                url_a = generate_image(prompt_a, dashscope_api_key, model=model)
                url_b = generate_image(prompt_b, dashscope_api_key, model=model)
                images_dir = self._mirror_dir / "shang" / "images"
                images_dir.mkdir(parents=True, exist_ok=True)
                if url_a:
                    if download_and_save_image(url_a, str(images_dir / f"{record_id}_A.png")):
                        image_a_url = f"/api/v1/mirror/shang/image?recordId={record_id}&slot=A"
                    else:
                        image_a_url = url_a
                if url_b:
                    if download_and_save_image(url_b, str(images_dir / f"{record_id}_B.png")):
                        image_b_url = f"/api/v1/mirror/shang/image?recordId={record_id}&slot=B"
                    else:
                        image_b_url = url_b
            except Exception as e:
                logger.warning("Qwen-Image regenerate failed: %s", e)

        updates = {}
        if image_a_url is not None:
            updates["imageA"] = image_a_url
        if image_b_url is not None:
            updates["imageB"] = image_b_url

        if updates:
            self._repo.update_shang_record(record_id, updates)
            # Return updated record
            return self._repo.get_shang_record(record_id)

        return record

    def submit_shang_choice(
        self, record_id: str, choice: str, attribution: str
    ) -> dict[str, Any]:
        """Submit A/B choice and attribution for a shang record."""
        record = self._repo.get_shang_record(record_id)
        if record is None:
            raise KeyError("shang record not found")

        updates = {
            "choice": choice,
            "attribution": attribution,
            "status": "done",
            "analysis": {
                "jungType": None,
                "bigFive": None,
                "archetype": None,
                "crossValidation": None,
            },
        }

        self._repo.update_shang_record(record_id, updates)
        return self._repo.get_shang_record(record_id)

    def update_shang_analysis(self, record_id: str, analysis: dict[str, Any]) -> dict[str, Any] | None:
        """更新赏记录的 analysis 字段。"""
        record = self._repo.get_shang_record(record_id)
        if record is None:
            return None

        self._repo.update_shang_record(record_id, {"analysis": analysis})
        return self._repo.get_shang_record(record_id)

    def delete_shang_record(self, record_id: str) -> bool:
        """删除赏记录。"""
        return self._repo.delete_shang_record(record_id)

    def write_shang_record_to_memory(self, record: dict[str, Any]) -> None:
        """将赏记录的主要多维评价维度写入日期 MD 和 MEMORY.md，与悟/辩保持一致。"""
        self._write_shang_record_to_sqlite(record)

    def _write_shang_record_to_sqlite(self, record: dict[str, Any]) -> None:
        """写入 mirror-shang daily notes 和 memory."""
        try:
            record_date = record.get("date") or date.today().strftime("%Y-%m-%d")
            now_str = datetime.now().strftime("%H:%M")
            record_id = record.get("id", "")
            topic = record.get("topic", "")
            choice = record.get("choice", "")
            desc_a = record.get("descriptionA", "")
            desc_b = record.get("descriptionB", "")
            attribution = record.get("attribution", "")

            analysis_lines = []
            analysis = record.get("analysis") or {}
            if isinstance(analysis, dict):
                jung = analysis.get("jungType")
                if jung and isinstance(jung, dict):
                    tc = jung.get("typeCode", "")
                    desc = jung.get("description", "")
                    if tc or desc:
                        analysis_lines.append(f"**荣格类型**: {tc} - {desc}")
                archetype = analysis.get("archetype")
                if archetype and isinstance(archetype, dict):
                    prim = archetype.get("primary", "")
                    sec = archetype.get("secondary", "")
                    if prim or sec:
                        analysis_lines.append(f"**主/次原型**: {prim} / {sec}")
                b5 = analysis.get("bigFive")
                if b5 and isinstance(b5, dict):
                    for k, v in b5.items():
                        if v:
                            analysis_lines.append(f"**{k}**: {v}")
            extra = "\n".join(analysis_lines) if analysis_lines else ""

            analysis_block = f"""
### SHANG分析 #{record_id[-8:] if record_id else '?'} ({now_str})
**记录ID**: {record_id}
**命题主题**: {topic}
**选择**: {choice}（图A 或 图B）
**图A特征**: {desc_a[:150]}{"..." if len(desc_a) > 150 else ""}
**图B特征**: {desc_b[:150]}{"..." if len(desc_b) > 150 else ""}
**归因**: {attribution[:300]}{"..." if len(attribution) > 300 else ""}
{f"{chr(10)}{extra}{chr(10)}" if extra else ""}
---
"""
            # Write to daily notes in SQLite
            self._repo.append_daily_note(
                content=analysis_block,
                note_date=record_date,
                scope="mirror-shang",
            )

            # Append to memory in SQLite
            summary_line = f"赏 #{record_id[-8:] if record_id else '?'} 主题:{topic} 选择:{choice}"
            self._repo.append_memory(
                content=summary_line,
                scope="mirror-shang",
                source_type="shang_analysis",
                source_id=record_id,
            )

            logger.info(f"Shang record written to memory for {record_id}")
        except Exception as e:
            logger.error(f"Failed to write shang record to memory: {e}")

    # ---------- Profile (吾) ----------

    def get_fusion_data(self) -> dict[str, Any]:
        """汇总悟/辩/赏数据供镜融合使用。"""
        wu_summary = self._load_module_summary("wu")
        bian_summary = self._load_module_summary("bian")
        shang_summary = self._load_shang_summary()
        return {
            "wu_sessions_summary": wu_summary.get("sessions", "（暂无封存悟会话）"),
            "wu_insights": wu_summary.get("insights", ""),
            "wu_count": wu_summary.get("count", 0),
            "bian_sessions_summary": bian_summary.get("sessions", "（暂无封存辩会话）"),
            "bian_insights": bian_summary.get("insights", ""),
            "bian_count": bian_summary.get("count", 0),
            "shang_records_summary": shang_summary.get("records", "（暂无赏记录）"),
            "shang_insights": shang_summary.get("insights", ""),
            "shang_count": shang_summary.get("count", 0),
        }

    def _load_module_summary(self, stype: str) -> dict[str, Any]:
        """加载悟或辩的汇总数据。"""
        scope = f"mirror-{stype}"

        # Get daily notes
        from datetime import timedelta
        sessions_text: list[str] = []
        insights: list[str] = []

        today = date.today()
        for i in range(30):  # Last 30 days
            check_date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            note = self._repo.get_daily_note(note_date=check_date, scope=scope)
            if note:
                sessions_text.append(f"### {check_date}\n{note[:3000]}")
                # Extract insights
                for line in note.split("\n"):
                    if "核心洞察" in line or "core_insight" in line.lower():
                        parts = line.split(":", 1)
                        if len(parts) > 1 and parts[1].strip():
                            insights.append(parts[1].strip())

        # Get memory entries
        memories = self._repo.get_memories(scope=scope, limit=50)
        if memories:
            mem_content = "\n".join([f"- [{m['entry_date']}] {m['content']}" for m in memories[:20]])
            sessions_text.append(f"### MEMORY\n{mem_content[:1500]}")

        text = "\n\n".join(sessions_text) if sessions_text else "（暂无数据）"
        return {"sessions": text, "insights": "；".join(insights[:5]), "count": len(sessions_text)}

    def _load_shang_summary(self) -> dict[str, Any]:
        """加载赏记录汇总。"""
        records_data = self._repo.list_shang_records(page=1, page_size=100)
        items = records_data.get("items", [])
        done = [r for r in items if r.get("status") == "done"]

        lines = []
        for r in done[-20:]:
            topic = r.get("topic", "")
            choice = r.get("choice", "")
            attribution = (r.get("attribution") or "")[:200]
            lines.append(f"- 主题:{topic} 选择:{choice} 归因:{attribution}")

        text = "\n".join(lines) if lines else "（暂无赏记录）"
        return {"records": text, "insights": "", "count": len(done)}

    def save_profile(self, profile: dict[str, Any]) -> None:
        """保存 profile 到 SQLite 并追加快照。"""
        profile["updateTime"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._repo.save_mirror_profile(profile)
        logger.info("Mirror profile saved to SQLite")

    def get_profile(self) -> dict[str, Any] | None:
        """Get the current mirror profile from SQLite."""
        # 优先从主表获取
        profile = self._repo.get_mirror_profile()
        if profile:
            return profile
        # 降级：从快照表获取最新
        return self._repo.get_latest_mirror_profile_snapshot()
