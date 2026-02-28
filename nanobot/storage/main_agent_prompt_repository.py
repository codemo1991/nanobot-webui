"""主 Agent System Prompt 配置的 SQLite 存储。"""

from pathlib import Path
from typing import Optional

from loguru import logger


class MainAgentPromptRepository:
    """主 Agent 系统提示词配置的 Repository，按 workspace 隔离存储。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _connect(self):
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        schema = """
        CREATE TABLE IF NOT EXISTS main_agent_prompt_config (
            workspace_path TEXT PRIMARY KEY,
            identity_content TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        """
        with self._connect() as conn:
            conn.executescript(schema)
            conn.commit()

    def get(self, workspace_path: str) -> Optional[dict]:
        """获取指定 workspace 的主 Agent 提示词配置。"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT workspace_path, identity_content, updated_at FROM main_agent_prompt_config WHERE workspace_path = ?",
                (workspace_path or "",),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert(self, workspace_path: str, identity_content: str) -> dict:
        """插入或更新配置。"""
        from datetime import datetime
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO main_agent_prompt_config (workspace_path, identity_content, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(workspace_path) DO UPDATE SET
                     identity_content = excluded.identity_content,
                     updated_at = excluded.updated_at""",
                (workspace_path or "", identity_content, now),
            )
            conn.commit()
        result = self.get(workspace_path)
        logger.info(f"主 Agent 提示词配置已保存: workspace={workspace_path or '(default)'}")
        return result

    def reset(self, workspace_path: str) -> bool:
        """清空配置（恢复默认），删除该 workspace 的记录。"""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM main_agent_prompt_config WHERE workspace_path = ?",
                (workspace_path or "",),
            )
            conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            logger.info(f"主 Agent 提示词已恢复默认: workspace={workspace_path or '(default)'}")
        return deleted
