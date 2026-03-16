# 主 AgentLoop + 微内核 集成方案（细化版）

## 0. 设计原则：能力对等

**微内核与主 AgentLoop 具备相同能力**，确保委托后能无缝继续执行任意任务。

### 0.1 能力清单（对等）

| 主 AgentLoop 工具 | 微内核 Capability | 实现方式 |
|------------------|-------------------|----------|
| read_file | read_file_tool | 共享主 agent 的 ReadFileTool 逻辑 |
| write_file | write_file_tool | 共享 WriteFileTool |
| edit_file | edit_file_tool | 共享 EditFileTool |
| list_dir | list_dir_tool | 共享 ListDirTool |
| exec | exec_tool | 共享 ExecTool |
| web_search | web_search_tool | 共享 WebSearchTool |
| web_fetch | web_fetch_tool | 共享 WebFetchTool |
| remember | remember_tool | 共享 RememberTool |
| spawn | spawn_agent | 共享 SubagentManager（需 run_inline 模式） |
| cron | cron_tool | 共享 CronTool |
| get_subagent_results | get_subagent_results_tool | 共享 GetSubagentResultsTool |
| search_tool | search_tool | 已有 |
| planner_agent | planner_agent | 已有（需增强为动态 planner） |
| drafter_agent | drafter_agent | 已有 |
| critic_agent | critic_agent | 已有 |
| reducers | reducers | 已有 |

### 0.2 共享实现架构

```
主 AgentLoop 的 Tool 实现         微内核 Capability
(nanobot.agent.tools.*)          (nanobot.agentloop.capabilities.tools.*)
        │                                    │
        └────────────────┬───────────────────┘
                         │
                  Capability 内部调用 Tool
                  或抽取公共 ToolRunner 供两者使用
```

- **方案 A**：微内核 Capability 的 `invoke()` 内部实例化并调用主 agent 的 Tool
- **方案 B**：抽取 `nanobot.tools` 公共层，主 agent 与微内核均依赖该层

### 0.3 效果

- 委托后微内核可执行 read_file、spawn、exec 等任意步骤
- 通过 `initial_artifacts` 传递主 agent 已产出结果，避免重复执行
- 新增工具时只需在一处实现，微内核通过共享获得能力

---

## 1. 架构总览

```
用户消息
    │
    ▼
主 AgentLoop（调度器）
    │
    ├─ 轻量任务（~90%）→ 直接处理（LLM + 工具）
    │
    └─ 复杂任务（~10%）→ delegate_to_microkernel(goal)
                            │
                            ├─ 立即返回："任务已提交，执行中..."
                            │
                            └─ 微内核异步执行
                                  │
                                  └─ 完成后 → 结果汇总 → 写入消息记录 → 通知用户
```

---

## 2. 示例任务：「先读文档，再调用 claude code，再推送 github」

### 2.1 任务类型判断

该任务属于**多步链式任务**（有依赖顺序）：
- 步骤 1：读文档 → 产出文档内容
- 步骤 2：claude code（依赖步骤 1 的输出）
- 步骤 3：git push（依赖步骤 2 的代码产出）

**两种处理路径**：

| 路径 | 适用条件 | 执行方 |
|------|----------|--------|
| **主 AgentLoop** | 主 agent 判断为「可自行完成」 | read_file → spawn(coder) → exec(git push) |
| **微内核** | 主 agent 判断为「需编排的复杂任务」 | planner 动态 spawn read_file → coder → exec 链 |

> 采用**能力对等**设计：微内核需具备与主 agent 相同的 read_file、spawn、exec 等能力，通过共享实现避免重复开发。

### 2.2 主 AgentLoop 处理时的流程（当前即可支持）

```
用户: "先读文档，再调用claude code,再推送github"
    │
    ▼
主 AgentLoop
    ├─ 1. read_file(doc_path)  → 得到文档内容
    ├─ 2. spawn(template="claude-coder", task="根据文档实现...")  → 等待完成
    ├─ 3. exec("git add . && git commit -m '...' && git push")
    └─ 4. 合成回复 → session.add_message("assistant", 最终结果)
```

**消息记录**：用户消息 + 主 agent 的最终回复（含工具调用过程）都会写入 session。

### 2.3 微内核处理时的流程（需扩展能力后）

```
用户: "先读文档，再调用claude code,再推送github"
    │
    ▼
主 AgentLoop
    └─ delegate_to_microkernel(goal="先读文档，再调用claude code,再推送github")
        │
        ├─ 立即返回: "✅ 复杂任务已提交 (trace_id: tr_xxx)，执行完成后将通知你"
        │
        └─ session.add_message("assistant", "任务已提交...")
        │
        ▼
微内核（后台异步）
    │
    ├─ Root → Planner
    │   └─ Planner 解析 goal，spawn 链式任务:
    │       ├─ read_file_task (path 从 goal 或上下文推断)
    │       ├─ coder_task (input_schema="doc_content_v1", 依赖 read_file)
    │       └─ exec_task (input_schema="code_result_v1", 依赖 coder)
    │
    ├─ 执行链:
    │   read_file → artifact: doc_content_v1
    │       → coder 消费 doc_content_v1 → artifact: code_result_v1
    │           → exec 消费 code_result_v1 → git push
    │
    └─ 链式结果汇总:
        └─ ChainReducer 或最后一步产出作为 final_result
```

---

## 3. 结果汇总

### 3.1 微内核内部汇总

| 汇总点 | 说明 |
|--------|------|
| **RetrieverGroupReducer** | 多路 search 结果 → evidence_bundle |
| **FinalReducer** | plan + evidence + draft + critique → final_result |

### 3.2 链式任务汇总（需新增）

对于「读文档 → claude code → push」这类链式任务：

- **方案 A**：最后一步（exec）的产出作为 final_result
- **方案 B**：新增 **ChainReducer**，将各步骤产出串成结构化报告：
  ```json
  {
    "steps": [
      {"step": "read_file", "result": "文档内容摘要"},
      {"step": "claude_code", "result": "代码实现摘要"},
      {"step": "exec", "result": "git push 成功"}
    ],
    "final_text": "已根据文档完成实现并推送至 GitHub。"
  }
  ```

### 3.3 微内核完成后的「对外汇总」

微内核完成后，需要将结果**回传给主 AgentLoop 和用户**：

- 从 `final_result_v1` 或 ChainReducer 产出中提取 `final_text`
- 可选：附带步骤摘要（供 Web UI 展示）
- 写入 session 消息记录
- 通过 SubagentProgressBus 推送给 Web UI

---

## 4. 消息记录

### 4.1 目标

微内核的**执行结果**应出现在用户可见的**会话消息记录**中，与 spawn 行为一致。

### 4.2 记录时机与内容

| 时机 | 记录内容 | 存储位置 |
|------|----------|----------|
| **主 agent 提交任务** | "✅ 复杂任务已提交 (trace_id: tr_xxx)，执行完成后将通知你" | session.add_message("assistant", ...) |
| **微内核执行中** | （可选）进度事件：microkernel_progress | SubagentProgressBus → Web SSE |
| **微内核完成** | 汇总后的最终结果 | session.add_message("assistant", final_result) |

### 4.3 实现方式（复用 spawn 模式）

```python
# 微内核完成时
async def _on_microkernel_done(trace_id, final_result, origin_channel, origin_chat_id):
    origin_key = f"{origin_channel}:{origin_chat_id}"
    
    # 1. 存入 subagent_results（供 get_subagent_results 或后续合成）
    session = sessions.get_or_create(origin_key)
    session.subagent_results[f"microkernel:{trace_id}"] = {
        "label": "微内核任务",
        "task": goal,
        "result": final_result,
        "status": "ok",
        "trace_id": trace_id,
        "timestamp": ...,
    }
    
    # 2. 写入消息记录（用户可见）
    session.add_message(
        role="assistant",
        content=final_result,  # 或格式化的汇总文本
        source="microkernel_summary",
        trace_id=trace_id,
    )
    sessions.save(session)
    
    # 3. 推送 Web SSE（实时更新）
    bus.push(origin_key, {
        "type": "microkernel_end",
        "trace_id": trace_id,
        "result": final_result,
        "status": "ok",
        ...
    })
```

### 4.4 消息记录中的呈现

用户视角的会话流示例：

```
[user] 先读文档，再调用claude code,再推送github

[assistant] ✅ 复杂任务已提交 (trace_id: tr_xxx)，执行完成后将通知你。

--- （微内核执行中，用户可看到进度或等待）---

[assistant] 任务已完成。

**执行摘要**：
1. 已读取文档 xxx.md
2. 已根据文档实现代码并完成 claude code 执行
3. 已成功推送至 GitHub

**详细结果**：...
```

---

## 5. 完整时序图

```
用户                    主 AgentLoop                    微内核
 │                           │                            │
 │  "先读文档再..."          │                            │
 │─────────────────────────>│                            │
 │                           │ 判断：复杂任务              │
 │                           │ delegate_to_microkernel()  │
 │                           │──────────────────────────>│ submit
 │                           │<──────────────────────────│ trace_id
 │                           │                            │
 │  "任务已提交..."           │ 立即返回                    │
 │<─────────────────────────│                            │
 │                           │                            │
 │                           │                    [后台执行]
 │                           │                    planner → read_file
 │                           │                    → coder → exec
 │                           │                    → ChainReducer
 │                           │                            │
 │                           │<──────────────────────────│ 完成 + 结果
 │                           │                            │
 │                           │ session.add_message()     │
 │                           │ bus.push(microkernel_end)  │
 │                           │                            │
 │  "任务已完成。摘要：..."     │                            │
 │<─────────────────────────│ (Web SSE 推送)              │
```

---

## 6. 待实现清单

### 6.1 主 AgentLoop 侧

- [ ] 新增工具 `delegate_to_microkernel(goal, attempted_steps, initial_artifacts)`
- [ ] 实现 `_extract_initial_artifacts(tool_steps)`：从 read_file 等步骤提取可传递的产出
- [ ] 在 system prompt 中补充「何时使用微内核」的规则
- [ ] 注入 `_run_and_notify` 回调（session、bus、sessions）
- [ ] （可选）阈值切换：当 `len(tool_steps) >= N` 且仍有 tool_calls 时，自动委托微内核

### 6.2 微内核侧（能力对等）

- [ ] 支持 provider 注入（用于 planner/drafter/critic 等 LLM 调用）
- [ ] **能力对等**：补齐与主 agent 相同的 Capability（read_file、write_file、exec、spawn、web_search、remember 等），通过共享 Tool 实现
- [ ] Planner 增强：支持动态 spawn（根据 goal + attempted_steps + initial_artifacts 决定任务链）
- [ ] 支持 `kernel.submit(goal, initial_artifacts, attempted_steps)` 上下文传递

### 6.3 通知与记录

- [ ] 实现 `_on_microkernel_done`：写入 session + 推送 bus
- [ ] Web UI 消费 `microkernel_end` 事件并展示
- [ ] 非 Web 渠道：类似 `_announce_result`，直接发消息给用户

---

## 7. 弊端分析

### 7.1 方案本身的弊端

| 弊端 | 说明 |
|------|------|
| **上下文割裂** | （已缓解）通过传递 `initial_artifacts` + `attempted_steps`，微内核可复用主 agent 已产出结果。 |
| **成本叠加** | 主 agent 的 LLM 调用 + 微内核的 planner/drafter/critic 等 = 总 token 消耗增加。 |
| **体验断层** | 用户先看到「任务已提交」，再等微内核完成。若微内核失败，需有兜底逻辑。 |
| **双重决策** | 主 agent 需判断「何时委托」；判断不准会导致过度委托或委托不足。 |

### 7.2 缓解措施

- **上下文传递**：委托时附带 `attempted_steps` 摘要（工具名 + 简要结果），供微内核 planner 参考。
- **阈值切换**：见下文，用「工具调用次数」作为客观指标，减少主观判断。

---

## 8. 阈值切换：工具调用超 N 次则进入微内核

### 8.1 设计思路

在主 AgentLoop 的迭代循环中增加**工具调用次数**统计，当 `len(tool_steps) >= N`（如 10）且本轮仍有 tool_calls、尚未产出最终回复时，**主动中断**并委托微内核，而不是继续主 agent 循环。

### 8.2 触发条件

```
当同时满足：
1. len(tool_steps) >= microkernel_escalation_threshold（默认 10）
2. 本轮 LLM 返回了 tool_calls（说明任务尚未完成）
3. 配置启用阈值切换（microkernel_escalation_enabled = true）
→ 中断主 agent 循环，调用 delegate_to_microkernel(goal, attempted_steps)
```

### 8.3 实现要点

```python
# 在主 agent 循环中，每轮开始或工具执行后检查
MICROKERNEL_ESCALATION_THRESHOLD = 10  # 可配置

if (
    getattr(self, "microkernel_escalation_enabled", False)
    and len(tool_steps) >= MICROKERNEL_ESCALATION_THRESHOLD
    and response.has_tool_calls
):
    # 构建 attempted_steps 摘要（避免传大量原始结果）
    attempted_summary = [
        {"name": s["name"], "result_preview": str(s.get("result", ""))[:200]}
        for s in tool_steps[-10:]  # 最近 10 步
    ]
    # 从 tool_steps 提取可传递的 initial_artifacts（如 read_file 结果）
    initial_artifacts = _extract_initial_artifacts(tool_steps)

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

### 8.4 上下文传递（解决上下文割裂）

委托时传入三类上下文，微内核可无缝继续：

| 参数 | 用途 |
|------|------|
| **attempted_steps** | 已执行的工具及结果摘要，planner 据此跳过已完成步骤 |
| **initial_artifacts** | 主 agent 已产出的结果，直接作为微内核的 READY artifact，避免重复执行 |
| **conversation_summary** | 可选，最近对话摘要，辅助理解用户意图 |

**扩展 kernel.submit 接口**：

```python
trace_id, root_task_id = await kernel.submit(
    user_input=goal,
    initial_artifacts={  # 主 agent 已读的文件内容等
        "doc_content_v1": {"path": "doc.md", "content": "文档全文..."},
    },
    attempted_steps=[
        {"name": "read_file", "result_preview": "文档内容..."},
        {"name": "spawn", "result_preview": "任务已启动..."},
    ],
)
```

**initial_artifacts 处理**：在创建 trace 后，将各 payload 写入 `agentloop_artifacts` 并标记为 READY，后续任务可直接消费。

### 8.5 阈值方案的利弊

| 优点 | 缺点 |
|------|------|
| **自动升级** | 不依赖 LLM 主观判断，减少误判 |
| **保守使用微内核** | 先让主 agent 尝试，只有「明显卡住」时才升级 |
| **可配置** | 阈值、开关均可配置，便于调优 |
| **已有工作可复用** | 通过 attempted_steps 传递，微内核可避免重复 |

| 缺点 | 说明 |
|------|------|
| **前期成本** | 前 N 次工具调用已消耗 token，升级后微内核再跑一遍 |
| **阈值难定** | 10 次可能对某些任务偏大或偏小，需根据业务调参 |
| **语义不清** | 「10 次未完成」可能是任务本身复杂，也可能是主 agent 在绕路 |

### 8.6 建议配置

```yaml
# 示例配置
agents:
  defaults:
    microkernel_escalation_enabled: true
    microkernel_escalation_threshold: 10  # 工具调用次数
```

---

## 9. 总结

| 问题 | 答案 |
|------|------|
| **能力设计？** | **能力对等**：微内核与主 agent 具备相同能力，通过共享 Tool 实现，委托后可无缝继续执行。 |
| **复杂任务有结果汇总吗？** | 有。微内核内部有 FinalReducer；链式任务可增加 ChainReducer。 |
| **「先读文档再 claude code 再 push」如何运作？** | 主 agent 或微内核均可处理；微内核具备 read_file、spawn、exec 等能力。 |
| **微内核消息会记录吗？** | 会。提交时写入「任务已提交」；完成时写入「任务已完成 + 汇总结果」，与 spawn 一致。 |
| **上下文割裂如何解决？** | 传递 `initial_artifacts`（主 agent 已产出结果）+ `attempted_steps`，微内核直接复用。 |
| **有什么弊端？** | 成本叠加、体验断层、双重决策。可通过上下文传递、阈值切换缓解。 |
| **阈值切换可行吗？** | 可行。当工具调用次数 ≥ N（如 10）且仍未完成时，自动委托微内核。 |
