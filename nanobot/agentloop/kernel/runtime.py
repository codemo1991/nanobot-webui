"""Runtime：执行任务、构建 context、spawn、写 artifact。"""

import json
from pathlib import Path

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


class Runtime:
    """任务执行运行时。"""

    def __init__(self, conn, registry, workspace: Path | None = None):
        self.conn = conn
        self.registry = registry
        self.workspace = workspace

    def _load_artifact_payload(self, row) -> dict | None:
        """从 artifact 行加载 payload，支持 INLINE/FILE，含 JSON 异常处理。"""
        try:
            return get_artifact_payload(self.conn, row["artifact_id"])
        except json.JSONDecodeError as e:
            logger.warning("Artifact payload JSON 解析失败: %s", e)
        return None

    def build_context(self, task_id: str) -> dict:
        """构建任务执行上下文（含所需 artifacts）。"""
        task = self.conn.execute(
            "SELECT * FROM agentloop_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not task:
            return {}

        deps = self.conn.execute(
            """
            SELECT d.mode, d.alias, a.artifact_type, a.artifact_id, a.storage_kind, a.payload_text, a.payload_path
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
            payload = self._load_artifact_payload(row)
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

                # 为需要父产出的子任务注入 READ 依赖
                if parent_artifact_id and parent_artifact_type and spec.input_schema:
                    add_read_dep(
                        self.conn,
                        child_id,
                        parent_artifact_id,
                        alias=parent_artifact_type,
                        required=True,
                    )

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
                    # search_result_v1 为列表，用 alias=None 进入 artifact_list；其余用 type 作 alias
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

        result = await capability.invoke(request, context)

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
        else:
            mark_task_failed(self.conn, task["task_id"], "FAILED", str(exc))
