"""子 Agent 实时进度事件总线（全局单例）。

用于在子 Agent 执行期间将进度事件广播到多个订阅者（Web SSE / 飞书卡片等）。

事件格式：
- subagent_start : {"type": "subagent_start", "task_id": str, "label": str, "backend": str, "task": str}
- subagent_progress: {"type": "subagent_progress", "task_id": str, "label": str,
                       "subtype": str, "content": str, "tool_name": str | None}
- subagent_end   : {"type": "subagent_end", "task_id": str, "label": str,
                    "status": "ok" | "error", "summary": str}

origin_key 格式："{channel}:{chat_id}"，例如 "web:sess_abc123" 或 "feishu:oc_xxx"。
"""

import logging
import queue
import threading
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)


class SubagentProgressBus:
    """
    全局子 Agent 进度事件总线。

    支持多订阅者按 origin_key 订阅进度事件，线程安全，
    可在 asyncio 与多线程混合环境中使用。

    内置事件缓冲（最近 _MAX_BUFFER 个事件），
    晚到的订阅者可通过 replay=True 回放已发生的事件。
    """

    _MAX_BUFFER = 100

    _instance: "SubagentProgressBus | None" = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "SubagentProgressBus":
        """获取全局单例。"""
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def push(self, origin_key: str, event: dict[str, Any]) -> None:
        """向所有订阅该 origin_key 的队列推送事件，同时加入回放缓冲。"""
        with self._lock:
            buf = self._buffers.setdefault(origin_key, [])
            buf.append(event)
            if len(buf) > self._MAX_BUFFER:
                del buf[: -self._MAX_BUFFER]
            queues = list(self._subscribers.get(origin_key, []))

        dropped = 0
        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                dropped += 1
        if dropped > 0:
            # 使用 debug 级别避免日志泛滥，只有在调试时才显示
            logger.debug(f"[SubagentProgressBus] Dropped {dropped} events for {origin_key} due to full queue")

    def subscribe(
        self,
        origin_key: str,
        maxsize: int = 500,
        replay: bool = True,
    ) -> "queue.Queue[dict[str, Any]]":
        """
        订阅指定 origin_key 的进度事件，返回一个 Queue。

        replay=True 时会将缓冲中已有事件放入队列，避免晚到的订阅者遗漏事件。
        """
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=maxsize)
        with self._lock:
            if replay:
                for evt in self._buffers.get(origin_key, []):
                    try:
                        q.put_nowait(evt)
                    except queue.Full:
                        break
            self._subscribers.setdefault(origin_key, []).append(q)
        return q

    def unsubscribe(
        self, origin_key: str, q: "queue.Queue[dict[str, Any]]"
    ) -> None:
        """取消订阅，从列表中移除指定 Queue。"""
        with self._lock:
            subs = self._subscribers.get(origin_key, [])
            try:
                subs.remove(q)
            except ValueError:
                pass

    def clear_buffer(self, origin_key: str) -> None:
        """清除指定 origin_key 的回放缓冲（会话结束后可调用释放内存）。"""
        with self._lock:
            self._buffers.pop(origin_key, None)

    @contextmanager
    def subscription(
        self,
        origin_key: str,
        maxsize: int = 500,
        replay: bool = True,
    ) -> Generator["queue.Queue[dict[str, Any]]", None, None]:
        """上下文管理器：进入时订阅，退出时自动取消订阅。"""
        q = self.subscribe(origin_key, maxsize=maxsize, replay=replay)
        try:
            yield q
        finally:
            self.unsubscribe(origin_key, q)
