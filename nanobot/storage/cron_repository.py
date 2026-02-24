"""Cron job repository for SQLite-based storage."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class CronRepository:
    """Repository for cron job data, using SQLite storage."""

    def __init__(self, db_path: Path):
        """
        Initialize cron repository.

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
        CREATE TABLE IF NOT EXISTS cron_jobs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            is_system INTEGER DEFAULT 0,
            trigger_type TEXT NOT NULL,
            trigger_date_ms INTEGER,
            trigger_interval_seconds INTEGER,
            trigger_cron_expr TEXT,
            trigger_tz TEXT,
            payload_kind TEXT DEFAULT 'agent_turn',
            payload_message TEXT,
            payload_deliver INTEGER DEFAULT 0,
            payload_channel TEXT,
            payload_to TEXT,
            next_run_at_ms INTEGER,
            last_run_at_ms INTEGER,
            last_status TEXT,
            last_error TEXT,
            delete_after_run INTEGER DEFAULT 0,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_cron_jobs_enabled ON cron_jobs(enabled);
        CREATE INDEX IF NOT EXISTS idx_cron_jobs_next_run ON cron_jobs(next_run_at_ms);
    """

    # 系统任务 ID 常量
    SYSTEM_MEMORY_INTEGRATE = "system:memory_auto_integrate"
    SYSTEM_MEMORY_MAINTENANCE = "system:memory_maintenance"

    # 默认系统任务配置
    DEFAULT_SYSTEM_JOBS = [
        {
            "id": SYSTEM_MEMORY_INTEGRATE,
            "name": "自动记忆整合",
            "name_en": "Auto Memory Integration",
            "description": "从对话历史中自动提取长期记忆",
            "trigger_type": "every",
            "trigger_interval_seconds": 30 * 60,  # 30 分钟
            "payload_kind": "system_event",
            "payload_message": "auto_memory_integrate",
        },
        {
            "id": SYSTEM_MEMORY_MAINTENANCE,
            "name": "记忆维护总结",
            "name_en": "Memory Maintenance",
            "description": "压缩和合并长期记忆",
            "trigger_type": "every",
            "trigger_interval_seconds": 60 * 60,  # 60 分钟
            "payload_kind": "system_event",
            "payload_message": "memory_maintenance",
        },
    ]

    def _init_tables(self) -> None:
        """Initialize cron jobs table structure.

        如果数据库文件损坏（file is not a database），自动备份并重建。
        如果表已存在但缺少新列，自动迁移。
        """
        try:
            conn = self._connect()
            conn.executescript(self._CREATE_SCHEMA)
            conn.commit()
            conn.close()
            logger.debug("Cron jobs table initialized")
        except sqlite3.DatabaseError as e:
            # 文件存在但不是合法的 SQLite 数据库（损坏或被覆盖）
            bak_path = self.db_path.with_suffix(".db.bak")
            logger.warning(
                f"Cron database file is corrupted ({e}). "
                f"Renaming to {bak_path} and creating a new database."
            )
            try:
                self.db_path.rename(bak_path)
            except OSError as rename_err:
                logger.error(f"Failed to backup corrupted database: {rename_err}")
                self.db_path.unlink(missing_ok=True)

            # 重建数据库
            conn = self._connect()
            conn.executescript(self._CREATE_SCHEMA)
            conn.commit()
            conn.close()
            logger.info("Cron database rebuilt successfully")
        except Exception:
            logger.exception("Failed to initialize cron jobs table")
            raise
        finally:
            # 确保迁移新列（如果表已存在但缺少 is_system 列）
            self._migrate_columns()

    def _migrate_columns(self) -> None:
        """迁移表结构，添加缺失的列"""
        try:
            conn = self._connect()

            # 检查 is_system 列是否存在
            cursor = conn.execute("PRAGMA table_info(cron_jobs)")
            columns = [row[1] for row in cursor.fetchall()]

            if "is_system" not in columns:
                conn.execute("ALTER TABLE cron_jobs ADD COLUMN is_system INTEGER DEFAULT 0")
                conn.commit()
                logger.info("Migrated cron_jobs: added is_system column")

            conn.close()
        except Exception as e:
            logger.warning(f"Failed to migrate cron_jobs columns: {e}")

    def _get_timestamp_ms(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(datetime.now().timestamp() * 1000)

    def _row_to_job(self, row) -> dict[str, Any]:
        """Convert database row to job dict."""
        return {
            "id": row["id"],
            "name": row["name"],
            "enabled": bool(row["enabled"]),
            "is_system": bool(row["is_system"]),
            "trigger": {
                "type": row["trigger_type"],
                "dateMs": row["trigger_date_ms"],
                "intervalSeconds": row["trigger_interval_seconds"],
                "cronExpr": row["trigger_cron_expr"],
                "tz": row["trigger_tz"],
            },
            "payload": {
                "kind": row["payload_kind"],
                "message": row["payload_message"] or "",
                "deliver": bool(row["payload_deliver"]),
                "channel": row["payload_channel"],
                "to": row["payload_to"],
            },
            "nextRunAtMs": row["next_run_at_ms"],
            "lastRunAtMs": row["last_run_at_ms"],
            "lastStatus": row["last_status"],
            "lastError": row["last_error"],
            "deleteAfterRun": bool(row["delete_after_run"]),
            "createdAtMs": row["created_at_ms"],
            "updatedAtMs": row["updated_at_ms"],
        }

    def get_all_jobs(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        """
        Get all cron jobs.

        Args:
            include_disabled: Include disabled jobs in the result

        Returns:
            List of cron job dicts
        """
        try:
            with self._connect() as conn:
                if include_disabled:
                    rows = conn.execute(
                        "SELECT * FROM cron_jobs ORDER BY next_run_at_ms, name"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM cron_jobs WHERE enabled = 1 ORDER BY next_run_at_ms, name"
                    ).fetchall()
                return [self._row_to_job(row) for row in rows]
        except Exception as e:
            logger.warning(f"Failed to get all cron jobs: {e}")
            return []

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """
        Get a single cron job by ID.

        Args:
            job_id: Job ID

        Returns:
            Job dict or None if not found
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM cron_jobs WHERE id = ?",
                    (job_id,)
                ).fetchone()
                if row is None:
                    return None
                return self._row_to_job(row)
        except Exception as e:
            logger.warning(f"Failed to get cron job {job_id}: {e}")
            return None

    def create_job(
        self,
        job_id: str,
        name: str,
        trigger_type: str,
        trigger_date_ms: int | None = None,
        trigger_interval_seconds: int | None = None,
        trigger_cron_expr: str | None = None,
        trigger_tz: str | None = None,
        payload_kind: str = "agent_turn",
        payload_message: str = "",
        payload_deliver: bool = False,
        payload_channel: str | None = None,
        payload_to: str | None = None,
        delete_after_run: bool = False,
        is_system: bool = False,
    ) -> dict[str, Any]:
        """
        Create a new cron job.

        Args:
            job_id: Job ID
            name: Job name
            trigger_type: Trigger type ("at", "every", "cron")
            trigger_date_ms: For "at" trigger - timestamp in ms
            trigger_interval_seconds: For "every" trigger - interval in seconds
            trigger_cron_expr: For "cron" trigger - cron expression
            trigger_tz: Timezone for cron expression
            payload_kind: Payload kind ("agent_turn", "system_event")
            payload_message: Message to send
            payload_deliver: Whether to deliver response
            payload_channel: Channel for delivery
            payload_to: Recipient for delivery
            delete_after_run: Delete job after execution
            is_system: Whether this is a system job (cannot be deleted)

        Returns:
            Created job dict
        """
        now_ms = self._get_timestamp_ms()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO cron_jobs (
                        id, name, enabled, is_system, trigger_type, trigger_date_ms,
                        trigger_interval_seconds, trigger_cron_expr, trigger_tz,
                        payload_kind, payload_message, payload_deliver,
                        payload_channel, payload_to, delete_after_run,
                        created_at_ms, updated_at_ms
                    ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id, name, int(is_system), trigger_type, trigger_date_ms,
                        trigger_interval_seconds, trigger_cron_expr, trigger_tz,
                        payload_kind, payload_message, int(payload_deliver),
                        payload_channel, payload_to, int(delete_after_run),
                        now_ms, now_ms
                    )
                )
            logger.info(f"Created cron job: {job_id} ({name})")
            return self.get_job(job_id)
        except Exception as e:
            logger.exception(f"Failed to create cron job {job_id}")
            raise

    def update_job(
        self,
        job_id: str,
        name: str | None = None,
        enabled: bool | None = None,
        trigger_type: str | None = None,
        trigger_date_ms: int | None = None,
        trigger_interval_seconds: int | None = None,
        trigger_cron_expr: str | None = None,
        trigger_tz: str | None = None,
        payload_kind: str | None = None,
        payload_message: str | None = None,
        payload_deliver: bool | None = None,
        payload_channel: str | None = None,
        payload_to: str | None = None,
        delete_after_run: bool | None = None,
    ) -> dict[str, Any] | None:
        """
        Update an existing cron job.

        Args:
            job_id: Job ID
            name: New name (optional)
            enabled: New enabled state (optional)
            trigger_type: New trigger type (optional)
            trigger_date_ms: New trigger date (optional)
            trigger_interval_seconds: New interval (optional)
            trigger_cron_expr: New cron expression (optional)
            trigger_tz: New timezone (optional)
            payload_kind: New payload kind (optional)
            payload_message: New message (optional)
            payload_deliver: New deliver flag (optional)
            payload_channel: New channel (optional)
            payload_to: New recipient (optional)
            delete_after_run: New delete flag (optional)

        Returns:
            Updated job dict or None if not found
        """
        existing = self.get_job(job_id)
        if not existing:
            return None

        now_ms = self._get_timestamp_ms()
        updates = []
        values = []

        if name is not None:
            updates.append("name = ?")
            values.append(name)
        if enabled is not None:
            updates.append("enabled = ?")
            values.append(int(enabled))
        if trigger_type is not None:
            updates.append("trigger_type = ?")
            values.append(trigger_type)
        if trigger_date_ms is not None:
            updates.append("trigger_date_ms = ?")
            values.append(trigger_date_ms)
        if trigger_interval_seconds is not None:
            updates.append("trigger_interval_seconds = ?")
            values.append(trigger_interval_seconds)
        if trigger_cron_expr is not None:
            updates.append("trigger_cron_expr = ?")
            values.append(trigger_cron_expr)
        if trigger_tz is not None:
            updates.append("trigger_tz = ?")
            values.append(trigger_tz)
        if payload_kind is not None:
            updates.append("payload_kind = ?")
            values.append(payload_kind)
        if payload_message is not None:
            updates.append("payload_message = ?")
            values.append(payload_message)
        if payload_deliver is not None:
            updates.append("payload_deliver = ?")
            values.append(int(payload_deliver))
        if payload_channel is not None:
            updates.append("payload_channel = ?")
            values.append(payload_channel)
        if payload_to is not None:
            updates.append("payload_to = ?")
            values.append(payload_to)
        if delete_after_run is not None:
            updates.append("delete_after_run = ?")
            values.append(int(delete_after_run))

        if not updates:
            return existing

        updates.append("updated_at_ms = ?")
        values.append(now_ms)
        values.append(job_id)

        try:
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE cron_jobs SET {', '.join(updates)} WHERE id = ?",
                    values
                )
            logger.info(f"Updated cron job: {job_id}")
            return self.get_job(job_id)
        except Exception as e:
            logger.exception(f"Failed to update cron job {job_id}")
            raise

    def delete_job(self, job_id: str) -> bool:
        """
        Delete a cron job.

        Args:
            job_id: Job ID

        Returns:
            True if deleted, False if not found
        """
        try:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
                deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Deleted cron job: {job_id}")
            return deleted
        except Exception as e:
            logger.warning(f"Failed to delete cron job {job_id}: {e}")
            return False

    def update_job_status(
        self,
        job_id: str,
        next_run_at_ms: int | None = None,
        last_run_at_ms: int | None = None,
        last_status: str | None = None,
        last_error: str | None = None,
        clear_error: bool = False,
    ) -> None:
        """
        Update job execution status.

        Args:
            job_id: Job ID
            next_run_at_ms: Next scheduled run time in ms
            last_run_at_ms: Last run time in ms
            last_status: Last execution status
            last_error: Last error message (set to None to clear)
            clear_error: If True, explicitly set last_error to NULL
        """
        now_ms = self._get_timestamp_ms()
        updates = []
        values = []

        if next_run_at_ms is not None:
            updates.append("next_run_at_ms = ?")
            values.append(next_run_at_ms)
        if last_run_at_ms is not None:
            updates.append("last_run_at_ms = ?")
            values.append(last_run_at_ms)
        if last_status is not None:
            updates.append("last_status = ?")
            values.append(last_status)
        if clear_error:
            # 显式清除错误
            updates.append("last_error = NULL")
        elif last_error is not None:
            updates.append("last_error = ?")
            values.append(last_error)

        if not updates:
            return

        updates.append("updated_at_ms = ?")
        values.append(now_ms)
        values.append(job_id)

        try:
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE cron_jobs SET {', '.join(updates)} WHERE id = ?",
                    values
                )
        except Exception as e:
            logger.warning(f"Failed to update cron job status {job_id}: {e}")

    def get_next_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Get jobs with earliest next run times.

        Args:
            limit: Maximum number of jobs to return

        Returns:
            List of job dicts sorted by next_run_at_ms
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM cron_jobs
                    WHERE enabled = 1 AND next_run_at_ms IS NOT NULL
                    ORDER BY next_run_at_ms
                    LIMIT ?
                    """,
                    (limit,)
                ).fetchall()
                return [self._row_to_job(row) for row in rows]
        except Exception as e:
            logger.warning(f"Failed to get next cron jobs: {e}")
            return []

    def ensure_system_jobs(self) -> None:
        """确保系统默认任务存在，如果不存在则创建"""
        for job_config in self.DEFAULT_SYSTEM_JOBS:
            existing = self.get_job(job_config["id"])
            if not existing:
                # 创建系统任务
                self.create_job(
                    job_id=job_config["id"],
                    name=job_config["name"],
                    trigger_type=job_config["trigger_type"],
                    trigger_interval_seconds=job_config["trigger_interval_seconds"],
                    payload_kind=job_config["payload_kind"],
                    payload_message=job_config["payload_message"],
                    is_system=True,
                )
                logger.info(f"Created system cron job: {job_config['id']}")
