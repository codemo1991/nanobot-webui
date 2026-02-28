"""Agent template repository for SQLite-based storage."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


class AgentTemplateRepository:
    """Repository for agent template data, using SQLite storage."""

    def __init__(self, db_path: Path):
        """
        Initialize agent template repository.

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
        CREATE TABLE IF NOT EXISTS agent_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            workspace_path TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'user',  -- system | user
            config_json TEXT NOT NULL,  -- Full template config as JSON
            enabled INTEGER DEFAULT 1,
            is_system INTEGER DEFAULT 0,  -- 1 = system built-in, 0 = user created
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name, workspace_path)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_templates_name ON agent_templates(name);
        CREATE INDEX IF NOT EXISTS idx_agent_templates_workspace ON agent_templates(workspace_path);
        CREATE INDEX IF NOT EXISTS idx_agent_templates_is_system ON agent_templates(is_system);
    """

    def _init_tables(self):
        """Initialize database tables and migrate old schema."""
        with self._connect() as conn:
            # Check if table exists with old schema
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_templates'"
            )
            table_exists = cursor.fetchone() is not None

            if table_exists:
                # Check if is_system column exists
                cursor = conn.execute("PRAGMA table_info(agent_templates)")
                columns = {row[1] for row in cursor.fetchall()}

                if "is_system" not in columns:
                    # Migrate old table: add is_system column
                    logger.info("Migrating agent_templates schema: adding is_system column")
                    conn.execute("ALTER TABLE agent_templates ADD COLUMN is_system INTEGER DEFAULT 0")
                    # Migrate old source values: builtin -> is_system=1, user_yaml -> is_system=0
                    try:
                        conn.execute("UPDATE agent_templates SET is_system = 1 WHERE source = 'builtin'")
                        conn.execute("UPDATE agent_templates SET source = 'user' WHERE source IN ('builtin', 'user_yaml')")
                    except Exception as e:
                        logger.warning(f"Data migration warning: {e}")
                    conn.commit()
                    logger.info("Migration completed")

                    # Now create indexes (they may reference the new column)
                    conn.executescript("""
                        CREATE INDEX IF NOT EXISTS idx_agent_templates_is_system ON agent_templates(is_system);
                    """)
                # Table exists and has is_system, just ensure indexes
                conn.executescript("""
                    CREATE INDEX IF NOT EXISTS idx_agent_templates_name ON agent_templates(name);
                    CREATE INDEX IF NOT EXISTS idx_agent_templates_workspace ON agent_templates(workspace_path);
                    CREATE INDEX IF NOT EXISTS idx_agent_templates_is_system ON agent_templates(is_system);
                """)
            else:
                # Fresh install: create table with new schema
                conn.executescript(self._CREATE_SCHEMA)

            conn.commit()

    def get_by_name(self, name: str, workspace_path: str = '') -> Optional[dict]:
        """Get template by name."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_templates WHERE name = ? AND workspace_path = ?",
                (name, workspace_path)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def list_all(self, workspace_path: str = '') -> list[dict]:
        """List all templates for a workspace."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_templates WHERE workspace_path = ? ORDER BY is_system DESC, name",
                (workspace_path,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_by_source(self, source: str, workspace_path: str = '') -> list[dict]:
        """List templates by source (system or user)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_templates WHERE source = ? AND workspace_path = ? ORDER BY name",
                (source, workspace_path)
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_system_templates(self, workspace_path: str = '') -> list[dict]:
        """List system (built-in) templates."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_templates WHERE is_system = 1 AND workspace_path = ? ORDER BY name",
                (workspace_path,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_user_templates(self, workspace_path: str = '') -> list[dict]:
        """List user-created templates."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_templates WHERE is_system = 0 AND workspace_path = ? ORDER BY name",
                (workspace_path,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def create(self, name: str, config_json: str, source: str = 'user',
               workspace_path: str = '', enabled: bool = True, is_system: bool = False) -> dict:
        """Create a new template."""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO agent_templates
                   (name, workspace_path, source, config_json, enabled, is_system, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, workspace_path, source, config_json, 1 if enabled else 0,
                 1 if is_system else 0, now, now)
            )
            conn.commit()
        return self.get_by_name(name, workspace_path)

    def update(self, name: str, config_json: str, workspace_path: str = '',
               enabled: Optional[bool] = None) -> Optional[dict]:
        """Update a template."""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            if enabled is not None:
                conn.execute(
                    """UPDATE agent_templates
                       SET config_json = ?, enabled = ?, updated_at = ?
                       WHERE name = ? AND workspace_path = ?""",
                    (config_json, 1 if enabled else 0, now, name, workspace_path)
                )
            else:
                conn.execute(
                    """UPDATE agent_templates
                       SET config_json = ?, updated_at = ?
                       WHERE name = ? AND workspace_path = ?""",
                    (config_json, now, name, workspace_path)
                )
            conn.commit()
        return self.get_by_name(name, workspace_path)

    def delete(self, name: str, workspace_path: str = '') -> bool:
        """Delete a template."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM agent_templates WHERE name = ? AND workspace_path = ?",
                (name, workspace_path)
            )
            conn.commit()
            return cursor.rowcount > 0

    def upsert(self, name: str, config_json: str, source: str = 'user',
               workspace_path: str = '', enabled: bool = True, is_system: bool = False) -> dict:
        """Insert or update a template."""
        existing = self.get_by_name(name, workspace_path)
        if existing:
            return self.update(name, config_json, workspace_path, enabled)
        else:
            return self.create(name, config_json, source, workspace_path, enabled, is_system)
