# 主 AgentLoop + 微内核 集成技术方案

> 版本：1.0  
> 状态：待实现

---

## 1. 方案概述

### 1.1 设计目标

- 主 AgentLoop 作为调度器，轻量任务（~90%）直接处理，复杂任务（~10%）委托微内核
- 微内核与主 Agent 能力对等，委托后可无缝继续执行
- 使用 **initial_artifacts** 减少重复委派，主 Agent 已产出结果直接传递给微内核
- 微内核补齐与主 Agent 相同的工具能力
- 阈值切换**可配置化**，支持按工具调用次数自动委托

### 1.2 核心原则

| 原则 | 说明 |
|------|------|
| **能力对等** | 微内核具备 read_file、exec、spawn 等与主 Agent 相同的能力 |
| **initial_artifacts** | 委托时传递主 Agent 已产出结果，避免微内核重复执行 |
| **阈值可配置** | `microkernel_escalation_enabled`、`microkernel_escalation_threshold` 支持配置 |
| **共享实现** | 微内核 Capability 复用主 Agent 的 Tool 实现，避免重复开发 |

---

## 2. 架构设计

### 2.1 整体流程

```
用户消息
    │
    ▼
主 AgentLoop（调度器）
    │
    ├─ 轻量任务 → 直接处理（LLM + 工具）
    │
    └─ 复杂任务（LLM 判断 或 阈值触发）
            │
            ▼
    delegate_to_microkernel(goal, attempted_steps, initial_artifacts)
            │
            ├─ 立即返回："任务已提交 (trace_id: xxx)"
            │
            └─ 微内核异步执行
                    │
                    └─ 完成后 → 结果汇总 → 写入 session → 推送通知
```

### 2.2 委托触发方式

| 方式 | 说明 | 配置 |
|------|------|------|
| **LLM 判断** | 主 Agent 调用 delegate_to_microkernel 工具 | system prompt 指导 |
| **阈值触发** | 工具调用次数 ≥ N 且仍有 tool_calls 时自动委托 | `microkernel_escalation_*` |

---

## 3. 配置设计

### 3.1 新增配置项

在 `AgentDefaults` 中新增：

```python
# nanobot/config/schema.py - AgentDefaults 新增字段

# 微内核委托配置
microkernel_escalation_enabled: bool = False  # 是否启用阈值切换，默认关闭
microkernel_escalation_threshold: int = 10     # 工具调用次数阈值，超过则委托微内核
microkernel_timeout_seconds: float = 120.0    # 微内核单次执行超时（秒）
```

### 3.2 配置示例

```yaml
# 用户配置示例
agents:
  defaults:
    # 微内核委托
    microkernel_escalation_enabled: true
    microkernel_escalation_threshold: 10
    microkernel_timeout_seconds: 120
```

### 3.3 配置读取

```python
# 主 AgentLoop 初始化时
self.microkernel_escalation_enabled = getattr(
    config.agents.defaults, "microkernel_escalation_enabled", False
)
self.microkernel_escalation_threshold = getattr(
    config.agents.defaults, "microkernel_escalation_threshold", 10
)
```

---

## 4. initial_artifacts 方案

### 4.1 设计目标

减少重复委派：主 Agent 已执行的 read_file、spawn 等步骤的产出，通过 initial_artifacts 直接传递给微内核，微内核无需重复执行。

### 4.2 可提取的 Artifact 类型

| 工具 | artifact_type | payload 结构 |
|------|---------------|--------------|
| read_file | doc_content_v1 | `{"path": str, "content": str}` |
| list_dir | dir_listing_v1 | `{"path": str, "entries": list}` |
| web_search | search_result_v1 | `{"query": str, "items": list}` |
| spawn（已完成） | subagent_result_v1 | `{"template": str, "task": str, "result": str}` |

### 4.3 _extract_initial_artifacts 实现规范

```python
def _extract_initial_artifacts(tool_steps: list[dict]) -> dict[str, dict]:
    """
    从 tool_steps 提取可传递给微内核的 initial_artifacts。
    仅提取「可复用」的产出，避免传递过大 payload。
    """
    artifacts = {}
    for step in tool_steps:
        name = step.get("name", "")
        result = step.get("result", "")
        args = step.get("arguments", {}) or {}

        if name == "read_file" and result:
            path = args.get("path", "")
            # 限制长度，避免 token 爆炸
            content = str(result)[:50000] if result else ""
            artifacts["doc_content_v1"] = {"path": path, "content": content}

        elif name == "list_dir" and result:
            path = args.get("path", "")
            artifacts["dir_listing_v1"] = {"path": path, "entries": _parse_list_dir_result(result)}

        elif name == "web_search" and result:
            query = args.get("query", "")
            artifacts.setdefault("search_result_v1", []).append({"query": query, "raw": str(result)[:2000]})

        # spawn 已完成且结果已注入时，可从 session.subagent_results 获取
        # 此处仅记录 attempted_steps，initial_artifacts 由调用方从 session 补充
    return artifacts
```

### 4.4 大小限制

| 类型 | 单条上限 | 说明 |
|------|----------|------|
| doc_content | 50KB | 超长文件截断 |
| search_result | 2KB/条 | 避免过多搜索结果 |
| 总 initial_artifacts | 100KB | 超限时按优先级保留 |

---

## 5. 主 AgentLoop 侧实现

### 5.1 新增工具：delegate_to_microkernel

**工具定义**：

```python
# nanobot/agent/tools/delegate_microkernel.py

class DelegateMicrokernelTool(Tool):
    name = "delegate_to_microkernel"
    description = (
        "将复杂任务委托给微内核编排执行。适用于：多步链式任务、需要并行检索+起草+批评的任务、"
        "主 Agent 已尝试多次仍未完成的任务。委托后立即返回，微内核在后台执行，完成后会通知用户。"
    )

    # 参数：goal (必填), attempted_steps (可选), initial_artifacts (可选)
```

**工具参数 Schema**：

```json
{
  "goal": "用户目标的完整描述",
  "attempted_steps": [
    {"name": "read_file", "args": {"path": "doc.md"}, "result_preview": "..."}
  ],
  "initial_artifacts": {
    "doc_content_v1": {"path": "doc.md", "content": "..."}
  }
}
```

### 5.2 工具实现逻辑

```python
async def run(self, goal: str, attempted_steps: list | None = None, initial_artifacts: dict | None = None) -> str:
    # 1. 获取 origin（channel, chat_id）
    # 2. 创建 kernel（注入 provider、workspace、sessions 等）
    # 3. trace_id, _ = await kernel.submit(goal, initial_artifacts, attempted_steps)
    # 4. asyncio.create_task(_run_and_notify(kernel, trace_id, goal, origin))
    # 5. return f"✅ 复杂任务已提交 (trace_id: {trace_id})，执行完成后将通知你。"
```

### 5.3 阈值切换逻辑

在主 AgentLoop 的 `_process_message` 循环中，**每轮工具执行后、下一轮 LLM 调用前**检查：

```python
# 位置：tool 执行完成、准备下一轮 iteration 时
if (
    self.microkernel_escalation_enabled
    and len(tool_steps) >= self.microkernel_escalation_threshold
    and response.has_tool_calls
):
    attempted_summary = [
        {"name": s["name"], "result_preview": str(s.get("result", ""))[:200]}
        for s in tool_steps[-self.microkernel_escalation_threshold:]
    ]
    initial_artifacts = self._extract_initial_artifacts(tool_steps)

    trace_id = await self._delegate_to_microkernel(
        goal=current_message or msg.content,
        attempted_steps=attempted_summary,
        initial_artifacts=initial_artifacts,
        origin_channel=msg.channel,
        origin_chat_id=msg.chat_id,
    )
    final_content = f"✅ 任务较复杂，已交由微内核处理 (trace_id: {trace_id})，执行完成后将通知你。"
    break
```

### 5.4 _delegate_to_microkernel 内部实现

```python
async def _delegate_to_microkernel(
    self,
    goal: str,
    attempted_steps: list[dict],
    initial_artifacts: dict[str, dict],
    origin_channel: str,
    origin_chat_id: str,
) -> str:
    kernel = create_kernel(
        workspace=self.workspace,
        provider=self.provider,
        sessions=self.sessions,
        subagent_manager=self.subagents,
        # ... 其他依赖
    )
    trace_id, _ = await kernel.submit(
        user_input=goal,
        initial_artifacts=initial_artifacts,
        attempted_steps=attempted_steps,
    )
    asyncio.create_task(
        _run_kernel_and_notify(
            kernel, trace_id, goal,
            origin_channel, origin_chat_id,
            self.sessions, self._get_bus(),
        )
    )
    return trace_id
```

---

## 6. 微内核侧实现

### 6.1 kernel.submit 接口扩展

```python
# nanobot/agentloop/kernel/kernel.py

async def submit(
    self,
    user_input: str,
    initial_artifacts: dict[str, dict] | None = None,
    attempted_steps: list[dict] | None = None,
    conversation_summary: str | None = None,
) -> tuple[str, str]:
    trace_id, root_task_id = create_trace_and_root_task(
        self.conn,
        user_input=user_input,
        request_payload={
            "user_goal": user_input,
            "attempted_steps": attempted_steps or [],
            "conversation_summary": conversation_summary,
        },
    )
    if initial_artifacts:
        _create_initial_artifacts(self.conn, trace_id, root_task_id, initial_artifacts)
    return trace_id, root_task_id
```

### 6.2 初始 Artifact 创建

```python
def _create_initial_artifacts(conn, trace_id: str, root_task_id: str, artifacts: dict[str, dict]) -> None:
    """将 initial_artifacts 写入 agentloop_artifacts，状态为 READY。"""
    ts = now_ts()
    for artifact_type, payload in artifacts.items():
        artifact_id = new_id("ar")
        conn.execute(
            """
            INSERT INTO agentloop_artifacts(
                artifact_id, trace_id, producer_task_id, artifact_type, version, status,
                storage_kind, payload_text, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, 'READY', 'INLINE', ?, ?, ?)
            """,
            (artifact_id, trace_id, root_task_id, artifact_type, json.dumps(payload), ts, ts),
        )
        fulfill_pending_deps_for_artifact(conn, artifact_id)
        mark_waiting_artifacts_tasks_ready(conn, artifact_id)
```

### 6.3 能力对等：补齐 Capability

| Capability | 实现方式 | 文件路径 |
|------------|----------|----------|
| read_file_tool | 包装 ReadFileTool.run | nanobot/agentloop/capabilities/tools/read_file.py |
| write_file_tool | 包装 WriteFileTool.run | nanobot/agentloop/capabilities/tools/write_file.py |
| edit_file_tool | 包装 EditFileTool.run | ... |
| list_dir_tool | 包装 ListDirTool.run | ... |
| exec_tool | 包装 ExecTool.run | ... |
| web_search_tool | 包装 WebSearchTool.run | ... |
| web_fetch_tool | 包装 WebFetchTool.run | ... |
| remember_tool | 包装 RememberTool.run | ... |
| spawn_agent | 调用 SubagentManager.run_inline | ... |
| cron_tool | 包装 CronTool.run | ... |

**Capability 包装示例**：

```python
# nanobot/agentloop/capabilities/tools/read_file.py

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult
from nanobot.agent.tools.filesystem import ReadFileTool

class ReadFileCapability(Capability):
    name = "read_file_tool"
    kind = "tool"

    def __init__(self, workspace: Path):
        self._tool = ReadFileTool(workspace)

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        path = request.get("path", "")
        result = await self._tool.run(path=path)
        return CapabilityResult(
            status="DONE",
            output_artifact={
                "artifact_type": "doc_content_v1",
                "payload": {"path": path, "content": result},
            },
        )
```

### 6.4 Planner 增强

Planner 需根据 `attempted_steps` 和 `initial_artifacts` 动态决定 spawn 哪些任务：

- 若 `initial_artifacts` 已含 `doc_content_v1`，则**不** spawn read_file
- 若 `attempted_steps` 显示已执行 spawn(coder)，则视情况跳过或等待
- 使用 LLM 解析 goal + context，产出 TaskSpec 列表（或规则 + 模板）

---

## 7. 通知与消息记录

### 7.1 _run_kernel_and_notify

```python
async def _run_kernel_and_notify(
    kernel, trace_id: str, goal: str,
    origin_channel: str, origin_chat_id: str,
    sessions: SessionManager, bus,
):
    try:
        timeout = config.agents.defaults.microkernel_timeout_seconds
        await kernel.run_until_done(trace_id, worker_count=4, timeout_seconds=timeout)
        final_result = _get_final_result(kernel.conn, trace_id)
        await _on_microkernel_done(
            trace_id, goal, final_result,
            origin_channel, origin_chat_id,
            sessions, bus,
        )
    except Exception as e:
        await _on_microkernel_failed(trace_id, goal, str(e), ...)
    finally:
        kernel.conn.close()
```

### 7.2 _on_microkernel_done

```python
async def _on_microkernel_done(
    trace_id, goal, final_result,
    origin_channel, origin_chat_id,
    sessions: SessionManager, bus,
):
    origin_key = f"{origin_channel}:{origin_chat_id}"
    session = sessions.get_or_create(origin_key)

    session.subagent_results[f"microkernel:{trace_id}"] = {
        "label": "微内核任务",
        "task": goal,
        "result": final_result,
        "status": "ok",
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    session.add_message("assistant", final_result, source="microkernel_summary", trace_id=trace_id)
    sessions.save(session)

    bus.push(origin_key, {
        "type": "microkernel_end",
        "trace_id": trace_id,
        "result": final_result,
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
```

### 7.3 非 Web 渠道

类似 spawn 的 `_announce_result`，微内核完成后向主 Agent 发送 InboundMessage，触发综合回复。

---

## 8. 实现清单（按优先级）

### Phase 1：基础委托

- [ ] 配置：AgentDefaults 新增 microkernel_escalation_*
- [ ] 主 Agent：新增 DelegateMicrokernelTool
- [ ] 主 Agent：实现 _delegate_to_microkernel、_run_kernel_and_notify
- [ ] 微内核：kernel.submit 支持 initial_artifacts、attempted_steps
- [ ] 微内核：_create_initial_artifacts
- [ ] 通知：_on_microkernel_done、session + bus

### Phase 2：阈值与 initial_artifacts

- [ ] 主 Agent：_extract_initial_artifacts
- [ ] 主 Agent：阈值检查逻辑（可配置）
- [ ] 委托时传递 attempted_steps、initial_artifacts

### Phase 3：能力对等

- [ ] 微内核：补齐 read_file、write_file、exec、web_search、spawn 等 Capability
- [ ] 共享 Tool 实现（Capability 包装 Tool）
- [ ] SubagentManager.run_inline（spawn 同步模式）

### Phase 4：Planner 增强

- [ ] Planner 读取 attempted_steps、initial_artifacts
- [ ] 动态 spawn 逻辑（跳过已完成的步骤）

---

## 9. 附录

### A. 配置完整示例

```yaml
agents:
  defaults:
    max_tool_iterations: 40
    microkernel_escalation_enabled: true
    microkernel_escalation_threshold: 10
    microkernel_timeout_seconds: 120
```

### B. 关键文件路径

| 模块 | 路径 |
|------|------|
| 配置 | nanobot/config/schema.py |
| 主 Agent 循环 | nanobot/agent/loop.py |
| 委托工具 | nanobot/agent/tools/delegate_microkernel.py |
| 微内核 | nanobot/agentloop/kernel/kernel.py |
| trace 创建 | nanobot/agentloop/kernel/trace_repo.py |
| Capability 注册 | nanobot/agentloop/capabilities/registry.py |

### C. 数据库表

- agentloop_traces
- agentloop_tasks
- agentloop_artifacts
- agentloop_events
- agentloop_task_artifact_deps
- agentloop_task_pending_deps
