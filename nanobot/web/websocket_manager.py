"""WebSocket connection manager for browser channel."""

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
        """注册连接（每个 key 只允许一个连接）"""
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

    def is_connected(self, key: str) -> bool:
        """检查是否有活跃连接"""
        return key in self._connections

    @property
    def connection_count(self) -> int:
        """当前连接数"""
        return len(self._connections)
