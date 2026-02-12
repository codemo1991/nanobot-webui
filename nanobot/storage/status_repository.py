"""Status repository for system status persistence."""

import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger


class StatusRepository:
    """系统状态数据仓库，负责与 SQLite 数据库交互。"""
    
    def __init__(self, db_path: Path):
        """
        初始化状态仓库。
        
        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        self._init_db()
    
    def _connect(self) -> sqlite3.Connection:
        """创建数据库连接。"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self) -> None:
        """初始化数据库表结构。"""
        try:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS system_status (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    
                    CREATE INDEX IF NOT EXISTS idx_system_status_updated_at
                        ON system_status(updated_at DESC);
                    """
                )
            logger.debug("System status table initialized")
        except Exception as e:
            logger.exception("Failed to initialize system_status table")
            raise
    
    def get(self, key: str) -> str | None:
        """
        获取状态值。
        
        Args:
            key: 状态键
            
        Returns:
            状态值，如果不存在返回 None
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM system_status WHERE key = ?",
                    (key,)
                ).fetchone()
                
                if row is None:
                    return None
                
                return row["value"]
        except Exception as e:
            logger.exception(f"Failed to get status for key '{key}'")
            return None
    
    def set(self, key: str, value: str) -> None:
        """
        设置状态值。
        
        Args:
            key: 状态键
            value: 状态值
        """
        try:
            updated_at = datetime.now().isoformat()
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO system_status (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (key, value, updated_at)
                )
            logger.debug(f"Set status: {key} = {value}")
        except Exception as e:
            logger.exception(f"Failed to set status for key '{key}'")
            raise
    
    def get_start_time(self) -> float | None:
        """
        获取系统启动时间戳。
        
        Returns:
            启动时间戳（秒），如果不存在返回 None
        """
        value = self.get("start_time")
        if value is None:
            return None
        
        try:
            return float(value)
        except ValueError as e:
            logger.exception(f"Failed to parse start_time value '{value}'")
            return None
    
    def set_start_time(self, timestamp: float) -> None:
        """
        设置系统启动时间戳。
        
        Args:
            timestamp: 启动时间戳（秒）
        """
        self.set("start_time", str(timestamp))
