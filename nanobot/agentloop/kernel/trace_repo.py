"""Trace 仓储操作。"""

import json

from nanobot.agentloop.db import tx
from nanobot.agentloop.kernel.ids import new_id, now_ts


def create_trace_and_root_task(
    conn,
    user_input: str,
    request_payload: dict,
) -> tuple[str, str]:
    """创建 trace 和 root task，返回 (trace_id, root_task_id)。"""
    trace_id = new_id("tr")
    root_task_id = new_id("tk")
    ts = now_ts()

    with tx(conn, immediate=True):
        conn.execute(
            """
            INSERT INTO agentloop_traces(
                trace_id, root_task_id, user_input, status, success_criteria, created_at, updated_at
            )
            VALUES (?, ?, ?, 'RUNNING', ?, ?, ?)
            """,
            (trace_id, root_task_id, user_input, json.dumps({"kind": "final_answer"}), ts, ts),
        )

        conn.execute(
            """
            INSERT INTO agentloop_tasks(
                task_id, trace_id, parent_task_id, task_kind, capability_name, intent, state,
                priority, depth, budget_tokens, budget_millis, budget_cost_cents, deadline_ts,
                attempt_no, max_retries, expected_children, finished_children, join_policy,
                input_schema, output_schema, request_payload, created_at, updated_at
            )
            VALUES (?, ?, NULL, 'ROOT', 'root_agent', 'handle_user_request', 'READY',
                    10, 0, 0, 0, 0, NULL,
                    0, 1, 0, 0, 'ALL',
                    NULL, 'final_result_v1', ?, ?, ?)
            """,
            (root_task_id, trace_id, json.dumps(request_payload), ts, ts),
        )

        conn.execute(
            """
            INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
            VALUES (?, ?, NULL, 'TASK_SUBMIT', ?, ?)
            """,
            (trace_id, root_task_id, json.dumps({"user_input": user_input}), ts),
        )

    return trace_id, root_task_id


def mark_trace_canceled(conn, trace_id: str, reason: str = "CANCELED") -> None:
    """将 RUNNING 的 trace 标记为 CANCELED（如超时）。"""
    ts = now_ts()
    with tx(conn, immediate=True):
        conn.execute(
            """
            UPDATE agentloop_traces
            SET status = 'CANCELED', finished_at = ?, updated_at = ?
            WHERE trace_id = ? AND status = 'RUNNING'
            """,
            (ts, ts, trace_id),
        )
        conn.execute(
            """
            INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
            VALUES (?, NULL, NULL, 'TRACE_CANCEL', ?, ?)
            """,
            (trace_id, json.dumps({"reason": reason}), ts),
        )
