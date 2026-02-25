"""System status service for collecting and aggregating system status data."""

import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.skills import SkillsLoader
from nanobot.services.api_performance_monitor import get_api_performance_monitor
from nanobot.services.system_monitor_service import get_system_monitor
from nanobot.session.manager import SessionManager
from nanobot.storage.status_repository import StatusRepository


class SystemStatusService:
    """系统状态服务，负责收集和聚合系统状态数据。"""

    # 默认并发配置
    DEFAULT_CONCURRENCY_CONFIG = {
        "max_parallel_tool_calls": 5,
        "max_concurrent_subagents": 10,
        "enable_parallel_tools": True,
        "thread_pool_size": 4,
        "enable_subagent_parallel": True,
        "claude_code_max_concurrent": 3,
        "enable_smart_parallel": True,
        "smart_parallel_model": "",
    }

    def __init__(
        self,
        status_repo: StatusRepository,
        session_manager: SessionManager,
        workspace: Path
    ):
        """
        初始化系统状态服务。

        Args:
            status_repo: 状态数据仓库
            session_manager: 会话管理器
            workspace: 工作空间路径
        """
        self.status_repo = status_repo
        self.session_manager = session_manager
        self.workspace = workspace
        self.skills_loader = SkillsLoader(workspace)
    
    def initialize(self) -> None:
        """初始化系统状态（记录启动时间）。"""
        try:
            current_time = time.time()
            self.status_repo.set_start_time(current_time)
            logger.info(f"System status initialized with start_time: {current_time}")
        except Exception as e:
            logger.exception("Failed to initialize system status")
    
    def get_uptime(self) -> int:
        """
        获取系统运行时长。
        
        Returns:
            运行时长（秒）
        """
        try:
            start_time = self.status_repo.get_start_time()
            if start_time is None:
                logger.warning("Start time not found, returning 0")
                return 0
            
            current_time = time.time()
            uptime = int(current_time - start_time)
            return max(0, uptime)  # Ensure non-negative
        except Exception as e:
            logger.exception("Failed to get uptime")
            return 0
    
    def get_session_count(self) -> int:
        """
        获取会话数量。
        
        Returns:
            会话总数
        """
        try:
            sessions = self.session_manager.list_sessions()
            count = len(sessions)
            logger.debug(f"Session count: {count}")
            return count
        except Exception as e:
            logger.exception("Failed to get session count")
            return 0
    
    def get_skills_info(self) -> list[dict[str, Any]]:
        """
        获取已安装的 Skills 信息。
        
        Returns:
            Skills 信息列表，每个元素包含 name, version, description
        """
        try:
            all_skills = self.skills_loader.list_skills(filter_unavailable=False)
            skills_info = []
            
            for skill in all_skills:
                name = skill["name"]
                metadata = self.skills_loader.get_skill_metadata(name) or {}
                
                skills_info.append({
                    "name": name,
                    "version": metadata.get("version", "unknown"),
                    "description": metadata.get("description", name),
                    "source": skill.get("source", "unknown")
                })
            
            logger.debug(f"Skills info retrieved: {len(skills_info)} skills")
            return skills_info
        except Exception as e:
            logger.exception("Failed to get skills info")
            return []
    
    def get_status(self) -> dict[str, Any]:
        """
        获取完整的系统状态信息。

        Returns:
            系统状态字典，包含 uptime, sessions, skills, concurrency_config, metrics
        """
        try:
            uptime = self.get_uptime()
            session_count = self.get_session_count()
            skills_info = self.get_skills_info()
            concurrency_config = self.get_concurrency_config()
            metrics = self.get_metrics()

            status = {
                "uptime": uptime,
                "sessions": session_count,
                "skills": len(skills_info),
                "skills_list": skills_info,
                "concurrency_config": concurrency_config,
                "metrics": metrics,
            }

            logger.debug(f"System status retrieved: {status}")
            return status
        except Exception as e:
            logger.exception("Failed to get system status")
            # Return default values on error
            return {
                "uptime": 0,
                "sessions": 0,
                "skills": 0,
                "skills_list": [],
                "concurrency_config": self.DEFAULT_CONCURRENCY_CONFIG,
                "metrics": {}
            }

    def get_system_resources(self) -> dict[str, Any]:
        """
        获取系统资源使用情况。

        Returns:
            系统资源字典，包含 CPU、内存、磁盘使用信息
        """
        try:
            system_monitor = get_system_monitor()
            return system_monitor.get_current_resources()
        except Exception as e:
            logger.exception("Failed to get system resources")
            return {}

    def get_resource_history(self, duration_seconds: int = 300) -> dict[str, Any]:
        """
        获取系统资源历史数据。

        Args:
            duration_seconds: 历史数据时长（秒）

        Returns:
            资源历史数据字典
        """
        try:
            system_monitor = get_system_monitor()
            return system_monitor.get_resource_history(duration_seconds)
        except Exception as e:
            logger.exception("Failed to get resource history")
            return {}

    def get_process_info(self) -> dict[str, Any]:
        """
        获取当前进程信息。

        Returns:
            进程信息字典
        """
        try:
            system_monitor = get_system_monitor()
            return system_monitor.get_process_info()
        except Exception as e:
            logger.exception("Failed to get process info")
            return {}

    def get_platform_info(self) -> dict[str, Any]:
        """
        获取平台信息。

        Returns:
            平台信息字典
        """
        try:
            system_monitor = get_system_monitor()
            return system_monitor.get_platform_info()
        except Exception as e:
            logger.exception("Failed to get platform info")
            return {}

    def get_api_performance_summary(self) -> dict[str, Any]:
        """
        获取 API 性能汇总。

        Returns:
            API 性能汇总字典
        """
        try:
            monitor = get_api_performance_monitor()
            return monitor.get_summary()
        except Exception as e:
            logger.exception("Failed to get API performance summary")
            return {}

    def get_api_endpoint_metrics(self, endpoint: str | None = None) -> list[dict[str, Any]]:
        """
        获取 API 端点性能指标。

        Args:
            endpoint: 可选的端点过滤器

        Returns:
            API 端点指标列表
        """
        try:
            monitor = get_api_performance_monitor()
            return monitor.get_endpoint_metrics(endpoint)
        except Exception as e:
            logger.exception("Failed to get API endpoint metrics")
            return []

    def get_enhanced_status(self) -> dict[str, Any]:
        """
        获取增强的系统状态信息（包含资源监控和API性能）。

        Returns:
            增强的系统状态字典
        """
        try:
            status = self.get_status()
            system_resources = self.get_system_resources()
            process_info = self.get_process_info()
            platform_info = self.get_platform_info()
            api_summary = self.get_api_performance_summary()

            return {
                **status,
                "system_resources": system_resources,
                "process_info": process_info,
                "platform_info": platform_info,
                "api_performance": api_summary,
            }
        except Exception as e:
            logger.exception("Failed to get enhanced status")
            return self.get_status()

    # ============================================================
    # 并发配置管理
    # ============================================================

    def get_concurrency_config(self) -> dict[str, Any]:
        """
        获取并发配置。

        Returns:
            并发配置字典
        """
        try:
            config = self.status_repo.get_concurrency_config()
            # 合并默认配置
            result = self.DEFAULT_CONCURRENCY_CONFIG.copy()
            result.update(config)
            return result
        except Exception as e:
            logger.exception("Failed to get concurrency config")
            return self.DEFAULT_CONCURRENCY_CONFIG.copy()

    def set_concurrency_config(self, config: dict[str, Any]) -> None:
        """
        设置并发配置。

        Args:
            config: 并发配置字典
        """
        try:
            self.status_repo.set_concurrency_config(config)
            logger.info(f"Concurrency config updated: {config}")
        except Exception as e:
            logger.exception("Failed to set concurrency config")
            raise

    # ============================================================
    # 监控指标管理
    # ============================================================

    def get_metrics(self) -> dict[str, Any]:
        """
        获取监控指标。

        Returns:
            监控指标字典
        """
        try:
            return self.status_repo.get_metrics()
        except Exception as e:
            logger.exception("Failed to get metrics")
            return {}

    def update_metric(self, key: str, value: Any, increment: bool = False) -> None:
        """
        更新监控指标。

        Args:
            key: 指标键
            value: 指标值
            increment: 是否增量更新
        """
        try:
            self.status_repo.update_metric(key, value, increment)
        except Exception as e:
            logger.exception(f"Failed to update metric {key}")

    def increment_tool_call(self, is_parallel: bool = False) -> None:
        """
        记录工具调用。

        Args:
            is_parallel: 是否并行调用
        """
        self.update_metric("total_tool_calls", 1, increment=True)
        if is_parallel:
            self.update_metric("parallel_tool_calls", 1, increment=True)
        else:
            self.update_metric("serial_tool_calls", 1, increment=True)

    def increment_failed_tool_call(self) -> None:
        """记录失败的工具调用。"""
        self.update_metric("failed_tool_calls", 1, increment=True)

    def increment_subagent_spawn(self) -> None:
        """记录子Agent spawn次数。"""
        self.update_metric("total_subagent_spawns", 1, increment=True)

    def update_tool_execution_time(self, execution_time: float) -> None:
        """
        更新工具执行时间统计。

        Args:
            execution_time: 执行时间（秒）
        """
        import json
        current = self.get_metrics().get("avg_tool_execution_time", 0)
        total_calls = self.get_metrics().get("total_tool_calls", 0)
        if total_calls > 0:
            new_avg = (current * (total_calls - 1) + execution_time) / total_calls
        else:
            new_avg = execution_time
        self.update_metric("avg_tool_execution_time", new_avg)

    def update_max_concurrent_tools(self, current: int) -> None:
        """
        更新最大并发工具数。

        Args:
            current: 当前并发数
        """
        current_max = self.get_metrics().get("max_concurrent_tools", 0)
        if current > current_max:
            self.update_metric("max_concurrent_tools", current)

    def increment_llm_call(self) -> None:
        """记录LLM调用次数。"""
        self.update_metric("llm_call_count", 1, increment=True)

    def update_token_usage(self, tokens: int) -> None:
        """
        更新token使用量。

        Args:
            tokens: 使用的token数
        """
        self.update_metric("total_token_usage", tokens, increment=True)

    def reset_metrics(self) -> None:
        """重置所有监控指标。"""
        try:
            self.status_repo.reset_metrics()
            logger.info("Metrics reset")
        except Exception as e:
            logger.exception("Failed to reset metrics")
