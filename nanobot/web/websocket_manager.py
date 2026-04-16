"""WebSocket connection manager for browser channel."""

import asyncio

from fastapi import WebSocket
from loguru import logger


class WebSocketManager:
    """
    管理 WebSocket 连接。

    每个 session 只有一个 WebSocket 连接，直接映射管理。
    """

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    def register(self, key: str, websocket: WebSocket) -> None:
        """注册连接（每个 key 只允许一个连接，自动清理旧连接）"""
        # Close any existing connection for this key before registering new one
        if key in self._connections:
            old_ws = self._connections[key]
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(old_ws.close())
            except RuntimeError:
                # Fallback: no running loop, skip async close
                pass
            logger.debug(f"[WebSocketManager] Replaced existing connection: {key}")
        self._connections[key] = websocket
        logger.debug(f"[WebSocketManager] Registered: {key}")

    def unregister(self, key: str, websocket: WebSocket) -> None:
        """注销连接"""
        if self._connections.get(key) is websocket:
            del self._connections[key]
            logger.debug(f"[WebSocketManager] Unregistered: {key}")

    async def send(self, key: str, data: dict) -> bool:
        """发送数据到指定连接，成功返回 True"""
        ws = self._connections.get(key)
        if ws is None:
            return False
        try:
            await ws.send_json(data)
            return True
        except Exception as e:
            logger.warning(f"[WebSocketManager] Send failed for {key}: {e}")
            self.unregister(key, ws)
            return False

    async def send_delta(
        self,
        key: str,
        delta: str,
        stream_end: bool = False,
        stream_id: str | None = None,
    ) -> bool:
        """Send incremental delta to a WebSocket connection."""
        event: dict[str, Any] = {"type": "delta", "text": delta}
        if stream_end:
            event = {"type": "stream_end"}
        if stream_id is not None:
            event["stream_id"] = stream_id
        return await self.send(key, {"type": "event", "event": event})

    def is_connected(self, key: str) -> bool:
        """检查是否有活跃连接"""
        return key in self._connections

    @property
    def connection_count(self) -> int:
        """当前连接数"""
        return len(self._connections)
