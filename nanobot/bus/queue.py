"""Async message queue for decoupled channel-agent communication."""

from __future__ import annotations

import asyncio
import threading
from typing import Callable, Awaitable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage

class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self, max_inbound: int = 200):
        # maxsize 防止无限积压；超出时 try_publish_inbound_sync 返回 (False, "queue_full")
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
    ) -> tuple[bool, str]:
        """
        从 HTTP 工作线程安全地将消息入队（线程安全，非协程）。

        返回 (True, "") 表示成功；(False, reason) 中 reason 取值：
        - queue_full：入站队列已满
        - enqueue_timeout：在超时时间内 event loop 未执行入队协程（常见于 loop 被同步阻塞）
        - loop_none / loop_not_running / loop_status_error：loop 无效
        - enqueue_error：future 完成时出现其它异常

        使用 call_soon_threadsafe 而非 run_coroutine_threadsafe：与 SubagentManager._publish_inbound_safe
        一致，更易唤醒阻塞于 selector 的 loop；入队逻辑为同步 put_nowait。
        """
        def _depth() -> int:
            try:
                return self.inbound.qsize()
            except Exception:
                return -1

        maxsz = getattr(self.inbound, "maxsize", 0) or 0

        # 前置检查：loop 是否有效
        if loop is None:
            logger.warning("[MessageBus] try_publish_inbound_sync: loop is None")
            return False, "loop_none"
        try:
            is_running = loop.is_running()
        except Exception as e:
            logger.warning(f"[MessageBus] try_publish_inbound_sync: loop.is_running() failed: {e!r}")
            return False, "loop_status_error"
        if not is_running:
            logger.warning("[MessageBus] try_publish_inbound_sync: loop not running")
            return False, "loop_not_running"

        # 前置检查：队列是否已满（快速路径，避免不必要的线程调度）
        try:
            if self.inbound.full():
                logger.warning(
                    "[MessageBus] try_publish_inbound_sync: 入站队列已满 "
                    f"(depth≈{_depth()}/{maxsz})"
                )
                return False, "queue_full"
        except Exception:
            pass  # 检查失败时继续尝试入队

        _SYNC_PUT_TIMEOUT = 30.0
        done = threading.Event()
        outcome: list[bool | None | BaseException] = [None]

        def _do_put() -> None:
            try:
                self.inbound.put_nowait(msg)
                outcome[0] = True
            except asyncio.QueueFull:
                outcome[0] = False
            except BaseException as e:
                outcome[0] = e
            finally:
                done.set()

        try:
            loop.call_soon_threadsafe(_do_put)
        except Exception as e:
            logger.warning(f"[MessageBus] call_soon_threadsafe 失败: {type(e).__name__}: {e!r}")
            return False, "enqueue_error"

        if not done.wait(timeout=_SYNC_PUT_TIMEOUT):
            logger.warning(
                "[MessageBus] try_publish_inbound_sync: 等待 event loop 执行入队超时 "
                f"({_SYNC_PUT_TIMEOUT}s), inbound_depth≈{_depth()}/{maxsz} — "
                "多为 core loop 长时间未让出（同步阻塞）而非单纯队满"
            )
            return False, "enqueue_timeout"

        res = outcome[0]
        if res is True:
            return True, ""
        if res is False:
            logger.warning(
                "[MessageBus] try_publish_inbound_sync: put_nowait 失败（队满，竞态） "
                f"depth≈{_depth()}/{maxsz}"
            )
            return False, "queue_full"
        if isinstance(res, BaseException):
            logger.warning(f"[MessageBus] try_publish_inbound_sync 入队异常: {type(res).__name__}: {res!r}")
            return False, "enqueue_error"
        return False, "enqueue_error"

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
