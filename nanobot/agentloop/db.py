"""AgentLoop 数据库连接与事务封装。"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from loguru import logger

from nanobot.agentloop.config import get_chat_db_path, get_system_db_path

# 当前 chat.db schema 版本（每次不兼容变更时递增）
CHAT_SCHEMA_VERSION = 2

# 线程本地存储，用于 HTTP handler 线程的独立连接池
_thread_local = threading.local()


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


def get_thread_chat_conn(workspace: Path | None = None) -> sqlite3.Connection:
    """获取当前线程专属的 chat.db 连接（线程安全，每线程独立连接）。

    HTTP handler 线程应使用此函数而非直接访问 Kernel.conn，
    Kernel.conn 仅供其所在的 asyncio 事件循环线程使用。
    """
    key = f"chat:{workspace}"
    if not hasattr(_thread_local, "conns"):
        _thread_local.conns = {}
    if key not in _thread_local.conns:
        conn = connect_chat(workspace)
        init_chat_schema(conn)
        run_chat_migrations(conn)
        _thread_local.conns[key] = conn
    return _thread_local.conns[key]


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
    """初始化 chat.db 中的 AgentLoop 表结构，并执行待执行的迁移。"""
    sql = _load_schema("agentloop_chat.sql")
    if not sql:
        logger.warning("AgentLoop chat schema not found")
        return
    conn.executescript(sql)
    # 每次初始化后运行迁移（幂等，已执行的迁移会跳过）
    run_chat_migrations(conn)


def init_system_schema(conn: sqlite3.Connection) -> None:
    """初始化 system.db 中的 AgentLoop 表结构。"""
    sql = _load_schema("agentloop_system.sql")
    if not sql:
        logger.warning("AgentLoop system schema not found")
        return
    conn.executescript(sql)


# ─────────────────────────── Schema 迁移系统 ───────────────────────────

def _get_chat_schema_version(conn: sqlite3.Connection) -> int:
    """获取当前 chat schema 版本，不存在则返回 0。"""
    try:
        row = conn.execute(
            "SELECT version FROM agentloop_schema_version LIMIT 1"
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _set_chat_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """持久化 schema 版本号。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agentloop_schema_version(version INTEGER NOT NULL)"
    )
    conn.execute("DELETE FROM agentloop_schema_version")
    conn.execute("INSERT INTO agentloop_schema_version(version) VALUES (?)", (version,))


def _migrate_v2_fix_dep_unique(conn: sqlite3.Connection) -> None:
    """迁移 v2：修复 agentloop_task_artifact_deps UNIQUE(alias) 对 NULL 无效问题。

    旧表在 CREATE TABLE 中声明了 UNIQUE(task_id, artifact_id, mode, alias)，
    但 SQLite 的 UNIQUE 约束允许多行 alias=NULL 共存，导致重复依赖记录。
    迁移步骤：重建不含该约束的新表，再添加两个局部唯一索引代替。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='agentloop_task_artifact_deps'"
    ).fetchone()

    needs_rebuild = row and "UNIQUE(task_id, artifact_id, mode, alias)" in (row["sql"] or "")

    if needs_rebuild:
        logger.info("[Migration v2] 重建 agentloop_task_artifact_deps 以修复 UNIQUE NULL 约束...")
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                CREATE TABLE agentloop_task_artifact_deps_v2 (
                    dep_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id  TEXT    NOT NULL,
                    artifact_id TEXT NOT NULL,
                    mode     TEXT    NOT NULL CHECK(mode IN ('READ', 'WRITE')),
                    required INTEGER NOT NULL DEFAULT 1 CHECK(required IN (0, 1)),
                    alias    TEXT,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(task_id)     REFERENCES agentloop_tasks(task_id),
                    FOREIGN KEY(artifact_id) REFERENCES agentloop_artifacts(artifact_id)
                )
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO agentloop_task_artifact_deps_v2
                    (dep_id, task_id, artifact_id, mode, required, alias, created_at)
                SELECT dep_id, task_id, artifact_id, mode, required, alias, created_at
                FROM agentloop_task_artifact_deps
                """
            )
            conn.execute("DROP TABLE agentloop_task_artifact_deps")
            conn.execute(
                "ALTER TABLE agentloop_task_artifact_deps_v2 "
                "RENAME TO agentloop_task_artifact_deps"
            )
            conn.execute("COMMIT")
            logger.info("[Migration v2] 表重建完成")
        except Exception:
            conn.execute("ROLLBACK")
            conn.execute("PRAGMA foreign_keys = ON")
            raise
        conn.execute("PRAGMA foreign_keys = ON")

    # 添加局部唯一索引（幂等，重建后和首次安装均可执行）
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agentloop_deps_unique_aliased
            ON agentloop_task_artifact_deps(task_id, artifact_id, mode, alias)
            WHERE alias IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agentloop_deps_unique_no_alias
            ON agentloop_task_artifact_deps(task_id, artifact_id, mode)
            WHERE alias IS NULL
        """
    )
    # 确保表重建后其他索引仍存在
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agentloop_deps_task "
        "ON agentloop_task_artifact_deps(task_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agentloop_deps_artifact "
        "ON agentloop_task_artifact_deps(artifact_id)"
    )
    # 补充 events.task_id 索引（可能在旧 schema 中缺失）
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agentloop_events_task "
        "ON agentloop_events(task_id)"
    )


def run_chat_migrations(conn: sqlite3.Connection) -> None:
    """运行 chat.db 所有待执行的 schema 迁移（幂等，已执行的版本自动跳过）。"""
    v = _get_chat_schema_version(conn)

    if v < 1:
        # v1：初始 schema 已由 init_chat_schema 的 executescript 创建，直接标记版本
        _set_chat_schema_version(conn, 1)
        v = 1

    if v < 2:
        _migrate_v2_fix_dep_unique(conn)
        _set_chat_schema_version(conn, 2)
        v = 2

    # v3, v4, ... 未来迁移追加到此处
    # if v < 3:
    #     _migrate_v3_xxx(conn)
    #     _set_chat_schema_version(conn, 3)
    #     v = 3
