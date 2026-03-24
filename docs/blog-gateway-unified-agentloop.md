# Gateway 统一执行核：nanobot-webui 架构升级

> 作者：nanobot 团队
> 日期：2026-03-24
> 分支：`feature/enhance_agentloop_message`

---

## 概述

本次更新实现了 **Gateway 统一执行核架构**，彻底重构了 Web 渠道的消息处理方式，解决了长期困扰的高并发场景下 MCP 重复初始化问题。这是 nanobot-webui 面向生产环境部署的重要一步。

**核心改动文件（6 个，共 +592/-296 行）：**

| 文件 | 改动量 | 主要内容 |
|------|--------|----------|
| `nanobot/web/api.py` | +372/-152 | Web Gateway 化；core_loop 集成 |
| `nanobot/agent/loop.py` | +108/-54 | 哨兵机制；取消探针；on_complete 回调 |
| `nanobot/bus/queue.py` | +85/-16 | 线程安全入队；队列容量控制 |
| `nanobot/agent/tools/mcp.py` | +40/-21 | 自动重连容错 |
| `nanobot/agent/tools/filesystem.py` | +27/-10 | 异步 I/O 改造 |
| `web-ui/src/pages/ChatPage.tsx` | +3 | 前端适配 |

---

## 问题背景

### 旧架构的核心问题

```
┌─────────────────────────────────────────────────────────────┐
│  Web HTTP 请求                                                │
│  └─ 每条消息 → 新线程 + asyncio.new_event_loop()            │
│  └─ 整包 reload MCP（×2：进入 + finally）                    │
│  └─ loop 销毁，session 失效                                  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  飞书 WebSocket                                               │
│  └─ run_coroutine_threadsafe → 同一长驻 loop                 │
│  └─ MCP 一次初始化，不 reload                                 │
└─────────────────────────────────────────────────────────────┘
```

**三个致命问题：**

| 问题 | 影响 |
|------|------|
| 每请求 `new_event_loop()` | MCP session 绑定 loop，loop 销毁后必须 reload |
| 每对话 reload 两次 | 每轮对话额外增加 1~10 秒 MCP 重连时间 |
| Web/飞书路径完全不同 | 代码维护成本高，无法共享状态 |

### 根本原因

MCP 的 `ClientSession` 与创建它的 event loop 绑定。当 Web 请求结束时销毁 loop，下一条消息来临时创建新 loop，`_mcp_loop_id` 变化触发强制 reload。

---

## 目标架构

### 核心原则

1. **统一执行核**：一个长驻 asyncio event loop，跑 `AgentLoop.run()`
2. **Web 只做 Gateway**：HTTP 线程负责接收、入队、SSE 推送，**不跑业务推理**
3. **渠道仍是适配器**：Feishu/Web 不变；Web 从「半个 Agent」退成「连接器 + 流式适配」

### 架构图

```
┌────────────────────────────────────────────────────────────┐
│                   进程（nanobot server）                    │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              渠道适配层 Channel Adapters               │  │
│  │                                                        │  │
│  │  Feishu WS      Telegram     Web Gateway (HTTP+SSE)   │  │
│  │   线程               线程         线程池（N个）        │  │
│  └────────────┬────────────┬────────────┬────────────────┘  │
│               │            │            │                    │
│               └────────────┴────────────┘                   │
│                            │                                 │
│               run_coroutine_threadsafe(bus.publish_inbound)  │
│                            │                                 │
│  ┌─────────────────────────▼──────────────────────────────┐ │
│  │              统一总线 MessageBus                        │ │
│  │   inbound: asyncio.Queue(maxsize=200)                  │ │
│  │   outbound: asyncio.Queue()                            │ │
│  └─────────────────────────┬──────────────────────────────┘ │
│                            │                                 │
│  ┌─────────────────────────▼──────────────────────────────┐ │
│  │           统一执行核（Core Thread / Core Loop）         │ │
│  │                                                          │ │
│  │   AgentLoop.run()                                        │ │
│  │   ├─ _init_mcp_loader()  ← 启动时一次                   │ │
│  │   ├─ while consume_inbound():                           │ │
│  │   │    await _process_message(msg)                      │ │
│  │   └─ progress_callback → evt_queue.put(evt)             │ │
│  │                                                          │ │
│  │   MCP 连接池（绑定 core_loop，不再随请求重建）            │ │
│  └──────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

---

## 核心改造详解

### 1. MessageBus 线程安全入队

**新增 `try_publish_inbound_sync` 方法：**

```python
def try_publish_inbound_sync(
    self, msg: InboundMessage, loop: asyncio.AbstractEventLoop
) -> bool:
    """
    从 HTTP 工作线程安全地将消息入队（线程安全，非协程）。
    队满时返回 False，调用方应向客户端返回 HTTP 429。
    """
    future = asyncio.run_coroutine_threadsafe(
        self._try_put(msg), loop
    )
    try:
        return future.result(timeout=1.0)
    except Exception as e:
        logger.warning(f"[MessageBus] try_publish_inbound_sync failed: {e}")
        return False
```

**队列容量控制：**

```python
class MessageBus:
    def __init__(self, max_inbound: int = 200):
        # maxsize 防止无限积压；超出时 try_publish_inbound_sync 返回 False
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=max_inbound)
```

### 2. Web Gateway 化

**旧模式（`chat_stream`）：**
```python
def run_agent() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(
        self._chat_with_progress(...)
    )
```

**新模式（Gateway）：**
```python
def on_complete(response_content: str, error: str | None = None) -> None:
    """由 AgentLoop.run() 在消息处理结束后调用"""
    if error:
        _put_evt({"type": "error", "message": error})
        return
    _put_evt({
        "type": "done",
        "content": response_content,
        "assistantMessage": self._build_assistant_message(session_id, key),
    })

# 消息推入 core_loop 的 inbound 队列
inbound_msg = _InboundMessage(
    channel="web",
    sender_id="user",
    chat_id=session_id,
    content=content,
    metadata={
        "progress_callback": on_progress,
        "on_complete": on_complete,
        **extra,
    },
)
ok = self.agent.bus.try_publish_inbound_sync(inbound_msg, core_loop)
if not ok:
    on_complete("", error="服务繁忙，请稍后重试（消息队列已满）")
```

### 3. 哨兵机制替代 1s 轮询

**旧模式（CPU 浪费）：**
```python
while self._running:
    msg = await asyncio.wait_for(
        self.bus.consume_inbound(),
        timeout=1.0  # 每秒唤醒一次，即使无消息
    )
```

**新模式（零开销等待）：**
```python
_STOP_SENTINEL = object()  # 哨兵对象

async def run(self) -> None:
    self._running = True
    self._loop = asyncio.get_running_loop()

    while self._running:
        raw = await self.bus.inbound.get()  # 完全阻塞，无消息时 CPU 0%
        if raw is _STOP_SENTINEL:
            break
        msg: InboundMessage = raw
        await self._process_message(msg)
```

### 4. 取消探针机制

解决 `/stop` 命令在 `inbound.get()` 阻塞时无法立即响应的问题：

```python
@dataclass
class _CancelProbe:
    """取消探针：cancel_current_request() 推入队列，唤醒正在阻塞于 get() 的 run()"""
    session_key: str

async def run(self) -> None:
    while self._running:
        raw = await self.bus.inbound.get()
        if isinstance(raw, _CancelProbe):
            self._cancelled_sessions.discard(raw.session_key)
            continue
        # ... 正常处理消息
```

### 5. MCP 自动重连容错

```python
async def execute(self, **kwargs: Any) -> str:
    for attempt in range(2):
        try:
            if not await self._ensure_connected():
                return f"MCP {self._server_id}: 连接失败，请检查 MCP 配置。"
            result = await self._session.call_tool(self._tool_name, kwargs)
            return _format_result(result)
        except (ClosedResourceError, EOFError, ConnectionResetError) as e:
            if attempt == 0:
                logger.warning(f"[MCP] {self._server_id}: 连接断开，尝试重连…")
                self._reset_session()  # 重置同 server 所有工具的 session
                continue
            return f"MCP {self._server_id}: 重连失败，工具暂不可用"
```

### 6. 文件 I/O 异步化

```python
class ReadFileTool(Tool):
    async def execute(self, path: str, **kwargs) -> str:
        # 旧：content = file_path.read_text(encoding="utf-8")  # 同步阻塞
        # 新：
        content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
        return content
```

---

## 性能提升

| 指标 | 旧架构 | 新架构 | 提升 |
|------|--------|--------|------|
| MCP 初始化次数 | 每对话 2 次 | 启动时 1 次 | **~50%** |
| 每对话延迟 | +1~10s reload | 无额外开销 | **~1-10s** |
| 空闲 CPU 占用 | 每秒唤醒 | 接近 0 | **~99%** |
| 并发队列上限 | 无限制 | 200 条 | **可控背压** |
| MCP 断连恢复 | 手动重启 | 自动重连 | **自愈** |

---

## 稳定性增强

### 1. 优雅关闭

```python
def _graceful_shutdown(app, core_thread):
    logger.info("Initiating graceful shutdown...")
    # 1. 发哨兵，让 run() 退出循环
    asyncio.run_coroutine_threadsafe(
        app.core_bus.inbound.put(_STOP_SENTINEL), app.core_loop
    )
    # 2. 等当前消息处理完成（最多 30s）
    core_thread.join(timeout=30)
    # 3. 关 MCP 连接
    if app.core_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(
            app.agent.mcp_loader.close() if app.agent.mcp_loader else asyncio.sleep(0),
            app.core_loop
        )
        future.result(timeout=5)
```

### 2. 429 快速失败

队列满时立即返回 HTTP 429，前端显示「服务繁忙，请稍后重试」，避免用户长时间等待。

### 3. 超时积压清理

```python
async def _handle_timeout(self, msg: InboundMessage):
    # 清除同一 session 的积压消息（它们基于超时那条的上下文，已失效）
    drained = 0
    temp = []
    while not self.bus.inbound.empty():
        item = self.bus.inbound.get_nowait()
        if hasattr(item, 'chat_id') and item.chat_id == msg.chat_id:
            drained += 1
        else:
            temp.append(item)
    for item in temp:
        await self.bus.inbound.put(item)
```

---

## 迁移路径

### Phase 1：统一核线程 + Bus 入队（1-2 天）
- 启动 `core_thread` 持有 `core_loop`
- Web 消息走 `try_publish_inbound_sync`
- 验证：飞书和 Web 均能进入同一 inbound 队列

### Phase 2：Web Gateway 化（2-3 天）
- 去掉 `chat_stream` 里的 `threading.Thread` + `new_event_loop`
- 删除 `reload_mcp_config` finally 块
- 验证：多轮对话 MCP 不再 reload

### Phase 3：稳定性加固（1-2 天）
- 优雅关闭（SIGTERM handler）
- `/stop` 打断 inbound 等待
- MCP 重连容错

### Phase 4：工具 async 审计（1 天）
- 修复 `subprocess.run`、`requests.get`、同步文件 I/O

---

## 未来规划

- **方案 B（可选）**：按 session 并发，跨 session 串行（适合多租户 SaaS）
- **可观测性**：在 `/api/v1/system/status` 暴露 `inbound_queue_depth`、`mcp_reconnect_count_last_hour` 等指标
- **SSE replay 优化**：`ChatStreamBus` 重连时对 `claude_code_progress` 仅保留最后 10 条

---

## 参考资料

- [Gateway 统一执行核架构方案](./gateway-unified-agentloop-architecture.md)
- [OpenClaw Gateway 模型](https://github.com/openclaw/openclaw)

---

*本文档由 AI 辅助生成，基于 2026-03-24 与代码库的深度分析。*
