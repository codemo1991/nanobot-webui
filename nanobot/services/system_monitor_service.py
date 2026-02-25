"""System resource monitoring service for tracking CPU, memory, and disk usage."""

import os
import platform
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not available, system resource monitoring will be limited")


@dataclass
class SystemResources:
    """System resource snapshot."""
    timestamp: float
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_percent: float
    disk_used_gb: float
    disk_available_gb: float
    process_count: int
    thread_count: int
    open_files: int = 0
    network_connections: int = 0


@dataclass
class ProcessInfo:
    """Current process resource usage."""
    pid: int
    name: str
    cpu_percent: float
    memory_mb: float
    num_threads: int
    num_fds: int = 0
    create_time: float = 0


class SystemMonitorService:
    """System resource monitoring service.

    Provides real-time monitoring of CPU, memory, disk usage,
    and process-level metrics for the nanobot application.
    """

    # Resource history retention (in seconds)
    HISTORY_RETENTION_SECONDS = 3600  # Keep 1 hour of history
    HISTORY_INTERVAL_SECONDS = 5  # Sample every 5 seconds

    def __init__(self):
        """Initialize the system monitor service."""
        self._history: list[SystemResources] = []
        self._last_sample_time = 0.0
        self._process = None
        self._initialized = False

        if PSUTIL_AVAILABLE:
            try:
                self._process = psutil.Process(os.getpid())
                self._initialized = True
                logger.info("System monitor service initialized with psutil")
            except Exception as e:
                logger.warning(f"Failed to initialize process monitor: {e}")

    def _should_sample(self) -> bool:
        """Check if enough time has passed to sample again."""
        current_time = time.time()
        if current_time - self._last_sample_time >= self.HISTORY_INTERVAL_SECONDS:
            self._last_sample_time = current_time
            return True
        return False

    def _clean_old_history(self) -> None:
        """Remove old samples from history."""
        current_time = time.time()
        cutoff_time = current_time - self.HISTORY_RETENTION_SECONDS
        self._history = [r for r in self._history if r.timestamp > cutoff_time]

    def get_system_resources(self) -> SystemResources:
        """Get current system resource snapshot.

        Returns:
            SystemResources: Current resource usage snapshot
        """
        timestamp = time.time()

        # Get system-wide resources
        if PSUTIL_AVAILABLE:
            try:
                cpu_percent = psutil.cpu_percent(interval=0.1)
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage('/')

                # Get process-specific info
                process_count = len(psutil.pids())

                # Current process info
                thread_count = 0
                open_files = 0
                network_connections = 0

                if self._process:
                    try:
                        thread_count = self._process.num_threads()
                        try:
                            open_files = len(self._process.open_files())
                        except (AttributeError, OSError):
                            pass  # Not available on all platforms

                        try:
                            network_connections = len(self._process.connections())
                        except (AttributeError, OSError):
                            pass  # Not available on all platforms
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                return SystemResources(
                    timestamp=timestamp,
                    cpu_percent=cpu_percent,
                    memory_percent=memory.percent,
                    memory_used_mb=memory.used / (1024 * 1024),
                    memory_available_mb=memory.available / (1024 * 1024),
                    disk_percent=disk.percent,
                    disk_used_gb=disk.used / (1024 * 1024 * 1024),
                    disk_available_gb=disk.free / (1024 * 1024 * 1024),
                    process_count=process_count,
                    thread_count=thread_count,
                    open_files=open_files,
                    network_connections=network_connections,
                )
            except Exception as e:
                logger.warning(f"Failed to get system resources: {e}")

        # Fallback: return basic info without psutil
        return self._get_fallback_resources(timestamp)

    def _get_fallback_resources(self, timestamp: float) -> SystemResources:
        """Get basic resources without psutil."""
        import gc

        # Estimate memory from GC
        memory_info = gc.get_stats()
        memory_mb = sum(
            s.get('pymalloc_blocks', 0) * 64 / 1024
            for s in memory_info
            if isinstance(s, dict)
        )

        return SystemResources(
            timestamp=timestamp,
            cpu_percent=0.0,  # Cannot measure without psutil
            memory_percent=0.0,
            memory_used_mb=memory_mb,
            memory_available_mb=0.0,
            disk_percent=0.0,
            disk_used_gb=0.0,
            disk_available_gb=0.0,
            process_count=len(gc.get_objects()),
            thread_count=0,
        )

    def record_sample(self) -> SystemResources:
        """Record a resource sample and add to history.

        Returns:
            SystemResources: The recorded sample
        """
        resources = self.get_system_resources()

        if self._should_sample():
            self._history.append(resources)
            self._clean_old_history()

        return resources

    def get_current_resources(self) -> dict[str, Any]:
        """Get current resource snapshot as dictionary.

        Returns:
            dict: Current resource usage
        """
        resources = self.record_sample()
        return {
            "cpu_percent": resources.cpu_percent,
            "memory_percent": resources.memory_percent,
            "memory_used_mb": round(resources.memory_used_mb, 2),
            "memory_available_mb": round(resources.memory_available_mb, 2),
            "disk_percent": resources.disk_percent,
            "disk_used_gb": round(resources.disk_used_gb, 2),
            "disk_available_gb": round(resources.disk_available_gb, 2),
            "process_count": resources.process_count,
            "thread_count": resources.thread_count,
            "open_files": resources.open_files,
            "network_connections": resources.network_connections,
        }

    def get_resource_history(self, duration_seconds: int = 300) -> dict[str, Any]:
        """Get resource usage history.

        Args:
            duration_seconds: Duration of history to retrieve (default 5 minutes)

        Returns:
            dict: Resource history with timestamps
        """
        current_time = time.time()
        cutoff_time = current_time - duration_seconds

        filtered_history = [
            r for r in self._history
            if r.timestamp >= cutoff_time
        ]

        return {
            "timestamps": [r.timestamp for r in filtered_history],
            "cpu_percent": [r.cpu_percent for r in filtered_history],
            "memory_percent": [r.memory_percent for r in filtered_history],
            "memory_used_mb": [r.memory_used_mb for r in filtered_history],
            "disk_percent": [r.disk_percent for r in filtered_history],
        }

    def get_process_info(self) -> dict[str, Any]:
        """Get current process information.

        Returns:
            dict: Current process resource usage
        """
        if not PSUTIL_AVAILABLE or not self._process:
            return {
                "pid": os.getpid(),
                "name": "nanobot",
                "cpu_percent": 0.0,
                "memory_mb": 0.0,
                "num_threads": 0,
            }

        try:
            # Refresh process info
            self._process = psutil.Process(os.getpid())

            cpu_percent = self._process.cpu_percent(interval=0.1)
            memory_info = self._process.memory_info()
            num_threads = self._process.num_threads()

            try:
                num_fds = self._process.num_fds()
            except (AttributeError, OSError):
                num_fds = 0

            return {
                "pid": self._process.pid,
                "name": self._process.name(),
                "cpu_percent": round(cpu_percent, 2),
                "memory_mb": round(memory_info.rss / (1024 * 1024), 2),
                "num_threads": num_threads,
                "num_fds": num_fds,
                "create_time": self._process.create_time(),
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            logger.warning(f"Failed to get process info: {e}")
            return {
                "pid": os.getpid(),
                "name": "nanobot",
                "cpu_percent": 0.0,
                "memory_mb": 0.0,
                "num_threads": 0,
            }

    def get_platform_info(self) -> dict[str, Any]:
        """Get platform information.

        Returns:
            dict: Platform details
        """
        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
            "cpu_count": psutil.cpu_count() if PSUTIL_AVAILABLE else 1,
            "total_memory_mb": round(
                psutil.virtual_memory().total / (1024 * 1024), 2
            ) if PSUTIL_AVAILABLE else 0,
        }


# Global singleton instance
_system_monitor: SystemMonitorService | None = None


def get_system_monitor() -> SystemMonitorService:
    """Get the global system monitor instance.

    Returns:
        SystemMonitorService: The system monitor singleton
    """
    global _system_monitor
    if _system_monitor is None:
        _system_monitor = SystemMonitorService()
    return _system_monitor
