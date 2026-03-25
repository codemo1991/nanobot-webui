"""Runtime：执行任务、构建 context、spawn、写 artifact。"""

import asyncio
import json
from pathlib import Path
from typing import Callable

from loguru import logger

from nanobot.agentloop.db import tx
from nanobot.agentloop.kernel.artifact_repo import create_artifact, get_artifact_payload
from nanobot.agentloop.kernel.dep_repo import add_read_dep
from nanobot.agentloop.kernel.ids import new_id, now_ts
from nanobot.agentloop.kernel.models import TaskSpec
from nanobot.agentloop.kernel.task_repo import (
    fulfill_pending_deps_for_artifact,
    mark_task_done,
    mark_task_failed,
    mark_task_reducing,
    mark_task_waiting_children,
    mark_task_waiting_artifacts,
    mark_waiting_artifacts_tasks_ready,
    reset_task_for_retry,
)

# 心跳更新间隔（秒）：定期刷新 RUNNING 任务的 updated_at，防止被误判为僵死
_HEARTBEAT_INTERVAL = 60.0


class Runtime:
    """任务执行运行时。"""

    def __init__(self, conn, registry, workspace: Path | None = None):
        self.conn = conn
        self.registry = registry
        self.workspace = workspace
        self._done_callback: Callable | None = None
        # Fix #4: 任务就绪通知回调，由 Kernel 注入，新 READY 任务出现时触发
        self._task_ready_callback: Callable | None = None

    def set_done_callback(self, callback: Callable) -> None:
        """注册 trace 完成通知回调（trace 变为 DONE/FAILED 时调用）。"""
        self._done_callback = callback

    def set_task_ready_callback(self, callback: Callable) -> None:
        """注册任务就绪通知回调（新 READY 任务出现时调用，用于唤醒 worker）。"""
        self._task_ready_callback = callback

    def _notify_if_trace_done(self, trace_id: str) -> None:
        """检查 trace 状态，若已终结则触发回调通知等待方。"""
        if not self._done_callback:
            return
        row = self.conn.execute(
            "SELECT status FROM agentloop_traces WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        if row and row["status"] in ("DONE", "FAILED", "CANCELED"):
            self._done_callback()

    def _notify_task_ready(self) -> None:
        """通知 worker 有新的 READY 任务。"""
        if self._task_ready_callback:
            self._task_ready_callback()

    def _load_artifact_payload_safe(self, artifact_id: str) -> dict | None:
        """加载 artifact payload，支持 INLINE/FILE，含 JSON 异常处理。

        Fix #12: build_context 原先在 JOIN 中取出 payload_text 再通过 _load_artifact_payload
        做第二次 SELECT（双重加载），现统一走 get_artifact_payload 单次读取。
        """
        try:
            return get_artifact_payload(self.conn, artifact_id)
        except json.JSONDecodeError as e:
            logger.warning("Artifact %s payload JSON 解析失败: %s", artifact_id, e)
        return None

    def build_context(self, task_id: str) -> dict:
        """构建任务执行上下文（含所需 artifacts）。

        Fix #12: 依赖查询不再 JOIN 取 payload_text/payload_path（避免大 payload
        全量加载进内存），改为单独调用 get_artifact_payload 按需读取。
        """
        task = self.conn.execute(
            "SELECT * FROM agentloop_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not task:
            return {}

        # 只取元信息，不取 payload 字段，减少内存占用
        deps = self.conn.execute(
            """
            SELECT d.alias, a.artifact_type, a.artifact_id
            FROM agentloop_task_artifact_deps d
            JOIN agentloop_artifacts a ON a.artifact_id = d.artifact_id
            WHERE d.task_id = ?
              AND d.mode = 'READ'
              AND a.status = 'READY'
            """,
            (task_id,),
        ).fetchall()

        artifacts: dict = {}
        artifact_list: dict = {}

        for row in deps:
            # 按需加载 payload（支持 INLINE 和 FILE 两种存储）
            payload = self._load_artifact_payload_safe(row["artifact_id"])
            if payload is None:
                payload = {}
            artifact_type = row["artifact_type"]
            alias = row["alias"]

            if alias:
                artifacts[alias] = payload
            else:
                if artifact_type.endswith("_v1"):
                    artifact_list.setdefault(artifact_type, []).append(payload)
                else:
                    artifacts[artifact_type] = payload

        ctx = {
            "trace_id": task["trace_id"],
            "task_id": task_id,
            "constraints": {
                "deadline_ts": task["deadline_ts"],
                "budget_tokens": task["budget_tokens"],
                "budget_millis": task["budget_millis"],
                "budget_cost_cents": task["budget_cost_cents"],
            },
            "artifacts": artifacts,
            "artifact_list": artifact_list,
        }
        if self.workspace is not None:
            ctx["workspace"] = self.workspace
        return ctx

    def write_output_artifact(self, task: dict, output_artifact: dict | None) -> str | None:
        """写入任务产出 artifact，返回 artifact_id。"""
        if not output_artifact:
            return None
        artifact_id = create_artifact(
            self.conn,
            trace_id=task["trace_id"],
            producer_task_id=task["task_id"],
            artifact_type=output_artifact["artifact_type"],
            payload=output_artifact["payload"],
            workspace_root=self.workspace,
        )
        fulfill_pending_deps_for_artifact(self.conn, artifact_id)
        mark_waiting_artifacts_tasks_ready(self.conn, artifact_id)
        # Fix #4: artifact 就绪后可能有 WAITING_ARTIFACTS 任务变为 READY
        self._notify_task_ready()
        return artifact_id

    def spawn_children(
        self,
        parent_task: dict,
        specs: list[TaskSpec],
        parent_artifact_id: str | None = None,
        parent_artifact_type: str | None = None,
    ) -> None:
        """spawn 子任务，并为需要父产出的子任务注入 READ 依赖。"""
        ts = now_ts()

        with tx(self.conn, immediate=True):
            for spec in specs:
                child_id = new_id("tk")

                self.conn.execute(
                    """
                    INSERT INTO agentloop_tasks(
                        task_id, trace_id, parent_task_id, task_kind, capability_name, intent, state,
                        priority, depth, budget_tokens, budget_millis, budget_cost_cents, deadline_ts,
                        attempt_no, max_retries, expected_children, finished_children, join_policy,
                        input_schema, output_schema, request_payload, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'READY',
                            ?, ?, ?, ?, ?, ?,
                            0, ?, 0, 0, 'ALL',
                            ?, ?, ?, ?, ?)
                    """,
                    (
                        child_id,
                        parent_task["trace_id"],
                        parent_task["task_id"],
                        spec.task_kind,
                        spec.capability_name,
                        spec.intent,
                        spec.priority,
                        parent_task["depth"] + 1,
                        spec.budget_tokens,
                        spec.budget_millis,
                        spec.budget_cost_cents,
                        spec.deadline_ts,
                        spec.max_retries,
                        spec.input_schema,
                        spec.output_schema,
                        json.dumps(spec.request_payload, ensure_ascii=False),
                        ts,
                        ts,
                    ),
                )
                self.conn.execute(
                    """
                    INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
                    VALUES (?, ?, ?, 'TASK_SPAWN', ?, ?)
                    """,
                    (
                        parent_task["trace_id"],
                        child_id,
                        parent_task["task_id"],
                        json.dumps({"capability_name": spec.capability_name, "intent": spec.intent}),
                        ts,
                    ),
                )

                if parent_artifact_id and parent_artifact_type and spec.input_schema:
                    add_read_dep(
                        self.conn,
                        child_id,
                        parent_artifact_id,
                        alias=parent_artifact_type,
                        required=True,
                    )

        # Fix #4: 子任务已 READY，通知 worker 立即检查
        self._notify_task_ready()

    def _inject_sibling_deps(
        self, task: dict, artifact_id: str, artifact_type: str | None
    ) -> None:
        """任务完成后，为等待该 artifact 的兄弟任务注入 READ 依赖。"""
        parent_id = task.get("parent_task_id")
        if not parent_id or not artifact_id or not artifact_type:
            return

        siblings = self.conn.execute(
            """
            SELECT t.task_id, t.input_schema, t.state
            FROM agentloop_tasks t
            WHERE t.parent_task_id = ? AND t.task_id != ?
            """,
            (parent_id, task["task_id"]),
        ).fetchall()

        with tx(self.conn, immediate=True):
            for sib in siblings:
                if sib["state"] in ("READY", "WAITING_ARTIFACTS") and sib["input_schema"]:
                    alias = None if "search_result" in artifact_type else artifact_type
                    add_read_dep(self.conn, sib["task_id"], artifact_id, alias=alias, required=True)

    def after_task_done(
        self, task: dict, artifact_id: str | None, artifact_type: str | None
    ) -> None:
        """任务完成后推进父任务或 trace，并为兄弟 reducer 注入依赖。"""
        if artifact_id and artifact_type:
            self._inject_sibling_deps(task, artifact_id, artifact_type)

        parent_id = task.get("parent_task_id")
        if not parent_id:
            ts = now_ts()
            with tx(self.conn, immediate=True):
                self.conn.execute(
                    """
                    UPDATE agentloop_traces SET status = 'DONE', finished_at = ?, updated_at = ?
                    WHERE trace_id = ? AND status = 'RUNNING'
                    """,
                    (ts, ts, task["trace_id"]),
                )
                self.conn.execute(
                    """
                    INSERT INTO agentloop_events(trace_id, task_id, parent_task_id, event_type, event_payload, created_at)
                    VALUES (?, ?, NULL, 'TRACE_DONE', '{}', ?)
                    """,
                    (task["trace_id"], task["task_id"], ts),
                )
            return

        parent = self.conn.execute(
            """
            SELECT task_id, expected_children, finished_children, state, capability_name
            FROM agentloop_tasks WHERE task_id = ?
            """,
            (parent_id,),
        ).fetchone()

        if not parent:
            return

        if (
            parent["state"] == "WAITING_CHILDREN"
            and parent["finished_children"] >= parent["expected_children"]
        ):
            mark_task_reducing(self.conn, parent_id, task["trace_id"])

    async def _heartbeat_task_running(self, task_id: str) -> None:
        """Fix #5: 心跳协程，定期刷新 RUNNING 任务的 updated_at。

        防止 recover_stale_tasks 将合法的长时间运行任务（如大型 LLM 调用）
        错误地判定为僵死并触发重试，避免同一任务并发执行两次。
        """
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                self.conn.execute(
                    "UPDATE agentloop_tasks SET updated_at = ? WHERE task_id = ? AND state = 'RUNNING'",
                    (now_ts(), task_id),
                )
        except asyncio.CancelledError:
            pass

    async def execute_task(self, task: dict) -> None:
        """执行单个任务。"""
        try:
            capability = self.registry.get(task["capability_name"])
        except KeyError:
            mark_task_failed(
                self.conn,
                task["task_id"],
                "CAPABILITY_NOT_FOUND",
                f"Capability not registered: {task['capability_name']}",
            )
            return

        try:
            request = json.loads(task["request_payload"] or "{}")
        except json.JSONDecodeError as e:
            mark_task_failed(
                self.conn,
                task["task_id"],
                "INVALID_REQUEST_PAYLOAD",
                str(e),
            )
            return

        context = self.build_context(task["task_id"])

        # Fix #5: 启动心跳任务，防止合法长时间运行的任务被 recover_stale_tasks 误杀
        heartbeat = asyncio.create_task(self._heartbeat_task_running(task["task_id"]))
        try:
            result = await capability.invoke(request, context)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

        if result.status == "DONE":
            artifact_id = self.write_output_artifact(task, result.output_artifact)
            artifact_type = result.output_artifact["artifact_type"] if result.output_artifact else None
            mark_task_done(self.conn, task["task_id"], artifact_id)
            self.after_task_done(task, artifact_id, artifact_type)

        elif result.status == "WAITING_CHILDREN":
            artifact_id = None
            artifact_type = None
            if result.output_artifact:
                artifact_id = self.write_output_artifact(task, result.output_artifact)
                artifact_type = result.output_artifact["artifact_type"]

            self.spawn_children(
                task,
                result.spawn_specs,
                parent_artifact_id=artifact_id,
                parent_artifact_type=artifact_type,
            )

            mark_task_waiting_children(
                self.conn,
                task["task_id"],
                artifact_id,
                expected_children=len(result.spawn_specs),
            )

        elif result.status == "WAITING_ARTIFACTS":
            mark_task_waiting_artifacts(self.conn, task["task_id"], result.wait_for_artifacts)

        else:
            mark_task_failed(
                self.conn,
                task["task_id"],
                result.error_code or "UNKNOWN",
                result.error_message or "Unknown error",
            )

        self._notify_if_trace_done(task["trace_id"])

    def handle_task_exception(self, task: dict, exc: Exception) -> None:
        """处理任务执行异常（重试或失败）。
        attempt_no 表示已执行次数（mark_task_running 中自增），max_retries 表示允许的重试次数，
        故 attempt_no <= max_retries 时仍可重试。
        """
        row = self.conn.execute(
            "SELECT attempt_no, max_retries FROM agentloop_tasks WHERE task_id = ?",
            (task["task_id"],),
        ).fetchone()

        if row and row["attempt_no"] <= row["max_retries"]:
            reset_task_for_retry(
                self.conn,
                task["task_id"],
                "RETRYABLE_ERROR",
                str(exc),
            )
            # Fix #4: 重置为 READY 后通知 worker
            self._notify_task_ready()
        else:
            mark_task_failed(self.conn, task["task_id"], "FAILED", str(exc))

        self._notify_if_trace_done(task["trace_id"])
