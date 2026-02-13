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

        self._save_shang_record(record)
        return record

    def regenerate_shang_images(
        self,
        record_id: str,
        dashscope_api_key: str | None = None,
        qwen_image_model: str = "qwen-image-plus",
    ) -> dict[str, Any] | None:
        """重新生成指定 record 的 A/B 图片。仅 status=choosing 时可用。返回更新后的 record 或 None。"""
        records = self._load_shang_records()
        for i, r in enumerate(records):
            if r["id"] == record_id and r.get("status") == "choosing":
                topic = r.get("topic", "内在力量")
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
                if image_a_url is not None:
                    r["imageA"] = image_a_url
                if image_b_url is not None:
                    r["imageB"] = image_b_url
                records[i] = r
                self._save_all_shang_records(records)
                return r
        return None

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
        md_dir = self._mirror_dir / stype
        sessions_text: list[str] = []
        insights: list[str] = []
        for path in sorted(md_dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            try:
                content = path.read_text(encoding="utf-8")
                sessions_text.append(f"### {path.stem}\n{content[:3000]}")
                for line in content.split("\n"):
                    if "核心洞察" in line or "core_insight" in line.lower():
                        parts = line.split(":", 1)
                        if len(parts) > 1 and parts[1].strip():
                            insights.append(parts[1].strip())
            except (IOError, UnicodeError):
                continue
        memory_file = md_dir / "MEMORY.md"
        if memory_file.exists():
            try:
                mem = memory_file.read_text(encoding="utf-8")
                sessions_text.append(f"### MEMORY\n{mem[:1500]}")
            except (IOError, UnicodeError):
                pass
        text = "\n\n".join(sessions_text) if sessions_text else "（暂无数据）"
        return {"sessions": text, "insights": "；".join(insights[:5]), "count": len(sessions_text)}

    def _load_shang_summary(self) -> dict[str, Any]:
        """加载赏记录汇总。"""
        records = self._load_shang_records()
        done = [r for r in records if r.get("status") == "done"]
        lines = []
        for r in done[-20:]:
            topic = r.get("topic", "")
            choice = r.get("choice", "")
            attribution = (r.get("attribution") or "")[:200]
            lines.append(f"- 主题:{topic} 选择:{choice} 归因:{attribution}")
        text = "\n".join(lines) if lines else "（暂无赏记录）"
        return {"records": text, "insights": "", "count": len(done)}

    def save_profile(self, profile: dict[str, Any]) -> None:
        """保存 profile 到 profile.json 并追加快照。"""
        from datetime import datetime

        profile["updateTime"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        profile_file = self._mirror_dir / "profile.json"
        profile_file.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        snap_dir = self._mirror_dir / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_file = snap_dir / f"{datetime.now().strftime('%Y-%m-%d')}.json"
        snap_file.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Mirror profile saved to %s", profile_file)

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
