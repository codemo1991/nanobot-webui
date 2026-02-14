"""Memory repository for SQLite-based memory storage with FTS5 full-text search."""

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


# Migration tracking
MIGRATION_KEY = "memory_migration_v1"


def _entry_size(d: str, c: str) -> int:
    """Calculate entry size for limit checking."""
    return len(d) + len(c) + 20


def parse_memory_entries_with_dates(text: str) -> list[tuple[str, str]]:
    """Parse memory text into (date_str, content) tuples."""
    if not text or not text.strip():
        return []
    result = []
    pattern = r"^\s*-\s*\[([\d\-:\s]+)\]\s*(.+?)(?=\n\s*-\s*\[|\n#|\Z)"
    for m in re.finditer(pattern, text, re.DOTALL | re.MULTILINE):
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


def entries_to_text_preserve_dates(entries_with_dates: list[tuple[str, str]]) -> str:
    """Convert (date, content) list to MEMORY.md format."""
    if not entries_with_dates:
        return "# Long-term Memory\n\n"
    lines = ["# Long-term Memory"]
    for date_part, content in entries_with_dates:
        lines.append(f"\n- [{date_part}] {content}")
    return "\n".join(lines) + "\n"


class MemoryRepository:
    """SQLite-based memory storage with FTS5 full-text search."""

    _instance: "MemoryRepository | None" = None
    _lock = threading.Lock()

    def __new__(cls, db_path: Path | None = None):
        """Singleton pattern to ensure single repository instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: Path | None = None):
        if self._initialized:
            return

        if db_path is None:
            db_path = Path.home() / ".nanobot" / "chat.db"

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_tables()
        self._migrate_from_files()
        self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        """Create database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        """Initialize database tables."""
        try:
            with self._connect() as conn:
                conn.executescript(
                    """
                    PRAGMA foreign_keys = ON;

                    -- Main memory entries table
                    CREATE TABLE IF NOT EXISTS memory_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        agent_id TEXT,
                        scope TEXT DEFAULT 'global',
                        content TEXT NOT NULL,
                        source_type TEXT,
                        source_id TEXT,
                        entry_date TEXT,
                        entry_time TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    -- Daily notes table
                    CREATE TABLE IF NOT EXISTS daily_notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        agent_id TEXT,
                        scope TEXT DEFAULT 'global',
                        note_date TEXT NOT NULL,
                        content TEXT NOT NULL,
                        is_processed INTEGER DEFAULT 0,
                        processed_at TEXT,
                        created_at TEXT NOT NULL
                    );

                    -- Mirror shang records table
                    CREATE TABLE IF NOT EXISTS mirror_shang_records (
                        id TEXT PRIMARY KEY,
                        record_date TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        image_a_url TEXT,
                        image_b_url TEXT,
                        description_a TEXT,
                        description_b TEXT,
                        choice TEXT,
                        attribution TEXT,
                        analysis_json TEXT,
                        status TEXT DEFAULT 'choosing',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    -- Mirror profile (吾) table
                    CREATE TABLE IF NOT EXISTS mirror_profiles (
                        id INTEGER PRIMARY KEY CHECK(id = 1),
                        profile_json TEXT NOT NULL,
                        update_time TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    -- Mirror profile snapshots table
                    CREATE TABLE IF NOT EXISTS mirror_profile_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        snapshot_date TEXT NOT NULL,
                        profile_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );

                    -- Migration tracking
                    CREATE TABLE IF NOT EXISTS memory_migration_status (
                        key TEXT PRIMARY KEY,
                        migrated_at TEXT NOT NULL,
                        details TEXT
                    );

                    -- Indexes
                    CREATE INDEX IF NOT EXISTS idx_memory_scope 
                        ON memory_entries(scope, agent_id);
                    CREATE INDEX IF NOT EXISTS idx_memory_date 
                        ON memory_entries(entry_date DESC);
                    CREATE INDEX IF NOT EXISTS idx_memory_created 
                        ON memory_entries(created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_memory_source 
                        ON memory_entries(source_type, source_id);
                    CREATE INDEX IF NOT EXISTS idx_daily_notes_scope 
                        ON daily_notes(scope, agent_id, note_date);
                    CREATE INDEX IF NOT EXISTS idx_daily_notes_processed 
                        ON daily_notes(is_processed, note_date);
                    CREATE INDEX IF NOT EXISTS idx_shang_date 
                        ON mirror_shang_records(record_date DESC);
                    CREATE INDEX IF NOT EXISTS idx_shang_status 
                        ON mirror_shang_records(status);
                    CREATE INDEX IF NOT EXISTS idx_profile_snapshots_date 
                        ON mirror_profile_snapshots(snapshot_date DESC);
                    """
                )

                # Check if FTS5 is available and create virtual table
                try:
                    conn.execute(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                            content,
                            content_rowid=id,
                            tokenize='porter unicode61'
                        )
                        """
                    )
                    self._fts5_available = True
                except sqlite3.OperationalError:
                    logger.warning("FTS5 not available, falling back to LIKE search")
                    self._fts5_available = False

            logger.debug("Memory tables initialized")
        except Exception as e:
            logger.exception("Failed to initialize memory tables")
            raise

    # ========== Long-term Memory Operations ==========

    def append_memory(
        self,
        content: str,
        agent_id: str | None = None,
        scope: str = "global",
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> int:
        """Append a single memory entry."""
        now = datetime.now()
        entry_date = now.strftime("%Y-%m-%d")
        entry_time = now.strftime("%H:%M")
        created_at = now.isoformat()

        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO memory_entries 
                    (agent_id, scope, content, source_type, source_id, entry_date, entry_time, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        agent_id,
                        scope,
                        content,
                        source_type,
                        source_id,
                        entry_date,
                        entry_time,
                        created_at,
                        created_at,
                    ),
                )
                memory_id = cursor.lastrowid

                # Update FTS index if available
                if self._fts5_available:
                    conn.execute(
                        "INSERT INTO memory_fts (rowid, content) VALUES (?, ?)",
                        (memory_id, content),
                    )

                return memory_id
        except Exception as e:
            logger.exception(f"Failed to append memory: {e}")
            raise

    def append_memories(
        self,
        entries: list[tuple[str, str]],
        agent_id: str | None = None,
        scope: str = "global",
        source_type: str | None = None,
    ) -> None:
        """Batch append memory entries (date_str, content) list."""
        if not entries:
            return

        now = datetime.now()
        created_at = now.isoformat()

        try:
            with self._connect() as conn:
                for date_str, content in entries:
                    if not content or not content.strip():
                        continue

                    # Parse date and time from date_str
                    parts = date_str.split(" ", 1)
                    entry_date = parts[0] if len(parts) > 0 else now.strftime("%Y-%m-%d")
                    entry_time = parts[1] if len(parts) > 1 else now.strftime("%H:%M")

                    cursor = conn.execute(
                        """
                        INSERT INTO memory_entries 
                        (agent_id, scope, content, source_type, entry_date, entry_time, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            agent_id,
                            scope,
                            content.strip(),
                            source_type,
                            entry_date,
                            entry_time,
                            created_at,
                            created_at,
                        ),
                    )

                    if self._fts5_available:
                        conn.execute(
                            "INSERT INTO memory_fts (rowid, content) VALUES (?, ?)",
                            (cursor.lastrowid, content.strip()),
                        )
        except Exception as e:
            logger.exception(f"Failed to append memories: {e}")
            raise

    def get_memories(
        self,
        agent_id: str | None = None,
        scope: str = "global",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get memory entries."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, agent_id, scope, content, source_type, source_id, 
                           entry_date, entry_time, created_at
                    FROM memory_entries
                    WHERE scope = ? AND (agent_id = ? OR (? IS NULL AND agent_id IS NULL))
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (scope, agent_id, agent_id, limit, offset),
                ).fetchall()

                return [
                    {
                        "id": row["id"],
                        "agent_id": row["agent_id"],
                        "scope": row["scope"],
                        "content": row["content"],
                        "source_type": row["source_type"],
                        "source_id": row["source_id"],
                        "entry_date": row["entry_date"],
                        "entry_time": row["entry_time"],
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.exception(f"Failed to get memories: {e}")
            return []

    def get_memory_entries_count(
        self, agent_id: str | None = None, scope: str = "global"
    ) -> int:
        """Get memory entry count (for summarize check)."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT COUNT(*) as cnt FROM memory_entries
                    WHERE scope = ? AND (agent_id = ? OR (? IS NULL AND agent_id IS NULL))
                    """,
                    (scope, agent_id, agent_id),
                ).fetchone()
                return row["cnt"] if row else 0
        except Exception as e:
            logger.exception(f"Failed to get memory count: {e}")
            return 0

    def get_memories_char_count(
        self, agent_id: str | None = None, scope: str = "global"
    ) -> int:
        """Get total character count (for summarize check)."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT SUM(LENGTH(content) + LENGTH(entry_date) + LENGTH(entry_time) + 20) as total
                    FROM memory_entries
                    WHERE scope = ? AND (agent_id = ? OR (? IS NULL AND agent_id IS NULL))
                    """,
                    (scope, agent_id, agent_id),
                ).fetchone()
                return row["total"] or 0
        except Exception as e:
            logger.exception(f"Failed to get memory char count: {e}")
            return 0

    def get_memories_for_summarize(
        self, agent_id: str | None = None, scope: str = "global"
    ) -> list[tuple[str, str]]:
        """Get memories as (date, content) list for summarization."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT entry_date, entry_time, content
                    FROM memory_entries
                    WHERE scope = ? AND (agent_id = ? OR (? IS NULL AND agent_id IS NULL))
                    ORDER BY created_at ASC
                    """,
                    (scope, agent_id, agent_id),
                ).fetchall()

                return [
                    (
                        f"{row['entry_date']} {row['entry_time']}",
                        row["content"],
                    )
                    for row in rows
                ]
        except Exception as e:
            logger.exception(f"Failed to get memories for summarize: {e}")
            return []

    def replace_memories(
        self,
        entries: list[tuple[str, str]],
        agent_id: str | None = None,
        scope: str = "global",
    ) -> None:
        """Replace all memories (used after LLM summarization)."""
        try:
            with self._connect() as conn:
                # Get IDs to delete from FTS
                if self._fts5_available:
                    id_rows = conn.execute(
                        "SELECT id FROM memory_entries WHERE scope = ? AND (agent_id = ? OR (? IS NULL AND agent_id IS NULL))",
                        (scope, agent_id, agent_id),
                    ).fetchall()
                    ids_to_delete = [row["id"] for row in id_rows]

                # Delete old entries
                conn.execute(
                    "DELETE FROM memory_entries WHERE scope = ? AND (agent_id = ? OR (? IS NULL AND agent_id IS NULL))",
                    (scope, agent_id, agent_id),
                )

                # Delete from FTS
                if self._fts5_available and ids_to_delete:
                    for memory_id in ids_to_delete:
                        conn.execute(
                            "DELETE FROM memory_fts WHERE rowid = ?", (memory_id,)
                        )

                # Insert new entries
                now = datetime.now()
                created_at = now.isoformat()

                for date_str, content in entries:
                    if not content or not content.strip():
                        continue

                    parts = date_str.split(" ", 1)
                    entry_date = parts[0] if len(parts) > 0 else now.strftime("%Y-%m-%d")
                    entry_time = parts[1] if len(parts) > 1 else now.strftime("%H:%M")

                    cursor = conn.execute(
                        """
                        INSERT INTO memory_entries 
                        (agent_id, scope, content, entry_date, entry_time, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            agent_id,
                            scope,
                            content.strip(),
                            entry_date,
                            entry_time,
                            created_at,
                            created_at,
                        ),
                    )

                    if self._fts5_available:
                        conn.execute(
                            "INSERT INTO memory_fts (rowid, content) VALUES (?, ?)",
                            (cursor.lastrowid, content.strip()),
                        )

                logger.info(f"Replaced memories with {len(entries)} entries")
        except Exception as e:
            logger.exception(f"Failed to replace memories: {e}")
            raise

    def search_memories(
        self, query: str, scope: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search memories using FTS5 or fallback to LIKE."""
        try:
            with self._connect() as conn:
                if self._fts5_available:
                    if scope:
                        rows = conn.execute(
                            """
                            SELECT m.id, m.agent_id, m.scope, m.content, m.entry_date, m.entry_time
                            FROM memory_entries m
                            JOIN memory_fts fts ON m.id = fts.rowid
                            WHERE memory_fts MATCH ? AND m.scope = ?
                            ORDER BY rank
                            LIMIT ?
                            """,
                            (query, scope, limit),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            """
                            SELECT m.id, m.agent_id, m.scope, m.content, m.entry_date, m.entry_time
                            FROM memory_entries m
                            JOIN memory_fts fts ON m.id = fts.rowid
                            WHERE memory_fts MATCH ?
                            ORDER BY rank
                            LIMIT ?
                            """,
                            (query, limit),
                        ).fetchall()
                else:
                    # Fallback to LIKE search
                    like_query = f"%{query}%"
                    if scope:
                        rows = conn.execute(
                            """
                            SELECT id, agent_id, scope, content, entry_date, entry_time
                            FROM memory_entries
                            WHERE content LIKE ? AND scope = ?
                            ORDER BY created_at DESC
                            LIMIT ?
                            """,
                            (like_query, scope, limit),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            """
                            SELECT id, agent_id, scope, content, entry_date, entry_time
                            FROM memory_entries
                            WHERE content LIKE ?
                            ORDER BY created_at DESC
                            LIMIT ?
                            """,
                            (like_query, limit),
                        ).fetchall()

                return [
                    {
                        "id": row["id"],
                        "agent_id": row["agent_id"],
                        "scope": row["scope"],
                        "content": row["content"],
                        "entry_date": row["entry_date"],
                        "entry_time": row["entry_time"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.exception(f"Failed to search memories: {e}")
            return []

    # ========== Daily Notes Operations ==========

    def append_daily_note(
        self,
        content: str,
        note_date: str | None = None,
        agent_id: str | None = None,
        scope: str = "global",
    ) -> None:
        """Append to daily note."""
        if note_date is None:
            note_date = datetime.now().strftime("%Y-%m-%d")

        created_at = datetime.now().isoformat()

        try:
            with self._connect() as conn:
                # Check if note exists
                existing = conn.execute(
                    """
                    SELECT id, content FROM daily_notes
                    WHERE note_date = ? AND scope = ? AND (agent_id = ? OR (? IS NULL AND agent_id IS NULL))
                    """,
                    (note_date, scope, agent_id, agent_id),
                ).fetchone()

                if existing:
                    # Append to existing
                    new_content = existing["content"] + "\n" + content
                    conn.execute(
                        """
                        UPDATE daily_notes 
                        SET content = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (new_content, created_at, existing["id"]),
                    )
                else:
                    # Create new
                    conn.execute(
                        """
                        INSERT INTO daily_notes (agent_id, scope, note_date, content, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (agent_id, scope, note_date, content, created_at),
                    )
        except Exception as e:
            logger.exception(f"Failed to append daily note: {e}")
            raise

    def get_daily_note(
        self, note_date: str, agent_id: str | None = None, scope: str = "global"
    ) -> str:
        """Get daily note content."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT content FROM daily_notes
                    WHERE note_date = ? AND scope = ? AND (agent_id = ? OR (? IS NULL AND agent_id IS NULL))
                    """,
                    (note_date, scope, agent_id, agent_id),
                ).fetchone()
                return row["content"] if row else ""
        except Exception as e:
            logger.exception(f"Failed to get daily note: {e}")
            return ""

    def get_unprocessed_daily_notes(
        self,
        before_date: str,
        agent_id: str | None = None,
        scope: str = "global",
    ) -> list[dict[str, Any]]:
        """Get unprocessed daily notes (for daily merge)."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, note_date, content, created_at
                    FROM daily_notes
                    WHERE note_date < ? AND is_processed = 0 
                          AND scope = ? AND (agent_id = ? OR (? IS NULL AND agent_id IS NULL))
                    ORDER BY note_date ASC
                    """,
                    (before_date, scope, agent_id, agent_id),
                ).fetchall()

                return [
                    {
                        "id": row["id"],
                        "note_date": row["note_date"],
                        "content": row["content"],
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.exception(f"Failed to get unprocessed daily notes: {e}")
            return []

    def mark_daily_note_processed(
        self, note_date: str, agent_id: str | None = None, scope: str = "global"
    ) -> None:
        """Mark daily note as processed."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE daily_notes
                    SET is_processed = 1, processed_at = ?
                    WHERE note_date = ? AND scope = ? AND (agent_id = ? OR (? IS NULL AND agent_id IS NULL))
                    """,
                    (datetime.now().isoformat(), note_date, scope, agent_id, agent_id),
                )
        except Exception as e:
            logger.exception(f"Failed to mark daily note processed: {e}")
            raise

    # ========== Mirror Shang Records Operations ==========

    def save_shang_record(self, record: dict[str, Any]) -> None:
        """Save or update shang record."""
        now = datetime.now().isoformat()

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO mirror_shang_records 
                    (id, record_date, topic, image_a_url, image_b_url, description_a, description_b,
                     choice, attribution, analysis_json, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        record_date = excluded.record_date,
                        topic = excluded.topic,
                        image_a_url = excluded.image_a_url,
                        image_b_url = excluded.image_b_url,
                        description_a = excluded.description_a,
                        description_b = excluded.description_b,
                        choice = excluded.choice,
                        attribution = excluded.attribution,
                        analysis_json = excluded.analysis_json,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (
                        record.get("id"),
                        record.get("date"),
                        record.get("topic"),
                        record.get("imageA"),
                        record.get("imageB"),
                        record.get("descriptionA"),
                        record.get("descriptionB"),
                        record.get("choice"),
                        record.get("attribution"),
                        json.dumps(record.get("analysis")) if record.get("analysis") else None,
                        record.get("status", "choosing"),
                        record.get("created_at", now),
                        now,
                    ),
                )
        except Exception as e:
            logger.exception(f"Failed to save shang record: {e}")
            raise

    def get_shang_record(self, record_id: str) -> dict[str, Any] | None:
        """Get shang record by ID."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM mirror_shang_records WHERE id = ?",
                    (record_id,),
                ).fetchone()

                if not row:
                    return None

                return self._row_to_shang_record(row)
        except Exception as e:
            logger.exception(f"Failed to get shang record: {e}")
            return None

    def list_shang_records(
        self,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List shang records with pagination."""
        try:
            with self._connect() as conn:
                # Get total count
                if status:
                    count_row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM mirror_shang_records WHERE status = ?",
                        (status,),
                    ).fetchone()
                    total = count_row["cnt"]

                    rows = conn.execute(
                        """
                        SELECT * FROM mirror_shang_records 
                        WHERE status = ?
                        ORDER BY record_date DESC
                        LIMIT ? OFFSET ?
                        """,
                        (status, page_size, (page - 1) * page_size),
                    ).fetchall()
                else:
                    count_row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM mirror_shang_records"
                    ).fetchone()
                    total = count_row["cnt"]

                    rows = conn.execute(
                        """
                        SELECT * FROM mirror_shang_records 
                        ORDER BY record_date DESC
                        LIMIT ? OFFSET ?
                        """,
                        (page_size, (page - 1) * page_size),
                    ).fetchall()

                return {
                    "items": [self._row_to_shang_record(row) for row in rows],
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                }
        except Exception as e:
            logger.exception(f"Failed to list shang records: {e}")
            return {"items": [], "total": 0, "page": page, "page_size": page_size}

    def update_shang_record(self, record_id: str, updates: dict[str, Any]) -> bool:
        """Update shang record fields."""
        if not updates:
            return False

        allowed_fields = {
            "choice",
            "attribution",
            "analysis",
            "status",
            "imageA",
            "imageB",
        }

        # Map field names
        field_mapping = {
            "choice": "choice",
            "attribution": "attribution",
            "analysis": "analysis_json",
            "status": "status",
            "imageA": "image_a_url",
            "imageB": "image_b_url",
        }

        set_clauses = []
        values = []

        for key, value in updates.items():
            if key in allowed_fields:
                db_field = field_mapping[key]
                if key == "analysis":
                    value = json.dumps(value)
                set_clauses.append(f"{db_field} = ?")
                values.append(value)

        if not set_clauses:
            return False

        set_clauses.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(record_id)

        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    f"""
                    UPDATE mirror_shang_records
                    SET {', '.join(set_clauses)}
                    WHERE id = ?
                    """,
                    values,
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception(f"Failed to update shang record: {e}")
            return False

    def delete_shang_record(self, record_id: str) -> bool:
        """Delete shang record."""
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM mirror_shang_records WHERE id = ?",
                    (record_id,),
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception(f"Failed to delete shang record: {e}")
            return False

    def _row_to_shang_record(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert database row to shang record dict."""
        record = {
            "id": row["id"],
            "date": row["record_date"],
            "topic": row["topic"],
            "imageA": row["image_a_url"],
            "imageB": row["image_b_url"],
            "descriptionA": row["description_a"],
            "descriptionB": row["description_b"],
            "choice": row["choice"],
            "attribution": row["attribution"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

        if row["analysis_json"]:
            try:
                record["analysis"] = json.loads(row["analysis_json"])
            except json.JSONDecodeError:
                record["analysis"] = None

        return record

    # ========== Mirror Profile (吾) Operations ==========

    def save_mirror_profile(self, profile: dict[str, Any]) -> None:
        """Save mirror profile (吾)."""
        now = datetime.now()
        created_at = now.isoformat()
        update_time = profile.get("updateTime", now.strftime("%Y-%m-%d %H:%M"))

        try:
            with self._connect() as conn:
                # Check if profile exists
                existing = conn.execute(
                    "SELECT created_at FROM mirror_profiles WHERE id = 1"
                ).fetchone()

                if existing:
                    # Update
                    conn.execute(
                        """
                        UPDATE mirror_profiles
                        SET profile_json = ?, update_time = ?, updated_at = ?
                        WHERE id = 1
                        """,
                        (json.dumps(profile, ensure_ascii=False), update_time, created_at),
                    )
                else:
                    # Insert
                    conn.execute(
                        """
                        INSERT INTO mirror_profiles (id, profile_json, update_time, created_at, updated_at)
                        VALUES (1, ?, ?, ?, ?)
                        """,
                        (json.dumps(profile, ensure_ascii=False), update_time, created_at, created_at),
                    )

                # Also save as snapshot
                snapshot_date = now.strftime("%Y-%m-%d")
                conn.execute(
                    """
                    INSERT INTO mirror_profile_snapshots (snapshot_date, profile_json, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot_date, json.dumps(profile, ensure_ascii=False), created_at),
                )

                logger.info("Mirror profile saved to SQLite")
        except Exception as e:
            logger.exception(f"Failed to save mirror profile: {e}")
            raise

    def get_mirror_profile(self) -> dict[str, Any] | None:
        """Get current mirror profile."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT profile_json FROM mirror_profiles WHERE id = 1"
                ).fetchone()

                if row and row["profile_json"]:
                    return json.loads(row["profile_json"])
                return None
        except Exception as e:
            logger.exception(f"Failed to get mirror profile: {e}")
            return None

    def get_latest_mirror_profile_snapshot(self) -> dict[str, Any] | None:
        """Get latest mirror profile snapshot."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT profile_json FROM mirror_profile_snapshots
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ).fetchone()

                if row and row["profile_json"]:
                    return json.loads(row["profile_json"])
                return None
        except Exception as e:
            logger.exception(f"Failed to get mirror profile snapshot: {e}")
            return None

    def list_mirror_profile_snapshots(self, limit: int = 30) -> list[dict[str, Any]]:
        """List mirror profile snapshots."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT snapshot_date, profile_json, created_at
                    FROM mirror_profile_snapshots
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

                return [
                    {
                        "snapshot_date": row["snapshot_date"],
                        "profile": json.loads(row["profile_json"]),
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.exception(f"Failed to list mirror profile snapshots: {e}")
            return []

    # ========== Data Migration ==========

    def _check_migration_done(self) -> bool:
        """Check if migration has been completed."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM memory_migration_status WHERE key = ?",
                    (MIGRATION_KEY,),
                ).fetchone()
                return row is not None
        except Exception:
            return False

    def _mark_migration_done(self) -> None:
        """Mark migration as completed."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO memory_migration_status (key, migrated_at, details)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        migrated_at = excluded.migrated_at,
                        details = excluded.details
                    """,
                    (MIGRATION_KEY, datetime.now().isoformat(), "Migrated from file system"),
                )
        except Exception as e:
            logger.exception(f"Failed to mark migration done: {e}")

    def _migrate_from_files(self) -> None:
        """Migrate data from old file-based storage."""
        if self._check_migration_done():
            return

        logger.info("Starting memory migration from files to SQLite...")

        try:
            # Discover workspace paths
            workspace = self._discover_workspace()
            if not workspace:
                logger.warning("No workspace found for migration")
                self._mark_migration_done()
                return

            # Migrate global memory
            self._migrate_scope_memory("global", workspace / "memory")

            # Migrate agent-specific memories
            agents_dir = workspace / "agents"
            if agents_dir.exists():
                for agent_dir in agents_dir.iterdir():
                    if agent_dir.is_dir():
                        agent_id = agent_dir.name
                        self._migrate_scope_memory(
                            "global", agent_dir / "memory", agent_id=agent_id
                        )

            # Migrate mirror memories
            mirror_dir = workspace / "mirror"
            if mirror_dir.exists():
                for stype in ["wu", "bian", "shang"]:
                    self._migrate_scope_memory(
                        f"mirror-{stype}", mirror_dir / stype
                    )

            # Migrate shang records
            self._migrate_shang_records(mirror_dir / "shang")

            # Migrate mirror profile (吾)
            self._migrate_mirror_profile(mirror_dir)

            self._mark_migration_done()
            logger.info("Memory migration completed successfully")

        except Exception as e:
            logger.exception(f"Memory migration failed: {e}")
            # Don't mark as done so it can retry

    def _discover_workspace(self) -> Path | None:
        """Discover workspace path from common locations."""
        # Try default location first
        default_path = Path.home() / ".nanobot" / "web-ui"
        if (default_path / "memory").exists():
            return default_path

        # Try to find any directory with memory folder
        nanobot_dir = Path.home() / ".nanobot"
        if nanobot_dir.exists():
            for subdir in nanobot_dir.iterdir():
                if subdir.is_dir() and (subdir / "memory").exists():
                    return subdir

        return None

    def _migrate_scope_memory(
        self, scope: str, memory_dir: Path, agent_id: str | None = None
    ) -> None:
        """Migrate memory files for a specific scope."""
        if not memory_dir.exists():
            return

        logger.info(f"Migrating {scope} memory (agent={agent_id}) from {memory_dir}")

        # Migrate MEMORY.md
        memory_file = memory_dir / "MEMORY.md"
        if memory_file.exists():
            try:
                content = memory_file.read_text(encoding="utf-8")
                entries = parse_memory_entries_with_dates(content)
                if entries:
                    self.append_memories(
                        entries, agent_id=agent_id, scope=scope, source_type="migrated"
                    )
                    logger.info(f"Migrated {len(entries)} entries from {memory_file}")

                # Backup original file
                backup_path = memory_file.with_suffix(".md.backup")
                memory_file.rename(backup_path)
                logger.info(f"Backed up {memory_file} to {backup_path}")
            except Exception as e:
                logger.warning(f"Failed to migrate {memory_file}: {e}")

        # Migrate daily notes
        for daily_file in memory_dir.glob("????-??-??.md"):
            try:
                note_date = daily_file.stem
                content = daily_file.read_text(encoding="utf-8")
                if content.strip():
                    self.append_daily_note(
                        content, note_date=note_date, agent_id=agent_id, scope=scope
                    )

                # Backup original file
                backup_path = daily_file.with_suffix(".md.backup")
                daily_file.rename(backup_path)
            except Exception as e:
                logger.warning(f"Failed to migrate {daily_file}: {e}")

    def _migrate_shang_records(self, shang_dir: Path) -> None:
        """Migrate shang records from JSON file."""
        if not shang_dir.exists():
            return

        records_file = shang_dir / "records.json"
        if not records_file.exists():
            return

        try:
            records = json.loads(records_file.read_text(encoding="utf-8"))
            for record in records:
                self.save_shang_record(record)

            logger.info(f"Migrated {len(records)} shang records")

            # Backup original file
            backup_path = records_file.with_suffix(".json.backup")
            records_file.rename(backup_path)
        except Exception as e:
            logger.warning(f"Failed to migrate shang records: {e}")

    def _migrate_mirror_profile(self, mirror_dir: Path) -> None:
        """Migrate mirror profile (吾) from JSON files."""
        if not mirror_dir.exists():
            return

        # Try to load from profile.json first
        profile_file = mirror_dir / "profile.json"
        if profile_file.exists():
            try:
                profile = json.loads(profile_file.read_text(encoding="utf-8"))
                self.save_mirror_profile(profile)
                logger.info("Migrated mirror profile from profile.json")

                # Backup original file
                backup_path = profile_file.with_suffix(".json.backup")
                profile_file.rename(backup_path)
                return
            except Exception as e:
                logger.warning(f"Failed to migrate profile.json: {e}")

        # Fallback: try to load from snapshots
        snapshots_dir = mirror_dir / "snapshots"
        if snapshots_dir.exists():
            try:
                snaps = sorted(
                    snapshots_dir.glob("*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )
                for snap in snaps:
                    try:
                        profile = json.loads(snap.read_text(encoding="utf-8"))
                        self.save_mirror_profile(profile)
                        logger.info(f"Migrated mirror profile from snapshot {snap.name}")
                        return
                    except (json.JSONDecodeError, IOError):
                        continue
            except Exception as e:
                logger.warning(f"Failed to migrate profile snapshots: {e}")


# Global repository instance
_memory_repository: MemoryRepository | None = None


def get_memory_repository(db_path: Path | None = None) -> MemoryRepository:
    """Get or create the global memory repository instance."""
    global _memory_repository
    if _memory_repository is None:
        _memory_repository = MemoryRepository(db_path)
    return _memory_repository


def reset_memory_repository() -> None:
    """Reset the global repository instance (for testing)."""
    global _memory_repository
    _memory_repository = None
    MemoryRepository._instance = None
