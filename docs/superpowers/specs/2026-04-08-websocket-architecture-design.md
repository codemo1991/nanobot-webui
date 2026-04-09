# WebSocket 通信架构设计

## 背景

当前 nanobot-webui 的本地 Web UI 使用 SSE (Server-Sent Events) 与后端通信。对于本地用户场景，SSE 存在以下问题：

1. **状态同步复杂**：SSE 是单向的，客户端断开重连后需要从 ChatStreamBus replay 缓冲区恢复状态
2. **连接管理困难**：一个 session 需要维护 POST 连接 + SSE GET 连接共两个连接
3. **企业防火墙问题**：部分企业网络会阻断长连接的 SSE
4. **与现有 channel 模式不一致**：其他 channel（如 Feishu）使用 WebSocket，架构不统一

## 目标

将 Web UI 的通信方式从 SSE 改为 WebSocket，抽象为 `browser` channel，实现统一的双向通信。

## 架构变化

### 当前架构

```
Web UI ← SSE over HTTP → ThreadingHTTPServer → ChatStreamBus → AgentLoop
         (两个连接: POST + SSE GET)
```

### 新架构

```
Web UI ← WebSocket → FastAPI (独立 ASGI Server) → MessageBus → AgentLoop
         (单一连接: 双向 WebSocket)
              ↓
         browser channel (按配置启用)
```

### browser Channel 启动条件

- 只有在配置文件中启用 browser channel 时才启动 WebSocket 服务器
- 与其他 channel（Feishu/Telegram）一样，由 ChannelManager 统一管理生命周期

## 技术方案

### 1. WebSocket Server 方案

**方案选择：** FastAPI + Uvicorn

**设计要点：**
- browser channel 内部启动独立的 FastAPI/Uvicorn ASGI server
- 端口在配置中指定（如 `browser.port: 8765`）
- 与现有的 ThreadingHTTPServer 独立运行（避免侵入现有 channel）
- browser channel 启用时启动，禁用时关闭

**新增依赖：**
- `fastapi`
- `uvicorn`（ASGI server）

### 2. WebSocket 消息协议

#### 客户端 → 服务端

```typescript
// 发送聊天消息
{
  type: "message",
  content: string,        // 消息内容
  media?: string[],       // 媒体文件路径列表
  session_id?: string     // 可选，指定 session
}

// 心跳保活
{ type: "ping" }
```

#### 服务端 → 客户端

```typescript
// Agent 事件（与原 SSE 事件格式一致）
{
  type: "event",
  event: {
    type: "agent_start" | "message_start" | "message_end" | "tool_start" | "tool_end" | "thinking" | "done" | "error",
    content?: string,
    role?: string,
    name?: string,
    id?: string,
    arguments?: object,
    result?: string,
    has_tool_calls?: boolean,
    finish_reason?: string,
    error?: string,
    // ... 其他 SSE 原有字段
  }
}

// 心跳响应
{ type: "pong" }

// 错误通知
{ type: "error", error: string, session_id?: string }
```

### 3. browser Channel 实现

**文件：** `nanobot/channels/browser.py`

**职责：**
- 管理 WebSocket 连接生命周期
- 将 WebSocket 消息转换为 `InboundMessage`（channel = "browser"）
- 接收 `OutboundMessage` 并推送给对应客户端
- **启动独立的 WebSocket 服务器**（按配置启用/禁用）

**核心逻辑：**
```python
class BrowserChannel(BaseChannel):
    name = "browser"

    def __init__(self, config, bus: MessageBus):
        self.config = config
        self.bus = bus
        self.ws_manager = WebSocketManager()
        self._server_task: asyncio.Task | None = None

    async def start(self):
        """启动 WebSocket 服务器（由 ChannelManager 调用）"""
        if not self.config.get("enabled", False):
            return
        port = self.config.get("port", 8765)
        host = self.config.get("host", "127.0.0.1")

        app = FastAPI()

        @app.websocket("/ws/{session_id}")
        async def websocket_endpoint(websocket: WebSocket, session_id: str):
            await self._handle_connection(websocket, session_id)

        # 在独立线程中运行 uvicorn（不阻塞主事件循环）
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(server.serve())

    async def stop(self):
        """停止 WebSocket 服务器"""
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

    async def _handle_connection(self, websocket: WebSocket, session_id: str):
        """处理单个 WebSocket 连接"""
        await websocket.accept()
        key = f"browser:{session_id}"
        self.ws_manager.register(key, websocket)

        try:
            while True:
                data = await websocket.receive_json()
                msg = self._parse_message(data, session_id)
                if msg:
                    await self.bus.publish_inbound(msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            self.ws_manager.unregister(key, websocket)

    def _parse_message(self, data: dict, session_id: str) -> InboundMessage | None:
        """解析 WebSocket 消息为 InboundMessage"""
        msg_type = data.get("type")

        if msg_type == "message":
            return InboundMessage(
                channel="browser",
                sender_id=session_id,
                chat_id=session_id,
                content=data.get("content", ""),
                media=data.get("media"),
                metadata={"session_id": session_id, "ws": True}
            )
        elif msg_type == "ping":
            # 心跳，异步响应
            asyncio.create_task(self.ws_manager.send(
                f"browser:{session_id}",
                {"type": "pong"}
            ))
        return None

    async def send(self, message: OutboundMessage):
        """发送 OutboundMessage 到客户端"""
        key = f"browser:{message.chat_id}"
        await self.ws_manager.send(key, {"type": "message", "content": message.content})
```

### 4. WebSocket Manager

**文件：** `nanobot/web/websocket_manager.py`

**职责：**
- 管理所有 WebSocket 连接（key → WebSocket mapping）
- 单连接推送（每个 session 只有一个 WebSocket 连接）
- 处理连接心跳
- 清理断开的连接

**核心逻辑：**
```python
class WebSocketManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    def register(self, key: str, websocket: WebSocket):
        """注册连接（每个 key 只允许一个连接）"""
        self._connections[key] = websocket

    def unregister(self, key: str, websocket: WebSocket):
        """注销连接"""
        if self._connections.get(key) is websocket:
            del self._connections[key]

    async def send(self, key: str, data: dict):
        """发送数据到指定连接"""
        ws = self._connections.get(key)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.unregister(key, ws)
```

### 5. Agent Loop 集成（progress 事件路由）

**问题：** agent loop 的 `process_direct` 需要将 progress 事件实时推送到 WebSocket。

**方案：** browser channel 提供一个全局的 progress 回调注册机制。

```python
class BrowserChannel(BaseChannel):
    # ...

    def get_progress_sender(self, session_id: str) -> Callable:
        """返回该 session 的 progress 回调"""
        key = f"browser:{session_id}"

        async def send_progress(event: dict):
            await self.ws_manager.send(key, {"type": "event", "event": event})

        return send_progress
```

**调用流程：**
```
browser channel.start()
    ↓
WebSocket 连接建立
    ↓
browser channel._handle_connection()
    ↓
progress_callback = get_progress_sender(session_id)
    ↓
progress_callback → ws_manager.send() → WebSocket
```

**实现细节：** 通过 `InboundMessage.metadata["progress_callback"]` 传递 progress_sender，agent loop 读取并使用。

### 6. 移除的组件

**删除：**
- `nanobot/web/chat_stream_bus.py` — SSE 专用逻辑不再需要
- SSE 端点：
  - `_handle_chat_stream()`
  - `_handle_chat_stream_resume()`
  - `_handle_subagent_progress_stream()`
  - `/api/v1/traces/stream`

### 7. WebSocket 端点设计

```
WebSocket: ws://{host}:{port}/ws/{session_id}
```

- host/port 在 browser channel 配置中指定
- session_id 用于标识会话
- 客户端负责管理 session 生命周期

### 8. 错误处理

| 错误场景 | 处理方式 |
|----------|----------|
| WebSocket 断开 | 清理连接，下次连接会重新开始 session |
| 消息解析失败 | 发送 `{type: "error", error: "invalid message format"}` |
| Agent 处理失败 | 通过 WebSocket 发送 `error` 事件 |
| 连接超时 | WebSocket 自动关闭，客户端重连 |

### 9. 会话管理

- session_id 通过 URL 路径传递（`/ws/{session_id}`）
- 复用现有的 SessionManager
- 断线重连后，客户端从 session 历史重新构建 UI

## 文件清单

### 新增文件
| 文件 | 用途 |
|------|------|
| `nanobot/channels/browser.py` | browser channel 实现 |
| `nanobot/web/websocket_manager.py` | WebSocket 连接管理器 |
| `nanobot/web/protocol.py` | WebSocket 消息协议定义 |

### 修改文件
| 文件 | 改动 |
|------|------|
| `nanobot/config/schema.py` | 添加 browser channel 配置 schema |
| `nanobot/config/builtin_templates_data.py` | 添加 browser channel 默认配置 |
| `nanobot/channels/manager.py` | 注册 browser channel |
| `nanobot/bus/events.py` | 确认 browser channel 事件格式 |
| `web-ui/src/api.ts` | WebSocket 客户端实现 |
| `web-ui/src/pages/ChatPage.tsx` | 适配 WebSocket 事件 |
| `web-ui/src/hooks/useWebSocket.ts` | 新增 WebSocket hook |
| `web-ui/src/config.ts` | 添加 WebSocket 连接配置 |
| `pyproject.toml` | 添加 fastapi、uvicorn 依赖 |

### 删除文件
| 文件 | 原因 |
|------|------|
| `nanobot/web/chat_stream_bus.py` | SSE 专用，WebSocket 不需要 |
| SSE 端点方法（api.py） | 全部迁移到 WebSocket |

## 测试场景

1. **基本流程**：连接 WebSocket → 发送消息 → 收到响应事件 → 收到 done 事件
2. **流式响应**：发送消息 → 逐个收到 content 片段 → 流正确结束
3. **工具调用**：发送消息 → 收到 tool_start 事件 → 收到 tool_end 事件 → 收到最终响应
4. **断线重连**：断开 WebSocket → 重连 → 恢复 session → 继续对话
5. **错误处理**：发送无效消息 → 收到 error 事件
6. **心跳**：定期 ping/pong 保持连接活跃

## 迁移步骤

1. 添加 FastAPI、Uvicorn 依赖
2. 实现 WebSocketManager（`nanobot/web/websocket_manager.py`）
3. 实现 browser channel（`nanobot/channels/browser.py`）
4. 添加 browser channel 配置 schema
5. 在 ChannelManager 中注册 browser channel
6. 客户端 WebSocket 实现（`useWebSocket.ts`、`api.ts`）
7. 适配 ChatPage 使用 WebSocket
8. 移除 SSE 相关代码（chat_stream_bus.py、SSE 端点）
9. 测试验证
