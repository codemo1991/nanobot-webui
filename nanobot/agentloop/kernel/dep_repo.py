"""Task-Artifact 依赖仓储操作。"""

from nanobot.agentloop.kernel.ids import now_ts


def add_read_dep(
    conn,
    task_id: str,
    artifact_id: str,
    alias: str | None = None,
    required: bool = True,
) -> None:
    """为任务添加 READ 依赖。调用方需保证处于事务上下文中。"""
    ts = now_ts()
    conn.execute(
        """
        INSERT OR IGNORE INTO agentloop_task_artifact_deps(task_id, artifact_id, mode, required, alias, created_at)
        VALUES (?, ?, 'READ', ?, ?, ?)
        """,
        (task_id, artifact_id, 1 if required else 0, alias, ts),
    )


def add_write_dep(conn, task_id: str, artifact_id: str) -> None:
    """为任务添加 WRITE 依赖。预留接口，当前未使用。调用方需保证处于事务上下文中。"""
    ts = now_ts()
    conn.execute(
        """
        INSERT OR IGNORE INTO agentloop_task_artifact_deps(task_id, artifact_id, mode, required, alias, created_at)
        VALUES (?, ?, 'WRITE', 1, NULL, ?)
        """,
        (task_id, artifact_id, ts),
    )
