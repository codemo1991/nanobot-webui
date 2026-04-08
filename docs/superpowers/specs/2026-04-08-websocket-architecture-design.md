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
Web UI ← WebSocket → FastAPI/Starlette → MessageBus → AgentLoop
         (单一连接: 双向 WebSocket)
         ↓
      browser channel
```

## 技术方案

### 1. Server 迁移到 FastAPI

**改动概述：**
- 将 `ThreadingHTTPServer` + 自定义 handler 迁移到 FastAPI
- 使用 Starlette 的 WebSocket 支持
- 全面异步化

**新增依赖：**
- `fastapi`
- `uvicorn`（ASGI server）
- `websockets`（如果 Starlette 不够用）

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
- 处理连接认证（复用现有 session 机制）

**核心逻辑：**
```python
class BrowserChannel(BaseChannel):
    name = "browser"

    def __init__(self, config, bus: MessageBus, ws_manager):
        self.ws_manager = ws_manager
        self.bus = bus

    async def handle_websocket(self, websocket: WebSocket, session_id: str):
        """处理 WebSocket 连接"""
        await websocket.accept()

        # 注册到 ws_manager
        self.ws_manager.register(session_id, websocket)

        try:
            # 接收消息 -> 转换为 InboundMessage -> 推送到 bus
            while True:
                data = await websocket.receive_json()
                msg = self._parse_message(data, session_id)
                await self.bus.publish_inbound(msg)
        except WebSocketDisconnect:
            pass
        finally:
            self.ws_manager.unregister(session_id)

    async def send(self, message: OutboundMessage):
        """发送消息到客户端"""
        key = f"browser:{message.chat_id}"
        await self.ws_manager.send(key, message)
```

### 4. WebSocket Manager

**文件：** `nanobot/web/websocket_manager.py`

**职责：**
- 管理所有 WebSocket 连接（session_id → WebSocket mapping）
- 广播消息到连接
- 处理连接心跳
- 清理断开的连接

**核心逻辑：**
```python
class WebSocketManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    def register(self, session_id: str, websocket: WebSocket):
        """注册新连接"""
        key = f"browser:{session_id}"
        if key not in self._connections:
            self._connections[key] = []
        self._connections[key].append(websocket)

    def unregister(self, session_id: str, websocket: WebSocket):
        """注销连接"""
        key = f"browser:{session_id}"
        if key in self._connections:
            try:
                self._connections[key].remove(websocket)
            except ValueError:
                pass
            if not self._connections[key]:
                del self._connections[key]

    async def broadcast(self, key: str, event: dict):
        """广播事件到所有订阅者"""
        if key not in self._connections:
            return
        disconnected = []
        for ws in self._connections[key]:
            try:
                await ws.send_json(event)
            except Exception:
                disconnected.append(ws)
        # 清理断开的连接
        for ws in disconnected:
            self._connections[key].remove(ws)
```

### 5. Agent Loop 集成

AgentLoop 的 `process_direct()` 方法已有 `progress_callback` 机制。只需要修改回调实现，让它通过 `browser` channel 推送事件：

```python
# browser channel 注册 progress 回调
async def on_progress(event: dict):
    session_id = event.get("session_id")
    await ws_manager.broadcast(f"browser:{session_id}", {
        "type": "event",
        "event": event
    })

# 传给 agent loop
metadata["progress_callback"] = on_progress
```

### 6. 移除的组件

**删除：**
- `nanobot/web/chat_stream_bus.py` — SSE 专用逻辑不再需要
- SSE 端点：
  - `_handle_chat_stream()`
  - `_handle_chat_stream_resume()`
  - `_handle_subagent_progress_stream()`
  - `/api/v1/traces/stream`

### 7. API 路由设计

```
WebSocket: /ws/{session_id}          # 主 WebSocket 连接
HTTP:      (保持现有非流式端点)         # 如配置获取等
```

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
| `nanobot/web/api.py` | 迁移到 FastAPI，添加 WebSocket 端点 |
| `nanobot/web/server.py` | 替换为 Uvicorn/FastAPI server |
| `nanobot/bus/events.py` | 确认 browser channel 事件格式 |
| `nanobot/channels/manager.py` | 注册 browser channel |
| `web-ui/src/api.ts` | WebSocket 客户端实现 |
| `web-ui/src/pages/ChatPage.tsx` | 适配 WebSocket 事件 |
| `web-ui/src/hooks/useWebSocket.ts` | 新增 WebSocket hook |
| `pyproject.toml` | 添加 fastapi、uvicorn 依赖 |

### 删除文件
| 文件 | 原因 |
|------|------|
| `nanobot/web/chat_stream_bus.py` | SSE 专用，WebSocket 不需要 |
| SSE 端点方法 | 全部迁移到 WebSocket |

## 测试场景

1. **基本流程**：连接 WebSocket → 发送消息 → 收到响应事件 → 收到 done 事件
2. **流式响应**：发送消息 → 逐个收到 content 片段 → 流正确结束
3. **工具调用**：发送消息 → 收到 tool_start 事件 → 收到 tool_end 事件 → 收到最终响应
4. **断线重连**：断开 WebSocket → 重连 → 恢复 session → 继续对话
5. **错误处理**：发送无效消息 → 收到 error 事件
6. **心跳**：定期 ping/pong 保持连接活跃

## 迁移步骤

1. 添加 FastAPI 依赖
2. 实现 WebSocketManager
3. 实现 browser channel
4. 创建 WebSocket 端点
5. 修改 AgentLoop progress 回调
6. 客户端 WebSocket 实现
7. 移除 SSE 相关代码
8. 测试验证
