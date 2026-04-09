"""Browser/WebUI channel using WebSocket."""

import asyncio
import functools
import queue
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
            logger.debug(f"[Browser] on_progress fired: type={evt.get('type')}, qsize_before={evt_queue.qsize()}")
            try:
                evt_queue.put_nowait(evt)
                logger.debug(f"[Browser] on_progress queued OK, qsize_now={evt_queue.qsize()}")
            except asyncio.QueueFull:
                logger.warning(f"[Browser] Event queue full, dropping event")
            except Exception as e:
                logger.error(f"[Browser] on_progress FAILED: {e}")

        async def safe_send(data: dict) -> bool:
            """Safely send JSON to WebSocket, returns False if connection closed."""
            try:
                await websocket.send_json(data)
                logger.debug(f"[Browser] safe_send OK: type={data.get('type')}")
                return True
            except (WebSocketDisconnect, RuntimeError) as e:
                logger.warning(f"[Browser] safe_send FAILED: {e}")
                return False

        async def drain_events():
            """Background task: drain queue -> push WebSocket."""
            logger.debug(f"[Browser] drain_events task started for session={session_id}")
            while True:
                try:
                    evt = await asyncio.wait_for(evt_queue.get(), timeout=60)
                    logger.debug(f"[Browser] drain_events: got evt type={evt.get('type')}, sending...")
                    if not await safe_send({"type": "event", "event": evt}):
                        logger.warning(f"[Browser] drain_events: safe_send False, breaking")
                        break
                    logger.debug(f"[Browser] drain_events: sent OK")
                except asyncio.TimeoutError:
                    if not await safe_send({"type": "ping_check"}):
                        break
                except asyncio.CancelledError:
                    logger.info(f"[Browser] drain_events: cancelled")
                    break
                except Exception as e:
                    logger.warning(f"[Browser] drain_events error: {e}")
                    break

        # Subscribe to subagent progress events via SubagentProgressBus
        from nanobot.agent.subagent_progress import SubagentProgressBus
        origin_key = f"browser:{session_id}"
        subagent_queue: queue.Queue = SubagentProgressBus.get().subscribe(origin_key, replay=False)

        async def drain_subagent_events():
            """Forward subagent progress events to WebSocket."""
            logger.debug(f"[Browser] Subagent drain started for session={session_id}")
            loop = asyncio.get_running_loop()
            try:
                while True:
                    try:
                        # Use run_in_executor to avoid blocking the asyncio event loop
                        evt = await loop.run_in_executor(None, functools.partial(subagent_queue.get, timeout=0.5))
                        logger.debug(f"[Browser] Subagent event: type={evt.get('type')}, task_id={evt.get('task_id', '')}")
                        if not await safe_send({"type": "event", "event": evt}):
                            logger.warning(f"[Browser] Subagent drain: safe_send False, breaking")
                            break
                    except queue.Empty:
                        pass
                    except asyncio.CancelledError:
                        logger.info(f"[Browser] Subagent drain: cancelled")
                        raise
                    except Exception as e:
                        logger.warning(f"[Browser] Subagent drain error: {e}")
                        break
            finally:
                SubagentProgressBus.get().unsubscribe(origin_key, subagent_queue)

        drain_subagent_task = asyncio.create_task(drain_subagent_events())
        drain_task = asyncio.create_task(drain_events())

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")
                logger.info(f"[Browser] Received message type={msg_type}, data={data}")

                if msg_type == "message":
                    content = data.get("content", "")
                    media = data.get("media")

                    resp_content = ""
                    # Directly call agent with progress_callback
                    try:
                        logger.info(f"[Browser] Calling agent.process_direct for session={session_id}")
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
                        resp_content = response.content if response else ""
                        logger.info(f"[Browser] Agent returned response (len={len(resp_content)})")

                        # Build assistantMessage from session (mirrors SSE done event)
                        assistant_msg = None
                        try:
                            messages = self.agent.sessions.get_messages(key=f"browser:{session_id}", limit=2)
                            assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
                            if assistant:
                                assistant_msg = {
                                    "id": f"msg_{assistant['sequence']}",
                                    "sessionId": session_id,
                                    "role": assistant["role"],
                                    "content": assistant["content"],
                                    "createdAt": assistant["timestamp"],
                                    "sequence": assistant["sequence"],
                                }
                                if assistant.get("tool_steps"):
                                    assistant_msg["toolSteps"] = assistant["tool_steps"]
                                if assistant.get("token_usage"):
                                    tu = assistant["token_usage"]
                                    assistant_msg["tokenUsage"] = {
                                        "promptTokens": int(tu.get("prompt_tokens", 0) or 0),
                                        "completionTokens": int(tu.get("completion_tokens", 0) or 0),
                                        "totalTokens": int(tu.get("total_tokens", 0) or 0),
                                    }
                        except Exception as e:
                            logger.warning(f"[Browser] Failed to build assistantMessage: {e}")

                        # Send done event (check if connection still open)
                        logger.info(f"[Browser] Sending done event, response preview: {resp_content[:100]}")
                        if not await safe_send({
                            "type": "event",
                            "event": {"type": "done", "content": resp_content, "assistantMessage": assistant_msg}
                        }):
                            logger.info(f"[Browser] Connection closed before sending done event")
                            break
                        logger.info(f"[Browser] Done event sent successfully")
                    except asyncio.TimeoutError:
                        logger.error(f"[Browser] Agent call timed out after {self.config.agent_timeout}s")
                        if not await safe_send({
                            "type": "error",
                            "error": f"Agent call timed out after {self.config.agent_timeout}s"
                        }):
                            break
                        break
                    except Exception as e:
                        logger.error(f"[Browser] Agent error: {e}")
                        if not await safe_send({
                            "type": "error",
                            "error": str(e)
                        }):
                            break
                        break

                elif msg_type == "ping":
                    # 响应客户端心跳
                    logger.debug(f"[Browser] Received ping, sending pong")
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "pong":
                    # 客户端心跳回执，忽略
                    pass
                else:
                    logger.warning(f"[Browser] Unknown message type: {msg_type}")

        except WebSocketDisconnect:
            logger.info(f"[Browser] Client disconnected: {session_id}")
        except RuntimeError as e:
            # "Cannot call 'send' once a close message has been sent" and similar
            logger.debug(f"[Browser] WebSocket runtime error: {e}")
        except Exception as e:
            logger.error(f"[Browser] WebSocket error: {e}")
        finally:
            drain_task.cancel()
            drain_subagent_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass
            try:
                await drain_subagent_task
            except asyncio.CancelledError:
                pass
            self.ws_manager.unregister(key, websocket)
