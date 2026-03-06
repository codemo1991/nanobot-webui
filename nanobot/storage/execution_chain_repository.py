"""Repository for execution chain data storage."""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger


class ExecutionChainRepository:
    """执行链路数据仓库"""

    _instances: dict[Path, "ExecutionChainRepository"] = {}
    _lock = threading.Lock()

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._ensure_tables()

    @classmethod
    def get_instance(cls, db_path: Path) -> "ExecutionChainRepository":
        """获取数据库实例"""
        if db_path not in cls._instances:
            with cls._lock:
                if db_path not in cls._instances:
                    cls._instances[db_path] = cls(db_path)
        return cls._instances[db_path]

    def _ensure_tables(self):
        """确保数据库表存在"""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_chains (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain_id TEXT NOT NULL UNIQUE,
                    session_key TEXT NOT NULL,
                    channel TEXT,
                    chat_id TEXT,
                    root_prompt TEXT,
                    status TEXT DEFAULT 'running',
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP,
                    duration_ms INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL UNIQUE,
                    chain_id TEXT NOT NULL,
                    parent_node_id TEXT,
                    node_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    arguments TEXT,
                    result TEXT,
                    status TEXT DEFAULT 'running',
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP,
                    duration_ms INTEGER,
                    error_message TEXT,
                    FOREIGN KEY (chain_id) REFERENCES execution_chains(chain_id)
                )
            """)

            # 创建索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chain_session ON execution_chains(session_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chain_status ON execution_chains(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chain_started ON execution_chains(started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_chain ON execution_nodes(chain_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_parent ON execution_nodes(parent_node_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type_name ON execution_nodes(node_type, name)")

            conn.commit()
        finally:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def create_chain(self, chain) -> None:
        """创建链路记录"""
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO execution_chains (chain_id, session_key, channel, chat_id, root_prompt, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                chain.chain_id,
                chain.session_key,
                chain.channel,
                chain.chat_id,
                chain.root_prompt,
                chain.status,
                chain.started_at.isoformat() if chain.started_at else None
            ))
            conn.commit()
        finally:
            conn.close()

    def update_chain(self, chain) -> None:
        """更新链路记录"""
        conn = self._get_conn()
        try:
            conn.execute("""
                UPDATE execution_chains
                SET status = ?, finished_at = ?, duration_ms = ?
                WHERE chain_id = ?
            """, (
                chain.status,
                chain.finished_at.isoformat() if chain.finished_at else None,
                chain.duration_ms,
                chain.chain_id
            ))
            conn.commit()
        finally:
            conn.close()

    def query_chains(
        self,
        session_key: str = None,
        status: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100
    ) -> list[dict]:
        """查询链路列表"""
        conn = self._get_conn()
        try:
            query = "SELECT * FROM execution_chains WHERE 1=1"
            params = []

            if session_key:
                query += " AND session_key = ?"
                params.append(session_key)
            if status:
                query += " AND status = ?"
                params.append(status)
            if start_time:
                query += " AND started_at >= ?"
                params.append(start_time.isoformat())
            if end_time:
                query += " AND started_at <= ?"
                params.append(end_time.isoformat())

            query += " ORDER BY started_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            rows = [dict(row) for row in cursor.fetchall()]
            return rows
        finally:
            conn.close()

    def get_chain_by_id(self, chain_id: str) -> Optional[dict]:
        """根据 ID 获取链路"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "SELECT * FROM execution_chains WHERE chain_id = ?",
                (chain_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def upsert_node(self, node) -> None:
        """插入或更新节点"""
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO execution_nodes
                (node_id, chain_id, parent_node_id, node_type, name, arguments, result, status, started_at, finished_at, duration_ms, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node.node_id,
                node.chain_id,
                node.parent_node_id,
                node.node_type,
                node.name,
                node.arguments,
                node.result,
                node.status,
                node.started_at.isoformat() if node.started_at else None,
                node.finished_at.isoformat() if node.finished_at else None,
                node.duration_ms,
                node.error_message
            ))
            conn.commit()
        finally:
            conn.close()

    def get_nodes_by_chain(self, chain_id: str) -> list[dict]:
        """获取链路的所有节点"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "SELECT * FROM execution_nodes WHERE chain_id = ? ORDER BY started_at",
                (chain_id,)
            )
            rows = [dict(row) for row in cursor.fetchall()]
            return rows
        finally:
            conn.close()

    def get_node_by_id(self, node_id: str) -> Optional[dict]:
        """根据 ID 获取节点"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "SELECT * FROM execution_nodes WHERE node_id = ?",
                (node_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def cleanup_old_chains(self, days: int = 30) -> int:
        """清理旧链路数据"""
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                DELETE FROM execution_chains
                WHERE started_at < datetime('now', '-' || ? || ' days')
            """, (days,))
            conn.commit()
            deleted = cursor.rowcount
            logger.info(f"[ExecutionChain] Cleaned up {deleted} old chains")
            return deleted
        finally:
            conn.close()
