# Enhanced Trace System with Self-Evolution Design

**Date**: 2025-03-27
**Scope**: nanobot-webui
**Status**: Design Ready for Implementation

---

## 1. Overview

本设计扩展 nanobot 现有的 trace 系统，实现：

1. **全链路追踪** - 覆盖 Agent → Subagent → Tool 的完整调用链
2. **深度溯源** - 保留足够上下文用于后续分析和诊断
3. **自我进化** - 基于 trace 数据自动沉淀工作指导到 memory

---

## 2. Goals

### 2.1 Primary Goals

| Goal | Description | Success Criteria |
|------|-------------|------------------|
| G1 | 记录完整的调用链路耗时 | 可从 HTTP 请求追踪到单个 tool 执行 |
| G2 | 记录成功率和错误分布 | 按 agent/subagent/tool 维度统计 |
| G3 | 记录 token 消耗 | 每个 LLM call 的 input/output tokens |
| G4 | 记录 tool 调用结果 | 包括参数、返回值、执行时间 |
| G5 | 自动沉淀工作指导 | 异常模式自动写入 self_improve memory |

### 2.2 Secondary Goals

- 保持对现有代码的低侵入性
- 支持异步批量写入，不影响主流程性能
- 提供查询接口用于实时监控

---

## 3. Architecture

### 3.1 High-Level Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                         Request Entry                           │
│                    (HTTP / Telegram / Cron)                     │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Trace Context Manager                      │
│              (trace_context with enhanced attrs)                │
└─────────────────────────────┬───────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
       ┌────────────┐  ┌────────────┐  ┌────────────┐
       │ Agent Loop │  │ Subagent   │  │   Tools    │
       │  Spans     │  │  Spans     │  │   Spans    │
       └──────┬─────┘  └──────┬─────┘  └──────┬─────┘
              │               │               │
              └───────────────┼───────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Unified Trace Emitter                        │
│              (Extended TraceEmitter with enrichment)            │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Trace Data Lake                             │
│              ~/.nanobot/traces/trace_YYYY-MM-DD.jsonl           │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Async Analysis Pipeline                      │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐            │
│  │ Collect │→│ Aggregate│→│ Detect  │→│ Trigger │            │
│  │  Raw    │  │  Stats   │  │ Anomaly │  │ Evolution│            │
│  │  Data   │  │          │  │         │  │          │            │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘            │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              Self-Improving Memory Integration                  │
│       persist_self_improvement(scope=self_improve)              │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Component Breakdown

#### 3.2.1 Trace Context Manager

扩展现有的 `trace_context` 以支持更多属性：

```python
@asynccontextmanager
async def trace_context(
    trace_id: str,
    span_name: str = "agent.turn",
    attrs: dict | None = None,
    # NEW: 增强的属性
    session_meta: dict | None = None,  # 会话级元数据
    enable_subagent_tracing: bool = True,
    enable_tool_tracing: bool = True,
):
```

**新增属性**:
- `session_key`: 会话标识 (已有)
- `channel`: 来源渠道 (telegram/web/cron)
- `user_id`: 用户标识
- `intent`: 用户意图分类
- `parent_trace_id`: 父级 trace（用于子 agent 关联）

#### 3.2.2 Span Types Hierarchy

```
Trace Root (agent.turn)
├── llm.call                     # LLM 调用
│   ├── usage.prompt_tokens
│   ├── usage.completion_tokens
│   ├── usage.total_tokens
│   └── model
├── tool.execute                 # 工具执行
│   ├── tool_name
│   ├── arguments (truncated)
│   ├── result_status
│   ├── result_summary
│   └── execution_time_ms
├── subagent.spawn              # 子 agent 启动
│   ├── subagent_id
│   ├── task_description
│   └── child_trace_id          # 关联子 trace
└── subagent.result             # 子 agent 结果汇总
    ├── num_completed
    ├── num_failed
    └── total_duration_ms
```

#### 3.2.3 Enhanced TraceEmitter

扩展 `nanobot/tracing/emitter.py`：

```python
class TraceEmitter:
    # 现有功能保留...

    # NEW: 实时统计聚合
    def get_live_stats(self, window_seconds: int = 300) -> dict:
        """返回最近 N 秒的实时统计"""
        return {
            "total_requests": int,
            "success_rate": float,
            "avg_latency_ms": float,
            "token_usage": {
                "prompt": int,
                "completion": int,
            },
            "tool_calls": {
                "total": int,
                "by_tool": dict[str, int],
            },
            "error_distribution": dict[str, int],
        }

    # NEW: 按 trace_id 获取完整链路
    def get_trace_chain(self, trace_id: str) -> list[dict]:
        """获取完整的调用链（按 seq 排序）"""
```

### 3.3 Data Schema

#### 3.3.1 Span Record Format

```json
{
  "type": "span",
  "version": "2.0",
  "trace_id": "tr_abc123",
  "span_id": "sp_def456",
  "parent_id": "sp_root789",
  "name": "tool.execute",
  "service": "nanobot",
  "status": "ok",
  "start_ms": 1711545600000,
  "end_ms": 1711545600150,
  "duration_ms": 150,
  "seq": 5,
  "attrs": {
    // Core attributes
    "session_key": "telegram:12345",
    "channel": "telegram",
    "user_id": "user_123",

    // For tool.execute spans
    "tool_name": "read_file",
    "tool_args_hash": "md5:abc...",
    "tool_args_preview": "{\"file_path\": \"...\"}",
    "result_status": "success",
    "result_preview": "file content...",
    "error_type": null,
    "error_msg": null,

    // For llm.call spans
    "model": "anthropic/claude-sonnet-4-6",
    "usage": {
      "prompt_tokens": 1500,
      "completion_tokens": 300,
      "total_tokens": 1800
    },
    "finish_reason": "tool_calls",
    "num_tool_calls": 2,

    // For subagent spans
    "subagent_task_id": "tk_xyz789",
    "subagent_intent": "analyze_code",
    "child_trace_id": "tr_child456",

    // Self-evolution markers
    "evolution_candidate": false,
    "pattern_tags": ["file_io", "quick_read"]
  },
  "events": [
    {"name": "start", "ts_ms": 1711545600000, "attrs": {}},
    {"name": "validation_pass", "ts_ms": 1711545600010, "attrs": {}},
    {"name": "complete", "ts_ms": 1711545600150, "attrs": {}}
  ]
}
```

#### 3.3.2 Trace Summary Record

每天生成一个 summary 文件用于快速查询：

```json
{
  "type": "daily_summary",
  "date": "2025-03-27",
  "aggregated": {
    "total_traces": 150,
    "success_rate": 0.95,
    "avg_duration_ms": 2500,
    "token_usage": {
      "total": 150000,
      "by_model": {
        "claude-sonnet": 100000,
        "gpt-4": 50000
      }
    },
    "tool_usage": {
      "read_file": 300,
      "write_file": 50,
      "exec": 20,
      "web_search": 30
    },
    "error_patterns": [
      {"pattern": "file_not_found", "count": 5, "tool": "read_file"},
      {"pattern": "timeout", "count": 3, "tool": "web_search"}
    ]
  }
}
```

---

## 4. Implementation Details

### 4.1 Phase 1: Enhanced Span Coverage

#### 4.1.1 Tool Call Span Integration

修改 `nanobot/agent/loop.py` 中的工具执行逻辑：

```python
async def _execute_single_tool(self, tool_call, tool_def, span_parent_id: str):
    async with span(
        "tool.execute",
        parent_id=span_parent_id,
        attrs={
            "tool_name": tool_def.name,
            "tool_args_preview": self._truncate_args(tool_call.arguments),
        }
    ) as tool_span:
        try:
            result = await tool_def.execute(**tool_call.arguments)
            tool_span.set_attr("result_status", "success")
            tool_span.set_attr("result_preview", self._truncate_result(result))
            return result
        except Exception as e:
            tool_span.set_attr("result_status", "error")
            tool_span.set_attr("error_type", type(e).__name__)
            tool_span.set_attr("error_msg", str(e)[:200])
            raise
```

#### 4.1.2 Subagent Span Integration

修改 `nanobot/agent/subagent.py` 或 spawn tool：

```python
async def spawn_subagent(self, task_spec: TaskSpec, parent_span_id: str):
    child_trace_id = new_id("tr")

    async with span(
        "subagent.spawn",
        parent_id=parent_span_id,
        attrs={
            "subagent_task_id": task_spec.task_id,
            "subagent_intent": task_spec.intent,
            "child_trace_id": child_trace_id,
        }
    ) as spawn_span:
        # 启动子 agent
        result = await self._run_subagent(task_spec, child_trace_id)

        spawn_span.set_attr("result_status", result.status)
        spawn_span.set_attr("duration_ms", result.duration_ms)
        return result
```

### 4.2 Phase 2: Analysis Pipeline

#### 4.2.1 TraceAnalyzer 组件

```python
class TraceAnalyzer:
    """异步分析 trace 数据，检测异常模式"""

    def __init__(self, emitter: TraceEmitter, memory_store: MemoryStore):
        self.emitter = emitter
        self.memory = memory_store
        self._pattern_db: dict[str, PatternStats] = {}

    async def analyze_window(self, start_ts: int, end_ts: int) -> AnalysisReport:
        """分析指定时间窗口的 trace"""
        spans = self.emitter.query_range(start_ts, end_ts)

        report = AnalysisReport()

        # 1. 统计聚合
        report.stats = self._aggregate_stats(spans)

        # 2. 异常检测
        report.anomalies = self._detect_anomalies(spans)

        # 3. 模式识别
        report.patterns = self._identify_patterns(spans)

        return report

    def _detect_anomalies(self, spans: list[dict]) -> list[Anomaly]:
        """检测异常模式"""
        anomalies = []

        # 检测高耗时 tool
        for span in spans:
            if span["name"] == "tool.execute":
                duration = span.get("duration_ms", 0)
                tool_name = span["attrs"].get("tool_name")

                if self._is_slow_tool(tool_name, duration):
                    anomalies.append(Anomaly(
                        type="slow_tool",
                        severity="warning",
                        description=f"{tool_name} took {duration}ms",
                        context=span,
                    ))

        # 检测重复错误
        error_counts = defaultdict(int)
        for span in spans:
            if span["status"] == "error":
                key = (span["attrs"].get("tool_name"), span["attrs"].get("error_type"))
                error_counts[key] += 1

        for (tool, error), count in error_counts.items():
            if count >= 3:  # 阈值可配置
                anomalies.append(Anomaly(
                    type="repeated_error",
                    severity="critical",
                    description=f"{tool} failed {count} times with {error}",
                ))

        return anomalies
```

#### 4.2.2 Evolution Trigger

```python
class EvolutionTrigger:
    """根据分析结果触发自我进化"""

    TRIGGER_RULES = {
        "repeated_error": {
            "min_occurrences": 3,
            "cooldown_hours": 24,  # 同一模式24小时内只触发一次
        },
        "slow_pattern": {
            "p95_threshold_ms": 5000,
            "min_samples": 10,
        },
        "token_efficiency": {
            "high_token_tool_ratio": 0.5,  # token 消耗超过50%的 tool
        },
    }

    async def evaluate(self, report: AnalysisReport) -> list[EvolutionTask]:
        """评估是否需要触发自我进化"""
        tasks = []

        for anomaly in report.anomalies:
            if self._should_trigger(anomaly):
                tasks.append(EvolutionTask(
                    trigger=anomaly.type,
                    context=anomaly.context,
                    recommended_action=self._suggest_action(anomaly),
                ))

        return tasks

    def _suggest_action(self, anomaly: Anomaly) -> str:
        """建议的改进行动"""
        suggestions = {
            "repeated_error": "分析错误根因，更新 tool 使用指导",
            "slow_tool": "考虑缓存策略或异步化",
            "high_token_usage": "优化 prompt 设计，减少上下文",
        }
        return suggestions.get(anomaly.type, "需要人工审查")
```

### 4.3 Phase 3: Self-Improvement Integration

#### 4.3.1 Memory Writer

```python
class TraceBasedMemoryWriter:
    """将 trace 分析结果写入 self_improve memory"""

    async def write_pattern(self, pattern: Pattern, confidence: float):
        """写入识别的模式"""
        content = self._format_pattern_content(pattern)

        await self.persist_tool.execute(
            content=content,
            source_type="self_improve_pattern",
            source_id=pattern.id,
        )

    async def write_correction(self, anomaly: Anomaly, root_cause: str):
        """写入纠错记录"""
        content = f"""## 问题
{anomaly.description}

## 根因
{root_cause}

## 建议
{anomaly.suggested_fix}
"""
        await self.persist_tool.execute(
            content=content,
            source_type="self_improve_correction",
            source_id=f"corr-{anomaly.id}",
        )

    def _format_pattern_content(self, pattern: Pattern) -> str:
        return f"""## 场景模式识别

**触发条件**: {pattern.trigger_condition}

**观察到的行为**:
{pattern.observed_behavior}

**优化建议**:
{pattern.optimization}

**置信度**: {pattern.confidence}
**出现次数**: {pattern.occurrence_count}
"""
```

#### 4.3.2 触发时机

| 触发方式 | 频率 | 实现位置 |
|---------|------|---------|
| **定时** | 每小时 | cron 任务触发 analyze_window(now-1h, now) |
| **异常** | 实时 | 当 error_rate > 10% 时立即触发 |
| **手动** | 按需 | 用户说「分析今天的性能」时触发 |

---

## 5. API Interface

### 5.1 Query API

```python
# 获取实时统计
GET /api/traces/stats?window=300
Response: {
    "total_requests": 150,
    "success_rate": 0.95,
    "avg_latency_ms": 2500,
    "token_usage": {...},
    "tool_distribution": {...}
}

# 获取完整 trace 链
GET /api/traces/{trace_id}
Response: {
    "trace_id": "tr_abc123",
    "spans": [...],  # 按 seq 排序的完整链路
    "summary": {...}
}

# 查询异常模式
GET /api/traces/anomalies?since=2025-03-27T00:00:00
Response: {
    "anomalies": [...],
    "suggested_actions": [...]
}
```

### 5.2 Self-Improvement Integration

```python
# 触发自我分析（手动）
POST /api/evolution/analyze
Body: {
    "time_range": "last_24h",
    "focus_areas": ["tool_efficiency", "error_patterns"]
}
Response: {
    "analysis_id": "an_123",
    "patterns_found": 3,
    "memories_created": 2
}

# 获取进化建议
GET /api/evolution/suggestions
Response: {
    "suggestions": [
        {
            "type": "pattern",
            "description": "read_file 频繁访问同一文件",
            "recommended_action": "建议增加文件缓存",
            "confidence": 0.85
        }
    ]
}
```

---

## 6. Configuration

```yaml
# config/tracing.yaml
tracing:
  enabled: true
  retention_days: 7
  rotation: "50 MB"

  # 覆盖率配置
  coverage:
    agent: true
    subagent: true
    tool: true
    llm: true

  # 采样配置（高流量时降采样）
  sampling:
    default_rate: 1.0  # 100%
    high_traffic_rate: 0.1  # 10% when > 100 req/min

  # 分析管道配置
  analysis:
    enabled: true
    cron_schedule: "0 * * * *"  # 每小时
    anomaly_thresholds:
      slow_tool_ms: 5000
      repeated_error_count: 3
      error_rate_threshold: 0.1

  # 自我进化配置
  self_evolution:
    enabled: true
    auto_trigger: true
    min_confidence: 0.7
    cooldown_hours: 24
    max_memories_per_day: 10
```

---

## 7. Success Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Trace Coverage | ~30% (仅 LLM) | 95% | 可追踪的调用比例 |
| Query Latency | N/A | < 100ms | p95 查询耗时 |
| Storage Overhead | N/A | < 10% | 相对于原始流量 |
| Pattern Detection | N/A | > 80% | 手动验证的准确率 |
| Memory Creation | Manual | 5-10/day | 自动沉淀的记忆数 |

---

## 8. Risk & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| 性能开销 | 高 | 异步写入 + 批量 flush + 采样 |
| 存储膨胀 | 中 | JSONL 压缩 + 自动清理 + 聚合摘要 |
| 误报模式 | 中 | 人工确认机制 + 置信度阈值 |
| 隐私泄露 | 高 | 参数脱敏 + 敏感字段过滤 |

---

## 9. Appendix

### 9.1 Span Attribute Reference

| Attribute | Type | Description |
|-----------|------|-------------|
| `tool_name` | string | Tool identifier |
| `tool_args_preview` | string | Truncated args (max 500 chars) |
| `tool_args_hash` | string | MD5 of full args for dedup |
| `result_status` | enum | success/error/timeout |
| `result_preview` | string | Truncated result |
| `error_type` | string | Exception class name |
| `error_msg` | string | Truncated error message |
| `usage.*` | object | Token usage from LLM response |
| `subagent_task_id` | string | Task ID for spawned subagent |
| `child_trace_id` | string | Trace ID of subagent |
| `evolution_candidate` | bool | Marked for pattern analysis |

### 9.2 Migration Path

1. **Week 1**: 增强 span 覆盖率（tool + subagent）
2. **Week 2**: 实现分析管道 + 异常检测
3. **Week 3**: 接入 self-improvement 流程
4. **Week 4**: UI 集成 + 调优

---

**Next Step**: 使用 `writing-plans` skill 生成分阶段实现计划。
