"""AgentLoop 数据库连接与事务封装。"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from loguru import logger

from nanobot.agentloop.config import get_chat_db_path, get_system_db_path


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开 SQLite 连接并设置 PRAGMA。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA busy_timeout = 3000;")
    return conn


def connect_chat(workspace: Path | None = None) -> sqlite3.Connection:
    """连接业务数据库（chat.db）。"""
    return connect(get_chat_db_path(workspace))


def connect_system() -> sqlite3.Connection:
    """连接系统配置数据库（system.db）。"""
    return connect(get_system_db_path())


@contextmanager
def tx(conn: sqlite3.Connection, immediate: bool = False):
    """事务上下文管理器。"""
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _load_schema(name: str) -> str | None:
    """加载 schema 内容，优先包内资源，其次项目 sqls 目录。"""
    try:
        from importlib.resources import files
        pkg = files("nanobot.agentloop")
        res = pkg / "sql" / name
        with res.open(encoding="utf-8") as f:
            return f.read()
    except Exception:
        pass
    fallback = Path(__file__).resolve().parent.parent.parent / "sqls" / name
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    return None


def init_chat_schema(conn: sqlite3.Connection) -> None:
    """初始化 chat.db 中的 AgentLoop 表结构。"""
    sql = _load_schema("agentloop_chat.sql")
    if not sql:
        logger.warning("AgentLoop chat schema not found")
        return
    conn.executescript(sql)


def init_system_schema(conn: sqlite3.Connection) -> None:
    """初始化 system.db 中的 AgentLoop 表结构。"""
    sql = _load_schema("agentloop_system.sql")
    if not sql:
        logger.warning("AgentLoop system schema not found")
        return
    conn.executescript(sql)
