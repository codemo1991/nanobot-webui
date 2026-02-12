"""Session management for conversation history."""

import json
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir


@dataclass
class Session:
    """
    A conversation session.
    
    Stores messages in SQLite for robust querying and persistence.
    """
    
    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()
    
    def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
        """
        Get message history for LLM context.
        
        Args:
            max_messages: Maximum messages to return.
        
        Returns:
            List of messages in LLM format.
        """
        # Get recent messages
        recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
        
        # Convert to LLM format (just role and content)
        return [{"role": m["role"], "content": m["content"]} for m in recent]
    
    def clear(self) -> None:
        """Clear all messages in the session."""
        self.messages = []
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.
    
    Sessions are stored in a SQLite database.
    """
    
    # Max cached sessions; evict oldest when exceeded (LRU-like via OrderedDict)
    _CACHE_MAX = 500

    def __init__(self, workspace: Path):
        self.workspace = workspace
        data_dir = ensure_dir(Path.home() / ".nanobot")
        self.db_path = data_dir / "chat.db"
        self._cache: OrderedDict[str, Session] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._init_db()

    def _lock_for(self, key: str) -> threading.Lock:
        """Get or create a lock for the given session key."""
        with self._locks_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize database schema if needed."""
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS chat_sessions (
                    key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    extras_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(session_key) REFERENCES chat_sessions(key) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated_at
                    ON chat_sessions(updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_chat_messages_session_sequence
                    ON chat_messages(session_key, sequence);
                """
            )
    
    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.
        Thread-safe: uses per-key lock to avoid races for the same session.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        lock = self._lock_for(key)
        with lock:
            if key in self._cache:
                self._cache.move_to_end(key)  # LRU touch
                return self._cache[key]

            session = self._load(key)
            if session is None:
                session = Session(key=key)

            self._cache[key] = session
            self._evict_if_needed()
            return session

    def _evict_if_needed(self) -> None:
        """Evict oldest cache entries when over limit."""
        while len(self._cache) > self._CACHE_MAX:
            evicted_key, _ = self._cache.popitem(last=False)
            self._locks_lock.acquire()
            try:
                if evicted_key in self._locks:
                    del self._locks[evicted_key]
            finally:
                self._locks_lock.release()

    def get(self, key: str) -> Session | None:
        """Get an existing session without creating a new one."""
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        session = self._load(key)
        if session is not None:
            self._cache[key] = session
            self._evict_if_needed()
        return session
    
    def _load(self, key: str) -> Session | None:
        """Load a session from SQLite."""
        try:
            with self._connect() as conn:
                session_row = conn.execute(
                    """
                    SELECT key, created_at, updated_at, metadata_json
                    FROM chat_sessions
                    WHERE key = ?
                    """,
                    (key,),
                ).fetchone()

                if session_row is None:
                    return None

                message_rows = conn.execute(
                    """
                    SELECT role, content, timestamp, extras_json
                    FROM chat_messages
                    WHERE session_key = ?
                    ORDER BY sequence ASC
                    """,
                    (key,),
                ).fetchall()

            messages: list[dict[str, Any]] = []
            for row in message_rows:
                extras = json.loads(row["extras_json"]) if row["extras_json"] else {}
                msg: dict[str, Any] = {
                    "role": row["role"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                    **extras,
                }
                messages.append(msg)

            metadata = json.loads(session_row["metadata_json"]) if session_row["metadata_json"] else {}
            created_at = datetime.fromisoformat(session_row["created_at"])
            updated_at = datetime.fromisoformat(session_row["updated_at"])

            return Session(
                key=key,
                messages=messages,
                created_at=created_at,
                updated_at=updated_at,
                metadata=metadata,
            )
        except Exception as e:
            logger.warning(f"Failed to load session {key}: {e}", exc_info=True)
            return None
    
    def save(self, session: Session) -> None:
        """Save a session to SQLite. Thread-safe per session key."""
        session.updated_at = datetime.now()
        lock = self._lock_for(session.key)
        with lock:
            self._save_impl(session)

    def _save_impl(self, session: Session) -> None:
        """Internal save implementation (caller must hold lock)."""
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                INSERT INTO chat_sessions (key, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    session.key,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    json.dumps(session.metadata),
                ),
            )

            conn.execute("DELETE FROM chat_messages WHERE session_key = ?", (session.key,))

            for idx, msg in enumerate(session.messages, start=1):
                extras = {k: v for k, v in msg.items() if k not in {"role", "content", "timestamp"}}
                conn.execute(
                    """
                    INSERT INTO chat_messages (
                        session_key, role, content, timestamp, sequence, extras_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.key,
                        str(msg.get("role", "")),
                        str(msg.get("content", "")),
                        str(msg.get("timestamp", datetime.now().isoformat())),
                        idx,
                        json.dumps(extras),
                    ),
                )

        self._cache[session.key] = session
    
    def delete(self, key: str) -> bool:
        """
        Delete a session.
        
        Args:
            key: Session key.
        
        Returns:
            True if deleted, False if not found.
        """
        # Remove from cache
        self._cache.pop(key, None)
        
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM chat_sessions WHERE key = ?", (key,))
            return cursor.rowcount > 0
    
    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.
        
        Returns:
            List of session info dicts.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.key, s.created_at, s.updated_at, s.metadata_json, COUNT(m.id) AS message_count
                FROM chat_sessions s
                LEFT JOIN chat_messages m ON m.session_key = s.key
                GROUP BY s.key, s.created_at, s.updated_at, s.metadata_json
                ORDER BY s.updated_at DESC
                """
            ).fetchall()

        sessions: list[dict[str, Any]] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
            sessions.append(
                {
                    "key": row["key"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "message_count": int(row["message_count"]),
                    "metadata": metadata,
                }
            )
        return sessions

    def get_messages(
        self,
        key: str,
        limit: int = 50,
        before_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Read messages in ascending order with optional backward pagination.

        Args:
            key: Session key.
            limit: Maximum number of messages to return.
            before_sequence: If set, return messages with sequence < before_sequence.
        """
        safe_limit = max(1, min(limit, 200))

        with self._connect() as conn:
            if before_sequence is None:
                rows = conn.execute(
                    """
                    SELECT sequence, role, content, timestamp, extras_json
                    FROM chat_messages
                    WHERE session_key = ?
                    ORDER BY sequence DESC
                    LIMIT ?
                    """,
                    (key, safe_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT sequence, role, content, timestamp, extras_json
                    FROM chat_messages
                    WHERE session_key = ? AND sequence < ?
                    ORDER BY sequence DESC
                    LIMIT ?
                    """,
                    (key, before_sequence, safe_limit),
                ).fetchall()

        result: list[dict[str, Any]] = []
        for row in reversed(rows):
            extras = json.loads(row["extras_json"]) if row["extras_json"] else {}
            result.append(
                {
                    "sequence": int(row["sequence"]),
                    "role": row["role"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                    **extras,
                }
            )
        return result
