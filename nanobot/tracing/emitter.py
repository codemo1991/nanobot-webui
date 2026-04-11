"""
JSONL trace exporter with rotation, retention, and in-memory query.

Each span is written as one JSON line to a file in ~/.nanobot/traces/.
Supports batched writes for throughput and offline trace analysis.
"""

from __future__ import annotations

import gzip
import json
import os
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from nanobot.tracing.analysis import aggregate_spans

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False  # Windows


def _get_default_trace_dir() -> Path:
    return (Path.home() / ".nanobot" / "traces").resolve()


def _safe_json_dumps(obj: Any) -> str:
    """Serialize to JSON, handling non-standard types gracefully."""
    return json.dumps(obj, ensure_ascii=False, default=_json_default)


def _json_default(o: Any) -> Any:
    if isinstance(o, float):
        if o != o or o in (float("inf"), float("-inf")):
            return None
    return str(o)


class TraceEmitter:
    """
    Thread-safe JSONL trace exporter.

    Spans are buffered in memory and flushed to disk in batches.
    Files are rotated when they reach `rotation` bytes, and old files
    are deleted after `retention_days`.

    查询「最近 span / 摘要」时会合并 **内存待落盘缓冲** 与 **磁盘 JSONL**（仅缓冲会在 flush 后清空，
    若只读内存会导致 Web Trace 页空白，尽管文件已存在）。

    Args:
        trace_dir: Directory for trace files. Default: ~/.nanobot/traces/
        rotation: Max file size before rotation. Default: "50 MB"
        retention_days: Delete files older than this. Default: 7
        buffer_size: Number of spans to buffer before flushing. Default: 50
        enabled: If False, all emit() calls are no-ops. Default: True
    """

    def __init__(
        self,
        trace_dir: Path | str | None = None,
        rotation: str = "50 MB",
        retention_days: int = 7,
        buffer_size: int = 50,
        enabled: bool = True,
    ):
        self._trace_dir = (
            Path(trace_dir).expanduser().resolve()
            if trace_dir
            else _get_default_trace_dir()
        )
        self._rotation_bytes = self._parse_size(rotation)
        self._retention_days = retention_days
        self._buffer_size = buffer_size
        self._enabled = enabled

        self._buffer: deque[dict[str, Any]] = deque(maxlen=buffer_size * 2)
        self._lock = threading.Lock()
        # 使用独立锁保护 _observers，避免在持有 _lock 时重入死锁
        self._observers_lock = threading.Lock()
        self._observers: list[Callable[[dict[str, Any]], None]] = []
        self._flush_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._current_file: Path | None = None
        self._current_file_size = 0

        if self._enabled:
            self._ensure_dir()
            self._start_flush_thread()
            self._cleanup_old_files()

    @property
    def trace_dir(self) -> Path:
        """Span JSONL 输出目录（绝对路径）。"""
        return self._trace_dir

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, span_or_dict: Any) -> None:
        """
        Buffer a span for async-safe emission.

        Accepts a Span object or a pre-serialized dict.
        Thread-safe — can be called from any thread/coroutine.
        """
        if not self._enabled:
            return

        if hasattr(span_or_dict, "to_dict"):
            record = span_or_dict.to_dict()
        elif isinstance(span_or_dict, dict):
            record = span_or_dict
        else:
            logger.warning(f"[Tracing] emit() received unknown type: {type(span_or_dict)}")
            return

        record["_emit_ts"] = time.time()

        with self._lock:
            self._buffer.append(record)
            if len(self._buffer) >= self._buffer_size:
                self._flush_unlocked()

        # SSE 等观察者：在 span 结束时立即推送，不等待定时 flush（此前仅在落盘时通知）
        obs_payload = {k: v for k, v in record.items() if k != "_emit_ts"}
        self._notify_observers(obs_payload)

    def flush(self) -> None:
        """Synchronously flush buffered spans to disk."""
        with self._lock:
            self._flush_unlocked()

    def close(self) -> None:
        """Stop the flush thread and flush remaining spans."""
        self._stop_event.set()
        if self._flush_thread:
            self._flush_thread.join(timeout=5.0)
        self.flush()

    def query_by_trace_id(self, trace_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """
        Return all buffered + recent on-disk spans for a given trace_id.
        Reads from the in-memory buffer and the latest trace files.
        """
        results: list[dict[str, Any]] = []

        # From buffer
        with self._lock:
            for record in self._buffer:
                if record.get("trace_id") == trace_id:
                    results.append(record)

        # From latest files on disk
        if not self._trace_dir.exists():
            return results[:limit]

        try:
            files = sorted(
                self._trace_dir.glob("trace_*.jsonl*"),
                key=os.path.getmtime,
                reverse=True,
            )
            for fpath in files[:3]:  # Only check 3 most recent files
                if len(results) >= limit:
                    break
                results.extend(self._read_file_spans(fpath, trace_id, limit - len(results)))
        except Exception as e:
            logger.warning(f"[Tracing] Failed to query trace files: {e}")

        return results[:limit]

    def query_by_session(
        self,
        session_key: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Return spans for a given session_key (e.g., "telegram:12345").
        """
        results: list[dict[str, Any]] = []
        with self._lock:
            for record in self._buffer:
                if record.get("attrs", {}).get("session_key") == session_key:
                    results.append(record)

        if not self._trace_dir.exists():
            return results[:limit]

        try:
            files = sorted(
                self._trace_dir.glob("trace_*.jsonl*"),
                key=os.path.getmtime,
                reverse=True,
            )
            for fpath in files[:3]:
                if len(results) >= limit:
                    break
                for line in self._read_lines(fpath):
                    try:
                        rec = json.loads(line)
                        if rec.get("attrs", {}).get("session_key") == session_key:
                            results.append(rec)
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"[Tracing] Failed to query session traces: {e}")

        return results[:limit]

    def _recent_spans_from_disk(self, limit: int) -> list[dict[str, Any]]:
        """从 JSONL 按文件时间顺序读取，保留全局最近的 ``limit`` 条 span。"""
        if limit <= 0 or not self._trace_dir.is_dir():
            return []
        try:
            files = sorted(
                self._trace_dir.glob("trace_*.jsonl*"),
                key=lambda p: (p.stat().st_mtime, str(p)),
            )
        except OSError:
            return []
        ring: deque[dict[str, Any]] = deque(maxlen=limit)
        for fpath in files:
            for line in self._read_lines(fpath):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                ring.append(rec)
        return list(ring)

    def _buffer_spans_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{k: v for k, v in s.items() if k != "_emit_ts"} for s in self._buffer]

    def _merged_recent_spans(self, limit: int) -> list[dict[str, Any]]:
        """合并磁盘已落盘与内存未 flush 的 span，按时间排序后取最近 ``limit`` 条。"""
        if limit <= 0:
            return []
        disk = self._recent_spans_from_disk(limit)
        buf = self._buffer_spans_snapshot()
        merged = disk + buf
        merged.sort(key=lambda s: (s.get("start_ms", 0), s.get("seq", 0)))
        return merged[-limit:]

    def get_recent_spans(self, limit: int = 100) -> list[dict[str, Any]]:
        """返回最近的 span（内存缓冲 + 磁盘 JSONL，避免仅读空缓冲）。"""
        return self._merged_recent_spans(limit)

    def get_summary(self) -> dict[str, Any]:
        """基于最近若干条 span（含磁盘）聚合指标。"""
        spans = self._merged_recent_spans(1000)

        if not spans:
            return {
                "total_spans": 0,
                "by_type": {},
                "by_tool": {},
                "recent_success_rate": 1.0,
                "recent_avg_duration_ms": 0.0,
            }

        metrics = aggregate_spans(spans)

        # Calculate success rate for the most recent 100 spans
        recent = spans[-100:] if len(spans) > 100 else spans
        recent_ok = sum(1 for s in recent if s.get("status") == "ok")
        recent_success_rate = recent_ok / len(recent) if recent else 1.0

        # Calculate average duration
        durations = [s.get("duration_ms") for s in recent if s.get("duration_ms")]
        avg_duration = sum(durations) / len(durations) if durations else 0.0

        return {
            "total_spans": len(spans),
            "by_type": {k: v.to_dict() for k, v in metrics.by_type.items()},
            "by_tool": {k: v.to_dict() for k, v in metrics.by_tool.items()},
            "recent_success_rate": recent_success_rate,
            "recent_avg_duration_ms": avg_duration,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> None:
        self._trace_dir.mkdir(parents=True, exist_ok=True)

    def _start_flush_thread(self) -> None:
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def _flush_loop(self) -> None:
        """Background thread: flush every 2 seconds."""
        while not self._stop_event.wait(timeout=2.0):
            with self._lock:
                self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        """Flush buffer to disk. Must be called while holding _lock."""
        if not self._buffer:
            return

        batch = [self._buffer.popleft() for _ in range(len(self._buffer))]
        self._buffer.clear()

        if not batch:
            return

        try:
            fpath = self._get_current_file()
            mode = "a"

            # Check rotation
            if fpath.exists():
                size = fpath.stat().st_size
                if size >= self._rotation_bytes:
                    fpath = self._rotate_file(fpath)
                    mode = "w"

            line_count = 0
            with open(fpath, mode, encoding="utf-8") as f:
                for record in batch:
                    # Remove internal field before writing
                    rec = {k: v for k, v in record.items() if k != "_emit_ts"}
                    f.write(_safe_json_dumps(rec) + "\n")
                    line_count += 1

            self._current_file = fpath
            self._current_file_size = fpath.stat().st_size

        except Exception as e:
            # Put records back in buffer on failure
            self._buffer.extendleft(reversed(batch))
            logger.warning(f"[Tracing] Failed to flush {line_count} spans: {e}")

    def _get_current_file(self) -> Path:
        """Return the path for the current trace file (today's date)."""
        if self._current_file:
            return self._current_file
        today = time.strftime("%Y-%m-%d")
        return self._trace_dir / f"trace_{today}.jsonl"

    def _rotate_file(self, old_path: Path) -> Path:
        """Rotate a trace file with timestamp suffix."""
        ts = time.strftime("%H%M%S")
        new_name = f"{old_path.stem}_{ts}.jsonl{old_path.suffix}"
        new_path = old_path.with_name(new_name)
        try:
            old_path.rename(new_path)
            logger.debug(f"[Tracing] Rotated trace file to {new_path.name}")
        except OSError:
            pass
        return new_path

    def _cleanup_old_files(self) -> None:
        """Delete trace files older than retention_days."""
        if not self._trace_dir.exists():
            return
        cutoff = time.time() - self._retention_days * 86400
        try:
            for fpath in self._trace_dir.glob("trace_*.jsonl*"):
                if fpath.stat().st_mtime < cutoff:
                    fpath.unlink(missing_ok=True)
                    logger.debug(f"[Tracing] Deleted old trace file: {fpath.name}")
        except Exception as e:
            logger.warning(f"[Tracing] Failed to cleanup old files: {e}")

    def add_observer(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback to be invoked for every new span."""
        with self._observers_lock:
            self._observers.append(callback)

    def remove_observer(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Unregister a previously added observer callback."""
        with self._observers_lock:
            if callback in self._observers:
                self._observers.remove(callback)

    def _notify_observers(self, span: dict[str, Any]) -> None:
        """Invoke all registered observers with a span dict.

        使用独立的 _observers_lock（非 _lock），避免在 _flush_unlocked()
        持有 _lock 时重入导致死锁。
        """
        with self._observers_lock:
            callbacks = list(self._observers)
        for cb in callbacks:
            try:
                cb(span)
            except Exception as e:
                logger.warning(f"[Tracing] Observer callback raised: {e}")

    @staticmethod
    def _parse_size(size_str: str) -> int:
        """Parse size string like '50 MB' into bytes."""
        size_str = size_str.strip().upper()
        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
        for unit, mult in multipliers.items():
            if size_str.endswith(unit):
                try:
                    return int(float(size_str[:-len(unit)].strip()) * mult)
                except ValueError:
                    pass
        return int(size_str)

    def _read_file_spans(
        self,
        fpath: Path,
        trace_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Read matching spans from a single trace file."""
        results = []
        opener = gzip.open if fpath.suffix == ".gz" else open
        try:
            with opener(fpath, "rt", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if len(results) >= limit:
                        break
                    try:
                        rec = json.loads(line)
                        if rec.get("trace_id") == trace_id:
                            results.append(rec)
                    except Exception:
                        continue
        except Exception:
            pass
        return results

    def _read_lines(self, fpath: Path):
        opener = gzip.open if fpath.suffix == ".gz" else open
        try:
            with opener(fpath, "rt", encoding="utf-8", errors="replace") as f:
                yield from f
        except Exception:
            pass
