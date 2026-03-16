# AgentLoop 微内核 - 第四轮代码审查报告

**审查日期**: 2026-03-16  
**范围**: nanobot/agentloop 全量实现（第三轮修复后）

---

## 一、高优先级问题

### 1. DrafterAgent / CriticAgent 对 None 未防护

**位置**: `capabilities/agents/drafter.py` L15-19, `capabilities/agents/critic.py` L15-19

**问题**: `artifacts.get("plan_v1", {})` 在 key 存在且值为 `None` 时返回 `None`（default 仅对 key 缺失生效）。`plan.get("goal", ...)` 会触发 `AttributeError`。build_context 虽已将 None 替换为 `{}`，但若未来有路径绕过（或能力层直接接收原始 context），仍存在风险。为保持能力层防御式编程一致性，建议与 FinalReducer 一样使用 `or {}`。

**DrafterAgent**:
```python
plan = artifacts.get("plan_v1", {})      # 若 plan_v1 为 None 则得到 None
evidence = artifacts.get("evidence_bundle_v1", {})  # 同上
goal = plan.get("goal", ...)   # plan 为 None 时崩溃
```

**CriticAgent**:
```python
evidence = artifacts.get("evidence_bundle_v1", {})  # 若为 None 则 evidence.get("items") 崩溃
```

**建议修复**:
```python
plan = artifacts.get("plan_v1") or {}
evidence = artifacts.get("evidence_bundle_v1") or {}
```

---

## 二、中优先级问题

### 2. run_until_done 超时后 trace 状态未更新

**位置**: `kernel.py` L52-55

**问题**: 超时时仅设置 `shutdown=True` 并 break，trace 仍为 RUNNING。调用方通过返回值 `False` 可知超时，但数据库状态与「实际已停止」不一致，排查时易混淆。

**建议**: 超时时可选将 trace 更新为 `CANCELED` 或新增 `TIMEOUT` 状态；或至少在文档中说明「超时后 trace 保持 RUNNING，需调用方自行处理」。

---

### 3. lease_one_ready_task 未按 trace 过滤

**位置**: `kernel.py` `run_until_done`, `task_repo.py` `lease_one_ready_task`

**说明**: Worker 领取任意 trace 的 READY 任务。单 trace 场景无影响；多 trace 并发时，`run_until_done(trace_A)` 的 worker 可能执行 trace_B 的任务。设计上属共享 worker 池，但若需「仅处理指定 trace」的语义，需在 lease 条件中增加 `trace_id = ?`。

**建议**: 若产品要求 trace 隔离，在 `run_until_done` 中传入 trace_id 并让 `lease_one_ready_task` 支持可选 trace_id 过滤；否则在文档中明确「worker 池全局共享」。

---

### 4. add_read_dep 未在事务内调用

**位置**: `task_repo.py` `mark_task_waiting_artifacts` L237, `fulfill_pending_deps_for_artifact` L265

**说明**: `add_read_dep` 内部直接 `conn.execute`，无显式事务。调用方（`mark_task_waiting_artifacts`、`fulfill_pending_deps_for_artifact`）已处于 `with tx(conn)` 中，因此实际在事务内执行，无问题。但 `add_read_dep` 的 docstring 未说明「应由调用方保证事务上下文」，可补充注释。

---

## 三、低优先级 / 建议

### 5. WAITING_ARTIFACTS 的 artifact_id 来源

**说明**: `wait_for_artifacts` 要求 capability 返回 artifact_id 列表。artifact_id 在 `create_artifact` 时生成，capability 通常无法预先得知「尚未创建的」artifact 的 ID。当前 pending_deps 机制适用于：artifact 已存在（直接加 dep）或 capability 通过某种约定获得「未来」ID 的场景。若设计上 capability 只能表达「等待某类型 artifact」，则需扩展为按 artifact_type 匹配，而非 artifact_id。

**建议**: 在技术方案或 capability 开发文档中说明 `wait_for_artifacts` 的适用场景与 ID 来源约定。

---

### 6. agentloop_task_pending_deps 孤儿记录

**说明**: 若任务被取消或 trace 被删除，`agentloop_task_pending_deps` 中对应记录可能成为孤儿。当前无任务/trace 删除逻辑，暂无影响。若未来支持 cancel，需在删除任务时同步清理 `agentloop_task_pending_deps`。

---

### 7. 错误码风格

**位置**: `runtime.py` L313

**说明**: `handle_task_exception` 使用 `"FAILED"`，能力返回失败使用 `result.error_code or "UNKNOWN"`。建议统一为 `TASK_EXCEPTION` 等更具区分度的错误码，便于监控与排查。

---

## 四、已修复项（前三轮）

- Trace FAILED 传播
- CLI 结果查询兼容 FILE 存储
- mark_task_running 空值检查
- try_finish_trace 死代码删除
- dep_repo 未使用 tx 导入
- WAITING_ARTIFACTS 依赖持久化（含 pending_deps）
- build_context None 防护
- RetrieverGroupReducer / FinalReducer None 防护
- CLI conn 关闭
- 重试条件注释

---

## 五、审查结论

| 优先级 | 数量 | 建议 |
|--------|------|------|
| 高 | 1 | DrafterAgent / CriticAgent 增加 `or {}` 防护 |
| 中 | 3 | 超时 trace 状态、lease trace 过滤、add_read_dep 事务说明 |
| 低 | 3 | wait_for_artifacts 设计说明、孤儿记录、错误码风格 |

剩余问题以防御式编程与文档完善为主，无阻塞性逻辑缺陷。
