"""API performance monitoring middleware and metrics."""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class ApiMetrics:
    """API endpoint performance metrics."""
    endpoint: str
    method: str
    total_requests: int = 0
    total_errors: int = 0
    total_latency_ms: float = 0.0
    min_latency_ms: float = float('inf')
    max_latency_ms: float = 0.0
    latencies: deque = field(default_factory=lambda: deque(maxlen=1000))

    @property
    def avg_latency_ms(self) -> float:
        """Calculate average latency."""
        if self.total_requests == 0:
            return 0.0
        return self.total_latency_ms / self.total_requests

    @property
    def error_rate(self) -> float:
        """Calculate error rate."""
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests

    @property
    def p95_latency_ms(self) -> float:
        """Calculate 95th percentile latency."""
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        index = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[index] if index < len(sorted_latencies) else sorted_latencies[-1]

    @property
    def p99_latency_ms(self) -> float:
        """Calculate 99th percentile latency."""
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        index = int(len(sorted_latencies) * 0.99)
        return sorted_latencies[index] if index < len(sorted_latencies) else sorted_latencies[-1]


class ApiPerformanceMonitor:
    """API performance monitoring service.

    Tracks API endpoint performance metrics including:
    - Request count
    - Error rate
    - Latency (min, max, avg, p95, p99)
    - Requests per second
    """

    # Recent request window (in seconds)
    RECENT_WINDOW_SECONDS = 60

    def __init__(self):
        """Initialize the API performance monitor."""
        self._metrics: dict[str, ApiMetrics] = {}
        self._recent_requests: deque[tuple[str, float]] = deque(maxlen=10000)
        self._request_timestamps: deque[float] = deque(maxlen=1000)

    def _get_metric_key(self, endpoint: str, method: str) -> str:
        """Get metric key for endpoint and method."""
        return f"{method}:{endpoint}"

    def record_request(
        self,
        endpoint: str,
        method: str,
        latency_ms: float,
        status_code: int = 200,
    ) -> None:
        """Record an API request.

        Args:
            endpoint: API endpoint path
            method: HTTP method
            latency_ms: Request latency in milliseconds
            status_code: HTTP status code
        """
        key = self._get_metric_key(endpoint, method)

        if key not in self._metrics:
            self._metrics[key] = ApiMetrics(endpoint=endpoint, method=method)

        metrics = self._metrics[key]
        metrics.total_requests += 1
        metrics.total_latency_ms += latency_ms
        metrics.min_latency_ms = min(metrics.min_latency_ms, latency_ms)
        metrics.max_latency_ms = max(metrics.max_latency_ms, latency_ms)
        metrics.latencies.append(latency_ms)

        if status_code >= 400:
            metrics.total_errors += 1

        # Record for recent request tracking
        current_time = time.time()
        self._recent_requests.append((key, current_time))
        self._request_timestamps.append(current_time)

    def get_endpoint_metrics(self, endpoint: str | None = None) -> list[dict[str, Any]]:
        """Get metrics for specific endpoint or all endpoints.

        Args:
            endpoint: Optional endpoint to filter by

        Returns:
            list: List of metric dictionaries
        """
        results = []

        for key, metrics in self._metrics.items():
            if endpoint and metrics.endpoint != endpoint:
                continue

            results.append({
                "endpoint": metrics.endpoint,
                "method": metrics.method,
                "total_requests": metrics.total_requests,
                "total_errors": metrics.total_errors,
                "error_rate": round(metrics.error_rate * 100, 2),
                "latency_ms": {
                    "min": round(metrics.min_latency_ms, 2),
                    "max": round(metrics.max_latency_ms, 2),
                    "avg": round(metrics.avg_latency_ms, 2),
                    "p95": round(metrics.p95_latency_ms, 2),
                    "p99": round(metrics.p99_latency_ms, 2),
                },
            })

        # Sort by total requests descending
        results.sort(key=lambda x: x["total_requests"], reverse=True)
        return results

    def get_recent_throughput(self, window_seconds: int = 60) -> dict[str, Any]:
        """Get recent request throughput.

        Args:
            window_seconds: Time window in seconds

        Returns:
            dict: Throughput metrics
        """
        current_time = time.time()
        cutoff_time = current_time - window_seconds

        # Count recent requests
        recent_count = sum(
            1 for _, timestamp in self._recent_requests
            if timestamp >= cutoff_time
        )

        requests_per_second = recent_count / window_seconds if window_seconds > 0 else 0

        return {
            "window_seconds": window_seconds,
            "total_requests": recent_count,
            "requests_per_second": round(requests_per_second, 2),
        }

    def get_summary(self) -> dict[str, Any]:
        """Get overall API performance summary.

        Returns:
            dict: Summary of all API metrics
        """
        total_requests = sum(m.total_requests for m in self._metrics.values())
        total_errors = sum(m.total_errors for m in self._metrics.values())

        all_latencies = []
        for m in self._metrics.values():
            all_latencies.extend(m.latencies)

        avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0
        sorted_latencies = sorted(all_latencies)
        p95_latency = sorted_latencies[int(len(sorted_latencies) * 0.95)] if sorted_latencies else 0
        p99_latency = sorted_latencies[int(len(sorted_latencies) * 0.99)] if sorted_latencies else 0

        return {
            "total_requests": total_requests,
            "total_errors": total_errors,
            "error_rate": round((total_errors / total_requests * 100) if total_requests > 0 else 0, 2),
            "latency_ms": {
                "avg": round(avg_latency, 2),
                "p95": round(p95_latency, 2),
                "p99": round(p99_latency, 2),
            },
            "throughput": self.get_recent_throughput(),
            "endpoints": len(self._metrics),
        }

    def reset_metrics(self) -> None:
        """Reset all metrics."""
        self._metrics.clear()
        self._recent_requests.clear()
        self._request_timestamps.clear()
        logger.info("API performance metrics reset")


# Global singleton instance
_api_performance_monitor: ApiPerformanceMonitor | None = None


def get_api_performance_monitor() -> ApiPerformanceMonitor:
    """Get the global API performance monitor instance.

    Returns:
        ApiPerformanceMonitor: The performance monitor singleton
    """
    global _api_performance_monitor
    if _api_performance_monitor is None:
        _api_performance_monitor = ApiPerformanceMonitor()
    return _api_performance_monitor
