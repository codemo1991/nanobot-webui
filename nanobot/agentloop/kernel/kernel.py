"""AgentLoop 微内核主控。"""

import asyncio
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from nanobot.agentloop.db import connect_chat, connect_system, init_chat_schema, init_system_schema, run_chat_migrations
from nanobot.agentloop.kernel.task_repo import lease_one_ready_task, mark_task_running, recover_stale_tasks
from nanobot.agentloop.kernel.trace_repo import (
    create_initial_artifacts,
    create_trace_and_root_task,
    mark_trace_canceled,
)

# run_until_done 兜底轮询间隔（event 触发后会立即唤醒，此处仅作超时保底）
_MAIN_POLL_INTERVAL = 2.0
# worker 等待任务就绪事件的超时（兜底间隔，避免 event 丢失时永久阻塞）
_WORKER_WAIT_TIMEOUT = 2.0
# 僵死 RUNNING 任务的判定阈值（秒）—— 任务有心跳续租后可适当放大
_STALE_RUNNING_SECONDS = 300
# 维护任务执行间隔（秒）
_MAINTENANCE_INTERVAL = 60.0


class Kernel:
    """AgentLoop 微内核。

    Notes:
        self.conn 为创建时绑定的 SQLite 连接，仅供其所在的 asyncio 事件循环线程使用。
        HTTP handler 等其他线程应通过 db.get_thread_chat_conn(workspace) 获取线程安全连接。
    """

    def __init__(self, conn, registry, runtime, workspace: Path | None = None):
        self.conn = conn
        self.registry = registry
        self.runtime = runtime
        self.workspace = workspace
        self.shutdown = False
        # 任务就绪通知事件：新 READY 任务出现时设置，驱动 worker 立即醒来
        self._task_ready_event: asyncio.Event | None = None

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
        logger.info("AgentLoop 已提交 trace=%s root_task=%s", trace_id, root_task_id)
        # 根任务已 READY，通知 worker 立即检查
        if self._task_ready_event is not None:
            self._task_ready_event.set()
        return trace_id, root_task_id

    async def run_until_done(
        self,
        trace_id: str,
        worker_count: int = 4,
        poll_interval: float = _MAIN_POLL_INTERVAL,
        timeout_seconds: Optional[float] = 600.0,
    ) -> bool:
        """运行直到 trace 完成或超时，返回是否成功完成。

        支持同一 Kernel 实例多次调用（每次重置 shutdown 标志）。
        采用 event 通知 + 兜底轮询双模式。
        """
        # Fix #1: 每次运行前重置，支持内核实例复用（同一 Kernel 执行多个 trace）
        self.shutdown = False

        logger.info("[Kernel.run_until_done] 开始, trace_id=%s", trace_id)

        done_event = asyncio.Event()
        self.runtime.set_done_callback(lambda: done_event.set())

        # Fix #4: 任务就绪通知事件，驱动 worker 立即醒来，避免空转轮询
        self._task_ready_event = asyncio.Event()
        self.runtime.set_task_ready_callback(lambda: self._task_ready_event.set())

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
                        "[Kernel.run_until_done] trace 完成, status=%s, trace_id=%s",
                        row["status"], trace_id,
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

                done_event.clear()
                # 二次检查：避免 clear 后遗漏已完成的 trace
                row = self.conn.execute(
                    "SELECT status FROM agentloop_traces WHERE trace_id = ?", (trace_id,)
                ).fetchone()
                if row and row["status"] in ("DONE", "FAILED", "CANCELED"):
                    self.shutdown = True
                    break

                try:
                    await asyncio.wait_for(done_event.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.shutdown = True
            maintenance.cancel()
            logger.debug("[Kernel.run_until_done] 等待 %d workers 结束", len(workers))
            await asyncio.gather(*workers, maintenance, return_exceptions=True)
            logger.info("[Kernel.run_until_done] 全部协程已结束")

        row = self.conn.execute(
            "SELECT status FROM agentloop_traces WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        return row is not None and row["status"] == "DONE"

    async def _worker_loop(self, worker_idx: int) -> None:
        """Worker 协程：循环领取并执行任务。

        Fix #4: 有任务就绪时通过 _task_ready_event 立即唤醒，避免 100ms 盲轮询。
        """
        lease_owner = f"worker-{worker_idx}"
        while not self.shutdown:
            task = lease_one_ready_task(self.conn, lease_owner=lease_owner, lease_seconds=30)
            if not task:
                # 等待任务就绪事件（或兜底超时），避免空转
                if self._task_ready_event is not None:
                    self._task_ready_event.clear()
                    # 二次检查：clear 后立即再看一次，避免遗漏 clear 前设置的事件
                    task = lease_one_ready_task(self.conn, lease_owner=lease_owner, lease_seconds=30)
                    if not task:
                        try:
                            await asyncio.wait_for(
                                self._task_ready_event.wait(),
                                timeout=_WORKER_WAIT_TIMEOUT,
                            )
                        except asyncio.TimeoutError:
                            pass
                        continue
                else:
                    await asyncio.sleep(_WORKER_WAIT_TIMEOUT)
                    continue

            logger.info(
                "[Kernel worker-%d] 领取任务: %s, cap=%s, kind=%s",
                worker_idx, task["task_id"], task["capability_name"], task["task_kind"],
            )
            mark_task_running(self.conn, task["task_id"])

            try:
                await self.runtime.execute_task(task)
                logger.info("[Kernel worker-%d] 任务执行完成: %s", worker_idx, task["task_id"])
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
                    logger.info("[Kernel.maintenance] 恢复了 %d 个僵死任务", recovered)
                    # 通知 worker 有新的 READY 任务
                    if self._task_ready_event is not None:
                        self._task_ready_event.set()
            except Exception as e:
                logger.warning("[Kernel.maintenance] 维护异常: %s", e)

    async def run_forever(self, worker_count: int = 4) -> None:
        """持续运行 worker（用于多 trace 并发场景）。

        Fix #1 对齐: 同样重置 shutdown 并初始化 task_ready_event。
        """
        self.shutdown = False
        self._task_ready_event = asyncio.Event()
        self.runtime.set_task_ready_callback(lambda: self._task_ready_event.set())

        workers = [asyncio.create_task(self._worker_loop(i)) for i in range(worker_count)]
        maintenance = asyncio.create_task(self._maintenance_loop())
        await asyncio.gather(*workers, maintenance, return_exceptions=True)


def create_kernel(
    workspace: Path | None = None,
    registry=None,
    runtime=None,
    brave_api_key: str | None = None,
):
    """创建并初始化 Kernel 实例。"""
    conn = connect_chat(workspace)
    init_chat_schema(conn)  # 内部已调用 run_chat_migrations

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
