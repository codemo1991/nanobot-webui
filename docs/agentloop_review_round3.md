# AgentLoop 微内核 - 第三轮代码审查报告

**审查日期**: 2026-03-16  
**范围**: nanobot/agentloop 全量实现

---

## 一、高优先级问题

### 1. WAITING_ARTIFACTS 依赖未持久化（逻辑缺陷）

**位置**: `runtime.py` L282-283, `task_repo.py` `mark_task_waiting_artifacts`

**问题**: 当 capability 返回 `WAITING_ARTIFACTS` 且 `wait_for_artifacts=[af1, af2]` 时，仅更新任务状态为 `WAITING_ARTIFACTS`，**未将依赖写入 `agentloop_task_artifact_deps`**。`mark_waiting_artifacts_tasks_ready` 依赖该表查找「等待该 artifact 的任务」，因此 WAITING_ARTIFACTS → READY 的推进永远不会触发。

**影响**: 一旦有 capability 使用 `WAITING_ARTIFACTS`，相关任务将永久阻塞。

**建议修复**:
- 在 `mark_task_waiting_artifacts` 或调用方，对 `wait_for_artifacts` 中**已存在的** artifact_id 调用 `add_read_dep`（需满足 FK：artifact 须已存在）
- 若设计上 `wait_for_artifacts` 可包含尚未创建的 artifact，则需增加「待等待依赖」的持久化（如新表或扩展 task 字段），并在 `create_artifact` 时补齐依赖并推进

---

### 2. build_context 返回 None payload 导致能力层崩溃

**位置**: `runtime.py` `_load_artifact_payload` / `build_context`, `capabilities/reducers.py`

**问题**: `_load_artifact_payload` 在 JSON 解析失败时返回 `None`。`build_context` 将 `None` 放入 `artifacts` / `artifact_list`。能力层（如 `FinalReducer`）使用 `plan = artifacts.get("plan_v1", {})`，当值为 `None` 时，`plan.get("goal", "")` 会触发 `AttributeError`。

**建议修复**:
- 在 `build_context` 中，当 `_load_artifact_payload` 返回 `None` 时，用 `{}` 替代，或
- 在能力层统一使用 `(artifacts.get("plan_v1") or {}).get("goal", "")` 等防御式写法

---

### 3. retriever_group_reducer 对 None 结果未防护

**位置**: `capabilities/reducers.py` RetrieverGroupReducer

**问题**: `search_results` 可能包含 `None`（来自解析失败的 artifact）。`for result in search_results` 后 `result.get("items", [])` 在 `result is None` 时会抛 `AttributeError`。

**建议修复**:
```python
for result in search_results:
    if result is None:
        continue
    for item in result.get("items", []):
        ...
```

---

## 二、中优先级问题

### 4. chat 连接未关闭（资源泄漏）

**位置**: `kernel.py` `create_kernel`, `cli/commands.py` agentloop 命令

**问题**: `create_kernel` 创建的 `conn` 从未关闭。CLI 单次执行后进程退出，影响有限；若嵌入长驻服务，会导致连接泄漏。

**建议**: 在 `Kernel` 增加 `close()`，或在 `run_until_done` 结束后由调用方负责关闭；CLI 中在 `_run()` 末尾调用 `kernel.conn.close()`。

---

### 5. handle_task_exception 重试条件语义

**位置**: `runtime.py` L301

**问题**: `row["attempt_no"] <= row["max_retries"]` 的语义依赖「attempt_no 在 mark_task_running 中已自增」。当 `max_retries=1` 时，第一次失败会重试，第二次失败则标记为 FAILED，逻辑正确，但可读性差。

**建议**: 增加注释说明「attempt_no 表示已执行次数，max_retries 表示允许的重试次数，故 attempt_no <= max_retries 时仍可重试」。

---

### 6. reset_task_for_retry 未重置 attempt_no

**位置**: `task_repo.py` `reset_task_for_retry`

**问题**: 重置为 READY 时未将 `attempt_no` 归零。下次执行时 `mark_task_running` 会执行 `attempt_no + 1`，因此 attempt_no 会持续累加。若设计为「attempt_no 表示总执行次数」则合理；若希望「每次重试从 0 重新计数」则需在 reset 时清零。

**建议**: 确认产品语义后，在文档或注释中明确 attempt_no 含义；若需重置，在 `reset_task_for_retry` 中增加 `attempt_no = 0`。

---

## 三、低优先级 / 建议

### 7. artifact_repo 中 FILE 路径为绝对路径

**位置**: `artifact_repo.py` L36-42

**说明**: `payload_path` 存的是 `str(payload_path)` 的绝对路径。若 workspace 迁移或路径变化，历史 FILE 存储的 artifact 可能无法读取。当前设计可接受，建议在文档中说明「FILE 存储路径与 workspace 绑定，迁移时需同步」。

---

### 8. 并发与连接线程安全

**位置**: `db.py` L17

**说明**: `check_same_thread=False` 允许跨线程使用同一连接。当前 worker 为 asyncio 协程，若未来引入多进程/多线程 worker，需评估连接池或每 worker 独立连接。

---

### 9. 错误码不一致

**位置**: `runtime.py` L309

**问题**: `handle_task_exception` 使用 `error_code="FAILED"`，而 `execute_task` 中能力返回失败时使用 `result.error_code or "UNKNOWN"`。建议统一错误码风格（如 `TASK_EXCEPTION` vs `UNKNOWN`）。

---

## 四、已修复项（前两轮）

- Trace FAILED 传播
- CLI 结果查询兼容 FILE 存储
- mark_task_running 空值检查
- try_finish_trace 死代码删除
- dep_repo 未使用 tx 导入

---

## 五、审查结论

| 优先级 | 数量 | 建议 |
|--------|------|------|
| 高 | 3 | 建议优先修复 WAITING_ARTIFACTS 依赖与 None 防护 |
| 中 | 3 | 资源关闭与语义澄清 |
| 低 | 3 | 文档与风格优化 |

当前无 capability 使用 `WAITING_ARTIFACTS`，因此问题 1 暂未暴露；问题 2、3 在 artifact 解析失败或异常数据时可能触发。
