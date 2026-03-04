"""Chat 流式事件总线，支持 SSE 重连。

当用户刷新或切换 tab 时，可重新订阅以继续接收推送结果。
origin_key 格式: "web:{session_id}"
"""

import logging
import queue
import threading
from typing import Any

logger = logging.getLogger(__name__)


class ChatStreamBus:
    """
    全局 Chat 流式事件总线。

    支持多订阅者按 origin_key 订阅事件，线程安全。
    内置事件缓冲（最近 _MAX_BUFFER 个事件），
    晚到的订阅者可通过 replay=True 回放已发生的事件。
    """

    _MAX_BUFFER = 200

    _instance: "ChatStreamBus | None" = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "ChatStreamBus":
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
                del buf[: len(buf) - self._MAX_BUFFER]
            queues = list(self._subscribers.get(origin_key, []))

        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                logger.debug(
                    "[ChatStreamBus] Dropped event for %s due to full queue", origin_key
                )

    def subscribe(
        self,
        origin_key: str,
        maxsize: int = 500,
        replay: bool = True,
    ) -> "queue.Queue[dict[str, Any]]":
        """
        订阅指定 origin_key 的流式事件，返回一个 Queue。

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

    def unsubscribe(self, origin_key: str, q: "queue.Queue[dict[str, Any]]") -> None:
        """取消订阅，从列表中移除指定 Queue。"""
        with self._lock:
            subs = self._subscribers.get(origin_key, [])
            try:
                subs.remove(q)
            except ValueError:
                pass

    def clear_buffer(self, origin_key: str) -> None:
        """清除指定 origin_key 的回放缓冲（流结束后可调用释放内存）。"""
        with self._lock:
            self._buffers.pop(origin_key, None)
