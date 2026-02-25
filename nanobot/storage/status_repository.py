"""Status repository for system status persistence."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

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

    # ============================================================
    # 并发配置管理
    # ============================================================

    CONCURRENCY_CONFIG_KEYS = [
        "max_parallel_tool_calls",
        "max_concurrent_subagents",
        "enable_parallel_tools",
        "thread_pool_size",
        "enable_subagent_parallel",
        "claude_code_max_concurrent",
        "enable_smart_parallel",
        "smart_parallel_model",
    ]

    def get_concurrency_config(self) -> dict[str, Any]:
        """
        获取并发配置。

        Returns:
            并发配置字典
        """
        config = {}
        try:
            with self._connect() as conn:
                for key in self.CONCURRENCY_CONFIG_KEYS:
                    row = conn.execute(
                        "SELECT value FROM system_status WHERE key = ?",
                        (f"concurrency_{key}",)
                    ).fetchone()
                    if row:
                        import json
                        try:
                            config[key] = json.loads(row["value"])
                        except json.JSONDecodeError:
                            config[key] = row["value"]
        except Exception as e:
            logger.exception("Failed to get concurrency config")
        return config

    def set_concurrency_config(self, config: dict[str, Any]) -> None:
        """
        设置并发配置。

        Args:
            config: 并发配置字典
        """
        import json
        try:
            with self._connect() as conn:
                for key, value in config.items():
                    if key in self.CONCURRENCY_CONFIG_KEYS:
                        key_name = f"concurrency_{key}"
                        value_str = json.dumps(value)
                        updated_at = datetime.now().isoformat()
                        conn.execute(
                            """
                            INSERT INTO system_status (key, value, updated_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(key) DO UPDATE SET
                                value=excluded.value,
                                updated_at=excluded.updated_at
                            """,
                            (key_name, value_str, updated_at)
                        )
            logger.info(f"Concurrency config updated: {config}")
        except Exception as e:
            logger.exception("Failed to set concurrency config")
            raise

    # ============================================================
    # 监控指标管理
    # ============================================================

    METRIC_KEYS = [
        "total_tool_calls",
        "parallel_tool_calls",
        "serial_tool_calls",
        "failed_tool_calls",
        "total_subagent_spawns",
        "avg_tool_execution_time",
        "max_concurrent_tools",
        "llm_call_count",
        "total_token_usage",
    ]

    def get_metrics(self) -> dict[str, Any]:
        """
        获取监控指标。

        Returns:
            监控指标字典
        """
        metrics = {}
        try:
            with self._connect() as conn:
                for key in self.METRIC_KEYS:
                    row = conn.execute(
                        "SELECT value FROM system_status WHERE key = ?",
                        (f"metric_{key}",)
                    ).fetchone()
                    if row:
                        import json
                        try:
                            metrics[key] = json.loads(row["value"])
                        except json.JSONDecodeError:
                            metrics[key] = row["value"]
        except Exception as e:
            logger.exception("Failed to get metrics")
        return metrics

    def update_metric(self, key: str, value: Any, increment: bool = False) -> None:
        """
        更新监控指标。

        Args:
            key: 指标键（不含 metric_ 前缀）
            value: 指标值
            increment: 是否增量更新
        """
        if key not in self.METRIC_KEYS:
            return

        import json
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM system_status WHERE key = ?",
                    (f"metric_{key}",)
                ).fetchone()

                current_value = 0
                if row:
                    try:
                        current_value = json.loads(row["value"])
                    except json.JSONDecodeError:
                        current_value = 0

                if increment and isinstance(current_value, (int, float)):
                    new_value = current_value + (value if isinstance(value, (int, float)) else 1)
                else:
                    new_value = value

                value_str = json.dumps(new_value)
                updated_at = datetime.now().isoformat()
                conn.execute(
                    """
                    INSERT INTO system_status (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (f"metric_{key}", value_str, updated_at)
                )
        except Exception as e:
            logger.exception(f"Failed to update metric {key}")

    def reset_metrics(self) -> None:
        """重置所有监控指标。"""
        try:
            with self._connect() as conn:
                for key in self.METRIC_KEYS:
                    conn.execute(
                        "DELETE FROM system_status WHERE key = ?",
                        (f"metric_{key}",)
                    )
            logger.info("Metrics reset")
        except Exception as e:
            logger.exception("Failed to reset metrics")
