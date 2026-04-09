# WebSocket 通信架构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Web UI 的 SSE 通信替换为 WebSocket，实现 browser channel 统一管理。

**Architecture:**
- 新增 `nanobot/channels/browser.py` 作为 browser channel
- 新增 `nanobot/web/websocket_manager.py` 管理 WebSocket 连接
- browser channel 启动独立 FastAPI/Uvicorn ASGI server
- 直接调用 `agent.process_direct()` 传入 progress_callback
- 移除 SSE 相关代码

**Tech Stack:** Python 3.11+, asyncio, FastAPI, uvicorn, WebSocket

---

## Task 1: 添加 FastAPI/Uvicorn 依赖

**Files:**
- Modify: `pyproject.toml`

**前置条件:** 无

- [ ] **Step 1: 添加依赖到 pyproject.toml**

找到 `[project.dependencies]` 部分，添加：

```toml
fastapi = ">=0.110.0"
uvicorn = {extras = ["standard"], version = ">=0.27.0"}
```

- [ ] **Step 2: 安装依赖验证**

Run: `cd E:/workSpace/nanobot-webui && pip install fastapi "uvicorn[standard]" -q && python -c "import fastapi, uvicorn; print('OK')"`
Expected: 输出 `OK`

- [ ] **Step 3: 提交**

```bash
git add pyproject.toml
git commit -m "feat: add fastapi and uvicorn dependencies"
```

---

## Task 2: 实现 WebSocketManager

**Files:**
- Create: `nanobot/web/websocket_manager.py`

**前置条件:** Task 1 完成

- [ ] **Step 1: 创建 WebSocketManager**

```python
"""WebSocket connection manager for browser channel."""

import asyncio
from typing import Any

from loguru import logger


class WebSocketManager:
    """
    管理 WebSocket 连接。

    每个 session 只有一个 WebSocket 连接，直接映射管理。
    """

    def __init__(self):
        self._connections: dict[str, Any] = {}

    def register(self, key: str, websocket: Any) -> None:
        """注册连接（每个 key 只允许一个连接）"""
        self._connections[key] = websocket
        logger.debug(f"[WebSocketManager] Registered: {key}")

    def unregister(self, key: str, websocket: Any) -> None:
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
```

- [ ] **Step 2: 验证语法**

Run: `cd E:/workSpace/nanobot-webui && python -m py_compile nanobot/web/websocket_manager.py`
Expected: 无输出

- [ ] **Step 3: 提交**

```bash
git add nanobot/web/websocket_manager.py
git commit -m "feat: add WebSocketManager for connection management"
```

---

## Task 3: 添加 BrowserConfig 配置 schema

**Files:**
- Modify: `nanobot/config/schema.py` (在 `ChannelsConfig` 类中添加)

**前置条件:** 无

- [ ] **Step 1: 添加 BrowserConfig 类**

在 `DingTalkConfig` 类后、`ChannelsConfig` 类前添加：

```python
class BrowserConfig(BaseModel):
    """Browser/WebUI channel configuration using WebSocket."""
    enabled: bool = False
    host: str = "127.0.0.1"  # WebSocket 监听地址
    port: int = 8765  # WebSocket 监听端口
```

- [ ] **Step 2: 添加到 ChannelsConfig**

在 `ChannelsConfig` 类中添加：

```python
class ChannelsConfig(BaseModel):
    """Configuration for chat channels."""
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)  # 新增
```

- [ ] **Step 3: 验证语法**

Run: `cd E:/workSpace/nanobot-webui && python -m py_compile nanobot/config/schema.py`
Expected: 无输出

- [ ] **Step 4: 提交**

```bash
git add nanobot/config/schema.py
git commit -m "feat: add BrowserConfig schema for browser channel"
```

---

## Task 4: 实现 browser channel

**Files:**
- Create: `nanobot/channels/browser.py`

**前置条件:** Tasks 2, 3 完成

- [ ] **Step 1: 创建 browser channel**

```python
"""Browser/WebUI channel using WebSocket."""

import asyncio
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from loguru import logger

from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.bus.events import InboundMessage, OutboundMessage
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
        """启动 WebSocket 服务器"""
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
        """停止 WebSocket 服务器"""
        self._running = False
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
        logger.info("[Browser] WebSocket server stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """发送消息到客户端（OutboundMessage 路由）"""
        key = f"browser:{msg.chat_id}"
        await self.ws_manager.send(key, {"type": "message", "content": msg.content})

    async def _handle_connection(self, websocket: WebSocket, session_id: str):
        """处理单个 WebSocket 连接"""
        await websocket.accept()
        key = f"browser:{session_id}"
        self.ws_manager.register(key, websocket)
        logger.info(f"[Browser] Client connected: {session_id}")

        # 为这个连接创建专属的 event queue
        evt_queue: asyncio.Queue = asyncio.Queue(maxsize=500)

        def on_progress(evt: dict) -> None:
            """Progress 回调，同步写入 queue（与 SSE 一致）"""
            try:
                evt_queue.put_nowait(evt)
            except asyncio.QueueFull:
                logger.warning(f"[Browser] Event queue full, dropping event")

        async def drain_events():
            """后台任务：drain queue → 推 WebSocket"""
            while True:
                try:
                    evt = await asyncio.wait_for(evt_queue.get(), timeout=60)
                    await websocket.send_json({"type": "event", "event": evt})
                except asyncio.TimeoutError:
                    # 超时检查连接是否存活
                    try:
                        await websocket.send_json({"type": "ping_check"})
                    except Exception:
                        break
                except asyncio.CancelledError:
                    break
                except Exception:
                    break

        drain_task = asyncio.create_task(drain_events())

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")

                if msg_type == "message":
                    content = data.get("content", "")
                    media = data.get("media")

                    # 直接调用 agent，传入 progress_callback
                    try:
                        response = await self.agent.process_direct(
                            content=content,
                            session_key=f"browser:{session_id}",
                            channel="browser",
                            progress_callback=on_progress,
                            media=media,
                        )

                        # 发送完成事件
                        await websocket.send_json({
                            "type": "event",
                            "event": {"type": "done", "content": response}
                        })
                    except Exception as e:
                        logger.error(f"[Browser] Agent error: {e}")
                        await websocket.send_json({
                            "type": "error",
                            "error": str(e)
                        })

                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "pong":
                    # 心跳响应，忽略
                    pass

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
```

- [ ] **Step 2: 验证语法**

Run: `cd E:/workSpace/nanobot-webui && python -m py_compile nanobot/channels/browser.py`
Expected: 无输出

- [ ] **Step 3: 提交**

```bash
git add nanobot/channels/browser.py
git commit -m "feat: add browser channel with WebSocket support"
```

---

## Task 5: 在 ChannelManager 中注册 browser channel

**Files:**
- Modify: `nanobot/channels/manager.py` (在 `_init_channels` 方法中添加)

**前置条件:** Task 4 完成

- [ ] **Step 1: 添加 browser channel 初始化**

在 `_init_channels` 方法中，在 DingTalk channel 之后添加：

```python
        # Browser channel
        if self.config.channels.browser.enabled:
            try:
                from nanobot.channels.browser import BrowserChannel
                self.channels["browser"] = BrowserChannel(
                    self.config.channels.browser,
                    self.bus,
                    agent=self.agent,
                )
                logger.info("Browser channel enabled")
            except ImportError as e:
                logger.warning(f"Browser channel not available: {e}")
```

- [ ] **Step 2: 验证语法**

Run: `cd E:/workSpace/nanobot-webui && python -m py_compile nanobot/channels/manager.py`
Expected: 无输出

- [ ] **Step 3: 提交**

```bash
git add nanobot/channels/manager.py
git commit -m "feat: register browser channel in ChannelManager"
```

---

## Task 6: 客户端 WebSocket 实现

**Files:**
- Modify: `web-ui/src/api.ts` (修改发送消息方法)
- Create: `web-ui/src/hooks/useWebSocket.ts` (新增 WebSocket hook)

**前置条件:** Task 5 完成

### Part A: 创建 useWebSocket hook

- [ ] **Step 1: 创建 useWebSocket.ts**

```typescript
import { useEffect, useRef, useCallback, useState } from 'react';

export interface WsEvent {
  type: string;
  event?: {
    type: string;
    content?: string;
    [key: string]: any;
  };
  error?: string;
}

export interface UseWebSocketOptions {
  url: string;
  onMessage: (event: WsEvent) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Event) => void;
  reconnect?: boolean;
  reconnectInterval?: number;
}

export function useWebSocket(options: UseWebSocketOptions) {
  const {
    url,
    onMessage,
    onConnect,
    onDisconnect,
    onError,
    reconnect = true,
    reconnectInterval = 3000,
  } = options;

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    const ws = new WebSocket(url);

    ws.onopen = () => {
      setIsConnected(true);
      onConnect?.();
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WsEvent;
        onMessage(data);
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e);
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      onDisconnect?.();

      if (reconnect) {
        reconnectTimeoutRef.current = window.setTimeout(() => {
          connect();
        }, reconnectInterval);
      }
    };

    ws.onerror = (error) => {
      onError?.(error);
    };

    wsRef.current = ws;
  }, [url, onMessage, onConnect, onDisconnect, onError, reconnect, reconnectInterval]);

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const send = useCallback((data: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  useEffect(() => {
    connect();
    return () => {
      disconnect();
    };
  }, [connect, disconnect]);

  return {
    isConnected,
    send,
    disconnect,
    reconnect: connect,
  };
}
```

### Part B: 修改 api.ts

- [ ] **Step 2: 修改 api.ts 添加 WebSocket 方法**

找到现有的 `sendMessageStream` 相关方法，添加 WebSocket 版本：

```typescript
// WebSocket 配置
const WS_BASE_URL = `ws://${window.location.hostname}:8765`;

export interface WebSocketChatOptions {
  sessionId: string;
  onEvent: (event: WsEvent) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Event) => void;
}

export function createWebSocketChat(options: WebSocketChatOptions) {
  const { sessionId, onEvent, onConnect, onDisconnect, onError } = options;

  return useWebSocket({
    url: `${WS_BASE_URL}/ws/${sessionId}`,
    onMessage: onEvent,
    onConnect,
    onDisconnect,
    onError,
  });
}

// 发送聊天消息
export function wsSendMessage(
  send: (data: object) => void,
  content: string,
  media?: string[]
) {
  send({
    type: 'message',
    content,
    media,
  });
}
```

- [ ] **Step 3: 提交**

```bash
git add web-ui/src/api.ts web-ui/src/hooks/useWebSocket.ts
git commit -m "feat: add WebSocket client implementation for browser channel"
```

---

## Task 7: 适配 ChatPage 使用 WebSocket

**Files:**
- Modify: `web-ui/src/pages/ChatPage.tsx`

**前置条件:** Task 6 完成

- [ ] **Step 1: 修改 ChatPage 使用 WebSocket**

将 SSE 相关的状态和逻辑替换为 WebSocket：

```typescript
import { useWebSocket, createWebSocketChat, wsSendMessage } from '../api';

// 在组件中添加
const [wsInstance, setWsInstance] = useState<ReturnType<typeof useWebSocket> | null>(null);

// 初始化 WebSocket
useEffect(() => {
  const instance = createWebSocketChat({
    sessionId: currentSessionId,
    onEvent: (event) => {
      if (event.event?.type === 'done') {
        // 处理完成
        refreshSessions();
      } else if (event.event?.type) {
        // 处理其他事件
        handleAgentEvent(event.event);
      } else if (event.error) {
        // 处理错误
        setError(event.error);
      }
    },
    onConnect: () => {
      console.log('WebSocket connected');
    },
    onDisconnect: () => {
      console.log('WebSocket disconnected');
    },
  });

  setWsInstance(instance);

  return () => {
    instance.disconnect();
  };
}, [currentSessionId]);

// 发送消息
const handleSendMessage = () => {
  if (wsInstance?.send && input.trim()) {
    wsSendMessage(wsInstance.send, input, selectedImages);
    setInput('');
    setSelectedImages([]);
  }
};
```

- [ ] **Step 2: 验证 TypeScript 编译**

Run: `cd E:/workSpace/nanobot-webui/web-ui && npx tsc --noEmit 2>&1 | head -20`
Expected: 无错误（或仅已有错误）

- [ ] **Step 3: 提交**

```bash
git add web-ui/src/pages/ChatPage.tsx
git commit -m "feat: adapt ChatPage to use WebSocket instead of SSE"
```

---

## Task 8: 移除 SSE 相关代码

**Files:**
- Modify: `nanobot/web/api.py` (移除 SSE 端点和 chat_stream 方法)
- Delete: `nanobot/web/chat_stream_bus.py`

**前置条件:** Tasks 1-7 完成

- [ ] **Step 1: 移除 SSE 相关端点**

在 `api.py` 中找到并删除以下方法和相关代码：
- `_handle_chat_stream` 方法
- `_handle_chat_stream_resume` 方法
- `_handle_subagent_progress_stream` 方法
- `chat_stream` 方法
- `chat_stream_resume` 方法
- `chat_stream_subagent_progress` 方法
- `NanobotWebAPI` 类中引用 `ChatStreamBus` 的部分

**注意：** 保留其他非 SSE 相关的端点（如配置获取、session 管理等）。

- [ ] **Step 2: 移除 ChatStreamBus**

删除 `nanobot/web/chat_stream_bus.py` 文件。

- [ ] **Step 3: 验证语法**

Run: `cd E:/workSpace/nanobot-webui && python -m py_compile nanobot/web/api.py`
Expected: 无输出

- [ ] **Step 4: 提交**

```bash
git add nanobot/web/api.py
git rm nanobot/web/chat_stream_bus.py
git commit -m "refactor: remove SSE code, migrate to WebSocket"
```

---

## Task 9: 验证改动完整性

**前置条件:** Tasks 1-8 完成

- [ ] **Step 1: 运行完整语法检查**

Run: `cd E:/workSpace/nanobot-webui && python -m py_compile nanobot/channels/browser.py nanobot/web/websocket_manager.py nanobot/channels/manager.py nanobot/config/schema.py`
Expected: 无输出

- [ ] **Step 2: 验证配置可以加载**

Run: `cd E:/workSpace/nanobot-webui && python -c "from nanobot.config.schema import BrowserConfig, ChannelsConfig; c = ChannelsConfig(); print(f'browser enabled: {c.browser.enabled}, port: {c.browser.port}')"`
Expected: 输出 `browser enabled: False, port: 8765`

- [ ] **Step 3: 验证 ChannelManager 可以初始化**

Run: `cd E:/workSpace/nanobot-webui && python -c "from nanobot.channels.manager import ChannelManager; print('ChannelManager OK')"`
Expected: 输出 `ChannelManager OK`

- [ ] **Step 4: 对照设计文档检查**

对照 `docs/superpowers/specs/2026-04-08-websocket-architecture-design.md` 检查：
- [x] WebSocketManager 实现
- [x] browser channel 实现
- [x] 配置 schema 添加
- [x] ChannelManager 注册
- [x] 客户端 WebSocket 实现
- [x] SSE 代码移除
