"""Browser/WebUI channel using WebSocket."""

import asyncio
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from loguru import logger

from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.bus.events import OutboundMessage
from nanobot.config.schema import BrowserConfig
from nanobot.web.websocket_manager import WebSocketManager


class BrowserChannel(BaseChannel):
    """Browser/WebUI channel using WebSocket for bidirectional communication."""

    name = "browser"

    def __init__(
        self,
        config: BrowserConfig,
        bus: MessageBus,
        agent: Any,
    ):
        super().__init__(config, bus)
        self.agent = agent
        self.ws_manager = WebSocketManager()
        self._server_task: asyncio.Task | None = None
        self._app: FastAPI | None = None

    async def start(self) -> None:
        """Start WebSocket server."""
        if not self.config.enabled:
            logger.info("Browser channel disabled")
            return

        host = self.config.host
        port = self.config.port

        self._app = FastAPI()

        @self._app.websocket("/ws/{session_id}")
        async def websocket_endpoint(websocket: WebSocket, session_id: str):
            await self._handle_connection(websocket, session_id)

        config = uvicorn.Config(
            self._app,
            host=host,
            port=port,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        self._running = True
        self._server_task = asyncio.create_task(server.serve())
        logger.info(f"[Browser] WebSocket server started on {host}:{port}")

    async def stop(self) -> None:
        """Stop WebSocket server."""
        self._running = False
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
        logger.info("[Browser] WebSocket server stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """Send message to client (OutboundMessage routing)."""
        key = f"browser:{msg.chat_id}"
        await self.ws_manager.send(key, {"type": "message", "content": msg.content})

    async def _handle_connection(self, websocket: WebSocket, session_id: str):
        """Handle individual WebSocket connection."""
        await websocket.accept()
        key = f"browser:{session_id}"
        self.ws_manager.register(key, websocket)
        logger.info(f"[Browser] Client connected: {session_id}")

        # Per-connection event queue
        evt_queue: asyncio.Queue = asyncio.Queue(maxsize=500)

        def on_progress(evt: dict) -> None:
            """Progress callback, writes synchronously to queue (consistent with SSE)."""
            try:
                evt_queue.put_nowait(evt)
            except asyncio.QueueFull:
                logger.warning(f"[Browser] Event queue full, dropping event")

        async def drain_events():
            """Background task: drain queue -> push WebSocket."""
            while True:
                try:
                    evt = await asyncio.wait_for(evt_queue.get(), timeout=60)
                    await websocket.send_json({"type": "event", "event": evt})
                except asyncio.TimeoutError:
                    # Check if connection is alive
                    try:
                        await websocket.send_json({"type": "ping_check"})
                    except Exception:
                        break
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.exception(f"[Browser] drain_events error: {e}")
                    break

        drain_task = asyncio.create_task(drain_events())

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")

                if msg_type == "message":
                    content = data.get("content", "")
                    media = data.get("media")

                    # Directly call agent with progress_callback
                    try:
                        response = await asyncio.wait_for(
                            self.agent.process_direct(
                                content=content,
                                session_key=f"browser:{session_id}",
                                channel="browser",
                                progress_callback=on_progress,
                                media=media,
                            ),
                            timeout=self.config.agent_timeout,
                        )

                        # Send done event
                        await websocket.send_json({
                            "type": "event",
                            "event": {"type": "done", "content": response}
                        })
                    except asyncio.TimeoutError:
                        logger.error(f"[Browser] Agent call timed out after {self.config.agent_timeout}s")
                        await websocket.send_json({
                            "type": "error",
                            "error": f"Agent call timed out after {self.config.agent_timeout}s"
                        })
                        break
                    except Exception as e:
                        logger.error(f"[Browser] Agent error: {e}")
                        await websocket.send_json({
                            "type": "error",
                            "error": str(e)
                        })
                        break

                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "pong":
                    # Heartbeat response, ignore
                    pass
                else:
                    logger.warning(f"[Browser] Unknown message type: {msg_type}")

        except WebSocketDisconnect:
            logger.info(f"[Browser] Client disconnected: {session_id}")
        except Exception as e:
            logger.error(f"[Browser] WebSocket error: {e}")
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass
            self.ws_manager.unregister(key, websocket)
