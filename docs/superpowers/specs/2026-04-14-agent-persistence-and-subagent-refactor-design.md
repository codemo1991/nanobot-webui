# Agent 持久化与子代理架构重构设计文档

## 1. 背景与目标

### 1.1 当前痛点
1. **Claude Code 长任务丢失**：`ClaudeCodeManager` 将运行中任务保存在纯内存字典 `_running_tasks` 中，后端进程一旦重启（部署、OOM、崩溃），已启动的 Claude Code 子进程即成为"孤儿"，用户无法看到任务状态和结果。
2. **Spawn 子代理无法真正取消**：`SubagentManager` 使用 `threading.Thread` + 私有 `asyncio` 事件循环启动子代理，`cancel_by_session()` 仅从字典移除追踪项，子线程仍在后台运行，造成资源泄漏和不可预期行为。
3. **AgentLoop 缺乏中断恢复能力**：会话在处理中如果进程崩溃，没有 `runtime_checkpoint` 机制，下次启动时该会话处于不完整状态，可能丢失上下文或重复响应。
4. **同会话并发消息无队列缓冲**：用户在等待长任务时发送第二条消息，可能产生竞态或覆盖。

### 1.2 设计目标
- 建立统一的**内存-持久化-恢复**三层架构，使后端进程重启后可自愈。
- 将子代理和 Claude Code 任务迁回主事件循环的 `asyncio.Task`，实现真正的取消与追踪。
- 引入 `runtime_checkpoint`、`pending_queues`、后台调度器和并发信号量，提升系统韧性和可控性。

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    AgentLoop (主事件循环)                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Semaphore  │  │ Pending Qs  │  │ Background Scheduler│  │
│  │  (并发上限)   │  │ (同会话串行) │  │   (consolidation)   │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              Runtime Checkpoint (内存+SQLite)             │ │
│  │   每轮 assistant / tool_end 后写入 session.metadata      │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────┬──────────────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
┌───────────────┐     ┌─────────────────┐
│SubagentManager│     │ClaudeCodeManager│
│ (asyncio.Task)│     │ (asyncio.Task + │
│               │     │  subprocess.PID)│
└───────────────┘     └─────────────────┘
        │                     │
        ▼                     ▼
   结果 → MessageBus      结果 → result_dir
   (InboundMessage)       + MessageBus (via watcher)
```

**核心原则**：
- 主事件循环是唯一的调度中心，所有长任务以 `asyncio.Task` 形式存在。
- 状态是持久化的副产品：运行中持续写 checkpoint，而非运行完再保存。
- 进程重启可自愈：启动时扫描遗留状态，重新认领或补全结果。

---

## 3. 组件设计

### 3.1 SubagentManager：从线程迁回 asyncio.Task

#### 接口变更
```python
class SubagentManager:
    def __init__(self, ...):
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._session_tasks: dict[str, set[str]] = {}
        self._task_records: dict[str, dict] = {}  # 内存状态快照
        self._spawn_semaphore: asyncio.Semaphore | None = None

    async def spawn(self, ...) -> str:
        ...

    def cancel_by_session(self, channel: str, chat_id: str) -> int:
        """真正调用 task.cancel() 并等待清理。"""
        ...

    async def recover_tasks(self) -> None:
        """启动时恢复：扫描 _task_records 中 running 但 task 已丢失的记录。"""
        ...

    async def run_vision_analysis(self, ...) -> Any:
        """保留线程隔离模式，专门应对 WebSocket 超时场景。"""
        ...
```

#### 关键行为
- `spawn()` 创建 `asyncio.Task` 并在 `add_done_callback` 中清理字典和记录。
- `cancel_by_session()` 遍历 `_running_tasks`，对未完成的任务调用 `task.cancel()`。
- `_run_subagent()` 完成后向 `MessageBus` 发布 `channel="system"` 的 `InboundMessage`，使结果在断线重连后仍可被消费。
- `run_vision_analysis()` **保留** `daemon=True` 线程 + 私有 event loop，因为它是专门用于必须在 WebSocket 超时后仍然存活的视觉分析场景。

### 3.2 ClaudeCodeManager：进程级长任务 + 状态持久化

#### 元数据增强
`.meta.json` 新增字段：
```json
{
  "task_id": "abc123",
  "pid": 12345,
  "prompt": "...",
  "origin": {"channel": "web", "chat_id": "session-1"},
  "status": "running",
  "timestamp": "..."
}
```

#### SQLite 持久化
新增 `claude_tasks` 表：
```sql
CREATE TABLE IF NOT EXISTS claude_tasks (
    task_id TEXT PRIMARY KEY,
    session_key TEXT,
    pid INTEGER,
    status TEXT, -- running | done | timeout | error | cancelled | lost
    prompt TEXT,
    workdir TEXT,
    result TEXT,
    created_at TEXT,
    updated_at TEXT
);
```

#### 核心方法
```python
class ClaudeCodeManager:
    async def start_task(self, ...) -> str:
        # 1. 生成 task_id，写入 .meta.json（含 pid）
        # 2. INSERT claude_tasks (status='running')
        # 3. 创建 asyncio.Task(_run_claude_code)
        ...

    async def _run_claude_code(self, ...) -> None:
        # 取消 asyncio.wait_for 硬性包裹
        # 用 process.wait() 自然等待
        # 完成后写结果文件 + 更新 DB
        ...

    def _recover_tasks(self) -> None:
        # 1. 读 DB 中 status='running' 的记录
        # 2. 检查 pid 是否存活
        #    - 存活 → asyncio.create_task(_reattach_wait(pid, task_id))
        #    - 已结束且无 .json → 补写 'lost'，更新 DB
        #    - 已结束且有 .json → 更新 DB 为对应状态
        ...

    def cancel_task(self, task_id: str) -> bool:
        # task.cancel() + process.terminate() + 写 cancelled 结果
        ...
```

#### 结果双保险
- **Primary**：Claude Code 的 `Stop` / `SessionEnd` hook 写 `.json` 结果文件。
- **Fallback**：`_run_claude_code()` 在 `process.wait()` 返回后，主动读取 stdout 最后 N 行写入 `.json`，覆盖 hook 因进程被强杀而未执行的场景。

#### 超时策略
取消 `asyncio.wait_for` 的硬性超时。长任务由用户通过 `/stop` 主动取消，或业务层设置软提示，避免合法的长任务被误杀。默认超时大幅提升至 `3600` 秒或更高。

### 3.3 AgentLoop：Checkpoint + Pending Queues + Background Scheduler + Semaphore

#### Runtime Checkpoint
```python
_RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"

def _set_runtime_checkpoint(self, session: Session, payload: dict) -> None:
    session.metadata[self._RUNTIME_CHECKPOINT_KEY] = {
        "messages": [m for m in session.messages],
        "payload": payload,
        "timestamp": time.time(),
    }
    self.sessions.save(session)

def _restore_runtime_checkpoint(self, session: Session) -> bool:
    checkpoint = session.metadata.pop(self._RUNTIME_CHECKPOINT_KEY, None)
    if not checkpoint:
        return False
    restored = checkpoint["messages"]
    overlap = _find_overlap(session.messages, restored)
    session.messages.extend(restored[overlap:])
    return True
```

**写入时机**：
- 每次 assistant 消息追加后
- 每轮 tool results 返回后（若后续还有思考/调用）
- 异常退出前（`CancelledError` / `TimeoutError`）

**恢复时机**：`_process_message()` 开头，若 session 有 checkpoint 则先恢复再继续。

#### Pending Message Queues
```python
self._pending_queues: dict[str, asyncio.Queue] = {}
self._session_locks: dict[str, asyncio.Lock] = {}
```

`AgentLoop.run()` 逻辑调整：
- 若 `effective_key` 对应的会话正在处理（有 `_session_locks` 或活跃 task），新消息入 `pending_queues`。
- `_dispatch()` 结束或 `_process_message()` 轮询时，调用 `_drain_pending()` 消费队列。
- Drain 出的消息可以作为 `system` 追加到当前回合，或重新 `publish_inbound` 进入下一轮。

#### Background Scheduler
```python
def _schedule_background(self, coro) -> None:
    task = asyncio.create_task(coro)
    self._background_tasks.append(task)
    task.add_done_callback(self._background_tasks.remove)
```

**应用场景**：
- `consolidator.maybe_consolidate()` 改为 `_schedule_background(...)` 调用
- MCP 预热等非阻塞操作

#### Concurrency Semaphore
```python
_max = int(os.environ.get("NANOBOT_MAX_CONCURRENT_REQUESTS", "3"))
self._concurrency_gate = asyncio.Semaphore(_max) if _max > 0 else None
```

在 `_dispatch()` 中：
```python
async with self._concurrency_gate:
    response = await self._process_message(msg)
```

---

## 4. 数据流：长任务完整生命周期（以 Claude Code 为例）

```
1. 用户请求 → browser WebSocket → MessageBus → AgentLoop
2. AgentLoop dispatch (acquire semaphore)
3. SpawnTool / ClaudeCodeTool 调用 ClaudeCodeManager.start_task()
4. start_task():
   - 生成 task_id, 写入 .meta.json (含 pid)
   - INSERT claude_tasks (status=running)
   - 创建 asyncio.Task(_run_claude_code)
5. AgentLoop 立即返回："Claude Code 任务已启动，完成后通知您"
6. Claude Code 子进程运行中（可能 10 分钟）
   - 若后端重启：
     → __init__ 调用 _recover_tasks()
     → 检查 pid 存活，重新 attach 或补写 lost 结果
7. 子进程结束：
   - Hook 写 abc123.json
   - _run_claude_code 的 process.wait() 返回，写 fallback 结果
   - 更新 claude_tasks (status=done)
8. ResultWatcher 检测到 abc123.json → MessageBus InboundMessage
9. AgentLoop 消费 system 消息 → LLM 总结结果 → 用户收到回复
```

---

## 5. 持久化与恢复矩阵

| 场景 | 当前行为 | 新行为 |
|---|---|---|
| 后端重启，Claude Code 还在跑 | 丢失，不认识 task | 扫描 DB + PID，重新 attach 或标记状态 |
| 后端重启，Claude Code 已结束 | 依赖 watcher 盲区，可能漏通知 | 启动扫描 .json 文件，补发 InboundMessage |
| 用户点击 /stop | 字典清理，进程继续跑 | task.cancel() + process.terminate()，写 cancelled 结果 |
| 子代理运行中超时 | N/A（线程无超时） | 父请求返回超时提示，子代理 Task 继续运行 |
| AgentLoop 处理中崩溃 | 会话可能处于不完整状态 | 下次启动 checkpoint 恢复，继续或优雅报错 |
| 用户同会话连续发消息 | 可能竞态或覆盖 | 进入 pending queue，当前回合结束后顺序处理 |

---

## 6. 错误处理与边界情况

- **PID 复用**：恢复时除了 `pid_exists`，还要对比进程启动时间与 meta 中的 `timestamp`，避免误认。
- **SQLite 写入失败**：持久化层用 `try/except` 包裹，失败时降级为仅写 `.meta.json`，不影响主流程。
- **多次恢复**：`_restore_runtime_checkpoint` 成功后立即 `save(session)` 并清除 checkpoint，防止重复恢复。
- **Pending 队列溢出**：设置 `maxsize=10`，超出时 oldest 消息被丢弃并记录 warning。
- **结果竞争**：Hook 和 Python fallback 可能同时写 `.json`。使用文件锁或先写临时文件再原子重命名避免损坏。

---

## 7. 测试策略

### 单元测试
- `SubagentManager.cancel_by_session()`：验证 `task.cancel()` 被调用且 `done_callback` 正常清理。
- `ClaudeCodeManager._recover_tasks()`：模拟遗留 `.meta.json` + 已结束 PID，验证状态补全。
- `AgentLoop._restore_runtime_checkpoint()`：模拟含 checkpoint 的 Session，验证消息恢复。

### 集成测试
- 启动一个慢速 spawn，模拟 `asyncio.TimeoutError`，验证父请求返回提示且子代理仍能完成并发布 bus 消息。
- 启动 Claude Code 子进程（用 `sleep 30` 模拟），杀掉 backend 进程再重启，验证任务被重新认领或结果补全。
- 验证同会话快速发送两条消息，第二条进入 pending queue 并按顺序处理。

---

## 8. 迁移计划（Phase 1-4）

| Phase | 内容 | 目标 |
|---|---|---|
| **Phase 1** | `ClaudeCodeManager` 持久化 + 恢复 | 风险最低，直接解决 Claude Code 任务丢失痛点 |
| **Phase 2** | `SubagentManager` 迁回 `asyncio.Task` + 真正取消 | 根治子代理资源泄漏和取消失效问题 |
| **Phase 3** | `AgentLoop` checkpoint + pending queues + background scheduler | 建立系统级中断恢复和消息有序处理能力 |
| **Phase 4** | 并发 semaphore + 全链路集成测试 | 完成性能保护和回归验证 |

---

## 9. 参考与依赖

- 参考仓库：`E:\Users\GYENNO\Documents\GitHub\nanobot`
- 关键借鉴点：`runtime_checkpoint`、`_pending_queues`、`_schedule_background`、`asyncio.Semaphore`
- 当前项目需修改的核心文件：
  - `nanobot/agent/loop.py`
  - `nanobot/agent/subagent.py`
  - `nanobot/claude_code/manager.py`
  - `nanobot/claude_code/watcher.py`
  - `nanobot/session/manager.py`（可能需要 metadata 结构兼容）
