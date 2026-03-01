"""Cron service using APScheduler for scheduling agent tasks."""

import asyncio
import uuid
from pathlib import Path
from typing import Any, Callable, Coroutine

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from nanobot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule
from nanobot.storage.cron_repository import CronRepository


def _now_ms() -> int:
    """Get current timestamp in milliseconds."""
    import time
    return int(time.time() * 1000)


def _compute_next_run_ms(trigger_type: str, trigger_date_ms: int | None,
                          trigger_interval_seconds: int | None,
                          trigger_cron_expr: str | None) -> int | None:
    """Compute next run time in ms based on trigger config."""
    from croniter import croniter
    import time

    if trigger_type == "at":
        if trigger_date_ms and trigger_date_ms > _now_ms():
            return trigger_date_ms
        return None

    if trigger_type == "every":
        if trigger_interval_seconds and trigger_interval_seconds > 0:
            return _now_ms() + (trigger_interval_seconds * 1000)
        return None

    if trigger_type == "cron" and trigger_cron_expr:
        try:
            cron = croniter(trigger_cron_expr, time.time())
            next_time = cron.get_next()
            return int(next_time * 1000)
        except Exception as e:
            logger.warning(f"Invalid cron expression: {trigger_cron_expr}: {e}")
            return None

    return None


class CronService:
    """Service for managing and executing scheduled jobs using APScheduler."""

    def __init__(
        self,
        db_path: Path,
        on_job: Callable[[dict[str, Any]], Coroutine[Any, Any, str | None]] | None = None
    ):
        self.db_path = db_path
        self.repository = CronRepository(db_path)
        self.on_job = on_job  # Callback to execute job, returns response text

        # APScheduler setup
        self._scheduler: AsyncIOScheduler | None = None
        self._executors = {"default": AsyncIOExecutor()}
        self._job_defaults = {"coalesce": True, "max_instances": 1}

    async def start(self) -> None:
        """Start the cron service and scheduler."""
        # Load existing jobs from database and add to scheduler
        jobs = self.repository.get_all_jobs(include_disabled=True)

        self._scheduler = AsyncIOScheduler(
            executors=self._executors,
            job_defaults=self._job_defaults,
        )

        # Start scheduler first
        self._scheduler.start()

        # Add all enabled jobs to scheduler
        for job in jobs:
            if job["enabled"]:
                self._add_job_to_scheduler(job)

        logger.info(f"Cron service started with {len([j for j in jobs if j['enabled']])} enabled jobs")

    async def sync_from_db(self) -> None:
        """将调度器与数据库中的任务同步（用于 gateway 感知 web-ui 创建/更新的任务）。"""
        if not self._scheduler:
            return
        try:
            db_jobs = {j["id"]: j for j in self.repository.get_all_jobs(include_disabled=True)}
            scheduled_ids = {j.id for j in self._scheduler.get_jobs()}

            # 移除调度器中已不存在或已禁用的任务
            for job_id in list(scheduled_ids):
                db_job = db_jobs.get(job_id)
                if not db_job or not db_job["enabled"]:
                    self._scheduler.remove_job(job_id)
                    logger.debug(f"Cron sync: 移除任务 {job_id}")

            # 将 DB 中新启用的任务加入调度器
            scheduled_ids = {j.id for j in self._scheduler.get_jobs()}
            added = 0
            for job_id, job in db_jobs.items():
                if job["enabled"] and job_id not in scheduled_ids:
                    self._add_job_to_scheduler(job)
                    added += 1
                    logger.info(f"Cron sync: 新增任务 '{job['name']}' ({job_id})")

            if added:
                logger.info(f"Cron sync: 共新增 {added} 个任务")
        except Exception as e:
            logger.warning(f"Cron DB sync 失败: {e}")

    def stop(self) -> None:
        """Stop the cron service and scheduler."""
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        logger.info("Cron service stopped")

    def _add_job_to_scheduler(self, job: dict[str, Any]) -> None:
        """Add a job to the APScheduler."""
        if not self._scheduler:
            return

        job_id = job["id"]
        trigger = self._build_trigger(job["trigger"])

        if trigger:
            try:
                self._scheduler.add_job(
                    self._execute_job_callback,
                    trigger=trigger,
                    id=job_id,
                    name=job["name"],
                    replace_existing=True,
                    next_run_time=None,  # Will be set by APScheduler
                    jobstore="default",
                    job_id=job_id,  # Pass job_id as kwarg so callback receives it
                )
            except Exception as e:
                logger.error(f"Failed to add job {job_id} to scheduler: {e}")

    def _build_trigger(self, trigger_config: dict[str, Any]):
        """Build APScheduler trigger from job trigger config."""
        import pytz

        trigger_type = trigger_config.get("type")

        if trigger_type == "at":
            date_ms = trigger_config.get("dateMs")
            if date_ms:
                from datetime import datetime
                # Convert ms to datetime
                dt = datetime.fromtimestamp(date_ms / 1000, tz=pytz.UTC)
                return DateTrigger(run_date=dt)

        elif trigger_type == "every":
            interval_sec = trigger_config.get("intervalSeconds")
            if interval_sec and interval_sec > 0:
                return IntervalTrigger(seconds=interval_sec)

        elif trigger_type == "cron":
            expr = trigger_config.get("cronExpr")
            if expr:
                tz = trigger_config.get("tz") or "UTC"
                try:
                    tz_obj = pytz.timezone(tz)
                except Exception:
                    tz_obj = pytz.UTC

                # 获取结束日期 - apscheduler 3.x 的 from_crontab 不支持 end_date
                # 如果需要 end_date 功能，需要升级到更高版本或使用其他方式
                # 这里暂时忽略 end_date

                return CronTrigger.from_crontab(expr, timezone=tz_obj)

        return None

    async def _execute_job_callback(self, job_id: str = None) -> None:
        """Callback wrapper to execute a job."""
        if job_id is None:
            logger.warning("Cron job callback received no job_id")
            return
        await self._execute_job(job_id)

    async def _execute_job(self, job_id: str) -> None:
        """Execute a single job."""
        job = self.repository.get_job(job_id)
        if not job:
            logger.warning(f"Cron job not found: {job_id}")
            return

        start_ms = _now_ms()
        logger.info(f"Cron: executing job '{job['name']}' ({job_id})")

        try:
            # Call the on_job callback if provided
            response = None
            if self.on_job:
                response = await self.on_job(job)

            # Update job status (成功时清除错误信息)
            self.repository.update_job_status(
                job_id=job_id,
                last_run_at_ms=start_ms,
                last_status="ok",
                clear_error=True,
            )
            logger.info(f"Cron: job '{job['name']}' completed")

        except Exception as e:
            self.repository.update_job_status(
                job_id=job_id,
                last_run_at_ms=start_ms,
                last_status="error",
                last_error=str(e),
            )
            logger.error(f"Cron: job '{job['name']}' failed: {e}")

        # Handle one-shot jobs ("at" trigger)
        if job["trigger"]["type"] == "at":
            if job["deleteAfterRun"]:
                self.repository.delete_job(job_id)
                if self._scheduler:
                    self._scheduler.remove_job(job_id)
                logger.info(f"Cron: deleted one-shot job {job_id}")
            else:
                # Disable the job after running
                self.repository.update_job(job_id, enabled=False)
                if self._scheduler:
                    self._scheduler.remove_job(job_id)
        else:
            # 更新下次运行时间到数据库（APScheduler 的 Interval/CronTrigger 会自动重新调度）
            next_run_ms = _compute_next_run_ms(
                job["trigger"]["type"],
                job["trigger"].get("dateMs"),
                job["trigger"].get("intervalSeconds"),
                job["trigger"].get("cronExpr"),
            )
            if next_run_ms:
                self.repository.update_job_status(job_id, next_run_at_ms=next_run_ms)

    def _reschedule_job(self, job: dict[str, Any]) -> None:
        """Reschedule a job in APScheduler."""
        if not self._scheduler:
            return

        trigger = self._build_trigger(job["trigger"])
        if trigger:
            try:
                self._scheduler.reschedule_job(job["id"], trigger=trigger)
            except Exception as e:
                logger.error(f"Failed to reschedule job {job['id']}: {e}")

    # ========== Public API ==========

    def list_jobs(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        """List all jobs."""
        return self.repository.get_all_jobs(include_disabled=include_disabled)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Get a single job by ID."""
        return self.repository.get_job(job_id)

    def add_job(
        self,
        name: str,
        trigger_type: str,
        trigger_date_ms: int | None = None,
        trigger_interval_seconds: int | None = None,
        trigger_cron_expr: str | None = None,
        trigger_tz: str | None = None,
        trigger_end_date: str | None = None,
        payload_kind: str = "agent_turn",
        payload_message: str = "",
        payload_deliver: bool = False,
        payload_channel: str | None = None,
        payload_to: str | None = None,
        delete_after_run: bool = False,
        source: str = "",
    ) -> dict[str, Any]:
        """Add a new job.

        Args:
            name: Job name
            trigger_type: Trigger type ("at", "every", "cron")
            trigger_date_ms: For "at" trigger - timestamp in ms
            trigger_interval_seconds: For "every" trigger - interval in seconds
            trigger_cron_expr: For "cron" trigger - cron expression
            trigger_tz: Timezone for cron expression
            trigger_end_date: For "cron" trigger - end date (YYYY-MM-DD)
            payload_kind: Payload kind ("agent_turn", "system_event", "calendar_reminder")
            payload_message: Message to send
            payload_deliver: Whether to deliver response
            payload_channel: Channel for delivery
            payload_to: Recipient for delivery
            delete_after_run: Delete job after execution
            source: Job source ("system" | "calendar" | "")
        """
        job_id = str(uuid.uuid4())[:8]
        next_run_ms = _compute_next_run_ms(
            trigger_type, trigger_date_ms, trigger_interval_seconds, trigger_cron_expr
        )

        job = self.repository.create_job(
            job_id=job_id,
            name=name,
            trigger_type=trigger_type,
            trigger_date_ms=trigger_date_ms,
            trigger_interval_seconds=trigger_interval_seconds,
            trigger_cron_expr=trigger_cron_expr,
            trigger_tz=trigger_tz,
            trigger_end_date=trigger_end_date,
            payload_kind=payload_kind,
            payload_message=payload_message,
            payload_deliver=payload_deliver,
            payload_channel=payload_channel,
            payload_to=payload_to,
            delete_after_run=delete_after_run,
            source=source,
        )

        # Update next run time in DB
        if next_run_ms:
            self.repository.update_job_status(job_id, next_run_at_ms=next_run_ms)

        # Add to scheduler if enabled
        if self._scheduler:
            job["trigger"] = {
                "type": trigger_type,
                "dateMs": trigger_date_ms,
                "intervalSeconds": trigger_interval_seconds,
                "cronExpr": trigger_cron_expr,
                "tz": trigger_tz,
                "endDate": trigger_end_date,
            }
            job["enabled"] = True
            try:
                self._add_job_to_scheduler(job)
            except RuntimeError as e:
                # Event loop issue - try to recover
                if "loop is closed" in str(e).lower():
                    logger.warning(f"Event loop issue when adding job {job_id}, will be scheduled on next restart")
                else:
                    raise

        logger.info(f"Cron: added job '{name}' ({job_id})")
        return self.repository.get_job(job_id)

    def update_job(
        self,
        job_id: str,
        name: str | None = None,
        enabled: bool | None = None,
        trigger_type: str | None = None,
        trigger_date_ms: int | None = None,
        trigger_interval_seconds: int | None = None,
        trigger_cron_expr: str | None = None,
        trigger_tz: str | None = None,
        payload_kind: str | None = None,
        payload_message: str | None = None,
        payload_deliver: bool | None = None,
        payload_channel: str | None = None,
        payload_to: str | None = None,
        delete_after_run: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing job."""
        existing = self.repository.get_job(job_id)
        if not existing:
            return None

        # Determine new trigger values
        new_trigger_type = trigger_type if trigger_type is not None else existing["trigger"]["type"]
        new_trigger_date_ms = trigger_date_ms if trigger_date_ms is not None else existing["trigger"].get("dateMs")
        new_trigger_interval = trigger_interval_seconds if trigger_interval_seconds is not None else existing["trigger"].get("intervalSeconds")
        new_trigger_cron = trigger_cron_expr if trigger_cron_expr is not None else existing["trigger"].get("cronExpr")
        new_trigger_tz = trigger_tz if trigger_tz is not None else existing["trigger"].get("tz")

        # Compute new next run time
        new_next_run_ms = None
        if enabled is None or enabled:
            new_next_run_ms = _compute_next_run_ms(
                new_trigger_type, new_trigger_date_ms, new_trigger_interval, new_trigger_cron
            )

        # Update job in database
        job = self.repository.update_job(
            job_id=job_id,
            name=name,
            enabled=enabled,
            trigger_type=trigger_type,
            trigger_date_ms=trigger_date_ms,
            trigger_interval_seconds=trigger_interval_seconds,
            trigger_cron_expr=trigger_cron_expr,
            trigger_tz=trigger_tz,
            payload_kind=payload_kind,
            payload_message=payload_message,
            payload_deliver=payload_deliver,
            payload_channel=payload_channel,
            payload_to=payload_to,
            delete_after_run=delete_after_run,
        )

        if job and new_next_run_ms:
            self.repository.update_job_status(job_id, next_run_at_ms=new_next_run_ms)
            job["nextRunAtMs"] = new_next_run_ms

        # Update scheduler
        if self._scheduler and job:
            # Remove existing job from scheduler
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass

            # Add back if enabled
            if job["enabled"]:
                job["trigger"] = {
                    "type": new_trigger_type,
                    "dateMs": new_trigger_date_ms,
                    "intervalSeconds": new_trigger_interval,
                    "cronExpr": new_trigger_cron,
                    "tz": new_trigger_tz,
                }
                self._add_job_to_scheduler(job)

        logger.info(f"Cron: updated job '{job_id}'")
        return job

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID."""
        # Remove from scheduler
        if self._scheduler:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass

        # Remove from database
        removed = self.repository.delete_job(job_id)
        if removed:
            logger.info(f"Cron: removed job {job_id}")
        return removed

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """Manually run a job."""
        job = self.repository.get_job(job_id)
        if not job:
            return False

        if not force and not job["enabled"]:
            return False

        await self._execute_job(job_id)
        return True

    def status(self) -> dict:
        """Get service status."""
        jobs = self.repository.get_all_jobs(include_disabled=True)
        enabled_jobs = [j for j in jobs if j["enabled"]]
        next_wake = None
        if enabled_jobs:
            next_run_times = [j.get("nextRunAtMs") for j in enabled_jobs if j.get("nextRunAtMs")]
            if next_run_times:
                next_wake = min(next_run_times)

        return {
            "enabled": self._scheduler is not None,
            "jobs": len(jobs),
            "enabled_jobs": len(enabled_jobs),
            "next_wake_at_ms": next_wake,
        }
