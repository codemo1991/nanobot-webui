# 主 AgentLoop + 微内核 集成优化方案

> 版本：2.0（综合修订版）  
> 参考：agentloop_microkernel_revised_design、main-agent-microkernel-integration-plan、main-agent-microkernel-technical-spec

---

## 1. 方案概述

### 1.1 设计目标

- 主 AgentLoop 作为调度器，轻量任务直接处理，复杂任务**异步升级**到微内核
- 微内核与主 Agent **能力对等**，通过 **Tool Gateway** 共享执行后端
- 使用 **ArtifactSeed 列表**（非 dict）传递 initial_artifacts，避免类型覆盖和身份不清
- 使用 **StepLedger** 替代简单 preview，为 planner 提供完整上下文
- **DB 驱动的持久化 Runner**，替代 `asyncio.create_task`，支持 lease 恢复
- **单写者 Notifier**，避免 session/bus 多写竞态
- 升级阈值**可配置**，支持「工具次数」或「复杂度分数」两种模式

### 1.2 核心原则

| 原则 | 说明 |
|------|------|
| **只保留异步升级** | 不再混用同步 handoff 和异步执行，产品语义统一 |
| **DB 驱动执行** | Runner 通过 poll + lease 从 SQLite 领取任务，可恢复 |
| **ArtifactSeed 列表** | initial_artifacts 为 `list[ArtifactSeed]`，每项有 artifact_id、key、content_hash |
| **StepLedger** | attempted_steps 为 `list[StepLedger]`，含 status、artifact_refs、idempotency_key |
| **Tool Gateway** | 微内核通过 Tool Gateway 调用工具后端，不直接复用主 Agent 的 Tool 包装层 |
| **单写者 Notifier** | 微内核只写 notifications 表，由 Notifier 统一投递 session/bus |
| **阈值可配置** | 支持简单次数阈值或复杂度分数，配置化 |

---

## 2. 架构设计

### 2.1 总体架构

```text
+----------------------+
|      Main Agent      |
|----------------------|
| dialog loop          |
| tool steps           |
| escalation judge     |
+----------+-----------+
           |
           | submit_to_microkernel(SubmitRequest)
           v
+----------------------+
|   SubmitService      |
|----------------------|
| 只写 DB，不执行      |
+----------+-----------+
           |
           v
+----------------------+
|   SQLite DB Layer    |
|----------------------|
| traces               |
| tasks                |
| artifacts            |
| task_artifact_deps   |
| step_ledger          |
| events               |
| notifications        |
+----------+-----------+
           |
           | poll + lease
           v
+----------------------+
|  Kernel Runner(s)     |
|----------------------|
| 独立进程/协程轮询    |
| claim → execute      |
| lease 超时可恢复     |
+----+-------------+---+
     |             |
     v             v
+---------+   +---------------+
| Agent   |   | Tool Gateway  |
| Worker  |   | 共享执行后端  |
+---------+   +---------------+
                    |
                    v
             Existing tool backend

+----------------------+
|      Notifier        |
|----------------------|
| 单写者               |
| 读 PENDING 通知      |
| 投递 session/bus     |
| 标记 SENT            |
+----------------------+
```

### 2.2 与旧版关键差异

| 项目 | 旧版 | 优化版 |
|------|------|--------|
| 后台执行 | `asyncio.create_task` | DB 驱动 Runner（poll + lease） |
| initial_artifacts | dict，类型覆盖 | `list[ArtifactSeed]`，每项有 key、hash |
| attempted_steps | `{name, result_preview}` | `StepLedger`，含 status、artifact_refs |
| Tool 复用 | Capability 直接包装 Tool | Tool Gateway 共享执行后端 |
| 通知 | kernel/session/bus 多写 | Notifier 单写者 |
| 升级阈值 | 仅工具次数 | 可配置：次数 或 复杂度分数 |

---

## 3. 统一接口定义

### 3.1 提交请求

```python
from dataclasses import dataclass, field
from typing import Any, Literal

StepStatus = Literal["DONE", "FAILED", "PARTIAL", "TIMEOUT", "CANCELED"]
ArtifactStatus = Literal["READY", "PARTIAL", "INVALID"]

@dataclass
class StepLedger:
    """主 Agent 已执行步骤的完整记录，供 Planner 消费。"""
    step_id: str
    name: str
    args_json: dict[str, Any]
    status: StepStatus
    artifact_refs: list[str] = field(default_factory=list)  # 产出的 artifact_id 列表
    error_code: str | None = None
    error_message: str | None = None
    idempotency_key: str | None = None
    started_at_ms: int | None = None
    finished_at_ms: int | None = None

@dataclass
class ArtifactSeed:
    """主 Agent 已产出的结果，避免微内核重复执行。"""
    artifact_id: str
    artifact_type: str
    key: str  # 如 "read_file:/docs/a.md"，用于去重和引用
    payload_json: dict[str, Any]
    content_hash: str
    status: ArtifactStatus = "READY"
    is_partial: bool = False
    source: str = "main_agent"
    dedupe_key: str | None = None

@dataclass
class OriginRef:
    session_id: str
    channel: str
    user_id: str | None = None
    message_id: str | None = None

@dataclass
class SubmitRequest:
    goal: str
    origin: OriginRef
    attempted_steps: list[StepLedger]
    initial_artifacts: list[ArtifactSeed]
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 3.2 返回句柄

```python
@dataclass
class JobHandle:
    trace_id: str
    root_task_id: str
    status: str
```

---

## 4. 配置设计

### 4.1 新增配置项

```python
# nanobot/config/schema.py - AgentDefaults

# 微内核委托配置
microkernel_escalation_enabled: bool = False
microkernel_escalation_mode: str = "count"  # "count" | "score"
microkernel_escalation_threshold: int = 10   # count 模式：工具调用次数
microkernel_escalation_score_threshold: int = 10  # score 模式：复杂度分数
microkernel_timeout_seconds: float = 120.0
microkernel_runner_poll_interval_ms: int = 500
microkernel_lease_seconds: int = 30
```

### 4.2 复杂度分数（可选，替代简单次数）

```python
def calc_escalation_score(ctx) -> int:
    """可配置的复杂度分数，替代单一工具次数。"""
    return (
        ctx.tool_calls * 1
        + ctx.unique_tools * 2
        + ctx.repeated_failures * 3
        + ctx.spawn_requests * 4
        + ctx.timeout_count * 5
        + (3 if ctx.has_partial_artifacts and not ctx.converging else 0)
    )
```

建议阈值：`score < 6` 继续主 Agent；`6 <= score < 10` 可提示；`score >= 10` 自动升级。

---

## 5. 数据库模型

### 5.1 与现有 agentloop 表的兼容

在现有 `agentloop_*` 表基础上，**新增**以下表（或扩展）：

| 表 | 用途 |
|----|------|
| step_ledger | 主 Agent 已执行步骤，Planner 输入 |
| notifications | 单写者通知队列，Notifier 消费 |

### 5.2 step_ledger

```sql
CREATE TABLE IF NOT EXISTS agentloop_step_ledger (
    step_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    task_id TEXT,
    actor TEXT NOT NULL DEFAULT 'main_agent',
    name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    status TEXT NOT NULL,
    artifact_refs_json TEXT NOT NULL DEFAULT '[]',
    error_code TEXT,
    error_message TEXT,
    idempotency_key TEXT,
    started_at_ms INTEGER,
    finished_at_ms INTEGER,
    created_at_ms INTEGER NOT NULL,
    FOREIGN KEY(trace_id) REFERENCES agentloop_traces(trace_id)
);
```

### 5.3 notifications

```sql
CREATE TABLE IF NOT EXISTS agentloop_notifications (
    notification_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    payload_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY(trace_id) REFERENCES agentloop_traces(trace_id)
);
CREATE INDEX IF NOT EXISTS idx_agentloop_notifications_pending
ON agentloop_notifications(status, created_at_ms) WHERE status = 'PENDING';
```

### 5.4 artifacts 扩展

现有 `agentloop_artifacts` 需支持：
- `artifact_key`：唯一标识，如 `read_file:/docs/a.md`
- `content_hash`：用于去重
- `source`：`main_agent` | `microkernel`

若现有表无这些字段，可通过 migration 添加，或使用 `metadata_json` 存储。

---

## 6. 主 AgentLoop 侧实现

### 6.1 构建 SubmitRequest

```python
def _build_submit_request(
    self,
    goal: str,
    tool_steps: list[dict],
    origin_channel: str,
    origin_chat_id: str,
) -> SubmitRequest:
    """从 tool_steps 构建 StepLedger 和 ArtifactSeed。"""
    ledgers = []
    seeds = []

    for i, step in enumerate(tool_steps):
        step_id = f"step_{uuid.uuid4().hex[:12]}"
        args = step.get("arguments", {}) or {}
        result = step.get("result", "")
        name = step.get("name", "")

        # StepLedger
        status = "DONE" if result else "FAILED"
        artifact_refs = []
        if name == "read_file" and result:
            art_id = f"ar_{uuid.uuid4().hex[:12]}"
            artifact_refs.append(art_id)
            path = args.get("path", "")
            content = str(result)[:50000]
            seeds.append(ArtifactSeed(
                artifact_id=art_id,
                artifact_type="doc_content_v1",
                key=f"read_file:{path}",
                payload_json={"path": path, "content": content},
                content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                status="READY",
                source="main_agent",
                dedupe_key=f"read_file:{path}",
            ))
        # ... 其他工具类型类似

        ledgers.append(StepLedger(
            step_id=step_id,
            name=name,
            args_json=args,
            status=status,
            artifact_refs=artifact_refs,
        ))

    return SubmitRequest(
        goal=goal,
        origin=OriginRef(session_id=f"{origin_channel}:{origin_chat_id}", channel=origin_channel),
        attempted_steps=ledgers,
        initial_artifacts=seeds,
        metadata={"complexity_score": self._calc_escalation_score(tool_steps)},
    )
```

### 6.2 提交服务（只写 DB）

```python
# 主 Agent 调用
def submit_to_microkernel(self, req: SubmitRequest) -> JobHandle:
    """只写 DB，不启动任何后台执行。Runner 独立轮询。"""
    return self.submit_service.submit(req)
```

**SubmitService 不做**：
- 不 `asyncio.create_task`
- 不 `kernel.run_until_done`
- 只 insert trace / root_task / step_ledger / artifacts / events

### 6.3 阈值检查（可配置）

```python
# 次数模式
if self.microkernel_escalation_mode == "count":
    trigger = len(tool_steps) >= self.microkernel_escalation_threshold

# 分数模式
elif self.microkernel_escalation_mode == "score":
    score = self._calc_escalation_score(tool_steps)
    trigger = score >= self.microkernel_escalation_score_threshold

if self.microkernel_escalation_enabled and trigger and response.has_tool_calls:
    req = self._build_submit_request(goal, tool_steps, msg.channel, msg.chat_id)
    handle = self.submit_to_microkernel(req)
    final_content = f"✅ 任务已进入深度处理 (trace_id: {handle.trace_id})，完成后将通知你。"
    break
```

---

## 7. Runner 设计

### 7.1 独立轮询

Runner 作为**独立进程或协程**，与主 Agent 解耦：

```python
class KernelRunner:
    """DB 驱动的任务执行器。"""
    def __init__(self, db_path: Path, registry: CapabilityRegistry, tool_gateway: ToolGateway):
        self.conn = sqlite3.connect(db_path)
        self.registry = registry
        self.tool_gateway = tool_gateway
        self.runner_id = f"runner:{socket.gethostname()}:{os.getpid()}"

    def run_loop(self):
        while True:
            if self.claim_and_run_once():
                continue
            time.sleep(self.poll_interval_ms / 1000.0)
```

### 7.2 认领规则

- 候选：`state IN ('READY', 'LEASED')` 且 `lease_until_ms < now`（若 LEASED）
- 原子 UPDATE：只有 rowcount=1 才认为 claim 成功
- lease 超时后其他 Runner 可抢占，实现恢复

---

## 8. Tool Gateway

### 8.1 设计目标

- 主 Agent 和微内核**共享执行后端**，不共享对话层
- 微内核 Capability 通过 Tool Gateway 调用，便于审计、幂等、side_effect 控制

### 8.2 接口

```python
class ToolGateway:
    def __init__(self, workspace: Path, exec_config, filesystem_config, ...):
        self._backends = {}  # tool_name -> backend instance

    def call(
        self,
        tool_name: str,
        args: dict,
        *,
        actor: str,
        trace_id: str,
        task_id: str,
        side_effect_policy: str = "allow",
    ) -> dict:
        backend = self._get_backend(tool_name)
        return backend.execute(args, actor=actor, trace_id=trace_id, task_id=task_id)
```

### 8.3 能力对等

微内核 Capability 通过 Tool Gateway 调用：
- read_file_tool → ToolGateway.call("read_file", {"path": ...})
- exec_tool → ToolGateway.call("exec", {"command": ...})
- spawn_agent → ToolGateway.call("spawn", {...}) 或 SubagentManager.run_inline

---

## 9. 单写者 Notifier

### 9.1 原则

微内核**不直接**写 session、不直接 push bus。

微内核只做：
- 更新 trace 状态
- 写 final artifact
- **INSERT INTO notifications** (status=PENDING)

Notifier 负责：
- SELECT 一条 PENDING
- 生成用户可见摘要
- 投递 session.add_message + bus.push
- UPDATE notifications SET status='SENT'

### 9.2 触发时机

当 trace 状态变为 DONE 或 FAILED 时，Runner 插入一条 notification：

```python
# Runner 在 trace 完成时
conn.execute("""
    INSERT INTO agentloop_notifications (notification_id, trace_id, kind, status, payload_json, created_at_ms, updated_at_ms)
    VALUES (?, ?, 'FINAL_RESULT', 'PENDING', ?, ?, ?)
""", (notification_id, trace_id, json.dumps({"goal": goal, "final_text": final_text}), ts, ts))
```

### 9.3 Notifier 实现

```python
class MicrokernelNotifier:
    def send_pending_once(self) -> bool:
        row = self.conn.execute("""
            SELECT notification_id, trace_id, payload_json
            FROM agentloop_notifications
            WHERE status = 'PENDING'
            ORDER BY created_at_ms ASC
            LIMIT 1
        """).fetchone()
        if not row:
            return False
        # 投递 session + bus
        # UPDATE status = 'SENT'
        return True
```

---

## 10. 实现清单（按阶段）

### Phase 1：DB + SubmitService

- [ ] 新增 agentloop_step_ledger、agentloop_notifications 表
- [ ] 扩展 agentloop_artifacts（artifact_key、content_hash、source）
- [ ] SubmitService.submit_to_microkernel：只写 DB
- [ ] 主 Agent：_build_submit_request（StepLedger、ArtifactSeed）
- [ ] 主 Agent：阈值检查 + 调用 SubmitService

### Phase 2：Runner + Tool Gateway

- [ ] KernelRunner：poll、claim、execute、lease 恢复
- [ ] Tool Gateway：封装现有 Tool 执行逻辑
- [ ] 补齐微内核 Capability（通过 Tool Gateway）
- [ ] Runner 与 gateway 进程/主进程的集成方式

### Phase 3：Notifier + 通知

- [ ] MicrokernelNotifier：读 PENDING、投递、标记 SENT
- [ ] Notifier 与 Web SSE、飞书等渠道的对接
- [ ] trace 完成时插入 notification 的逻辑

### Phase 4：Planner 增强

- [ ] Planner 读取 step_ledger、artifacts
- [ ] 根据 initial_artifacts 跳过已完成的步骤
- [ ] 动态 spawn 逻辑

---

## 11. 附录

### A. 配置完整示例

```yaml
agents:
  defaults:
    microkernel_escalation_enabled: true
    microkernel_escalation_mode: "count"  # 或 "score"
    microkernel_escalation_threshold: 10
    microkernel_escalation_score_threshold: 10
    microkernel_timeout_seconds: 120
    microkernel_runner_poll_interval_ms: 500
    microkernel_lease_seconds: 30
```

### B. 三版方案对比

| 项目 | integration-plan | technical-spec | 本优化版 |
|------|------------------|----------------|----------|
| 后台执行 | asyncio.create_task | asyncio.create_task | DB Runner |
| initial_artifacts | dict | dict | list[ArtifactSeed] |
| attempted_steps | {name, preview} | {name, preview} | StepLedger |
| Tool 复用 | Capability 包装 Tool | Capability 包装 Tool | Tool Gateway |
| 通知 | 多写 session+bus | 多写 session+bus | 单写者 Notifier |
| 阈值 | 次数 | 次数 | 次数 或 分数 |

### C. 关键文件路径

| 模块 | 路径 |
|------|------|
| 配置 | nanobot/config/schema.py |
| SubmitService | nanobot/agentloop/submit_service.py |
| KernelRunner | nanobot/agentloop/runner.py |
| ToolGateway | nanobot/agentloop/tool_gateway.py |
| Notifier | nanobot/agentloop/notifier.py |
| 主 Agent 集成 | nanobot/agent/loop.py |
