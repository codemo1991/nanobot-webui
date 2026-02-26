"""Calendar reminder service - 事件与 Cron Job 的转换适配层"""

from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from nanobot.cron.service import CronService
from nanobot.storage.calendar_repository import CalendarRepository

# 重复规则到 Cron 表达式的映射
RECURRENCE_TO_CRON = {
    "daily": "{minute} {hour} * * *",       # 每天
    "weekly": "{minute} {hour} * * 1",       # 每周一
    "monthly": "{minute} {hour} 1 * *",      # 每月1号
}

# 最大提醒时间（1年）
MAX_REMINDER_DAYS = 365


class CalendarReminderService:
    """日历提醒服务 - 薄适配层，处理事件与 Cron Job 的转换"""

    def __init__(
        self,
        calendar_repo: CalendarRepository,
        cron_service: CronService,
    ):
        self.calendar_repo = calendar_repo
        self.cron_service = cron_service

    def _get_event_time_parts(self, event_start: datetime) -> tuple[int, int]:
        """从事件开始时间提取小时和分钟"""
        return event_start.hour, event_start.minute

    def _adjust_for_reminder_time(self, event_start: datetime, reminder_minutes: int) -> datetime:
        """根据提醒时间调整触发时间

        例如：事件9:00开始，提前15分钟提醒，则触发时间为8:45
        """
        return event_start - timedelta(minutes=reminder_minutes)

    def _build_cron_expr(self, trigger_time: datetime) -> str:
        """根据触发时间构建 cron 表达式"""
        hour, minute = trigger_time.hour, trigger_time.minute
        return f"{minute} {hour} * * *"

    def _build_recurrence_cron(self, trigger_time: datetime, recurrence: str) -> str:
        """根据重复规则构建 cron 表达式"""
        if recurrence == "none" or not recurrence:
            return self._build_cron_expr(trigger_time)

        cron_template = RECURRENCE_TO_CRON.get(recurrence)
        if not cron_template:
            logger.warning(f"Unknown recurrence: {recurrence}, using daily")
            return self._build_cron_expr(trigger_time)

        return cron_template.format(minute=trigger_time.minute, hour=trigger_time.hour)

    def _generate_job_id(self, event_id: str, reminder_time: int) -> str:
        """生成 cron job ID"""
        return f"cal:{event_id}:{reminder_time}"

    def create_reminder_jobs(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """为日历事件创建提醒任务

        Args:
            event: 日历事件 dict

        Returns:
            创建的 cron jobs 列表
        """
        created_jobs = []

        # 获取提醒配置
        reminders = event.get("reminders", [])
        if not reminders:
            return created_jobs

        reminder_config = reminders[0]  # 只支持1个提醒
        reminder_time = reminder_config.get("time", 15)
        channel = reminder_config.get("channel") or ""
        target = reminder_config.get("target") or ""

        # 获取重复规则
        recurrence = event.get("recurrence") or "none"

        # 计算事件开始时间
        event_start_str = event.get("start_time") or event.get("start")
        if not event_start_str:
            logger.warning(f"Event {event.get('id')} has no start time")
            return created_jobs

        # 解析事件开始时间
        try:
            # 尝试解析 ISO 格式
            if event_start_str.endswith('Z'):
                event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00')).replace(tzinfo=None)
            elif '+' in event_start_str:
                event_start = datetime.fromisoformat(event_start_str.replace('+00:00', ''))
            else:
                event_start = datetime.fromisoformat(event_start_str)
        except Exception as e:
            logger.warning(f"Failed to parse event start time: {event_start_str}, {e}")
            return created_jobs

        # 根据提醒时间调整触发时间（提前 reminder_time 分钟）
        trigger_time = self._adjust_for_reminder_time(event_start, reminder_time)

        # 构建 cron 表达式（基于调整后的触发时间）
        cron_expr = self._build_recurrence_cron(trigger_time, recurrence)

        # 计算结束时间（事件开始时间 + 1年）
        end_dt = event_start + timedelta(days=MAX_REMINDER_DAYS)

        # 构建消息
        title = event.get("title", "事件")
        if reminder_time == 0:
            message = f"事件 \"{title}\" 即将开始"
        else:
            message = f"事件 \"{title}\" 将在 {reminder_time} 分钟后开始"

        # 生成 job ID
        job_id = self._generate_job_id(event.get("id"), reminder_time)

        try:
            job = self.cron_service.add_job(
                name=f"[日历] {title}",
                trigger_type="cron",
                trigger_cron_expr=cron_expr,
                trigger_tz="local",
                trigger_end_date=end_dt.strftime("%Y-%m-%d"),  # 限制结束日期
                payload_kind="calendar_reminder",
                payload_message=message,
                payload_deliver=bool(channel and target),  # 只有配置了渠道才推送
                payload_channel=channel,
                payload_to=target,
                delete_after_run=False,
                source="calendar",
            )
            created_jobs.append(job)
            logger.info(f"Created calendar reminder job: {job_id}, cron: {cron_expr}, end: {end_dt.date()}")
        except Exception as e:
            logger.error(f"Failed to create calendar reminder job: {e}")

        return created_jobs

    def update_reminder_jobs(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """更新日历事件的提醒任务

        先删除旧的，再创建新的
        """
        # 删除旧的
        self.delete_reminder_jobs(event.get("id"))

        # 创建新的
        return self.create_reminder_jobs(event)

    def delete_reminder_jobs(self, event_id: str) -> list[str]:
        """删除日历事件的所有提醒任务

        Args:
            event_id: 事件 ID

        Returns:
            删除的 job IDs 列表
        """
        deleted_ids = []

        # 查找所有与该事件相关的 cron jobs
        jobs = self.cron_service.list_jobs(include_disabled=True)
        calendar_jobs = [j for j in jobs if j.get("source") == "calendar"]

        for job in calendar_jobs:
            # 通过 job ID 匹配：cal:event_id:reminder_time
            if job.get("id", "").startswith(f"cal:{event_id}:"):
                try:
                    self.cron_service.remove_job(job["id"])
                    deleted_ids.append(job["id"])
                    logger.info(f"Deleted calendar reminder job: {job['id']}")
                except Exception as e:
                    logger.error(f"Failed to delete calendar reminder job {job['id']}: {e}")

        return deleted_ids

    def get_calendar_jobs(self) -> list[dict[str, Any]]:
        """获取所有日历相关的 cron jobs"""
        jobs = self.cron_service.list_jobs(include_disabled=True)
        return [j for j in jobs if j.get("source") == "calendar"]
