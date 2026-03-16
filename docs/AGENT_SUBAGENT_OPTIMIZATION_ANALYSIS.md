# Agent → 子 Agent 交互流程优化分析

> 从系统稳定性、性能角度审视主 Agent 与子 Agent 的交互流程，识别潜在风险与优化点。

---

## 一、当前交互流程概览

```
用户消息 → 主 Agent Loop → spawn 工具 → SubagentManager.spawn()
                                    ↓
                    [信号量获取] → [线程创建] → run_in_thread()
                                    ↓
                    ┌───────────────────────────────────────────────┐
                    │ 子 Agent 在独立线程 + 独立 event loop 中运行    │
                    │ _run_subagent() → native/claude_code/voice/   │
                    │                   dashscope_vision 等 backend  │
                    └───────────────────────────────────────────────┘
                                    ↓
                    完成 → _announce_result() / _generate_and_push_summary()
                                    ↓
                    bus.publish_inbound(InboundMessage)  ← 跨 loop 调用
                                    ↓
                    主 Agent consume_inbound() → _process_system_message()
                                    ↓
                    LLM 综合子 Agent 结果 → 回复用户
```

---

## 二、稳定性风险点

### 2.1 跨 Event Loop 的 bus.publish_inbound（高优先级）

**位置**：`subagent.py` 中 `_announce_result`、`_announce_batch_result`、`_inject_voice_as_user_message` 等

**问题**：子 Agent 运行在独立线程的独立 `asyncio` event loop 中，完成时调用 `await self.bus.publish_inbound(msg)`。`MessageBus.inbound` 是 `asyncio.Queue`，通常由主 loop 创建并在主 loop 上 `consume_inbound()`。从子 Agent 的 loop 向该 queue 做 `put()` 属于跨 loop 操作，可能导致：

- 竞态或未定义行为（asyncio.Queue 非为跨线程设计）
- 已有注释：`publish_inbound failed (possible cross-loop issue)`（voice 注入处）

**建议**：

1. **方案 A（推荐）**：使用线程安全桥接
   - 在 `MessageBus` 中增加 `thread_safe_put_inbound(msg)`，内部用 `queue.Queue` 或 `loop.call_soon_threadsafe` 将消息投递到主 loop 的 inbound
   - 子 Agent 调用同步的 `thread_safe_put_inbound`，不再 `await publish_inbound`

2. **方案 B**：子 Agent 通过 `asyncio.run_coroutine_threadsafe` 在主 loop 上执行 `publish_inbound`，需持有主 loop 引用

### 2.2 Daemon 线程与资源泄漏

**位置**：`subagent.py` 第 363 行

```python
thread = threading.Thread(target=run_in_thread, daemon=True)
```

**问题**：

- 主进程退出时 daemon 线程会被强制终止，`finally` 可能不执行
- 若在 `semaphore.release()` 或 `_running_tasks.pop()` 之前被杀死，会导致信号量泄漏、`_running_tasks` 残留

**建议**：

- 进程退出前显式调用 `cancel_all_tasks()` 并等待线程结束（或设置 `daemon=False` 并实现优雅关闭）
- 或在 shutdown 时对 `_subagent_semaphore` 做一次补偿性 release，避免长期泄漏

### 2.3 SessionManager 并发访问

**位置**：`subagent.py` 多处 `main_session.subagent_results[task_id] = {...}` 与 `self.sessions.save(main_session)`

**现状**：`SessionManager` 使用 `_lock` 保护 `get_or_create`、`save` 等，理论上可支持多线程。

**建议**：确认 `save()` 与 `get_or_create()` 的锁粒度覆盖所有对 `subagent_results` 的读写，避免子 Agent 与主 Agent 并发修改同一 session 时出现竞态。

### 2.4 Batch 聚合的竞态

**位置**：`_is_last_in_batch`、`_deliver_batch_complete`

**现状**：`_batch_lock` 保护 `_batch_tasks` 的读写，`_is_last_in_batch` 在持有锁时检查 `_running_tasks`。`_running_tasks` 的修改在子线程的 `finally` 中，与 `_batch_lock` 无直接关系，但逻辑上「最后一个完成者」的判定是合理的。

**建议**：在 `_is_last_in_batch` 与 `_deliver_batch_complete` 之间保持原子性（例如在 `_batch_lock` 下完成「判断 + 弹出 batch」），避免极端并发下重复交付。

### 2.5 取消与 stream_done 的时序

**位置**：`run_in_thread` 的 `finally` 中

```python
remaining = sum(1 for ok, _ in running_tasks_ref.values() if ok == origin_key)
if remaining == 0:
    bus.push(origin_key, {"type": "stream_done"})
```

**问题**：`running_tasks_ref.pop(task_id, None)` 与 `remaining` 计算在同一 `finally` 中，顺序正确。但若多个子 Agent 几乎同时完成，`remaining` 可能短暂为 0 多次，导致多次 `stream_done`。`SubagentProgressBus` 对重复事件通常可容忍，但可考虑幂等或去重。

---

## 三、性能优化点

### 3.1 信号量等待策略

**位置**：`spawn()` 第 246–252 行

```python
if current_running >= self._max_concurrent_subagents:
    try:
        async with asyncio.timeout(60):
            await self._subagent_semaphore.acquire()
    except asyncio.TimeoutError:
        return f"Error: Too many concurrent subagents..."
else:
    await self._subagent_semaphore.acquire()
```

**问题**：达到上限时需等待最多 60 秒，主 Agent 的 spawn 调用会阻塞。若主 Agent 在 batch 模式下等待多个 spawn，整体延迟会叠加。

**建议**：

- 可配置超时时间
- 或改为「快速失败」：达到上限时立即返回「请稍后重试」，由用户或主 Agent 决定是否重试

### 3.2 工具实例缓存

**位置**：`_create_tools_for_template` 与 `_tools_cache`

**现状**：已按 template 缓存工具实例，避免每次 spawn 重建，实现合理。

**建议**：若 template 数量有限，可考虑启动时预创建常用 template 的工具实例，进一步减少首次 spawn 延迟。

### 3.3 SubagentProgressBus 事件节流

**位置**：`_run_via_claude_code` 中的 `_progress_callback`

**现状**：已有 `_PROGRESS_MIN_INTERVAL`（1 秒）和 `_PROGRESS_SAME_CONTENT_DEDUP`（2 秒）节流，可减少前端事件 flood。

**建议**：native 路径若也有类似流式 progress，可复用相同节流逻辑。

### 3.4 LLM Summary 超时与降级

**位置**：`_generate_and_push_summary`、`_generate_batch_summary`

**现状**：`asyncio.wait_for(..., timeout=15.0)`，超时后使用 fallback 文本。

**建议**：15 秒对 summary 通常足够；若结果很长，可考虑分段 summary 或提高 timeout 配置。

### 3.5 线程内 loop 关闭前的 sleep

**位置**：`run_in_thread` 的 `finally` 中

```python
try:
    loop.run_until_complete(asyncio.sleep(0.5))
except Exception:
    pass
loop.close()
```

**现状**：给 LiteLLM LoggingWorker 等 0.5 秒处理时间，避免 "Event loop is closed" 类错误。

**建议**：0.5 秒为经验值，若仍出现关闭相关错误，可适当延长或改为更明确的资源清理逻辑。

---

## 四、架构层面建议

### 4.1 统一子 Agent 执行模型

**现状**：子 Agent 在独立线程 + 独立 loop 中运行，主要目的是避免主请求的 event loop 关闭导致任务被取消。

**可选方向**：

- **保持现状**：适合长时间任务，但需解决跨 loop 通信
- **专用 worker 进程/线程**：子 Agent 在常驻 worker 中执行，通过进程间队列与主 Agent 通信，隔离性更好
- **主 loop 内 asyncio.Task**：短任务可在主 loop 中 `asyncio.create_task`，无需跨线程，但需确保主 loop 生命周期足够长

### 4.2 结果交付路径统一

**现状**：Web 渠道走 `_generate_and_push_summary` → SubagentProgressBus；非 Web 走 `_announce_result` → MessageBus.inbound。两套路径逻辑相似但实现分散。

**建议**：抽象统一的「子 Agent 结果交付」接口，根据 channel 选择推送到 Bus 或生成 summary，减少重复与分支。

### 4.3 可观测性增强

**建议**：

- 对 spawn → 完成 → announce 全链路打 trace（如 OpenTelemetry）
- 记录 spawn 延迟、子 Agent 执行时长、announce 到主 Agent 处理的延迟
- 监控 `_running_tasks` 数量、信号量等待次数、batch 聚合耗时

---

## 五、优先级汇总

| 优先级 | 项目                         | 类型     | 说明                                   |
|--------|------------------------------|----------|----------------------------------------|
| P0     | 跨 loop 的 publish_inbound   | 稳定性   | 可能导致 announce 失败或未定义行为     |
| P0     | Daemon 线程与资源清理        | 稳定性   | 进程退出时可能泄漏信号量/任务状态     |
| P1     | SessionManager 并发校验      | 稳定性   | 确认锁覆盖所有 subagent_results 访问  |
| P1     | Batch 聚合原子性             | 稳定性   | 避免重复交付或漏交付                 |
| P2     | 信号量等待策略可配置         | 性能     | 减少长时间阻塞                       |
| P2     | 工具预创建                   | 性能     | 降低首次 spawn 延迟                  |
| P3     | 统一结果交付接口             | 架构     | 简化维护与扩展                       |
| P3     | 可观测性增强                 | 运维     | 便于排查问题与性能调优               |

---

## 六、实施记录（已完成）

1. **P0**：`MessageBus.put_inbound_threadsafe` + `set_main_loop`，子 Agent 使用 `put_inbound_threadsafe` 替代 `await publish_inbound`。
2. **P0**：`SubagentManager.shutdown_for_close()`，进程关闭时补偿释放信号量；`AgentLoop.close()` 调用之。
3. **P1**：SessionManager 已确认 per-key 锁覆盖 `subagent_results` 读写，补充注释说明。
4. **P1**：`_pop_batch_if_last` 原子判断并弹出，替换所有 `_is_last_in_batch` + `pop` 调用点。
5. **P2–P3**：按需实施性能与架构优化。
