# 微内核超时后台化（Timeout-as-Backgrounding）设计方案

> 方案：B — Persistent Background Registry  
> 日期：2026-04-22  
> 状态：待评审

---

## 1. 背景与问题

当前工程中，微内核（AgentLoop Microkernel）的超时行为是**终止式**的：

- `kernel.run_until_done()` 的 `timeout_seconds`（默认 600s）超时后，调用 `mark_trace_canceled()` 将 trace 标记为 `CANCELED`
- 微内核 workers 被 `shutdown = True` 终止
- 正在执行的 capability 被中断，结果丢失

这与子 agent 的**后台化**行为不一致：

- 子 agent（Claude Code）超时后，`asyncio.shield` 保护其继续运行
- 完成后通过 `SubagentProgressBus` 推送 late result
- 结果自动注入主会话

**目标**：让微内核也支持"超时即后台化"——用户等待超时后任务继续运行，完成后自动将结果插入对话，且支持进程崩溃恢复。

---

## 2. 设计原则

1. **超时 ≠ 终止**：超时仅解除前端等待，后台继续执行
2. **状态持久化**：后台任务状态写入 SQLite，崩溃后可恢复
3. **结果自动注入**：late result 到达后自动追加到 session，无需用户干预
4. **防御重复注入**：通过消息 metadata 标记防止 late result 重复插入
5. **最小侵入**：不新增数据库表，复用现有 `agentloop_traces` + `session.metadata`

---

## 3. 状态机设计

### 3.1 Trace 生命周期（含后台化语义）

```
[AgentLoop 提交]
    │
    ▼
agentloop_traces.status = RUNNING
    │
    ├── 正常完成 (< 120s) ──► status = DONE
    │                           │
    │                           └──► _on_microkernel_done() 注入结果
    │
    └── 用户等待超时 (> 120s)
            │
            ├──► 保存后台标记到 session.metadata
            │
            ├──► 推送 microkernel_backgrounded 事件
            │
            ├──► 启动 _wait_microkernel_late_result Task
            │       │
            │       ├── 正常完成 ──► _on_microkernel_done() 注入 late result
            │       │
            │       ├── 失败 ─────► _on_microkernel_done(status="error")
            │       │
            │       └── AgentLoop 崩溃
            │               │
            │               └──► 重启时扫描恢复
            │
            └──► trace 保持 RUNNING（直到真正完成或取消）
```

### 3.2 后台任务状态（内存 + 持久化混合）

| 状态 | 存储位置 | 说明 |
|------|----------|------|
| `RUNNING` | `agentloop_traces.status` | trace 正在执行 |
| `BACKGROUNDED` | `session.metadata["microkernel_background"]` | 用户已解除等待，任务在后台 |
| `RECOVERING` | 内存（启动时临时） | 重启后重新挂载监控 |
| `DONE` | `agentloop_traces.status` | 完成，结果已注入 |

---

## 4. 数据模型

### 4.1 不新增表

复用现有表结构：

- **`agentloop_traces`**：`status` 字段保持 `RUNNING` 直到真正完成
- **`agentloop_tasks`**：任务状态机、租约、心跳续租照常工作
- **`session.metadata`**（JSON）：记录后台化元数据

### 4.2 Session Metadata 结构

```json
{
  "runtime_checkpoint": { ... },
  "microkernel_background": {
    "trace_id": "tr_abc123",
    "goal": "用户原始请求摘要",
    "channel": "browser",
    "chat_id": "session-uuid",
    "started_at": "2026-04-22T18:00:00",
    "backgrounded_at": "2026-04-22T18:02:00"
  }
}
```

### 4.3 Session Messages Metadata（去重标记）

```json
{
  "role": "assistant",
  "content": "✅ 微内核任务已完成...",
  "metadata": {
    "microkernel_trace_id": "tr_abc123"
  }
}
```

---

## 5. 核心组件变更

### 5.1 组件关系图

```
┌─────────────────────────────────────────────────────────────┐
│                        AgentLoop                              │
│  ┌─────────────────┐    ┌──────────────────────────────┐   │
│  │ _process_message │───►│ _delegate_to_microkernel     │   │
│  └─────────────────┘    │  - 创建 kernel                 │   │
│                         │  - submit trace                │   │
│                         │  - create_task(_run_kernel...) │   │
│                         └──────────────┬─────────────────┘   │
│                                        │                      │
│  ┌─────────────────────────────────────▼──────────────────┐  │
│  │         _run_kernel_and_notify                           │  │
│  │  ┌──────────────────────────────────────────────────┐   │  │
│  │  │  inner = ensure_future(kernel.run_until_done())  │   │  │
│  │  │  await wait_for(shield(inner), timeout=120)      │   │  │
│  │  └──────────────────────────────────────────────────┘   │  │
│  │                         │                                │  │
│  │         ┌───────────────┼───────────────┐                │  │
│  │         ▼               ▼               ▼                │  │
│  │    正常完成      TimeoutError        异常                │  │
│  │         │               │               │                │  │
│  │         ▼               ▼               ▼                │  │
│  │   _fetch_result   保存后台标记    _on_microkernel_done   │  │
│  │   _on_microkernel  推送事件         (status=error)       │  │
  │  │       (ok)       启动 late_wait                        │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                        │                      │
│  ┌─────────────────────────────────────▼──────────────────┐  │
│  │      _wait_microkernel_late_result (后台 Task)           │  │
│  │  - await inner (继续等待微内核)                          │  │
│  │  - _fetch_result()                                      │  │
│  │  - _on_microkernel_done() (注入 late result)             │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                        │                      │
│  ┌─────────────────────────────────────▼──────────────────┐  │
│  │      _recover_background_microkernels (启动时)          │  │
│  │  - 扫描 agentloop_traces.status='RUNNING'               │  │
│  │  - 检查 session.metadata 后台标记                       │  │
│  │  - 重新挂载 late_wait Task                              │  │
│  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │   SQLite (chat.db)│
                    │  agentloop_traces │
                    │  agentloop_tasks  │
                    └─────────────────┘
```

### 5.2 关键接口变更

#### `agentloop/kernel/kernel.py`

```python
# 修改：run_until_done 默认不设内部超时，由调用方控制
async def run_until_done(
    self,
    trace_id: str,
    worker_count: int = 4,
    timeout_seconds: Optional[float] = None,  # 默认 None，不限制
) -> bool:
    ...
```

#### `agent/loop.py`

新增字段：
```python
self._microkernel_tasks: dict[str, asyncio.Task] = {}  # trace_id -> Task
```

新增方法：
```python
async def _run_kernel_and_notify(self, kernel, trace_id, goal, channel, chat_id) -> None
async def _wait_microkernel_late_result(self, inner_task, kernel, trace_id, goal, channel, chat_id) -> None
async def _recover_background_microkernels(self) -> None
```

修改方法：
```python
async def _delegate_to_microkernel(...) -> str   # 取消 Thread，改用 create_task
def cancel_current_request(...) -> None           # 联动取消微内核
async def _on_microkernel_done(...) -> None        # 增加重复注入防御
```

---

## 6. 时序图

### 6.1 主线 1：正常完成（< 120s）

```
AgentLoop    _delegate_to_microkernel    kernel    _run_kernel_and_notify    Session    Bus
   │                    │                   │                │                  │       │
   │──► submit(goal) ──►│                 │                │                  │       │
   │                    │──► submit() ───►│                │                  │       │
   │                    │◄── trace_id ────│                │                  │       │
   │                    │                 │                │                  │       │
   │                    │──► create_task(_run_kernel...) ─►│                  │       │
   │                    │                 │                │                  │       │
   │◄── trace_id ───────│                 │                │                  │       │
   │                    │                 │                │                  │       │
   │                    │                 │                │──► shield(inner=run_until_done)
   │                    │                 │                │                  │       │
   │                    │                 │                │──► wait_for(shield, 120s)
   │                    │                 │                │◄── 正常返回       │       │
   │                    │                 │                │                  │       │
   │                    │                 │                │──► _fetch_result()
   │                    │                 │                │                  │       │
   │                    │                 │                │──► _on_microkernel_done()
   │                    │                 │                │                  │──► add_message
   │                    │                 │                │                  │──► save()
   │                    │                 │                │──► Bus.push(microkernel_end)
   │                    │                 │                │────────────────────────► push
```

### 6.2 主线 2：超时后台化（> 120s）

```
AgentLoop    _delegate_to_microkernel    _run_kernel_and_notify    Session    Bus    LateWaitTask
   │                    │                           │              │       │           │
   │──► submit(goal) ──►│                          │              │       │           │
   │                    │──► create_task(_run...) ─►│              │       │           │
   │◄── trace_id ───────│                          │              │       │           │
   │                    │                          │              │       │           │
   │                    │                          │──► shield(inner)    │       │           │
   │                    │                          │──► wait_for(120s)   │       │           │
   │                    │                          │◄── TimeoutError     │       │           │
   │                    │                          │              │       │           │
   │                    │                          │──► session.metadata["microkernel_background"] = {...}
   │                    │                          │              │──► save()
   │                    │                          │              │       │           │
   │                    │                          │──► Bus.push(microkernel_backgrounded)
   │                    │                          │────────────────────────► push   │
   │                    │                          │              │       │           │
   │                    │                          │──► create_task(_wait_microkernel_late_result)
   │                    │                          │──────────────────────────────────► 启动
   │                    │                          │              │       │           │
   │◄── 继续处理其他消息 ─│                          │              │       │           │
   │                    │                          │              │       │           │
   │                    │                          │              │       │           │──► await inner
   │                    │                          │              │       │           │──► _fetch_result()
   │                    │                          │              │       │           │──► _on_microkernel_done()
   │                    │                          │              │       │           │──► session.add_message()
   │                    │                          │              │──► save()          │
   │                    │                          │              │       │           │──► Bus.push(microkernel_end)
   │                    │                          │              │       │◄────────── push
   │                    │                          │              │       │           │
   │                    │                          │              │       │           │──► kernel.conn.close()
```

### 6.3 主线 3：崩溃恢复

```
AgentLoop 启动
    │
    ├──► _recover_background_microkernels()
    │       │
    │       ├──► SELECT * FROM agentloop_traces WHERE status = 'RUNNING'
    │       │
    │       ├──► 对每个 RUNNING trace：
    │       │       ├──► session = sessions.get_or_create(channel:chat_id)
    │       │       ├──► bg = session.metadata.get("microkernel_background")
    │       │       │
    │       │       ├──► bg 存在 ──► 重新创建 kernel 连接
    │       │       │       ├──► trace 已完成？──► _on_microkernel_done()
    │       │       │       └──► trace 仍在 RUNNING？──► create_task(_wait_microkernel_late_result)
    │       │       │
    │       │       └──► bg 不存在 ──► 非后台任务，交给 recover_stale_tasks 处理
    │       │
    │       └──► 清理 session.metadata 中已完成的标记
    │
    └──► 正常启动
```

### 6.4 主线 4：用户主动取消

```
用户点击"停止"
    │
    ├──► cancel_current_request(session_id)
    │
    ├──► _cancelled_sessions.add(origin_key)
    │
    ├──► _microkernel_tasks[trace_id].cancel()
    │
    └──► _run_kernel_and_notify 收到 CancelledError
            │
            ├──► kernel.shutdown = True
            │
            ├──► mark_trace_canceled(conn, trace_id, "CANCELLED_BY_AGENT")
            │
            ├──► session.metadata.pop("microkernel_background", None)
            │
            └──► Bus.push(microkernel_end, status="cancelled")
```

---

## 7. 竞态条件与防御

### 7.1 重复注入（最重要）

**场景**：后台任务完成后，`_on_microkernel_done` 被调用。如果此时 AgentLoop 重启，恢复逻辑也可能再次调用 `_on_microkernel_done`。

**防御**：消息 metadata 标记 + 最近 5 条消息扫描

```python
already_injected = any(
    m.get("metadata", {}).get("microkernel_trace_id") == trace_id
    for m in session.messages[-5:]
)
if already_injected:
    return
```

### 7.2 后台任务完成前用户刷新页面

- session 已保存到 SQLite（`self.sessions.save(session)`）
- late result 到来时再次 `session.add_message()` + `save()`
- 前端刷新后 `loadMessages()` 会加载到新消息

### 7.3 后台任务完成前用户发送新消息

- `session.messages` 追加用户新消息
- late result 到达后追加 assistant 消息
- 顺序正确：user → assistant(late result)

### 7.4 多个后台任务同时运行

- `_microkernel_tasks: dict[str, asyncio.Task]` 独立追踪
- 每个 trace 有独立的 kernel 连接和 late_wait Task

### 7.5 微内核僵死（RUNNING 但无心跳）

- 现有 `recover_stale_tasks` 机制继续工作
- 僵死任务会被重置为 READY 重试，或标记为 FAILED
- late_wait Task 会收到 `inner_task` 异常，进入 `_on_microkernel_done(status="error")`

---

## 8. 前端适配

### 8.1 新增 WebSocket 事件类型

| 事件类型 | 触发时机 | 前端行为 |
|----------|----------|----------|
| `microkernel_start` | 微内核开始执行 | 显示"深度处理中..." |
| `microkernel_progress` | 每 2s 推送（可选） | 更新任务进度条 |
| `microkernel_backgrounded` | 用户等待超时 | 显示后台任务指示器，提示"已转至后台运行" |
| `microkernel_end` | 后台任务完成 | 自动刷新消息列表，显示 late result |

### 8.2 后台任务指示器 UI

```typescript
interface BackgroundTask {
  trace_id: string
  goal: string
  status: 'running' | 'done' | 'error'
  startTime: number
  totalTasks?: number
  doneTasks?: number
}
```

在 ChatPage 底部或侧边栏显示后台任务列表，用户可随时查看有哪些任务在后台运行。

### 8.3 Late Result 自动插入

前端收到 `microkernel_end` 事件后：
1. 调用 `loadMessages(sessionId)` 从后端重新加载消息列表
2. 或直接在本地 `messages` state 中追加新消息（如果事件携带了完整内容）

---

## 9. 实现步骤（建议顺序）

1. **P0**：修改 `_delegate_to_microkernel` 取消 Thread，改用 `asyncio.create_task`
2. **P0**：重写 `_run_kernel_and_notify` 实现 `shield + wait_for` 模式
3. **P0**：新增 `_wait_microkernel_late_result`
4. **P0**：修改 `_on_microkernel_done` 增加重复注入防御
5. **P1**：修改 `cancel_current_request` 联动取消微内核
6. **P1**：新增 `_recover_background_microkernels` 启动恢复
7. **P1**：前端适配 `microkernel_backgrounded` / `microkernel_end` 事件
8. **P2**：可选：定期 `microkernel_progress` 推送

---

## 10. 回滚策略

如果该功能引入问题，可通过以下方式快速回滚：

1. 将 `_delegate_to_microkernel` 改回 `Thread` 模式
2. 将 `kernel.run_until_done` 恢复 `timeout_seconds=600` 默认值
3. 移除 `_recover_background_microkernels` 调用

不会影响现有 SQLite 数据（`agentloop_traces` 中的 `RUNNING` 记录会被 `recover_stale_tasks` 正常处理）。
