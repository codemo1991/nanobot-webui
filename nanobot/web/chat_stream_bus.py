"""Chat 流式事件总线，支持 SSE 重连。

当用户刷新或切换 tab 时，可重新订阅以继续接收推送结果。
origin_key 格式: "web:{session_id}"
"""

import logging
import queue
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# 会话缓冲区的默认 TTL（秒）：无活跃订阅者且超过此时间后会被 cleanup_stale_sessions 清理
_DEFAULT_SESSION_TTL = 3600.0  # 1 小时


class ChatStreamBus:
    """
    全局 Chat 流式事件总线。

    支持多订阅者按 origin_key 订阅事件，线程安全。
    内置事件缓冲（最近 _MAX_BUFFER 个事件），
    晚到的订阅者可通过 replay=True 回放已发生的事件。

    Fix #10: 新增 close_session（主动清理）和 cleanup_stale_sessions（TTL 被动清理），
    防止长时间运行时 session 缓冲区无限积累导致内存泄漏。
    """

    _MAX_BUFFER = 200

    _instance: "ChatStreamBus | None" = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        # Fix #10: 记录每个 origin_key 的最后活跃时间，用于 TTL 清理
        self._last_active: dict[str, float] = {}
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
            self._last_active[origin_key] = time.monotonic()
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
            self._last_active[origin_key] = time.monotonic()
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

    def close_session(self, origin_key: str) -> None:
        """Fix #10: 主动关闭会话，清理缓冲区、订阅者列表和活跃时间记录。

        应在会话结束（用户关闭聊天或 SSE 流正常终止）时调用，防止内存泄漏。
        所有正在监听的订阅者 Queue 将不再收到新事件，但已入队的事件仍可消费。
        """
        with self._lock:
            self._buffers.pop(origin_key, None)
            self._subscribers.pop(origin_key, None)
            self._last_active.pop(origin_key, None)
        logger.debug("[ChatStreamBus] 会话已关闭并清理: %s", origin_key)

    def cleanup_stale_sessions(self, ttl_seconds: float = _DEFAULT_SESSION_TTL) -> int:
        """Fix #10: 清理超过 TTL 且无活跃订阅者的会话缓冲区。

        建议由定时任务（如每小时）调用一次，防止 session 数量无上限积累。
        返回本次清理的 origin_key 数量。

        清理条件：
        1. 无活跃订阅者（订阅者列表为空）
        2. 最后活跃时间距今超过 ttl_seconds
        """
        now = time.monotonic()
        stale: list[str] = []

        with self._lock:
            for key, last_t in list(self._last_active.items()):
                has_subscribers = bool(self._subscribers.get(key))
                if not has_subscribers and (now - last_t) > ttl_seconds:
                    stale.append(key)

            for key in stale:
                self._buffers.pop(key, None)
                self._subscribers.pop(key, None)
                self._last_active.pop(key, None)

        if stale:
            logger.debug("[ChatStreamBus] TTL 清理了 %d 个过期会话: %s", len(stale), stale)
        return len(stale)
