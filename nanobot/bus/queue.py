"""Async message queue for decoupled channel-agent communication."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, Awaitable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from concurrent.futures import Future


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self, max_inbound: int = 200):
        # maxsize 防止无限积压；超出时 try_publish_inbound_sync 返回 False（HTTP 429）
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=max_inbound)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._outbound_subscribers: dict[str, list[Callable[[OutboundMessage], Awaitable[None]]]] = {}
        self._running = False
        # dispatch_outbound 运行时懒创建，确保在正确的 event loop 中
        self._dispatch_stop_event: asyncio.Event | None = None

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    def try_publish_inbound_sync(
        self, msg: InboundMessage, loop: asyncio.AbstractEventLoop
    ) -> bool:
        """
        从 HTTP 工作线程安全地将消息入队（线程安全，非协程）。
        队满时返回 False，调用方应向客户端返回 HTTP 429。
        """
        from concurrent.futures import Future as _Future
        future: _Future[bool] = asyncio.run_coroutine_threadsafe(
            self._try_put(msg), loop
        )
        try:
            return future.result(timeout=1.0)
        except Exception as e:
            logger.warning(f"[MessageBus] try_publish_inbound_sync failed: {e}")
            return False

    async def _try_put(self, msg: InboundMessage) -> bool:
        """非阻塞入队，队满返回 False。"""
        try:
            self.inbound.put_nowait(msg)
            return True
        except asyncio.QueueFull:
            return False

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    def subscribe_outbound(
        self,
        channel: str,
        callback: Callable[[OutboundMessage], Awaitable[None]],
    ) -> None:
        """Subscribe to outbound messages for a specific channel."""
        if channel not in self._outbound_subscribers:
            self._outbound_subscribers[channel] = []
        self._outbound_subscribers[channel].append(callback)

    async def dispatch_outbound(self) -> None:
        """
        Dispatch outbound messages to subscribed channels.

        阻塞于 outbound.get()，无消息时 CPU 占用接近 0；
        调用 stop_dispatch() 后通过 asyncio.Event 干净退出，避免 1s 轮询。
        """
        self._running = True
        self._dispatch_stop_event = asyncio.Event()
        while self._running:
            get_task = asyncio.create_task(self.outbound.get())
            stop_task = asyncio.create_task(self._dispatch_stop_event.wait())
            done, pending = await asyncio.wait(
                {get_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            if stop_task in done:
                # stop_dispatch() 已触发，干净退出
                if get_task in pending or get_task.cancelled():
                    break
            if get_task in done and not get_task.cancelled():
                try:
                    msg = get_task.result()
                except Exception:
                    continue
                subscribers = self._outbound_subscribers.get(msg.channel, [])
                for callback in subscribers:
                    try:
                        await callback(msg)
                    except Exception as e:
                        logger.error(f"Error dispatching to {msg.channel}: {e}")

    def stop_dispatch(self, loop: asyncio.AbstractEventLoop) -> None:
        """从非异步上下文安全地停止 dispatch_outbound 协程。"""
        self._running = False
        if self._dispatch_stop_event and loop.is_running():
            loop.call_soon_threadsafe(self._dispatch_stop_event.set)

    def stop(self) -> None:
        """停止 dispatcher 标志位（不带 loop 参数时使用）。"""
        self._running = False

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages. Note: qsize() may be approximate."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages. Note: qsize() may be approximate."""
        return self.outbound.qsize()
