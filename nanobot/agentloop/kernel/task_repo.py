"""Task 仓储操作。"""

import json

from nanobot.agentloop.db import tx
from nanobot.agentloop.kernel.ids import now_ts


def lease_one_ready_task(conn, lease_owner: str, lease_seconds: int = 30) -> dict | None:
    """领取一个 READY 且依赖满足的任务，返回任务 dict 或 None。"""
    now = now_ts()
    lease_until = now + lease_seconds

    with tx(conn, immediate=True):
        row = conn.execute(
            """
            SELECT t.task_id
            FROM agentloop_tasks t
            WHERE t.state = 'READY'
              AND (t.lease_until IS NULL OR t.lease_until < ?)
              AND (t.deadline_ts IS NULL OR t.deadline_ts > ?)
              AND NOT EXISTS (
                  SELECT 1
                  FROM agentloop_task_artifact_deps d
                  JOIN agentloop_artifacts a ON a.artifact_id = d.artifact_id
                  WHERE d.task_id = t.task_id
                    AND d.mode = 'READ'
                    AND d.required = 1
                    AND a.status <> 'READY'
              )
            ORDER BY t.priority ASC, t.depth ASC, t.created_at ASC
            LIMIT 1
            """,
            (now, now),
        ).fetchone()

        if not row:
            return None

        task_id = row["task_id"]
        cur = conn.execute(
            """
            UPDATE agentloop_tasks
            SET state = 'LEASED',
                lease_owner = ?,
                lease_until = ?,
                updated_at = ?
            WHERE task_id = ?
              AND state = 'READY'
              AND (lease_until IS NULL OR lease_until < ?)
            """,
            (lease_owner, lease_until, now, task_id, now),
        )

        if cur.rowcount != 1:
            return None

        task = conn.execute("SELECT * FROM agentloop_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(task)


def mark_task_running(conn, task_id: str) -> None:
    """将 LEASED 任务标记为 RUNNING。"""
    ts = now_ts()
    with tx(conn, immediate=True):
        conn.execute(
            """
            UPDATE agentloop_tasks
            SET state = 'RUNNING',
                started_at = COALESCE(started_at, ?),
                updated_at = ?,
                attempt_no = attempt_no + 1
            WHERE task_id = ? AND state = 'LEASED'
            """,
            (ts, ts, task_id),
        )
        row = conn.execute(
            "SELECT trace_id FROM agentloop_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return
        trace_id = row["trace_id"]
        conn.execute(
            """
            INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
            VALUES (?, ?, NULL, 'TASK_START', ?, ?)
            """,
            (trace_id, task_id, "{}", ts),
        )


def mark_task_done(conn, task_id: str, artifact_id: str | None) -> None:
    """将任务标记为 DONE。"""
    ts = now_ts()
    row = conn.execute(
        "SELECT trace_id, parent_task_id FROM agentloop_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    if not row:
        return

    with tx(conn, immediate=True):
        conn.execute(
            """
            UPDATE agentloop_tasks
            SET state = 'DONE',
                result_artifact_id = ?,
                finished_at = ?,
                updated_at = ?,
                lease_owner = NULL,
                lease_until = NULL
            WHERE task_id = ?
            """,
            (artifact_id, ts, ts, task_id),
        )
        conn.execute(
            """
            INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
            VALUES (?, ?, ?, 'TASK_DONE', ?, ?)
            """,
            (row["trace_id"], task_id, row["parent_task_id"], json.dumps({"artifact_id": artifact_id}), ts),
        )
        if row["parent_task_id"]:
            conn.execute(
                """
                UPDATE agentloop_tasks
                SET finished_children = finished_children + 1,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (ts, row["parent_task_id"]),
            )


def mark_task_failed(conn, task_id: str, error_code: str, error_message: str) -> None:
    """将任务标记为 FAILED；若为根任务则同时将 trace 传播为 FAILED。
    对非根任务：计入父任务的 finished_children，若父任务所有子任务均已结束则向上传播失败。
    """
    ts = now_ts()
    row = conn.execute(
        "SELECT trace_id, parent_task_id FROM agentloop_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    if not row:
        return

    trace_id = row["trace_id"]
    parent_task_id = row["parent_task_id"]

    with tx(conn, immediate=True):
        conn.execute(
            """
            UPDATE agentloop_tasks
            SET state = 'FAILED',
                error_code = ?,
                error_message = ?,
                finished_at = ?,
                updated_at = ?,
                lease_owner = NULL,
                lease_until = NULL
            WHERE task_id = ?
            """,
            (error_code, error_message, ts, ts, task_id),
        )
        conn.execute(
            """
            INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
            VALUES (?, ?, ?, 'TASK_FAIL', ?, ?)
            """,
            (trace_id, task_id, parent_task_id, json.dumps({"error_code": error_code}), ts),
        )
        if parent_task_id is None:
            # 根任务失败 → trace 直接 FAILED
            conn.execute(
                """
                UPDATE agentloop_traces
                SET status = 'FAILED', finished_at = ?, updated_at = ?
                WHERE trace_id = ? AND status = 'RUNNING'
                """,
                (ts, ts, trace_id),
            )
            conn.execute(
                """
                INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
                VALUES (?, NULL, NULL, 'TRACE_FAIL', ?, ?)
                """,
                (trace_id, json.dumps({"error_code": error_code, "error_message": error_message}), ts),
            )
        else:
            # 非根任务失败：计入父任务 finished_children，触发父任务失败传播
            conn.execute(
                """
                UPDATE agentloop_tasks
                SET finished_children = finished_children + 1, updated_at = ?
                WHERE task_id = ?
                """,
                (ts, parent_task_id),
            )
            parent = conn.execute(
                "SELECT state, expected_children, finished_children FROM agentloop_tasks WHERE task_id = ?",
                (parent_task_id,),
            ).fetchone()
            if (
                parent
                and parent["state"] == "WAITING_CHILDREN"
                and parent["finished_children"] >= parent["expected_children"]
            ):
                _propagate_failure_to_ancestor(
                    conn, parent_task_id, trace_id, ts, "CHILD_FAILED",
                    f"子任务 {task_id} 失败: {error_message}"
                )


def mark_task_waiting_children(
    conn,
    task_id: str,
    artifact_id: str | None,
    expected_children: int,
) -> None:
    """将任务标记为 WAITING_CHILDREN。"""
    ts = now_ts()
    with tx(conn, immediate=True):
        conn.execute(
            """
            UPDATE agentloop_tasks
            SET state = 'WAITING_CHILDREN',
                result_artifact_id = ?,
                expected_children = ?,
                updated_at = ?,
                lease_owner = NULL,
                lease_until = NULL
            WHERE task_id = ?
            """,
            (artifact_id, expected_children, ts, task_id),
        )


def mark_task_waiting_artifacts(conn, task_id: str, wait_for_artifacts: list[str]) -> None:
    """
    将任务标记为 WAITING_ARTIFACTS，并持久化依赖：
    - 已存在的 artifact：直接写入 agentloop_task_artifact_deps
    - 尚未存在的 artifact：写入 agentloop_task_pending_deps，待 create_artifact 时补齐
    """
    ts = now_ts()
    from nanobot.agentloop.kernel.dep_repo import add_read_dep

    with tx(conn, immediate=True):
        conn.execute(
            """
            UPDATE agentloop_tasks
            SET state = 'WAITING_ARTIFACTS',
                updated_at = ?,
                lease_owner = NULL,
                lease_until = NULL
            WHERE task_id = ?
            """,
            (ts, task_id),
        )
        for artifact_id in wait_for_artifacts:
            if not artifact_id:
                continue
            exists = conn.execute(
                "SELECT 1 FROM agentloop_artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if exists:
                add_read_dep(conn, task_id, artifact_id, alias=None, required=True)
            else:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO agentloop_task_pending_deps(task_id, artifact_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (task_id, artifact_id, ts),
                )


def fulfill_pending_deps_for_artifact(conn, artifact_id: str) -> None:
    """
    当 artifact 创建完成时，将 agentloop_task_pending_deps 中等待该 artifact 的
    任务补齐 READ 依赖，并删除 pending 记录。
    """
    from nanobot.agentloop.kernel.dep_repo import add_read_dep

    rows = conn.execute(
        "SELECT task_id FROM agentloop_task_pending_deps WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchall()
    if not rows:
        return
    with tx(conn, immediate=True):
        for row in rows:
            add_read_dep(conn, row["task_id"], artifact_id, alias=None, required=True)
        conn.execute(
            "DELETE FROM agentloop_task_pending_deps WHERE artifact_id = ?",
            (artifact_id,),
        )


def mark_waiting_artifacts_tasks_ready(conn, artifact_id: str) -> None:
    """
    当 artifact 变为 READY 时，检查依赖该 artifact 的 WAITING_ARTIFACTS 任务，
    若其所有 required READ 依赖已满足，则推进为 READY。
    """
    ts = now_ts()
    rows = conn.execute(
        """
        SELECT DISTINCT d.task_id
        FROM agentloop_task_artifact_deps d
        JOIN agentloop_tasks t ON t.task_id = d.task_id
        WHERE d.artifact_id = ?
          AND d.mode = 'READ'
          AND t.state = 'WAITING_ARTIFACTS'
        """,
        (artifact_id,),
    ).fetchall()

    for row in rows:
        task_id = row["task_id"]
        unmet = conn.execute(
            """
            SELECT 1
            FROM agentloop_task_artifact_deps d
            JOIN agentloop_artifacts a ON a.artifact_id = d.artifact_id
            WHERE d.task_id = ?
              AND d.mode = 'READ'
              AND d.required = 1
              AND a.status <> 'READY'
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if not unmet:
            with tx(conn, immediate=True):
                conn.execute(
                    """
                    UPDATE agentloop_tasks
                    SET state = 'READY', updated_at = ?
                    WHERE task_id = ? AND state = 'WAITING_ARTIFACTS'
                    """,
                    (ts, task_id),
                )


def _mark_task_reducing_impl(conn, parent_task_id: str, trace_id: str, ts: int) -> None:
    """mark_task_reducing 内部实现，避免递归时嵌套事务。"""
    parent_row = conn.execute(
        "SELECT parent_task_id FROM agentloop_tasks WHERE task_id = ?", (parent_task_id,)
    ).fetchone()

    conn.execute(
        """
        UPDATE agentloop_tasks
        SET state = 'DONE', finished_at = ?, updated_at = ?,
            lease_owner = NULL, lease_until = NULL
        WHERE task_id = ?
        """,
        (ts, ts, parent_task_id),
    )
    if parent_row and parent_row["parent_task_id"] is None:
        conn.execute(
            """
            UPDATE agentloop_traces SET status = 'DONE', finished_at = ?, updated_at = ?
            WHERE trace_id = ? AND status = 'RUNNING'
            """,
            (ts, ts, trace_id),
        )
        conn.execute(
            """
            INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
            VALUES (?, ?, NULL, 'TRACE_DONE', '{}', ?)
            """,
            (trace_id, parent_task_id, ts),
        )
    else:
        grandparent_id = parent_row["parent_task_id"] if parent_row else None
        if grandparent_id:
            conn.execute(
                """
                UPDATE agentloop_tasks
                SET finished_children = finished_children + 1, updated_at = ?
                WHERE task_id = ?
                """,
                (ts, grandparent_id),
            )
            gp = conn.execute(
                "SELECT state, expected_children, finished_children FROM agentloop_tasks WHERE task_id = ?",
                (grandparent_id,),
            ).fetchone()
            if gp and gp["state"] == "WAITING_CHILDREN" and gp["finished_children"] >= gp["expected_children"]:
                _mark_task_reducing_impl(conn, grandparent_id, trace_id, ts)


def mark_task_reducing(conn, parent_task_id: str, trace_id: str) -> None:
    """将父任务从 WAITING_CHILDREN 推进到 DONE；v1 简化不创建 reducer 子任务。"""
    ts = now_ts()
    with tx(conn, immediate=True):
        _mark_task_reducing_impl(conn, parent_task_id, trace_id, ts)


def reset_task_for_retry(conn, task_id: str, error_code: str, error_message: str) -> None:
    """任务失败后重置为 READY 以便重试。不重置 attempt_no，下次执行时 mark_task_running 会自增。"""
    ts = now_ts()
    with tx(conn, immediate=True):
        conn.execute(
            """
            UPDATE agentloop_tasks
            SET state = 'READY',
                error_code = ?,
                error_message = ?,
                lease_owner = NULL,
                lease_until = NULL,
                updated_at = ?
            WHERE task_id = ?
            """,
            (error_code, error_message, ts, task_id),
        )


def _propagate_failure_to_ancestor(
    conn, task_id: str, trace_id: str, ts: int, error_code: str, error_message: str
) -> None:
    """递归向上传播 FAILED，直至根任务，并将 trace 标记为 FAILED。"""
    row = conn.execute(
        "SELECT parent_task_id FROM agentloop_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()

    conn.execute(
        """
        UPDATE agentloop_tasks
        SET state = 'FAILED', error_code = ?, error_message = ?,
            finished_at = ?, updated_at = ?, lease_owner = NULL, lease_until = NULL
        WHERE task_id = ? AND state NOT IN ('FAILED', 'DONE', 'CANCELED')
        """,
        (error_code, error_message, ts, ts, task_id),
    )
    conn.execute(
        """
        INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
        VALUES (?, ?, NULL, 'TASK_FAIL', ?, ?)
        """,
        (trace_id, task_id, json.dumps({"error_code": error_code, "propagated": True}), ts),
    )

    parent_id = row["parent_task_id"] if row else None
    if parent_id is None:
        # 已到根任务，标记 trace FAILED
        conn.execute(
            """
            UPDATE agentloop_traces SET status = 'FAILED', finished_at = ?, updated_at = ?
            WHERE trace_id = ? AND status = 'RUNNING'
            """,
            (ts, ts, trace_id),
        )
        conn.execute(
            """
            INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
            VALUES (?, NULL, NULL, 'TRACE_FAIL', ?, ?)
            """,
            (trace_id, json.dumps({"error_code": error_code, "error_message": error_message}), ts),
        )
    else:
        # 继续向上传播：计入祖父的 finished_children
        conn.execute(
            "UPDATE agentloop_tasks SET finished_children = finished_children + 1, updated_at = ? WHERE task_id = ?",
            (ts, parent_id),
        )
        gp = conn.execute(
            "SELECT state, expected_children, finished_children FROM agentloop_tasks WHERE task_id = ?",
            (parent_id,),
        ).fetchone()
        if gp and gp["state"] == "WAITING_CHILDREN" and gp["finished_children"] >= gp["expected_children"]:
            _propagate_failure_to_ancestor(conn, parent_id, trace_id, ts, error_code, error_message)


def recover_stale_tasks(conn, stale_seconds: int = 300) -> int:
    """将长期停留在 RUNNING 状态（可能因进程崩溃而僵死）的任务恢复调度。
    - 还有重试机会：重置为 READY
    - 已无重试：标记为 FAILED 并传播
    返回恢复的任务数。
    """
    threshold = now_ts() - stale_seconds
    stale = conn.execute(
        """
        SELECT task_id, attempt_no, max_retries, trace_id
        FROM agentloop_tasks
        WHERE state = 'RUNNING' AND updated_at < ?
        """,
        (threshold,),
    ).fetchall()

    recovered = 0
    for row in stale:
        if row["attempt_no"] <= row["max_retries"]:
            reset_task_for_retry(conn, row["task_id"], "STALE_RUNNING", "任务执行超时，自动重试")
        else:
            mark_task_failed(conn, row["task_id"], "STALE_RUNNING", "任务执行超时且重试次数耗尽")
        recovered += 1

    return recovered
