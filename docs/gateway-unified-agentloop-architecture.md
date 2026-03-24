# Gateway 统一执行核架构方案

> 文档版本：v1.0  
> 创建日期：2026-03-24  
> 适用项目：nanobot-webui  
> 状态：**方案设计阶段**

---

## 目录

1. [背景与问题分析](#1-背景与问题分析)
2. [参考架构：OpenClaw Gateway 模型](#2-参考架构openclaw-gateway-模型)
3. [目标架构设计](#3-目标架构设计)
4. [核心组件说明](#4-核心组件说明)
5. [关键流程设计](#5-关键流程设计)
6. [并发策略选型：方案 A vs 方案 B](#6-并发策略选型方案-a-vs-方案-b)
7. [性能优化清单](#7-性能优化清单)
8. [稳定性优化清单](#8-稳定性优化清单)
9. [迁移实施步骤](#9-迁移实施步骤)
10. [与现有架构对照表](#10-与现有架构对照表)

---

## 1. 背景与问题分析

### 1.1 当前 Web 渠道的核心问题

**问题一：每条对话都创建新的 asyncio 事件循环**

`nanobot/web/api.py` 的 `chat_stream()` 里，每条用户消息都在一个新的 `threading.Thread` 里用 `asyncio.new_event_loop()` 处理：

```python
# api.py:1361 - 现状（有问题）
def run_agent() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(
        self._chat_with_progress(...)
    )
```

**问题二：每条对话都整包 reload MCP**

MCP 的 `ClientSession` / transport 与创建它的 event loop 绑定。loop id 一旦变化，`_process_message` 开头就触发 `reload_mcp_config()`：

```python
# loop.py:1912 - loop 变了就重载
if self.mcp_loader and self._mcp_loaded and self._mcp_loop_id is not None \
        and current_loop_id != self._mcp_loop_id:
    await self.reload_mcp_config()
```

**问题三：对话结束后再 reload 一次**

```python
# api.py:1437 - finally 里无条件 reload
finally:
    if getattr(self.agent, "mcp_loader", None):
        await self.agent.reload_mcp_config()
```

**综合影响：**

| 问题 | 影响 |
|------|------|
| `new_event_loop` per request | MCP session 失效 → reload 必然触发 |
| `reload_mcp_config` 开销 | 每轮对话额外几秒连接 MCP server |
| 飞书渠道不受影响 | 飞书走 `AgentLoop.run()` 同一 loop，MCP 不 reload |

### 1.2 MCP 加载机制说明

`_init_mcp_loader` 逻辑（`loop.py:1164`）：

- 配置里有 `tools` 字段 → 直接注册 `McpLazyToolAdapter`（懒加载代理，不建连接）
- 配置里**没有** `tools` 字段 → **立刻做一次 discovery**（连上 MCP server → `list_tools` → 断开）

因此：**配置里未声明 tools 的 MCP server，每次 reload 都要真正建连**，这是慢的根源。

### 1.3 当前架构局限

```
Web HTTP 线程
  └─ new_event_loop()
  └─ process_direct()         ← 每请求独立 loop
  └─ reload_mcp (×2)          ← 进入时 + finally

Feishu WS 线程
  └─ run_coroutine_threadsafe → AgentLoop.run()  ← 长驻 loop，MCP 一次初始化
```

Web 和 IM 渠道用了完全不同的处理路径，这是根本矛盾。

---

## 2. 参考架构：OpenClaw Gateway 模型

### 2.1 OpenClaw 高层架构

OpenClaw 使用**单 Gateway 实例**统管所有 IM 渠道：

```
WhatsApp / Telegram / Slack / Discord / WebChat / ...
                    ↓
           OpenClaw Gateway 层
        （事件归一化 + 路由分发）
                    ↓
         统一 Agent 核心（一个大脑）
         + Skill 执行（TOOLS.md / SKILLS.md）
                    ↓
           回包路由到原始渠道
```

**核心理念：** One agent, many platforms — 共享记忆、统一工具、集中路由。

### 2.2 工具/MCP 管理方式

OpenClaw 用 **TOOLS.md + SKILLS.md** 解决「MCP 数量爆炸占满 context」问题：

| OpenClaw | nanobot 对应物 |
|----------|---------------|
| `TOOLS.md`（轻量文字描述，每条 ~40 token） | `selected_mcp_servers` + session 策略 |
| `SKILLS.md`（MCP server 封装） | `McpLazyToolAdapter` 注册表 |
| ClawHub 安装 → 自动写 `SKILLS.md` | `create_mcp` → `_init_mcp_loader` |
| 每个 agent 只声明用到的工具 | `tool_mode=specified` + `selected_mcp_servers` |

**关键差异：** OpenClaw 不把完整 MCP JSON schema 注入 prompt（避免 4~32× token 膨胀），用人类可读的轻量描述替代；nanobot 通过 `_select_tools_for_message` 按 session 策略筛选工具定义，方向一致。

### 2.3 为什么「一渠道一 AgentLoop」不等于 OpenClaw

OpenClaw 是**多渠道 → 统一一个 Agent 核心**，而不是「每个渠道一个独立 AgentLoop」。
按渠道做的差异化是**配置与路由层**的策略，不是运行时的隔离边界。

---

## 3. 目标架构设计

### 3.1 核心原则

1. **统一执行核**：一个长驻 asyncio event loop，跑 `AgentLoop.run()`
2. **Web 只做 Gateway**：HTTP 线程负责接收、校验、入队、推 SSE，**不跑业务推理**
3. **渠道仍是适配器**：Feishu/Telegram 不变；Web 从「半个 Agent」退成「连接器 + 流式适配」
4. **分渠道策略用配置**：`session.metadata` + `channel` 控制工具子集，不拆多 Loop

### 3.2 目标形态

```
┌────────────────────────────────────────────────────────────┐
│                   进程（nanobot server）                    │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              渠道适配层 Channel Adapters               │  │
│  │                                                        │  │
│  │  Feishu WS      Telegram     Web Gateway (HTTP+SSE)   │  │
│  │   线程               线程         线程池（N个）         │  │
│  └────────────┬────────────┬────────────┬────────────────┘  │
│               │            │            │                    │
│               └────────────┴────────────┘                   │
│                            │                                 │
│               run_coroutine_threadsafe(bus.publish_inbound)  │
│                            │                                 │
│  ┌─────────────────────────▼──────────────────────────────┐ │
│  │              统一总线 MessageBus                         │ │
│  │   inbound: asyncio.Queue(maxsize=200)                   │ │
│  │   outbound: asyncio.Queue()                             │ │
│  └─────────────────────────┬──────────────────────────────┘ │
│                            │                                 │
│  ┌─────────────────────────▼──────────────────────────────┐ │
│  │           统一执行核（Core Thread / Core Loop）           │ │
│  │                                                          │ │
│  │   AgentLoop.run()                                        │ │
│  │   ├─ _init_mcp_loader()  ← 启动时一次                   │ │
│  │   ├─ while consume_inbound():                            │ │
│  │   │    await _process_message(msg)                       │ │
│  │   └─ progress_callback → evt_queue.put(evt)             │ │
│  │                                                          │ │
│  │   MCP 连接池（绑定 core_loop，不再随请求重建）             │ │
│  └──────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

### 3.3 线程/Loop 布局

| 组件 | 线程 | Loop | 生命周期 |
|------|------|------|---------|
| Core（AgentLoop） | `core_thread` | `core_loop`（单一长驻） | 进程生命周期 |
| Web HTTP Server | `ThreadingHTTPServer`（多线程） | 无 asyncio loop | 请求粒度 |
| Feishu WS | `feishu_ws_thread` | 复用 `core_loop`（via threadsafe） | 进程生命周期 |
| Telegram/其它 IM | 各自线程 | 同 Feishu | 进程生命周期 |
| Cron Service | `cron_thread` + `cron_loop` | 独立（现状保留） | 进程生命周期 |

---

## 4. 核心组件说明

### 4.1 MessageBus 改造

**改动点：**

```python
# nanobot/bus/queue.py
class MessageBus:
    def __init__(self, max_inbound: int = 200):
        # 加 maxsize，防止无限积压
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=max_inbound)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        ...

    def try_publish_inbound_sync(self, msg: InboundMessage, loop: asyncio.AbstractEventLoop) -> bool:
        """线程安全的入队（从 HTTP 线程调用）。队满返回 False（用于 429 拒绝）。"""
        future = asyncio.run_coroutine_threadsafe(self._try_put(msg), loop)
        try:
            return future.result(timeout=1.0)
        except Exception:
            return False

    async def _try_put(self, msg: InboundMessage) -> bool:
        try:
            self.inbound.put_nowait(msg)
            return True
        except asyncio.QueueFull:
            return False
```

### 4.2 Web Gateway（HTTP 请求处理）

Web 请求处理的职责**只剩**：

1. 鉴权、body 解析
2. 构造 `InboundMessage`（含 `progress_callback` 闭包）
3. `bus.try_publish_inbound_sync(msg, core_loop)` — 入队
4. 以 `evt_queue.get()` 轮询 SSE 推流

```python
# 流式请求路径（改造后）
def _handle_chat_stream(self, app, session_id, content, images):
    evt_queue: queue.SimpleQueue = queue.SimpleQueue()

    def progress_callback(evt: dict) -> None:
        evt_queue.put(evt)          # 线程安全，非 asyncio

    msg = InboundMessage(
        channel="web",
        sender_id="user",
        chat_id=session_id,
        content=content,
        metadata={
            "progress_callback": progress_callback,
            "tool_mode": ...,
            "selected_mcp_servers": ...,
            "user_message_saved": True,
        }
    )

    ok = app.core_bus.try_publish_inbound_sync(msg, app.core_loop)
    if not ok:
        # 队满：返回 HTTP 429
        self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
        ...
        return

    # SSE 推流（与现有逻辑一致）
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
    ...
    self.end_headers()

    while True:
        try:
            evt = evt_queue.get(timeout=0.5)
        except queue.Empty:
            # 心跳
            ...
            continue
        self.wfile.write(f"data: {json.dumps(evt)}\n\n".encode())
        self.wfile.flush()
        if evt.get("type") in ("done", "error"):
            break
```

**关键：不再有 `new_event_loop()`，不再有 `process_direct()` 在 HTTP 线程里调用。**

### 4.3 AgentLoop.run() 改造

**主要改动：**

```python
async def run(self) -> None:
    self._running = True
    self._loop = asyncio.get_running_loop()   # ← 记录 core_loop 引用

    # MCP 一次性初始化
    try:
        await asyncio.wait_for(self._init_mcp_loader(), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning("MCP init timed out, lazy load on first call")

    while self._running:
        # 用哨兵替换 1s 轮询（省去空转唤醒）
        raw = await self.bus.inbound.get()
        if raw is _STOP_SENTINEL:
            break
        msg: InboundMessage = raw

        try:
            await asyncio.wait_for(
                self._process_message(msg),
                timeout=self.message_timeout,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            await self._handle_timeout(msg)
        except Exception as e:
            await self._handle_error(msg, e)
```

### 4.4 run_server() 改造

```python
def run_server(host="127.0.0.1", port=6788, static_dir=None):
    import threading

    app = NanobotWebAPI()

    # ① 启动统一执行核线程（持有 core_loop）
    core_ready = threading.Event()

    def _core_thread_target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app.core_loop = loop                      # 暴露给 HTTP 线程用
        core_ready.set()
        loop.run_until_complete(_core_main(app))  # AgentLoop.run() + dispatch_outbound

    core_thread = threading.Thread(target=_core_thread_target, daemon=False, name="core")
    core_thread.start()
    core_ready.wait(timeout=10)                   # 等 loop 就绪

    # ② 可选：Cron 继续独立线程（现状保留）
    # ...

    # ③ HTTP Server（多线程，工作线程入队到 core_loop）
    server = NanobotHTTPServer((host, port), app, static_dir=static_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _graceful_shutdown(app, core_thread)
```

### 4.5 MCP reload 策略调整

| 场景 | 现状 | 改造后 |
|------|------|--------|
| 每条 Web 对话 `_chat_with_progress` finally | `reload_mcp_config()` | **去掉** |
| loop 变化检测 | `current_loop_id != _mcp_loop_id` → reload | **不再触发**（core_loop 不变） |
| MCP 配置增删（`create_mcp` / `delete_mcp`）| 已有 `_reload_mcp()` 触发 | **保留**（这是合法的热更新） |
| MCP server 断连重连 | 无 | **新增**：`execute()` 里捕 `ClosedResourceError` 重试一次 |

---

## 5. 关键流程设计

### 5.1 Web 流式对话完整流程

```
用户浏览器
  │  POST /api/v1/chat/sessions/{id}/messages?stream=1
  ▼
HTTP 工作线程（Thread-N）
  ├─ 1. 鉴权 + body 解析
  ├─ 2. 立即保存 user message 到 DB（现有逻辑）
  ├─ 3. 构造 InboundMessage（含 progress_callback 闭包）
  ├─ 4. try_publish_inbound_sync(msg, core_loop)
  │      └─ 队满 → HTTP 429，结束
  ├─ 5. 发送 SSE headers
  └─ 6. while True: evt_queue.get(timeout=0.5) → SSE 推流

Core Thread（asyncio event loop）
  ├─ bus.inbound.get() → msg
  ├─ _process_message(msg)
  │    ├─ session 恢复 tool_mode/selected_mcp_servers
  │    ├─ 选工具（按 specified/auto/disable）
  │    ├─ LLM 调用（await）
  │    ├─ 工具调用（await）
  │    │    └─ 每步结束 → progress_callback(evt) → evt_queue.put(evt)
  │    └─ 最终 → progress_callback({"type":"done", ...})
  └─ （不再 reload_mcp）

SSE 流结束：evt_queue 收到 type=done → HTTP 线程退出 while 循环
```

### 5.2 飞书消息流程（保持现状，对齐接口）

```
飞书 WS 线程
  └─ _on_message_sync(data) 
  └─ asyncio.run_coroutine_threadsafe(_on_message(data), core_loop)
                                                       ↑
                                           与 Web 入队同一个 core_loop
```

飞书的 `progress_callback`（更新进度卡片）与 Web 的 `progress_callback`（推 SSE）**完全对称**，形式一致。

### 5.3 /stop 打断流程

```
用户 POST /api/v1/chat/stop
  │
HTTP 工作线程
  └─ app.agent.cancel_current_request(channel="web", session_id=...)
       └─ _cancelled_sessions.add(origin_key)
       
Core Thread（_process_message 里 _check_cancelled 检测）
  └─ 下一个 await 点（工具调用前/LLM 前）检测到 → CancelledError
```

**需要额外处理的边缘情况：** 若此时 Core 正在 `bus.inbound.get()` 等待（无消息可处），`_check_cancelled` 不会触发。改造：

```python
# cancel_current_request 末尾追加：
asyncio.run_coroutine_threadsafe(
    bus.inbound.put(_CANCEL_PROBE(target_session=origin_key)),
    core_loop
)
```

Core 取出 `_CANCEL_PROBE` 后检测标记，直接 `discard` + 不做推理，0ms 响应取消。

### 5.4 MCP 懒连接 + 重连流程

```
LLM 决定调用 mcp_xxx_tool
  │
McpLazyToolAdapter.execute()
  ├─ _ensure_connected()
  │    ├─ 已有 session → 跳过
  │    └─ 无 session → connect_lazy(server_id, timeout=30s)
  │         ├─ 成功 → 设置 self._session + 同 server 其他工具共享此 session
  │         └─ 失败 → 返回 "MCP connection failed"
  │
  ├─ session.call_tool(...)
  │    ├─ 成功 → 返回结果
  │    └─ ClosedResourceError / EOFError（server 重启）
  │         ├─ self._session = None（所有同 server 工具也重置）
  │         └─ 重试一次 _ensure_connected() → call_tool()
  │              ├─ 成功 → 返回结果
  │              └─ 再次失败 → "MCP tool unavailable after reconnect"
  └─ 返回给 LLM
```

---

## 6. 并发策略选型：方案 A vs 方案 B

### 6.1 两方案定义

**方案 A（全局串行 - 推荐）**  
整条 `inbound` 消费链路上，任意时刻最多只有一个 `_process_message` 在执行。

**方案 B（按 session 串行、跨 session 并行）**  
同一 `session_key` 内串行，不同 `session_key` 可并发（`create_task` + session 级锁）。

### 6.2 深度对比

| 维度 | 方案 A（推荐） | 方案 B |
|------|--------------|--------|
| **语义保证** | 全局全序 | 会话内全序 |
| **多用户延迟** | 长任务会排队 | 不同 session 互不阻塞 |
| **共享状态风险** | 仅需防顺序污染 | 必须防并发交错（`set_context` 等全局变量） |
| **MCP 并发安全** | 无需额外保护 | 同 server 并发 `connect_lazy` 需单飞锁 |
| **`/stop` 语义** | 简单（全局唯一当前任务） | 需 `session_id → Task` 注册表 |
| **实现复杂度** | 低 | 中高 |
| **与 OpenClaw 对齐** | 更贴「一个大脑」 | 更像「多租户调度」 |
| **适用场景** | 小中规模、个人/团队 | 多租户 SaaS、高并发 |

### 6.3 方案 A 的性能上限与缓解

串行不代表无并发——`_process_message` 内部的多轮 LLM/工具调用都是 `await`，event loop 在等待期间可以处理**心跳、outbound 分发、cancel probe** 等轻量任务。

真正的阻塞来源（需排查并修复）：
- `subprocess.run()` 同步调用 → 改为 `asyncio.create_subprocess_exec`
- 同步文件读写（大文件）→ `await asyncio.to_thread(open(...).read)`
- 同步 HTTP（如 requests）→ 改用 `httpx.AsyncClient` 或 `aiohttp`

消除同步阻塞后，串行模式下单个 LLM 等待期间其它 SSE 心跳/flush 仍然正常运转，用户体验不受影响。

### 6.4 选型结论

**选 方案 A**，理由：

1. 贴合「Gateway → 单一 Agent 核心」的 OpenClaw 叙事
2. 改动量最小（不需要改 `AgentLoop` 内部并发逻辑）
3. 排障成本低（日志时间线 = 执行顺序）
4. 现阶段用户规模不需要跨 session 并行
5. 若未来需要 B，可在方案 A 稳定后再升级，不影响接口

---

## 7. 性能优化清单

### P0：必做（上线前）

#### 7.1 去掉每轮 Web 对话的 `reload_mcp_config`

```python
# api.py _chat_with_progress - 改造后
async def _chat_with_progress(self, ...):
    key = self.to_session_key(session_id)
    self.sessions.get_or_create(key)
    response = await self.agent.process_direct(...)
    messages = self.sessions.get_messages(key=key, limit=2)
    ...
    # finally 里的 reload_mcp_config 完全删除
```

**收益：** 每轮对话节省 1~10 秒 MCP 重连时间。

#### 7.2 确认工具调用全程 async

排查以下工具类，确保无同步阻塞调用：

- `ExecTool`：`subprocess.run` → `asyncio.create_subprocess_exec` + `communicate()`
- `ReadFileTool`（大文件）：`open().read()` → `await asyncio.to_thread(Path.read_bytes)`
- `WebFetchTool`：`requests.get` → `httpx.AsyncClient.get`

**收益：** 防止 core_loop 在工具调用期间完全冻结（心跳/SSE flush 无法发送）。

### P1：强烈建议

#### 7.3 inbound 队列加 maxsize + 429 拒绝

```python
# bus/queue.py
class MessageBus:
    def __init__(self, max_inbound: int = 200):
        self.inbound: asyncio.Queue = asyncio.Queue(maxsize=max_inbound)
```

超出时 HTTP 返回 429，前端显示「服务繁忙，请稍后重试」。

#### 7.4 用哨兵替换 1s 轮询

```python
# loop.py
_STOP_SENTINEL = object()

async def run(self):
    while self._running:
        raw = await self.bus.inbound.get()    # 无 timeout，完全阻塞等待
        if raw is _STOP_SENTINEL:
            break
        ...

def stop(self):
    self._running = False
    asyncio.run_coroutine_threadsafe(
        self.bus.inbound.put(_STOP_SENTINEL), self._loop
    )
```

**收益：** 无消息时 CPU 占用接近 0（现状每秒一次无效唤醒）。

### P2：按需

#### 7.5 progress_callback 解耦

飞书的卡片 patch 若有网络延迟，会在 `progress_callback` 里阻塞 core_loop。改为 `put_nowait` + 独立消费 task：

```python
# feishu.py
progress_q = asyncio.Queue(maxsize=50)

def on_progress(evt):
    try:
        progress_q.put_nowait(evt)
    except asyncio.QueueFull:
        pass   # 节流：丢弃，卡片下次刷新时补上

# 独立 task（与 _process_message 同 loop，异步不阻塞）
async def _card_updater():
    while True:
        evt = await progress_q.get()
        await _patch_card(evt)
```

#### 7.6 SSE replay 按事件类型裁剪

`ChatStreamBus` 重连时全量 replay 最近 200 条。对 `claude_code_progress`（每行输出）仅保留最后 10 条：

```python
# 在 _process_message 结束时（done 事件后）调用
bus.trim_buffer(origin_key, keep_last=10)
```

---

## 8. 稳定性优化清单

### P0：必做（上线前）

#### 8.1 优雅关闭

```python
import signal

def _graceful_shutdown(app, core_thread):
    logger.info("Initiating graceful shutdown...")
    # 1. 发哨兵，让 run() 退出 while 循环
    asyncio.run_coroutine_threadsafe(
        app.core_bus.inbound.put(_STOP_SENTINEL), app.core_loop
    )
    # 2. 等当前 _process_message 完成（最多 30s）
    core_thread.join(timeout=30)
    # 3. 关 MCP 连接
    if app.core_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(
            app.agent.mcp_loader.close() if app.agent.mcp_loader else asyncio.sleep(0),
            app.core_loop
        )
        future.result(timeout=5)
    logger.info("Shutdown complete")

signal.signal(signal.SIGTERM, lambda *_: _graceful_shutdown(app, core_thread))
```

#### 8.2 /stop 打断 inbound 等待

（详见第 5.3 节）增加 `_CANCEL_PROBE` 哨兵消息，使 `/stop` 在 0ms 内响应。

### P1：强烈建议

#### 8.3 MCP 重连容错

```python
# nanobot/agent/tools/mcp.py McpLazyToolAdapter.execute
async def execute(self, **kwargs):
    for attempt in range(2):
        try:
            if not await self._ensure_connected():
                return f"MCP {self._server_id}: 连接失败，请检查 MCP 配置"
            result = await self._session.call_tool(self._tool_name, kwargs)
            return _format_result(result)
        except (ClosedResourceError, EOFError, ConnectionResetError):
            if attempt == 0:
                logger.warning(f"[MCP] {self._server_id} 连接已断开，尝试重连...")
                self._session = None
                for t in self._lazy_tools.values():
                    t._session = None
            else:
                return f"MCP {self._server_id}: 重连失败，工具暂不可用"
    return f"MCP {self._server_id}: 工具调用异常"
```

#### 8.4 超时后清除同 session 积压消息

```python
# loop.py _handle_timeout
async def _handle_timeout(self, msg: InboundMessage):
    logger.warning(f"Message timeout for {msg.chat_id}")
    # 清除队列里同一 chat_id 的积压消息（它们基于超时那条的上下文，已失效）
    drained = 0
    temp = []
    while not self.bus.inbound.empty():
        try:
            item = self.bus.inbound.get_nowait()
            if hasattr(item, 'chat_id') and item.chat_id == msg.chat_id:
                drained += 1
            else:
                temp.append(item)
        except asyncio.QueueEmpty:
            break
    for item in temp:
        await self.bus.inbound.put(item)
    if drained:
        logger.info(f"Drained {drained} stale messages for {msg.chat_id}")
    # 回复超时提示
    await self.bus.publish_outbound(OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="⏳ 处理超时..."
    ))
```

#### 8.5 WebStreamRegistry 泄漏防护

```python
# 注册时附 TTL
@dataclass
class StreamEntry:
    queue: queue.SimpleQueue
    created_at: float
    session_id: str

class WebStreamRegistry:
    _MAX_SIZE = 500
    _TTL = 360.0   # 与 message_timeout + buffer 对齐

    def register(self, request_id: str, q: queue.SimpleQueue, session_id: str):
        if len(self._entries) >= self._MAX_SIZE:
            self._evict_expired()
        self._entries[request_id] = StreamEntry(q, time.time(), session_id)

    def _evict_expired(self):
        now = time.time()
        expired = [k for k, v in self._entries.items() if now - v.created_at > self._TTL]
        for k in expired:
            del self._entries[k]

    def unregister(self, request_id: str):
        self._entries.pop(request_id, None)
```

### P2：按需

#### 8.6 Task 异常回调防止静默丢失

```python
# spawn/子任务创建时
def _safe_create_task(coro, name=None):
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(
        lambda t: logger.error(f"Task {t.get_name()} raised: {t.exception()}")
        if not t.cancelled() and t.exception() else None
    )
    return task
```

#### 8.7 可观测指标暴露

在 `/api/v1/system/status` 里追加：

```json
{
  "inbound_queue_depth": 3,
  "message_processing_latency_p95_ms": 4200,
  "mcp_reconnect_count_last_hour": 0,
  "active_sse_connections": 2,
  "last_message_processed_at": "2026-03-24T10:00:00Z"
}
```

---

## 9. 迁移实施步骤

### Step 1：统一核线程 + Bus 入队（1~2 天）

**目标：** Web 消息走 `try_publish_inbound_sync` 而非 `new_event_loop`。

涉及文件：
- `nanobot/bus/queue.py`：加 `maxsize` + `try_publish_inbound_sync`
- `nanobot/web/api.py`：`run_server()` 里启动 `core_thread`，暴露 `app.core_loop`
- `nanobot/agent/loop.py`：`run()` 记录 `self._loop`，加哨兵退出

**验证：** 飞书和 Web 消息均能进入同一条 inbound 队列并被处理。

---

### Step 2：Web Gateway 化（2~3 天）

**目标：** 去掉 `chat_stream` 里的 `threading.Thread` + `new_event_loop`。

涉及文件：
- `nanobot/web/api.py`：`_handle_chat_stream` 改为 Gateway 模式（入队 + SSE 拉流）
- 删除或条件化 `_chat_with_progress` 的 `finally: reload_mcp_config()`
- 处理非流式 `chat()` 接口：类似入队 + 等待 done 事件

**验证：**
- 多轮对话 MCP 不再 reload（日志不再出现 `[MCP] reload_mcp_config called`）
- SSE 心跳正常推送
- 客户端断开 → SSE 循环结束 → 队列清理

---

### Step 3：稳定性加固（1~2 天）

按第 8 节 P0 + P1 顺序实施：
1. 优雅关闭 (`SIGTERM` handler)
2. `/stop` 打断 inbound 等待（`_CANCEL_PROBE`）
3. MCP 重连容错

---

### Step 4：工具 async 审计（1 天）

排查所有 `Tool.execute()` 实现，修复同步阻塞调用（`subprocess.run`、`requests.get`、同步文件 I/O）。

---

### Step 5：性能验证与指标建立（持续）

- 观测 `inbound_queue_depth` 曲线
- 观测首 token 延迟（用户发消息 → SSE 收到第一个 `tool_start` 事件）
- 压测：10 用户并发，每人 5 轮对话，观察排队深度与 P95 延迟

---

## 10. 与现有架构对照表

### 10.1 关键路径对比

| 场景 | 现架构（改造前） | 目标架构（改造后） |
|------|-----------------|-------------------|
| Web 流式对话 | 新线程 + `new_event_loop()` + `process_direct()` | HTTP 线程入队 + `core_loop` 消费 |
| MCP 初始化 | 每条 Web 对话 reload（2次）| 启动时一次，之后不 reload |
| 飞书消息 | `run_coroutine_threadsafe → core_loop`（已是目标形态） | 不变 |
| `/stop` 命令 | `_cancelled_sessions.add` + 等待 `_check_cancelled` 点 | + `_CANCEL_PROBE` 哨兵，0ms 响应 |
| 进程退出 | `KeyboardInterrupt` 直接终止 | `SIGTERM` → 哨兵 → 等当前消息完成 → 关 MCP |
| 队列积压 | `asyncio.Queue()` 无上限 | `maxsize=200` + 429 快速失败 |

### 10.2 与 OpenClaw 架构对照

| OpenClaw 概念 | nanobot 目标架构对应物 |
|---------------|----------------------|
| Gateway（单实例入口） | `run_server()` + `core_loop` 启动 |
| 事件归一化 | `InboundMessage` + `metadata` 统一封装 |
| 统一 Agent 核心 | `AgentLoop.run()` 在 `core_loop` 里长驻 |
| `TOOLS.md` / `SKILLS.md` 轻量描述 | `selected_mcp_servers` + `_select_tools_for_message` 筛选 |
| ClawHub 安装自动更新 | `create_mcp` → `_reload_mcp` 热更新 |
| 按 channel 分策略 | `session.metadata["tool_mode"]` + `channel` 字段 |
| One agent, many platforms | 飞书/Web/Telegram → 同一 `inbound` 队列 → 同一 `AgentLoop` |

### 10.3 不同于 OpenClaw 之处

| 方面 | OpenClaw | nanobot（本方案） |
|------|----------|-----------------|
| 语言/运行时 | Node.js / TypeScript | Python / asyncio |
| 工具描述注入 | `TOOLS.md`（40 token 描述）不注入 schema | 按需筛选 `selected_mcp_servers` 的完整 JSON schema |
| 子 Agent | `openclaw-code-agent` 插件独立进程 | `SpawnTool` + `SubagentManager` 同进程内 |
| MCP 管理 | `mcporter` + ClawHub skill 市场 | 全局配置 + `McpLazyToolAdapter` |

---

## 附录：文件改动索引

| 文件 | 改动类型 | 主要内容 |
|------|---------|---------|
| `nanobot/bus/queue.py` | 修改 | 加 `maxsize`、`try_publish_inbound_sync` |
| `nanobot/agent/loop.py` | 修改 | 记录 `self._loop`、哨兵退出、`_CANCEL_PROBE`、`_handle_timeout` 清积压 |
| `nanobot/web/api.py` | 重点修改 | `run_server` 启动 `core_thread`；`_handle_chat_stream` Gateway 化；删 `reload_mcp` finally |
| `nanobot/agent/tools/mcp.py` | 修改 | `execute` 加重连容错 |
| `nanobot/channels/feishu.py` | 小改 | `progress_callback` 解耦（`put_nowait` + 独立 task）|
| `nanobot/web/chat_stream_bus.py` | 小改 | `trim_buffer` 在 done 后调用 |

---

*文档由 AI 辅助生成，基于 2026-03-24 与代码库的深度分析。实施前请结合最新代码状态做最终评估。*
