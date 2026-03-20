"""AgentLoop 微内核主控。"""

import asyncio
import time
from pathlib import Path

from loguru import logger

from nanobot.agentloop.db import connect_chat, connect_system, init_chat_schema, init_system_schema
from nanobot.agentloop.kernel.task_repo import lease_one_ready_task, mark_task_running
from nanobot.agentloop.kernel.trace_repo import (
    create_initial_artifacts,
    create_trace_and_root_task,
    mark_trace_canceled,
)


class Kernel:
    """AgentLoop 微内核。"""

    def __init__(self, conn, registry, runtime, workspace: Path | None = None):
        self.conn = conn
        self.registry = registry
        self.runtime = runtime
        self.workspace = workspace
        self.shutdown = False

    async def submit(
        self,
        user_input: str,
        initial_artifacts: dict[str, dict] | None = None,
        attempted_steps: list[dict] | None = None,
        conversation_summary: str | None = None,
    ) -> tuple[str, str]:
        """提交用户请求，返回 (trace_id, root_task_id)。"""
        request_payload = {
            "user_goal": user_input,
            "attempted_steps": attempted_steps or [],
            "conversation_summary": conversation_summary,
        }
        if initial_artifacts:
            request_payload["initial_artifacts_keys"] = list(initial_artifacts.keys())
        trace_id, root_task_id = create_trace_and_root_task(
            self.conn,
            user_input=user_input,
            request_payload=request_payload,
        )
        if initial_artifacts:
            create_initial_artifacts(
                self.conn,
                trace_id=trace_id,
                root_task_id=root_task_id,
                artifacts=initial_artifacts,
                workspace_root=self.workspace,
            )
        from loguru import logger
        logger.info("AgentLoop 已提交 trace=%s root_task=%s", trace_id, root_task_id)
        logger.info(f"[Kernel.submit] 完成, trace_id={trace_id}, root_task_id={root_task_id}")
        return trace_id, root_task_id

    async def run_until_done(
        self,
        trace_id: str,
        worker_count: int = 4,
        poll_interval: float = 0.1,
        timeout_seconds: float | None = None,
    ) -> bool:
        """运行直到 trace 完成或超时，返回是否成功完成。"""
        from loguru import logger
        logger.info(f"[Kernel.run_until_done] 开始, trace_id={trace_id}")
        workers = [asyncio.create_task(self._worker_loop(i, poll_interval)) for i in range(worker_count)]
        start = time.monotonic()
        poll_count = 0
        try:
            while not self.shutdown:
                poll_count += 1
                if poll_count <= 3 or poll_count % 20 == 0:
                    logger.info(f"[Kernel.run_until_done] poll {poll_count}, trace_id={trace_id}")
                row = self.conn.execute(
                    "SELECT status FROM agentloop_traces WHERE trace_id = ?", (trace_id,)
                ).fetchone()
                if row and row["status"] in ("DONE", "FAILED", "CANCELED"):
                    logger.info(f"[Kernel.run_until_done] trace 完成, status={row['status']}, trace_id={trace_id}")
                    self.shutdown = True
                    break
                if timeout_seconds and (time.monotonic() - start) > timeout_seconds:
                    logger.warning("AgentLoop trace %s 超时", trace_id)
                    mark_trace_canceled(self.conn, trace_id, reason="TIMEOUT")
                    self.shutdown = True
                    break
                await asyncio.sleep(poll_interval)
        finally:
            self.shutdown = True
            logger.info(f"[Kernel.run_until_done] 等待 {len(workers)} workers 结束")
            await asyncio.gather(*workers)
            logger.info(f"[Kernel.run_until_done] workers 全部结束")

        row = self.conn.execute(
            "SELECT status FROM agentloop_traces WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        return row is not None and row["status"] == "DONE"

    async def _worker_loop(self, worker_idx: int, poll_interval: float) -> None:
        """Worker 协程：循环领取并执行任务。"""
        from loguru import logger
        lease_owner = f"worker-{worker_idx}"
        poll_count = 0
        while not self.shutdown:
            poll_count += 1
            task = lease_one_ready_task(self.conn, lease_owner=lease_owner, lease_seconds=30)
            if not task:
                if poll_count <= 3 or poll_count % 50 == 0:
                    logger.debug(f"[Kernel worker-{worker_idx}] 第 {poll_count} 次轮询，未找到 READY 任务")
                await asyncio.sleep(poll_interval)
                continue
            logger.info(f"[Kernel worker-{worker_idx}] 领取任务: {task['task_id']}, cap={task['capability_name']}, kind={task['task_kind']}")
            mark_task_running(self.conn, task["task_id"])

            try:
                await self.runtime.execute_task(task)
                from loguru import logger
                logger.info(f"[Kernel worker-{worker_idx}] 任务执行完成: {task['task_id']}")
            except Exception as exc:
                logger.exception("任务 %s 执行异常: %s", task["task_id"], exc)
                self.runtime.handle_task_exception(task, exc)

    async def run_forever(self, worker_count: int = 4, poll_interval: float = 0.05) -> None:
        """持续运行 worker（用于多 trace 并发）。"""
        workers = [asyncio.create_task(self._worker_loop(i, poll_interval)) for i in range(worker_count)]
        await asyncio.gather(*workers)


def create_kernel(
    workspace: Path | None = None,
    registry=None,
    runtime=None,
    brave_api_key: str | None = None,
):
    """创建并初始化 Kernel 实例。"""
    conn = connect_chat(workspace)
    init_chat_schema(conn)

    sys_conn = connect_system()
    init_system_schema(sys_conn)
    sys_conn.close()

    if registry is None:
        from nanobot.agentloop.capabilities.registry import create_default_registry
        registry = create_default_registry(brave_api_key=brave_api_key)

    if runtime is None:
        from nanobot.agentloop.kernel.runtime import Runtime
        runtime = Runtime(conn, registry, workspace=workspace)

    return Kernel(conn, registry, runtime, workspace)
