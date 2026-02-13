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
    
    DEFAULT_MAX_MESSAGE_LENGTH = 8000

    def add_message(self, role: str, content: str, max_length: int | None = None, **kwargs: Any) -> None:
        """Add a message to the session.
        
        Args:
            role: Message role (user/assistant/system).
            content: Message content.
            max_length: Maximum content length. Defaults to DEFAULT_MAX_MESSAGE_LENGTH.
            **kwargs: Additional metadata.
        """
        max_len = max_length or self.DEFAULT_MAX_MESSAGE_LENGTH
        if len(content) > max_len:
            content = content[:max_len] + f"\n... [截断，原长度 {len(content)} 字符]"
        
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
    
    DEFAULT_CACHE_MAX = 500

    def __init__(self, workspace: Path, max_cache_size: int | None = None):
        self.workspace = workspace
        data_dir = ensure_dir(Path.home() / ".nanobot")
        self.db_path = data_dir / "chat.db"
        self._cache: OrderedDict[str, Session] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._max_cache_size = max_cache_size or self.DEFAULT_CACHE_MAX
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

                CREATE TABLE IF NOT EXISTS chat_token_totals (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_session_token_totals (
                    session_key TEXT PRIMARY KEY,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_key) REFERENCES chat_sessions(key) ON DELETE CASCADE
                );
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
        while len(self._cache) > self._max_cache_size:
            evicted_key, _ = self._cache.popitem(last=False)
            self._locks_lock.acquire()
            try:
                if evicted_key in self._locks:
                    del self._locks[evicted_key]
            finally:
                self._locks_lock.release()
    
    def set_max_cache_size(self, size: int) -> None:
        """Set max cache size at runtime. Triggers eviction if needed."""
        self._max_cache_size = max(1, size)
        self._evict_if_needed()
    
    @property
    def cache_size(self) -> int:
        """Current number of cached sessions."""
        return len(self._cache)
    
    @property
    def max_cache_size(self) -> int:
        """Current max cache size setting."""
        return self._max_cache_size

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
    
    def list_sessions(self, key_prefix: str | None = None) -> list[dict[str, Any]]:
        """
        List all sessions, optionally filtered by key prefix.

        Args:
            key_prefix: If set, only return sessions whose key starts with this prefix.

        Returns:
            List of session info dicts.
        """
        with self._connect() as conn:
            if key_prefix:
                rows = conn.execute(
                    """
                    SELECT s.key, s.created_at, s.updated_at, s.metadata_json, COUNT(m.id) AS message_count
                    FROM chat_sessions s
                    LEFT JOIN chat_messages m ON m.session_key = s.key
                    WHERE s.key LIKE ? || '%%'
                    GROUP BY s.key, s.created_at, s.updated_at, s.metadata_json
                    ORDER BY s.updated_at DESC
                    """,
                    (key_prefix,),
                ).fetchall()
            else:
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

    def increment_token_usage(
        self,
        session_key: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        """Increment global and per-session token usage counters."""
        safe_prompt = max(0, int(prompt_tokens))
        safe_completion = max(0, int(completion_tokens))
        safe_total = max(0, int(total_tokens))
        if safe_prompt == 0 and safe_completion == 0 and safe_total == 0:
            return

        updated_at = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_token_totals (id, prompt_tokens, completion_tokens, total_tokens, updated_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens,
                    total_tokens = total_tokens + excluded.total_tokens,
                    updated_at = excluded.updated_at
                """,
                (safe_prompt, safe_completion, safe_total, updated_at),
            )
            conn.execute(
                """
                INSERT INTO chat_session_token_totals (
                    session_key, prompt_tokens, completion_tokens, total_tokens, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens,
                    total_tokens = total_tokens + excluded.total_tokens,
                    updated_at = excluded.updated_at
                """,
                (session_key, safe_prompt, safe_completion, safe_total, updated_at),
            )

    def get_session_token_usage(self, key: str) -> dict[str, int]:
        """Get cumulative token usage for one session."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT prompt_tokens, completion_tokens, total_tokens
                FROM chat_session_token_totals
                WHERE session_key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return {
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "total_tokens": int(row["total_tokens"]),
        }

    def get_global_token_usage(self) -> dict[str, int]:
        """Get cumulative token usage across all sessions."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT prompt_tokens, completion_tokens, total_tokens
                FROM chat_token_totals
                WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return {
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "total_tokens": int(row["total_tokens"]),
        }

    def reset_session_token_usage(self, key: str) -> bool:
        """Reset token usage for one session."""
        updated_at = datetime.now().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO chat_session_token_totals (
                    session_key, prompt_tokens, completion_tokens, total_tokens, updated_at
                )
                VALUES (?, 0, 0, 0, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    prompt_tokens = 0,
                    completion_tokens = 0,
                    total_tokens = 0,
                    updated_at = excluded.updated_at
                """,
                (key, updated_at),
            )
        return cursor.rowcount > 0

    def reset_global_token_usage(self) -> None:
        """Reset token usage for all sessions and global totals."""
        updated_at = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_token_totals (id, prompt_tokens, completion_tokens, total_tokens, updated_at)
                VALUES (1, 0, 0, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    prompt_tokens = 0,
                    completion_tokens = 0,
                    total_tokens = 0,
                    updated_at = excluded.updated_at
                """,
                (updated_at,),
            )
            conn.execute(
                """
                UPDATE chat_session_token_totals
                SET prompt_tokens = 0,
                    completion_tokens = 0,
                    total_tokens = 0,
                    updated_at = ?
                """,
                (updated_at,),
            )
