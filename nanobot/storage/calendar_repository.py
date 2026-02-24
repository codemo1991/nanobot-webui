"""Calendar repository for SQLite-based storage."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class CalendarRepository:
    """Repository for calendar data, using SQLite storage."""

    def __init__(self, db_path: Path):
        """
        Initialize calendar repository.

        Args:
            db_path: SQLite database file path
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _connect(self):
        """Create database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    _CREATE_SCHEMA = """
        CREATE TABLE IF NOT EXISTS calendar_events (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            is_all_day INTEGER NOT NULL DEFAULT 0,
            priority TEXT NOT NULL DEFAULT 'medium',
            reminders_json TEXT NOT NULL DEFAULT '[]',
            recurrence_json TEXT,
            recurrence_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_calendar_events_start ON calendar_events(start_time);
        CREATE INDEX IF NOT EXISTS idx_calendar_events_recurrence ON calendar_events(recurrence_id);

        CREATE TABLE IF NOT EXISTS calendar_settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            default_view TEXT NOT NULL DEFAULT 'dayGridMonth',
            default_priority TEXT NOT NULL DEFAULT 'medium',
            sound_enabled INTEGER NOT NULL DEFAULT 1,
            notification_enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );
    """

    _INSERT_DEFAULT_SETTINGS = """
        INSERT OR IGNORE INTO calendar_settings (id, default_view, default_priority, sound_enabled, notification_enabled, updated_at)
        VALUES (1, 'dayGridMonth', 'medium', 1, 1, datetime('now'));
    """

    def _init_tables(self) -> None:
        """Initialize calendar tables."""
        try:
            conn = self._connect()
            conn.executescript(self._CREATE_SCHEMA)
            conn.executescript(self._INSERT_DEFAULT_SETTINGS)
            conn.commit()
            conn.close()
            logger.debug("Calendar tables initialized")
        except sqlite3.DatabaseError as e:
            logger.error(f"Failed to initialize calendar tables: {e}")
            raise

    # ==================== Events ====================

    def get_events(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get calendar events within a time range."""
        conn = self._connect()
        cursor = conn.cursor()

        query = "SELECT * FROM calendar_events WHERE 1=1"
        params = []

        if start_time:
            query += " AND end_time >= ?"
            params.append(start_time)
        if end_time:
            query += " AND start_time <= ?"
            params.append(end_time)

        query += " ORDER BY start_time ASC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        """Get a single calendar event by ID."""
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        conn.close()

        return dict(row) if row else None

    def create_event(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new calendar event."""
        conn = self._connect()
        cursor = conn.cursor()

        now = datetime.now().isoformat()
        event_id = data.get("id") or f"evt_{int(datetime.now().timestamp() * 1000)}"

        cursor.execute(
            """INSERT INTO calendar_events
               (id, title, description, start_time, end_time, is_all_day, priority, reminders_json, recurrence_json, recurrence_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                data.get("title", ""),
                data.get("description") or "",
                data.get("start_time"),
                data.get("end_time"),
                1 if data.get("is_all_day") else 0,
                data.get("priority", "medium"),
                json.dumps(data.get("reminders") or []),
                json.dumps(data.get("recurrence")) if data.get("recurrence") else None,
                data.get("recurrence_id"),
                now,
                now,
            ),
        )

        conn.commit()
        conn.close()

        logger.info(f"Calendar event created: {event_id}")
        return self.get_event(event_id)

    def update_event(self, event_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update an existing calendar event."""
        existing = self.get_event(event_id)
        if not existing:
            return None

        conn = self._connect()
        cursor = conn.cursor()
        now = datetime.now().isoformat()

        cursor.execute(
            """UPDATE calendar_events
               SET title = ?, description = ?, start_time = ?, end_time = ?, is_all_day = ?,
                   priority = ?, reminders_json = ?, recurrence_json = ?, recurrence_id = ?, updated_at = ?
               WHERE id = ?""",
            (
                data.get("title", existing["title"]),
                data.get("description", existing["description"]),
                data.get("start_time", existing["start_time"]),
                data.get("end_time", existing["end_time"]),
                data.get("is_all_day", existing["is_all_day"]),
                data.get("priority", existing["priority"]),
                json.dumps(data.get("reminders", json.loads(existing["reminders_json"] or "[]"))),
                json.dumps(data.get("recurrence")) if data.get("recurrence") else existing["recurrence_json"],
                data.get("recurrence_id", existing["recurrence_id"]),
                now,
                event_id,
            ),
        )

        conn.commit()
        conn.close()

        logger.info(f"Calendar event updated: {event_id}")
        return self.get_event(event_id)

    def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event."""
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()

        if deleted:
            logger.info(f"Calendar event deleted: {event_id}")
        return deleted

    # ==================== Settings ====================

    def get_settings(self) -> dict[str, Any]:
        """Get calendar settings."""
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM calendar_settings WHERE id = 1")
        row = cursor.fetchone()
        conn.close()

        if not row:
            # Return defaults
            return {
                "default_view": "dayGridMonth",
                "default_priority": "medium",
                "sound_enabled": True,
                "notification_enabled": True,
            }

        result = dict(row)
        # Convert integer fields
        result["sound_enabled"] = bool(result.get("sound_enabled", 1))
        result["notification_enabled"] = bool(result.get("notification_enabled", 1))
        return result

    def update_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        """Update calendar settings."""
        conn = self._connect()
        cursor = conn.cursor()
        now = datetime.now().isoformat()

        cursor.execute(
            """UPDATE calendar_settings
               SET default_view = ?, default_priority = ?, sound_enabled = ?, notification_enabled = ?, updated_at = ?
               WHERE id = 1""",
            (
                data.get("default_view", "dayGridMonth"),
                data.get("default_priority", "medium"),
                1 if data.get("sound_enabled", True) else 0,
                1 if data.get("notification_enabled", True) else 0,
                now,
            ),
        )

        conn.commit()
        conn.close()

        logger.info("Calendar settings updated")
        return self.get_settings()


# Repository instance cache
_calendar_repo: CalendarRepository | None = None


def get_calendar_repository(workspace: Path) -> CalendarRepository:
    """Get or create CalendarRepository instance for the workspace."""
    global _calendar_repo
    db_path = workspace / ".nanobot" / "calendar.db"
    if _calendar_repo is None or _calendar_repo.db_path != db_path:
        _calendar_repo = CalendarRepository(db_path)
    return _calendar_repo
