"""AgentLoop 微内核主控。"""

import asyncio
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from nanobot.agentloop.db import connect_chat, connect_system, init_chat_schema, init_system_schema
from nanobot.agentloop.kernel.task_repo import lease_one_ready_task, mark_task_running, recover_stale_tasks
from nanobot.agentloop.kernel.trace_repo import (
    create_initial_artifacts,
    create_trace_and_root_task,
    mark_trace_canceled,
)

# worker 空闲时的轮询间隔（任务调度需保持响应）
_WORKER_POLL_INTERVAL = 0.1
# run_until_done 兜底轮询间隔（event 触发后会立即唤醒，此处仅作超时保底）
_MAIN_POLL_INTERVAL = 2.0
# 僵死 RUNNING 任务的判定阈值（秒）
_STALE_RUNNING_SECONDS = 300
# 维护任务执行间隔（秒）
_MAINTENANCE_INTERVAL = 60.0


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
        poll_interval: float = _MAIN_POLL_INTERVAL,
        timeout_seconds: Optional[float] = 600.0,
    ) -> bool:
        """运行直到 trace 完成或超时，返回是否成功完成。

        采用 event 通知 + 兜底轮询双模式：
        - workers 执行完任务后通过 done_event 立即唤醒主循环
        - poll_interval 作为兜底（防止 event 丢失），默认 2 秒（原来 0.1 秒）
        """
        logger.info(f"[Kernel.run_until_done] 开始, trace_id={trace_id}")

        # asyncio.Event 只在当前协程所在事件循环内有效
        done_event = asyncio.Event()
        self.runtime.set_done_callback(lambda: done_event.set())

        workers = [
            asyncio.create_task(self._worker_loop(i)) for i in range(worker_count)
        ]
        maintenance = asyncio.create_task(self._maintenance_loop())

        start = time.monotonic()
        try:
            while not self.shutdown:
                row = self.conn.execute(
                    "SELECT status FROM agentloop_traces WHERE trace_id = ?", (trace_id,)
                ).fetchone()
                if row and row["status"] in ("DONE", "FAILED", "CANCELED"):
                    logger.info(
                        f"[Kernel.run_until_done] trace 完成, status={row['status']}, trace_id={trace_id}"
                    )
                    self.shutdown = True
                    break

                if timeout_seconds is not None:
                    elapsed = time.monotonic() - start
                    if elapsed > timeout_seconds:
                        logger.warning("AgentLoop trace %s 超时 (%.0fs)", trace_id, elapsed)
                        mark_trace_canceled(self.conn, trace_id, reason="TIMEOUT")
                        self.shutdown = True
                        break
                    wait = min(poll_interval, timeout_seconds - elapsed)
                else:
                    wait = poll_interval

                # 清除 event 后再等待，避免丢失在等待期间触发的通知
                done_event.clear()
                # 二次检查：清除后若 trace 已完成则不必等待
                row = self.conn.execute(
                    "SELECT status FROM agentloop_traces WHERE trace_id = ?", (trace_id,)
                ).fetchone()
                if row and row["status"] in ("DONE", "FAILED", "CANCELED"):
                    self.shutdown = True
                    break

                try:
                    await asyncio.wait_for(done_event.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    pass  # 兜底轮询，继续下一轮检查
        finally:
            self.shutdown = True
            maintenance.cancel()
            logger.debug(f"[Kernel.run_until_done] 等待 {len(workers)} workers 结束")
            await asyncio.gather(*workers, maintenance, return_exceptions=True)
            logger.info("[Kernel.run_until_done] 全部协程已结束")

        row = self.conn.execute(
            "SELECT status FROM agentloop_traces WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        return row is not None and row["status"] == "DONE"

    async def _worker_loop(self, worker_idx: int) -> None:
        """Worker 协程：循环领取并执行任务。"""
        lease_owner = f"worker-{worker_idx}"
        idle_count = 0
        while not self.shutdown:
            task = lease_one_ready_task(self.conn, lease_owner=lease_owner, lease_seconds=30)
            if not task:
                idle_count += 1
                if idle_count <= 3 or idle_count % 50 == 0:
                    logger.debug(f"[Kernel worker-{worker_idx}] 第 {idle_count} 次空闲轮询")
                await asyncio.sleep(_WORKER_POLL_INTERVAL)
                continue

            idle_count = 0
            logger.info(
                f"[Kernel worker-{worker_idx}] 领取任务: {task['task_id']}, "
                f"cap={task['capability_name']}, kind={task['task_kind']}"
            )
            mark_task_running(self.conn, task["task_id"])

            try:
                await self.runtime.execute_task(task)
                logger.info(f"[Kernel worker-{worker_idx}] 任务执行完成: {task['task_id']}")
            except Exception as exc:
                logger.exception("任务 %s 执行异常: %s", task["task_id"], exc)
                self.runtime.handle_task_exception(task, exc)

    async def _maintenance_loop(self) -> None:
        """周期性维护：恢复僵死的 RUNNING 任务（进程崩溃后遗留）。"""
        while not self.shutdown:
            await asyncio.sleep(_MAINTENANCE_INTERVAL)
            if self.shutdown:
                break
            try:
                recovered = recover_stale_tasks(self.conn, stale_seconds=_STALE_RUNNING_SECONDS)
                if recovered:
                    logger.info(f"[Kernel.maintenance] 恢复了 {recovered} 个僵死任务")
            except Exception as e:
                logger.warning(f"[Kernel.maintenance] 维护异常: {e}")

    async def run_forever(self, worker_count: int = 4) -> None:
        """持续运行 worker（用于多 trace 并发）。"""
        workers = [asyncio.create_task(self._worker_loop(i)) for i in range(worker_count)]
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
