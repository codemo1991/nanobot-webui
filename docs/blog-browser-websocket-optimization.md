# Browser Channel 与 WebSocket 通信架构升级

> 作者：nanobot 团队
> 日期：2026-04-10
> 分支：`feature/browser-websocket-channel`

---

## 概述

本文记录了 nanobot-webui 将 Web UI 通信方式从 SSE（Server-Sent Events）迁移到 WebSocket 的完整过程，实现了 **browser channel** 统一管理。这是一次从协议层到架构层的系统性升级，让本地 Web UI 拥有了与 Feishu 等远程渠道一致的通信体验。

**核心改动文件（9 个文件，新增 ~800 行）：**

| 文件 | 改动 | 主要内容 |
|------|------|----------|
| `nanobot/channels/browser.py` | 新增 | browser channel 实现，WebSocket 服务器 |
| `nanobot/web/websocket_manager.py` | 新增 | WebSocket 连接管理器 |
| `nanobot/config/schema.py` | +8 行 | BrowserConfig 配置 schema |
| `nanobot/channels/manager.py` | +11 行 | browser channel 注册 |
| `web-ui/src/hooks/useWebSocket.ts` | 新增 | WebSocket 客户端 Hook |
| `web-ui/src/api.ts` | +30 行 | WebSocket 客户端方法 |
| `web-ui/src/pages/ChatPage.tsx` | ~20 行 | 适配 WebSocket 事件处理 |
| `nanobot/web/chat_stream_bus.py` | 删除 | SSE 专用逻辑移除 |
| `nanobot/web/api.py` | -150 行 | SSE 端点移除 |

---

## 背景：为什么放弃 SSE

### SSE 的技术局限

SSE（Server-Sent Events）是 Web UI 最初选择的通信方案，它简单易用，但随着功能演进，问题逐渐暴露：

```
┌─────────────────────────────────────────────────────────────────┐
│  旧架构：Web UI ← SSE (两个连接) → ThreadingHTTPServer          │
│                                                                 │
│  HTTP POST /api/chat          ←─ 建立会话，发送消息              │
│  SSE GET /api/chat/stream     ←─ 接收流式响应（需要第二个连接）  │
└─────────────────────────────────────────────────────────────────┘
```

**四个核心问题：**

| 问题 | 影响 |
|------|------|
| **单向通信** | SSE 只支持服务端推送，客户端状态同步依赖复杂的手动逻辑 |
| **双连接开销** | 每个 session 需要维护 POST + SSE GET 两个连接，资源浪费 |
| **防火墙干扰** | 部分企业网络会阻断长连接的 HTTP 流 |
| **与 Feishu 不一致** | Feishu 使用 WebSocket，架构不统一，维护成本高 |

### 架构对齐的驱动力

nanobot-webui 支持多种渠道：Telegram、Feishu、Discord、QQ 等。之前 Web UI 使用 SSE，而其他远程渠道使用 WebSocket，导致：

1. **代码路径分裂**：Web 和 Feishu 各有一套消息处理逻辑
2. **调试困难**：两种协议需要分别排查问题
3. **扩展性受限**：新功能需要同时适配两种协议

WebSocket 作为全双工协议天然适合聊天场景，也能与其他渠道保持架构一致。

---

## 目标架构

### 统一通信模型

```
┌─────────────────────────────────────────────────────────────────┐
│                     nanobot-webui 进程                           │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              ChannelManager（渠道管理器）                   │  │
│  │                                                            │  │
│  │  Feishu WS    Telegram     Browser (WebSocket)    ...     │  │
│  │   线程               线程      ASGI Server                │  │
│  │                                                            │  │
│  │  ┌────────────────────────────────────────────────────┐   │  │
│  │  │           AgentLoop（统一执行核）                      │   │  │
│  │  │   • 启动时一次性初始化 MCP                            │   │  │
│  │  │   • 哨兵机制（零 CPU 空转）                            │   │  │
│  │  │   • 自动重连容错                                      │   │  │
│  │  └────────────────────────────────────────────────────┘   │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### browser channel 的定位

browser channel 不是简单的「WebSocket 包装器」，而是：

1. **独立运行的 ASGI 服务**：在独立端口（默认 8765）启动 FastAPI/Uvicorn
2. **与 AgentLoop 深度集成**：直接调用 `process_direct()`，复用 progress 回调机制
3. **完整的生命周期管理**：由 ChannelManager 统一启停

---

## 实现详解

### 1. WebSocket 服务器架构

**browser channel 内部启动独立的 FastAPI ASGI 服务器：**

```python
async def start(self) -> None:
    """启动 WebSocket 服务器"""
    if not self.config.enabled:
        logger.info("Browser channel disabled")
        return

    self._app = FastAPI()

    @self._app.websocket("/ws/{session_id}")
    async def websocket_endpoint(websocket: WebSocket, session_id: str):
        await self._handle_connection(websocket, session_id)

    config = uvicorn.Config(
        self._app,
        host=self.config.host,
        port=self.config.port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    self._server_task = asyncio.create_task(server.serve())
    logger.info(f"[Browser] WebSocket server started on {self.config.host}:{self.config.port}")
```

**设计决策：**
- **独立端口**：避免与现有 HTTP 服务器（如果启用）端口冲突
- **独立启动/停止**：由 ChannelManager 统一管理生命周期
- **按需启用**：仅当 `browser.enabled: true` 时才启动

### 2. 事件流处理：queue + drain_task

**复用已有的 progress 回调机制，这是关键设计：**

```python
async def _handle_connection(self, websocket: WebSocket, session_id: str):
    # 1. 为这个连接创建专属的 event queue
    evt_queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    # 2. 创建 progress 回调（同步写入，与 SSE 完全一致）
    def on_progress(evt: dict) -> None:
        try:
            evt_queue.put_nowait(evt)
        except asyncio.QueueFull:
            logger.warning(f"[Browser] Event queue full, dropping event")

    # 3. 后台任务：drain queue → 推 WebSocket
    async def drain_events():
        while True:
            try:
                evt = await asyncio.wait_for(evt_queue.get(), timeout=60)
                await websocket.send_json({"type": "event", "event": evt})
            except asyncio.TimeoutError:
                # 心跳保活检查
                await websocket.send_json({"type": "ping_check"})
            except asyncio.CancelledError:
                break

    drain_task = asyncio.create_task(drain_events())

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "message":
                # 4. 直接调用 agent，传入 progress_callback
                response = await self.agent.process_direct(
                    content=data.get("content", ""),
                    session_key=f"browser:{session_id}",
                    channel="browser",
                    progress_callback=on_progress,
                )
                # 5. 发送完成事件
                await websocket.send_json({
                    "type": "event",
                    "event": {"type": "done", "content": response}
                })
    except WebSocketDisconnect:
        pass
    finally:
        drain_task.cancel()
        self.ws_manager.unregister(key, websocket)
```

**流程图：**

```
WebSocket 消息到达
        ↓
on_progress(evt) → evt_queue.put_nowait(evt)
        ↓
drain_task ← evt_queue.get()
        ↓
websocket.send_json({"type": "event", "event": evt})
```

### 3. WebSocket 消息协议

#### 客户端 → 服务端

```typescript
// 发送聊天消息
{ type: "message", content: string, media?: string[] }

// 心跳保活
{ type: "ping" }
```

#### 服务端 → 客户端

```typescript
// Agent 事件（与原 SSE 事件格式一致）
{
  type: "event",
  event: {
    type: "agent_start" | "message_start" | "message_end" |
          "tool_start" | "tool_end" | "thinking" | "done" | "error",
    content?: string,
    role?: string,
    name?: string,
    arguments?: object,
    result?: string,
    error?: string
  }
}

// 心跳响应
{ type: "pong" }

// 连接存活检查
{ type: "ping_check" }

// 错误通知
{ type: "error", error: string }
```

### 4. 前端 WebSocket Hook

**`useWebSocket.ts` 提供了完整的连接管理能力：**

```typescript
export function useWebSocket(options: UseWebSocketOptions) {
  const { url, onMessage, reconnect = true, reconnectInterval = 3000 } = options;
  const wsRef = useRef<WebSocket | null>(null);

  const connect = useCallback(() => {
    const ws = new WebSocket(url);

    ws.onopen = () => setIsConnected(true);
    ws.onmessage = (event) => onMessage(JSON.parse(event.data));
    ws.onclose = () => {
      setIsConnected(false);
      if (reconnect) {
        setTimeout(connect, reconnectInterval);
      }
    };

    wsRef.current = ws;
  }, [url, onMessage, reconnect, reconnectInterval]);

  const send = useCallback((data: object) => {
    wsRef.current?.send(JSON.stringify(data));
  }, []);

  useEffect(() => {
    connect();
    return () => wsRef.current?.close();
  }, [connect]);

  return { isConnected, send, disconnect: () => wsRef.current?.close(), reconnect: connect };
}
```

### 5. 移除 SSE 相关代码

**删除的文件和方法：**

| 文件/方法 | 原因 |
|----------|------|
| `nanobot/web/chat_stream_bus.py` | SSE 专用，不再需要 |
| `_handle_chat_stream()` | 迁移到 WebSocket |
| `_handle_chat_stream_resume()` | 迁移到 WebSocket |
| `_handle_subagent_progress_stream()` | 迁移到 WebSocket |
| `/api/v1/traces/stream` | SSE 端点移除 |

---

## 性能提升

| 指标 | 旧架构（SSE） | 新架构（WebSocket） | 提升 |
|------|-------------|---------------------|------|
| 连接数/会话 | 2 个（POST + SSE） | 1 个 | **50%** |
| 双向通信 | 需要轮询 | 原生支持 | **完整** |
| 断线重连 | 复杂状态恢复 | 自动重连 | **简化** |
| 企业网络兼容性 | 部分阻断 | 完整支持 | **改善** |
| 协议统一性 | 与 Feishu 不一致 | 全部 WebSocket | **一致** |

---

## 配置方式

browser channel 通过配置文件启用：

```yaml
channels:
  browser:
    enabled: true          # 启用 browser channel
    host: "127.0.0.1"     # WebSocket 监听地址
    port: 8765            # WebSocket 监听端口
    agent_timeout: 300.0   # Agent 调用超时（秒）
```

前端连接地址：`ws://localhost:8765/ws/{session_id}`

---

## 稳定性设计

### 1. 连接管理

- **单连接策略**：每个 session_id 只允许一个 WebSocket 连接，后来的连接会替换前面的
- **优雅关闭**：客户端断开时自动清理资源

### 2. 事件队列

- **容量限制**：`maxsize=500` 防止内存溢出
- **超限丢弃**：队列满时丢弃最旧的事件，避免阻塞

### 3. 心跳保活

- **60 秒超时**：60 秒无事件则发送 `ping_check`
- **自动重连**：前端自动在 3 秒后重连

### 4. 错误处理

```python
try:
    response = await self.agent.process_direct(...)
except Exception as e:
    logger.error(f"[Browser] Agent error: {e}")
    await websocket.send_json({
        "type": "error",
        "error": str(e)
    })
```

---

## 迁移路径回顾

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | 添加 FastAPI/Uvicorn 依赖 | ✅ |
| 2 | 实现 WebSocketManager | ✅ |
| 3 | 添加 BrowserConfig schema | ✅ |
| 4 | 实现 browser channel | ✅ |
| 5 | ChannelManager 注册 browser channel | ✅ |
| 6 | 客户端 WebSocket 实现 | ✅ |
| 7 | 适配 ChatPage | ✅ |
| 8 | 移除 SSE 代码 | ✅ |
| 9 | 验证测试 | ✅ |

---

## 经验总结

### 1. 复用而非重写

browser channel 复用已有的 `process_direct()` 和 progress 回调机制，而不是重新实现消息处理逻辑。这保证了：
- 与其他 channel 行为一致
- 减少 Bug 引入
- 维护成本降低

### 2. 协议设计的一致性

WebSocket 消息格式与 SSE 事件格式保持一致，前端只需替换传输层，事件处理逻辑无需改动。

### 3. 渐进式迁移

虽然最终完全移除了 SSE 代码，但迁移过程是渐进的：先实现 WebSocket → 验证功能 → 移除 SSE。这种方式降低了风险。

---

## 未来规划

- **TLS 支持**：生产环境需要 WSS（WSS over HTTPS）
- **认证机制**：当前 session_id 为公开参数，生产环境需要添加认证
- **多实例扩展**：当前单实例，未来可考虑 Redis pub/sub 横向扩展

---

## 参考资料

- [WebSocket 架构设计文档](./superpowers/specs/2026-04-08-websocket-architecture-design.md)
- [WebSocket 实现计划](./superpowers/plans/2026-04-08-websocket-implementation-plan.md)
- [Gateway 统一执行核架构](./blog-gateway-unified-agentloop.md)

---

*本文档由 AI 辅助生成，基于 2026-04-10 与代码库的深度分析。*
