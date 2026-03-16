# AgentLoop 微内核 - 第六轮代码审查报告

**审查日期**: 2026-03-16  
**范围**: nanobot/agentloop 全量实现（第五轮修复后）

---

## 一、中优先级问题

### 1. _inject_sibling_deps 缺少事务包装

**位置**: `runtime.py` L180-202

**问题**: `_inject_sibling_deps` 在循环中多次调用 `add_read_dep`，但未处于事务中。`add_read_dep` 的 docstring 要求调用方保证事务上下文。当前 `isolation_level=None` 下每次 `execute` 单独提交，若中途某次失败会导致部分依赖已写入、部分未写入，状态不一致。

**建议修复**: 将兄弟依赖注入逻辑包在 `with tx(self.conn, immediate=True)` 中。

---

### 2. FinalReducer 对 artifacts 的 None 防护

**位置**: `capabilities/reducers.py` L48

**问题**: `artifacts = context.get("artifacts", {})` 在 key 存在且值为 `None` 时返回 `None`，后续 `artifacts.get("plan_v1")` 会触发 `AttributeError`。

**建议修复**: `artifacts = context.get("artifacts") or {}`

---

## 二、低优先级 / 建议

### 3. create_artifact payload 类型校验

**位置**: `artifact_repo.py` L25-31

**说明**: `payload` 声明为 `dict`，若 capability 传入 `None` 或非 dict，`json.dumps(payload)` 可能抛错或产生非预期结果。可增加 `if not isinstance(payload, dict): payload = {}` 等防护。

---

### 4. run_until_done 超时与 worker 竞态

**位置**: `kernel.py` L51-56

**说明**: 超时分支中先调用 `mark_trace_canceled` 再 `break`。此时 worker 可能正在执行该 trace 的任务，执行完成后会尝试推进 trace，但 trace 已为 CANCELED。`mark_task_reducing` 等仅更新 `status = 'DONE'` 且无 `AND status = 'RUNNING'` 条件，可能覆盖 CANCELED。需确认 trace 更新逻辑是否应加 `AND status = 'RUNNING'` 条件。

**检查**: `_mark_task_reducing_impl` 中 `UPDATE agentloop_traces SET status = 'DONE' WHERE trace_id = ?` 无 status 条件，会覆盖 CANCELED/FAILED。若超时后某 worker 完成根任务，trace 会从 CANCELED 被改为 DONE。建议增加 `AND status = 'RUNNING'`，避免覆盖已终止的 trace。

---

### 5. add_write_dep 未使用

**位置**: `dep_repo.py` L24-33

**说明**: `add_write_dep` 未被引用，可能为预留接口。可保留并在文档中说明用途，或标注为 TODO。

---

## 三、审查结论

| 优先级 | 数量 | 建议 |
|--------|------|------|
| 中 | 2 | _inject_sibling_deps 事务包装、FinalReducer artifacts 防护 |
| 低 | 3 | payload 校验、trace 更新条件、add_write_dep 说明 |

建议优先修复中优先级两项，以提升一致性与健壮性。
