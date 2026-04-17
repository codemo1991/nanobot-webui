# Enhanced Trace System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现完整的调用链路追踪系统，覆盖 Agent → Subagent → Tool 全链路，并基于 trace 数据实现自我进化能力

**Architecture:** 扩展现有的 nanobot/tracing 模块，增加 tool/subagent span 支持，通过异步分析管道检测异常模式，最终触发 self-improvement 写入 memory

**Tech Stack:** Python asyncio, JSONL, SQLite (existing), nanobot tracing framework

---

## 1. Overview

本计划分 3 个阶段实现：
- **Phase 1**: 增强 span 覆盖率（tool + subagent 追踪）
- **Phase 2**: Trace 分析管道（统计聚合 + 异常检测）
- **Phase 3**: Self-Improvement 集成

---

## 2. File Structure

```
nanobot/
├── tracing/
│   ├── __init__.py          # 扩展导出
│   ├── spans.py             # 增强 Span 类
│   ├── emitter.py           # 扩展 TraceEmitter
│   ├── context.py           # 扩展 trace_context
│   └── types.py             # NEW: 类型定义和常量
├── analysis/
│   ├── __init__.py          # NEW: 分析模块入口
│   ├── collector.py         # NEW: Trace 数据收集器
│   ├── stats.py             # NEW: 统计聚合器
│   ├── anomaly.py           # NEW: 异常检测器
│   └── triggers.py          # NEW: 进化触发器
├── services/
│   └── trace_service.py      # NEW: Trace 查询服务
└── agent/
    └── loop.py              # MODIFY: 集成 tool span
```

---

## Phase 1: Enhanced Span Coverage

### Task 1: Create Trace Types Module

**Files:**
- Create: `nanobot/tracing/types.py`
- Test: `tests/test_tracing_types.py`

- [ ] **Step 1: Create types.py with constants and enums**

```python
"""Trace 类型定义和常量"""
from enum import Enum
from dataclasses import dataclass
from typing import TypedDict


class SpanType(str, Enum):
    """Span 类型枚举"""
    AGENT_TURN = "agent.turn"
    LLM_CALL = "llm.call"
    TOOL_EXECUTE = "tool.execute"
    SUBAGENT_SPAWN = "subagent.spawn"
    SUBAGENT_RESULT = "subagent.result"


class SpanStatus(str, Enum):
    """Span 状态"""
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


class ToolResultStatus(str, Enum):
    """工具执行结果状态"""
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


# Trace 版本
TRACE_VERSION = "2.0"

# 参数截断长度
ARGS_PREVIEW_MAX_LEN = 500
RESULT_PREVIEW_MAX_LEN = 1000
```

- [ ] **Step 2: Create test file**

```python
"""Tests for trace types"""
import pytest
from nanobot.tracing.types import (
    SpanType, SpanStatus, ToolResultStatus,
    TRACE_VERSION, ARGS_PREVIEW_MAX_LEN
)


def test_span_type_enum():
    assert SpanType.LLM_CALL == "llm.call"
    assert SpanType.TOOL_EXECUTE == "tool.execute"


def test_trace_version():
    assert TRACE_VERSION == "2.0"


def test_preview_max_lengths():
    assert ARGS_PREVIEW_MAX_LEN == 500
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd .. && python -m pytest tests/test_tracing_types.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add nanobot/tracing/types.py tests/test_tracing_types.py
git commit -m "feat(tracing): add trace types module"
```

---

### Task 2: Enhance Span Class with Tool/Subagent Support

**Files:**
- Modify: `nanobot/tracing/spans.py:36-104` (update Span class)
- Test: `tests/test_span_enhanced.py`

- [ ] **Step 1: Update Span dataclass with new attributes**

Locate the Span class (lines 36-104 in spans.py) and add these new attributes:

```python
@dataclass
class Span:
    # ... existing fields ...

    # NEW: Extended attributes for comprehensive tracing
    span_type: str = ""  # tool/subagent/llm/agent
    tool_name: str = ""
    tool_args: dict | None = None
    tool_result: dict | None = None
    subagent_id: str = ""
    subagent_intent: str = ""
    child_trace_id: str = ""
    evolution_candidate: bool = False
    pattern_tags: list[str] = None

    def __post_init__(self):
        if self.pattern_tags is None:
            self.pattern_tags = []

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSONL emission."""
        result = {
            # ... existing fields ...
            "type": "span",
            "version": "2.0",  # Update version
            # Add new fields
            "span_type": self.span_type,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
            "subagent_id": self.subagent_id,
            "subagent_intent": self.subagent_intent,
            "child_trace_id": self.child_trace_id,
            "evolution_candidate": self.evolution_candidate,
            "pattern_tags": self.pattern_tags,
        }
        return result
```

- [ ] **Step 2: Create helper methods for tool/subagent spans**

Add these methods to the Span class (after the existing `end()` method):

```python
    def mark_tool_span(self, tool_name: str, args: dict | None = None) -> None:
        """Mark this span as a tool execution"""
        self.span_type = "tool"
        self.tool_name = tool_name
        self.tool_args = args
        self.set_attr("tool_name", tool_name)
        if args:
            self.set_attr("tool_args_hash", hash_args(args))

    def mark_subagent_span(self, subagent_id: str, intent: str) -> None:
        """Mark this span as a subagent spawn"""
        self.span_type = "subagent"
        self.subagent_id = subagent_id
        self.subagent_intent = intent
        self.set_attr("subagent_id", subagent_id)
        self.set_attr("subagent_intent", intent)

    def set_tool_result(self, status: str, result: Any = None, error: str = None) -> None:
        """Set tool execution result"""
        self.tool_result = {
            "status": status,
            "result": truncate(str(result), RESULT_PREVIEW_MAX_LEN) if result else None,
            "error": error[:200] if error else None,
        }
        self.set_attr("tool_result_status", status)

    def mark_evolution_candidate(self, tags: list[str]) -> None:
        """Mark this span as a candidate for pattern analysis"""
        self.evolution_candidate = True
        self.pattern_tags = tags
        self.set_attr("evolution_candidate", True)
        self.set_attr("pattern_tags", tags)
```

Add helper functions at the top of spans.py:

```python
import hashlib
import json

def hash_args(args: dict) -> str:
    """Generate hash for args deduplication"""
    args_str = json.dumps(args, sort_keys=True, default=str)
    return hashlib.md5(args_str.encode()).hexdigest()[:12]

def truncate(s: str, max_len: int) -> str:
    """Truncate string to max length"""
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"... (truncated, {len(s)} chars)"
```

- [ ] **Step 3: Create test file**

```python
"""Tests for enhanced Span class"""
import pytest
from nanobot.tracing.spans import Span, hash_args, truncate


def test_hash_args():
    args1 = {"file": "test.py", "line": 10}
    args2 = {"line": 10, "file": "test.py"}  # Same content, different order
    assert hash_args(args1) == hash_args(args2)


def test_truncate():
    long_str = "a" * 1000
    assert len(truncate(long_str, 100)) == 109  # 100 + 9 for suffix


def test_span_tool_marking():
    span = Span(trace_id="tr_test", name="tool.execute")
    span.mark_tool_span("read_file", {"path": "test.py"})
    assert span.span_type == "tool"
    assert span.tool_name == "read_file"


def test_span_subagent_marking():
    span = Span(trace_id="tr_test", name="subagent.spawn")
    span.mark_subagent_span("sa_123", "analyze_code")
    assert span.span_type == "subagent"
    assert span.subagent_id == "sa_123"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .. && python -m pytest tests/test_span_enhanced.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/tracing/spans.py tests/test_span_enhanced.py
git commit -m "feat(tracing): enhance Span class with tool/subagent support"
```

---

### Task 3: Integrate Tool Span in Agent Loop

**Files:**
- Modify: `nanobot/agent/loop.py` (add span around tool execution)
- Test: `tests/test_agent_loop_tracing.py`

- [ ] **Step 1: Identify tool execution location**

Search for tool execution code in loop.py. The tool execution typically happens after LLM returns tool_calls. Look for pattern like:

```python
# After finding tool execution location, wrap it with span
```

Find the tool execution section (search for "execute" or tool result handling). The typical pattern is:

```python
# Find this section and wrap with span
async with span(
    "tool.execute",
    parent_id=current_span_id,
    attrs={"tool_name": tool_name}
) as tool_span:
    tool_span.mark_tool_span(tool_name, tool_call.arguments)
    try:
        result = await tool.execute(**tool_call.arguments)
        tool_span.set_tool_result("success", result)
    except Exception as e:
        tool_span.set_tool_result("error", None, str(e))
        tool_span.end(status="error")
        raise
```

- [ ] **Step 2: Add helper to get current span ID**

Add this method to loop.py near the AgentLoop class initialization:

```python
def _get_current_span_id(self) -> str | None:
    """Get current span ID from tracing context"""
    from nanobot.tracing.context import get_current_span_id
    return get_current_span_id()
```

- [ ] **Step 3: Wrap tool execution with span**

Find the tool execution loop in run() method and wrap each tool call:

```python
async def _execute_tool(self, tool_call, tool_def):
    """Execute a single tool with tracing"""
    current_span_id = self._get_current_span_id()

    async with span(
        "tool.execute",
        parent_id=current_span_id,
        attrs={
            "tool_name": tool_def.name,
            "num_messages": len(self.messages),
        }
    ) as tool_span:
        tool_span.mark_tool_span(tool_def.name, tool_call.arguments)

        try:
            result = await tool_def.execute(**tool_call.arguments)

            # Truncate result for storage
            result_str = str(result)[:500] if result else None
            tool_span.set_tool_result("success", result_str)

            # Check for evolution candidate patterns
            if self._is_evolution_candidate(tool_def.name, result):
                tool_span.mark_evolution_candidate([tool_def.name, "pattern_detected"])

            return result

        except TimeoutError as e:
            tool_span.set_tool_result("timeout", None, str(e))
            tool_span.end(status="error")
            raise
        except Exception as e:
            tool_span.set_tool_result("error", None, str(e))
            tool_span.end(status="error")
            raise


def _is_evolution_candidate(self, tool_name: str, result: Any) -> bool:
    """Determine if tool result should be marked for pattern analysis"""
    # Simple heuristics for now
    if tool_name == "exec" and result:
        return "test" in str(result).lower() or "fail" in str(result).lower()
    return False
```

- [ ] **Step 4: Create test file**

```python
"""Tests for agent loop tracing integration"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from nanobot.agent.loop import AgentLoop


@pytest.fixture
def mock_bus():
    bus = MagicMock()
    return bus


@pytest.fixture
def agent_loop(mock_bus, tmp_path):
    with patch('nanobot.agent.loop.init_tracing'):
        loop = AgentLoop(
            bus=mock_bus,
            workspace=tmp_path,
            max_iterations=5,
        )
    return loop


@pytest.mark.asyncio
async def test_execute_tool_creates_span(agent_loop):
    """Test that tool execution creates a trace span"""
    tool = AsyncMock()
    tool.name = "read_file"
    tool.execute = AsyncMock(return_value="file content")

    tool_call = MagicMock()
    tool_call.arguments = {"path": "test.py"}

    with patch('nanobot.agent.loop.span') as mock_span:
        mock_span.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_span.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await agent_loop._execute_tool(tool_call, tool)

        mock_span.assert_called_once()
        call_kwargs = mock_span.call_args[1]
        assert call_kwargs["name"] == "tool.execute"
        assert call_kwargs["attrs"]["tool_name"] == "read_file"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd .. && python -m pytest tests/test_agent_loop_tracing.py -v`
Expected: PASS (may need to adjust based on actual loop.py structure)

- [ ] **Step 6: Commit**

```bash
git add nanobot/agent/loop.py tests/test_agent_loop_tracing.py
git commit -m "feat(tracing): integrate tool span in agent loop"
```

---

### Task 4: Integrate Subagent Span

**Files:**
- Modify: `nanobot/agent/subagent.py` or spawn tool (identify location first)
- Test: `tests/test_subagent_tracing.py`

- [ ] **Step 1: Find subagent spawn location**

Search for subagent creation or spawn tool. The spawn typically happens when agent decides to delegate work.

```bash
grep -rn "spawn" --include="*.py" nanobot/agent/
```

Look for the spawn tool implementation or SubagentManager.

- [ ] **Step 2: Wrap subagent with span**

Add tracing to subagent spawn:

```python
async def _spawn_with_tracing(self, task_spec: TaskSpec, parent_span_id: str | None = None):
    """Spawn subagent with trace context"""
    from nanobot.agentloop.kernel.ids import new_id
    from nanobot.tracing import span

    child_trace_id = new_id("tr")

    async with span(
        "subagent.spawn",
        parent_id=parent_span_id,
        attrs={
            "subagent_intent": task_spec.intent,
            "capability": task_spec.capability_name,
            "child_trace_id": child_trace_id,
        }
    ) as subagent_span:
        subagent_span.mark_subagent_span(child_trace_id, task_spec.intent)

        try:
            result = await self._run_subagent(task_spec, child_trace_id)
            subagent_span.set_attr("result_status", result.status)
            subagent_span.set_attr("duration_ms", result.duration_ms)
            subagent_span.end(status="ok")
            return result
        except Exception as e:
            subagent_span.set_attr("result_status", "error")
            subagent_span.set_attr("error", str(e)[:200])
            subagent_span.end(status="error")
            raise
```

- [ ] **Step 3: Create test file**

```python
"""Tests for subagent tracing"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_subagent_span_creation():
    """Test that subagent spawn creates proper span"""
    with patch('nanobot.tracing.span') as mock_span:
        mock_span.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_span.return_value.__aexit__ = AsyncMock(return_value=None)

        # Mock the actual subagent execution
        with patch('nanobot.agent.subagent.SubagentManager._run_subagent',
                   new=AsyncMock(return_value=MagicMock(status="done", duration_ms=100))):
            result = await subagent_manager._spawn_with_tracing(task_spec, parent_span_id)

        mock_span.assert_called_once()
        call_kwargs = mock_span.call_args[1]
        assert call_kwargs["name"] == "subagent.spawn"
        assert "child_trace_id" in call_kwargs["attrs"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .. && python -m pytest tests/test_subagent_tracing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/agent/subagent.py tests/test_subagent_tracing.py
git commit -m "feat(tracing): integrate subagent span"
```

---

## Phase 2: Trace Analysis Pipeline

### Task 5: Create Analysis Module

**Files:**
- Create: `nanobot/analysis/__init__.py`
- Create: `nanobot/analysis/collector.py`
- Test: `tests/test_collector.py`

- [ ] **Step 1: Create analysis module init**

```python
"""Trace Analysis Module"""
from nanobot.analysis.collector import TraceCollector
from nanobot.analysis.stats import StatsAggregator
from nanobot.analysis.anomaly import AnomalyDetector
from nanobot.analysis.triggers import EvolutionTrigger

__all__ = [
    "TraceCollector",
    "StatsAggregator",
    "AnomalyDetector",
    "EvolutionTrigger",
]
```

- [ ] **Step 2: Create TraceCollector**

```python
"""Trace data collector from emitter"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
import json


class TraceCollector:
    """Collects and filters trace data from JSONL files"""

    def __init__(self, trace_dir: Path | None = None):
        if trace_dir is None:
            trace_dir = Path.home() / ".nanobot" / "traces"
        self.trace_dir = trace_dir

    def collect_window(
        self,
        start_ts: int,
        end_ts: int,
        trace_id: str | None = None
    ) -> Iterator[dict]:
        """Collect spans within time window"""
        import os
        from datetime import datetime

        start_dt = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc)

        for fpath in sorted(self.trace_dir.glob("trace_*.jsonl*"), reverse=True):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            span = json.loads(line)
                            span_time = span.get("start_ms", 0)

                            if start_ts <= span_time <= end_ts:
                                if trace_id is None or span.get("trace_id") == trace_id:
                                    yield span
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue

    def get_latest_traces(self, limit: int = 100) -> list[dict]:
        """Get latest N traces"""
        traces = {}
        for fpath in sorted(self.trace_dir.glob("trace_*.jsonl*"), reverse=True):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in reversed(f.readlines()):
                        span = json.loads(line)
                        tid = span.get("trace_id")
                        if tid and tid not in traces:
                            traces[tid] = span
                            if len(traces) >= limit:
                                return list(traces.values())
            except Exception:
                continue
        return list(traces.values())
```

- [ ] **Step 3: Create StatsAggregator**

```python
"""Statistics aggregation from trace data"""
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class TraceStats:
    """Aggregated trace statistics"""
    total_traces: int = 0
    total_spans: int = 0
    success_count: int = 0
    error_count: int = 0
    total_duration_ms: int = 0

    # Token usage
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # Tool stats
    tool_calls: int = 0
    tool_errors: int = 0
    tool_by_name: dict = field(default_factory=lambda: defaultdict(int))

    # LLM stats
    llm_calls: int = 0
    llm_errors: int = 0

    # Subagent stats
    subagent_spawns: int = 0
    subagent_errors: int = 0

    @property
    def success_rate(self) -> float:
        if self.total_traces == 0:
            return 0.0
        return self.success_count / self.total_traces

    @property
    def avg_duration_ms(self) -> float:
        if self.total_traces == 0:
            return 0.0
        return self.total_duration_ms / self.total_traces

    @property
    def tool_error_rate(self) -> float:
        if self.tool_calls == 0:
            return 0.0
        return self.tool_errors / self.tool_calls

    def to_dict(self) -> dict:
        return {
            "total_traces": self.total_traces,
            "success_rate": round(self.success_rate, 3),
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": self.total_tokens,
            },
            "tools": {
                "total_calls": self.tool_calls,
                "errors": self.tool_errors,
                "error_rate": round(self.tool_error_rate, 3),
                "by_name": dict(self.tool_by_name),
            },
            "llm": {
                "calls": self.llm_calls,
                "errors": self.llm_errors,
            },
            "subagent": {
                "spawns": self.subagent_spawns,
                "errors": self.subagent_errors,
            },
        }


class StatsAggregator:
    """Aggregates statistics from trace spans"""

    def aggregate(self, spans: list[dict]) -> TraceStats:
        """Aggregate spans into statistics"""
        stats = TraceStats()
        seen_traces = set()

        for span in spans:
            tid = span.get("trace_id")

            if tid not in seen_traces:
                seen_traces.add(tid)
                stats.total_traces += 1
                stats.total_duration_ms += span.get("duration_ms", 0)

            stats.total_spans += 1

            # Status
            if span.get("status") == "error":
                stats.error_count += 1
            else:
                stats.success_count += 1

            # Span type
            name = span.get("name", "")
            attrs = span.get("attrs", {})

            if name == "llm.call":
                stats.llm_calls += 1
                if span.get("status") == "error":
                    stats.llm_errors += 1
                usage = attrs.get("usage", {})
                stats.prompt_tokens += usage.get("prompt_tokens", 0)
                stats.completion_tokens += usage.get("completion_tokens", 0)
                stats.total_tokens += usage.get("total_tokens", 0)

            elif name == "tool.execute":
                stats.tool_calls += 1
                tool_name = attrs.get("tool_name", "unknown")
                stats.tool_by_name[tool_name] += 1

                if span.get("status") == "error" or attrs.get("result_status") == "error":
                    stats.tool_errors += 1

            elif name == "subagent.spawn":
                stats.subagent_spawns += 1
                if span.get("status") == "error":
                    stats.subagent_errors += 1

        return stats
```

- [ ] **Step 4: Create test file**

```python
"""Tests for analysis module"""
import pytest
from nanobot.analysis.collector import TraceCollector
from nanobot.analysis.stats import StatsAggregator, TraceStats


def test_trace_stats_success_rate():
    stats = TraceStats(total_traces=100, success_count=95, error_count=5)
    assert stats.success_rate == 0.95


def test_stats_aggregation():
    spans = [
        {
            "trace_id": "tr_1",
            "name": "llm.call",
            "status": "ok",
            "duration_ms": 100,
            "attrs": {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
        },
        {
            "trace_id": "tr_1",
            "name": "tool.execute",
            "status": "ok",
            "attrs": {"tool_name": "read_file"}
        },
    ]

    aggregator = StatsAggregator()
    stats = aggregator.aggregate(spans)

    assert stats.total_traces == 1
    assert stats.llm_calls == 1
    assert stats.tool_calls == 1
    assert stats.total_tokens == 150
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd .. && python -m pytest tests/test_collector.py tests/test_stats.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add nanobot/analysis/ tests/test_collector.py tests/test_stats.py
git commit -m "feat(analysis): add trace analysis module"
```

---

### Task 6: Create Anomaly Detector

**Files:**
- Create: `nanobot/analysis/anomaly.py`
- Test: `tests/test_anomaly.py`

- [ ] **Step 1: Create AnomalyDetector**

```python
"""Anomaly detection from trace data"""
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass
class Anomaly:
    """Detected anomaly in trace data"""
    anomaly_type: str  # slow_tool, repeated_error, high_token, etc.
    severity: str  # critical, warning, info
    description: str
    context: dict  # Original span data
    recommended_action: str
    occurrence_count: int = 1


class AnomalyDetector:
    """Detects anomalies in trace data"""

    THRESHOLDS = {
        "slow_tool_ms": 5000,  # Tool taking > 5s
        "slow_llm_ms": 30000,  # LLM call taking > 30s
        "repeated_error_count": 3,  # Same error 3+ times
        "error_rate_threshold": 0.2,  # > 20% error rate
        "high_token_per_call": 100000,  # > 100k tokens per call
    }

    def __init__(self, thresholds: dict | None = None):
        self.thresholds = {**self.THRESHOLDS, **(thresholds or {})}

    def detect(self, spans: list[dict], stats: "TraceStats") -> list[Anomaly]:
        """Detect anomalies from spans and stats"""
        anomalies = []

        # Check slow tools
        anomalies.extend(self._detect_slow_tools(spans))

        # Check repeated errors
        anomalies.extend(self._detect_repeated_errors(spans))

        # Check error rate
        if stats.success_rate < (1 - self.thresholds["error_rate_threshold"]):
            anomalies.append(Anomaly(
                anomaly_type="high_error_rate",
                severity="critical",
                description=f"Error rate {1 - stats.success_rate:.1%} exceeds threshold",
                context={},
                recommended_action="Investigate root cause of failures"
            ))

        # Check token efficiency
        anomalies.extend(self._detect_token_issues(spans))

        return anomalies

    def _detect_slow_tools(self, spans: list[dict]) -> list[Anomaly]:
        """Detect tools that are slower than threshold"""
        anomalies = []
        tool_durations = defaultdict(list)

        for span in spans:
            if span.get("name") == "tool.execute":
                duration = span.get("duration_ms", 0)
                tool_name = span.get("attrs", {}).get("tool_name", "unknown")
                tool_durations[tool_name].append(duration)

        for tool_name, durations in tool_durations.items():
            if durations:
                avg_duration = sum(durations) / len(durations)
                if avg_duration > self.thresholds["slow_tool_ms"]:
                    anomalies.append(Anomaly(
                        anomaly_type="slow_tool",
                        severity="warning",
                        description=f"{tool_name} avg duration {avg_duration:.0f}ms exceeds {self.thresholds['slow_tool_ms']}ms",
                        context={"tool_name": tool_name, "avg_duration": avg_duration, "count": len(durations)},
                        recommended_action=f"Consider caching or async optimization for {tool_name}"
                    ))

        return anomalies

    def _detect_repeated_errors(self, spans: list[dict]) -> list[Anomaly]:
        """Detect repeated errors of the same type"""
        anomalies = []
        error_patterns = defaultdict(lambda: {"count": 0, "spans": []})

        for span in spans:
            if span.get("status") == "error":
                attrs = span.get("attrs", {})
                tool_name = attrs.get("tool_name", "unknown")
                error_type = attrs.get("error_type", "unknown")
                key = (tool_name, error_type)

                error_patterns[key]["count"] += 1
                error_patterns[key]["spans"].append(span)

        for (tool_name, error_type), data in error_patterns.items():
            if data["count"] >= self.thresholds["repeated_error_count"]:
                anomalies.append(Anomaly(
                    anomaly_type="repeated_error",
                    severity="critical",
                    description=f"{tool_name} failed {data['count']} times with error: {error_type}",
                    context=data["spans"][0],
                    recommended_action=f"Analyze error pattern for {tool_name}, consider updating error handling"
                ))

        return anomalies

    def _detect_token_issues(self, spans: list[dict]) -> list[Anomaly]:
        """Detect unusual token consumption patterns"""
        anomalies = []

        for span in spans:
            if span.get("name") == "llm.call":
                usage = span.get("attrs", {}).get("usage", {})
                total = usage.get("total_tokens", 0)

                if total > self.thresholds["high_token_per_call"]:
                    anomalies.append(Anomaly(
                        anomaly_type="high_token_usage",
                        severity="warning",
                        description=f"LLM call consumed {total} tokens (>{self.thresholds['high_token_per_call']})",
                        context=span,
                        recommended_action="Review prompt efficiency, consider context truncation"
                    ))

        return anomalies
```

- [ ] **Step 2: Create test file**

```python
"""Tests for anomaly detection"""
import pytest
from nanobot.analysis.anomaly import AnomalyDetector, Anomaly
from nanobot.analysis.stats import TraceStats


def test_detect_slow_tool():
    detector = AnomalyDetector(thresholds={"slow_tool_ms": 100})

    spans = [
        {"name": "tool.execute", "duration_ms": 200, "attrs": {"tool_name": "slow_tool"}},
        {"name": "tool.execute", "duration_ms": 150, "attrs": {"tool_name": "slow_tool"}},
    ]

    anomalies = detector.detect(spans, TraceStats())

    assert len(anomalies) >= 1
    slow_tools = [a for a in anomalies if a.anomaly_type == "slow_tool"]
    assert len(slow_tools) == 1


def test_detect_repeated_error():
    detector = AnomalyDetector(thresholds={"repeated_error_count": 2})

    spans = [
        {"status": "error", "attrs": {"tool_name": "read_file", "error_type": "FileNotFound"}},
        {"status": "error", "attrs": {"tool_name": "read_file", "error_type": "FileNotFound"}},
    ]

    anomalies = detector.detect(spans, TraceStats())

    repeated = [a for a in anomalies if a.anomaly_type == "repeated_error"]
    assert len(repeated) == 1
    assert repeated[0].severity == "critical"
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd .. && python -m pytest tests/test_anomaly.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add nanobot/analysis/anomaly.py tests/test_anomaly.py
git commit -m "feat(analysis): add anomaly detector"
```

---

### Task 7: Create Evolution Trigger

**Files:**
- Create: `nanobot/analysis/triggers.py`
- Test: `tests/test_triggers.py`

- [ ] **Step 1: Create EvolutionTrigger**

```python
"""Evolution trigger based on anomaly detection"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any
import json
from pathlib import Path


@dataclass
class EvolutionTask:
    """Task to be executed for self-improvement"""
    task_id: str
    trigger_type: str  # anomaly, pattern, manual
    anomaly_type: str | None
    description: str
    context: dict
    recommended_action: str
    confidence: float  # 0.0 - 1.0
    created_at: datetime


class EvolutionTrigger:
    """Manages evolution triggers with cooldown"""

    # Cooldown period in hours
    DEFAULT_COOLDOWN_HOURS = 24

    def __init__(self, storage_path: Path | None = None):
        if storage_path is None:
            storage_path = Path.home() / ".nanobot" / "analysis"
        self.storage_path = storage_path
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._last_triggered: dict[str, datetime] = self._load_cooldown()

    def _load_cooldown(self) -> dict[str, datetime]:
        """Load last triggered timestamps"""
        cooldown_file = self.storage_path / "trigger_cooldown.json"
        if cooldown_file.exists():
            try:
                data = json.loads(cooldown_file.read_text())
                return {
                    k: datetime.fromisoformat(v)
                    for k, v in data.items()
                }
            except Exception:
                pass
        return {}

    def _save_cooldown(self) -> None:
        """Save cooldown timestamps"""
        cooldown_file = self.storage_path / "trigger_cooldown.json"
        data = {k: v.isoformat() for k, v in self._last_triggered.items()}
        cooldown_file.write_text(json.dumps(data, indent=2))

    def _is_in_cooldown(self, trigger_key: str) -> bool:
        """Check if trigger is in cooldown period"""
        if trigger_key not in self._last_triggered:
            return False

        last = self._last_triggered[trigger_key]
        elapsed = datetime.now(timezone.utc) - last
        return elapsed < timedelta(hours=self.DEFAULT_COOLDOWN_HOURS)

    def _set_triggered(self, trigger_key: str) -> None:
        """Mark trigger as triggered now"""
        self._last_triggered[trigger_key] = datetime.now(timezone.utc)
        self._save_cooldown()

    def evaluate(
        self,
        anomalies: list["Anomaly"],
        patterns: list[dict] | None = None
    ) -> list[EvolutionTask]:
        """Evaluate anomalies and patterns to generate evolution tasks"""
        tasks = []

        # Process anomalies
        for anomaly in anomalies:
            trigger_key = f"anomaly:{anomaly.anomaly_type}"

            if self._is_in_cooldown(trigger_key):
                continue

            # Calculate confidence based on severity and occurrence
            confidence = self._calculate_confidence(anomaly)

            if confidence >= 0.7:  # Minimum confidence threshold
                task = EvolutionTask(
                    task_id=f"evo_{anomaly.anomaly_type}_{datetime.now().strftime('%Y%m%d%H%M')}",
                    trigger_type="anomaly",
                    anomaly_type=anomaly.anomaly_type,
                    description=anomaly.description,
                    context=anomaly.context,
                    recommended_action=anomaly.recommended_action,
                    confidence=confidence,
                    created_at=datetime.now(timezone.utc)
                )
                tasks.append(task)
                self._set_triggered(trigger_key)

        return tasks

    def _calculate_confidence(self, anomaly: "Anomaly") -> float:
        """Calculate confidence score for evolution task"""
        base_confidence = {
            "critical": 0.9,
            "warning": 0.7,
            "info": 0.5,
        }.get(anomaly.severity, 0.5)

        # Boost for repeated occurrences
        occurrence_boost = min(anomaly.occurrence_count * 0.05, 0.2)

        return min(base_confidence + occurrence_boost, 1.0)
```

- [ ] **Step 2: Create test file**

```python
"""Tests for evolution trigger"""
import pytest
from datetime import datetime, timezone, timedelta
from nanobot.analysis.triggers import EvolutionTrigger, EvolutionTask
from nanobot.analysis.anomaly import Anomaly


def test_evolution_trigger_generates_task(tmp_path):
    trigger = EvolutionTrigger(storage_path=tmp_path)

    anomalies = [
        Anomaly(
            anomaly_type="repeated_error",
            severity="critical",
            description="read_file failed 5 times",
            context={},
            recommended_action="Update error handling"
        )
    ]

    tasks = trigger.evaluate(anomalies)

    assert len(tasks) == 1
    assert tasks[0].trigger_type == "anomaly"
    assert tasks[0].confidence >= 0.9


def test_cooldown_prevents_duplicate_triggers(tmp_path):
    trigger = EvolutionTrigger(storage_path=tmp_path)

    anomalies = [
        Anomaly(
            anomaly_type="slow_tool",
            severity="warning",
            description="slow_tool took 10s",
            context={},
            recommended_action="Optimize"
        )
    ]

    # First evaluation should create task
    tasks1 = trigger.evaluate(anomalies)
    assert len(tasks1) == 1

    # Immediate second evaluation should be blocked by cooldown
    tasks2 = trigger.evaluate(anomalies)
    assert len(tasks2) == 0
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd .. && python -m pytest tests/test_triggers.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add nanobot/analysis/triggers.py tests/test_triggers.py
git commit -m "feat(analysis): add evolution trigger"
```

---

## Phase 3: Self-Improvement Integration

### Task 8: Create Trace-Based Memory Writer

**Files:**
- Create: `nanobot/analysis/memory_writer.py`
- Modify: `nanobot/analysis/__init__.py`
- Test: `tests/test_memory_writer.py`

- [ ] **Step 1: Create TraceBasedMemoryWriter**

```python
"""Memory writer for trace-based self-improvement"""
from pathlib import Path
from datetime import datetime
from typing import Any


class TraceBasedMemoryWriter:
    """Writes trace analysis results to self_improve memory"""

    def __init__(self, workspace: Path, agent_id: str | None = None):
        self.workspace = workspace
        self.agent_id = agent_id

    async def write_anomaly_insight(
        self,
        anomaly_type: str,
        description: str,
        context: dict,
        recommended_action: str,
        confidence: float
    ) -> str:
        """Write anomaly-based insight to memory"""
        from nanobot.agent.tools.persist_self_improvement import PersistSelfImprovementTool

        tool = PersistSelfImprovementTool(self.workspace, self.agent_id)

        content = self._format_anomaly_content(
            anomaly_type, description, context, recommended_action, confidence
        )

        source_id = f"trace-anomaly-{anomaly_type}-{datetime.now().strftime('%Y%m%d%H%M')}"

        await tool.execute(
            content=content,
            source_type="self_improve_pattern",
            source_id=source_id
        )

        return source_id

    def _format_anomaly_content(
        self,
        anomaly_type: str,
        description: str,
        context: dict,
        recommended_action: str,
        confidence: float
    ) -> str:
        """Format anomaly as memory content"""
        # Extract relevant context info
        tool_name = context.get("tool_name", "N/A")
        duration = context.get("avg_duration", context.get("duration_ms", "N/A"))

        return f"""## Trace Analysis: {anomaly_type}

**问题**: {description}

**观察到的上下文**:
- Tool: {tool_name}
- Duration: {duration}ms
- Confidence: {confidence:.0%}

**根因分析**:
Based on trace pattern analysis, this anomaly indicates a potential issue that
may benefit from systematic improvement.

**建议行动**:
{recommended_action}

**元数据**:
- Detected at: {datetime.now().isoformat()}
- Source: trace_analysis
- Type: {anomaly_type}
"""

    async def write_pattern_insight(
        self,
        pattern_type: str,
        trigger_condition: str,
        behavior: str,
        optimization: str,
        occurrence_count: int
    ) -> str:
        """Write identified pattern to memory"""
        from nanobot.agent.tools.persist_self_improvement import PersistSelfImprovementTool

        tool = PersistSelfImprovementTool(self.workspace, self.agent_id)

        content = self._format_pattern_content(
            pattern_type, trigger_condition, behavior, optimization, occurrence_count
        )

        source_id = f"trace-pattern-{pattern_type}-{datetime.now().strftime('%Y%m%d%H%M')}"

        await tool.execute(
            content=content,
            source_type="self_improve_pattern",
            source_id=source_id
        )

        return source_id

    def _format_pattern_content(
        self,
        pattern_type: str,
        trigger_condition: str,
        behavior: str,
        optimization: str,
        occurrence_count: int
    ) -> str:
        """Format pattern as memory content"""
        return f"""## Identified Pattern: {pattern_type}

**触发条件**: {trigger_condition}

**观察到的行为**:
{behavior}

**优化建议**:
{optimization}

**统计数据**:
- Occurrence count: {occurrence_count}
- Detected at: {datetime.now().isoformat()}
- Source: trace_analysis
"""
```

- [ ] **Step 2: Update analysis __init__.py**

```python
"""Trace Analysis Module"""
from nanobot.analysis.collector import TraceCollector
from nanobot.analysis.stats import StatsAggregator, TraceStats
from nanobot.analysis.anomaly import AnomalyDetector, Anomaly
from nanobot.analysis.triggers import EvolutionTrigger, EvolutionTask
from nanobot.analysis.memory_writer import TraceBasedMemoryWriter

__all__ = [
    "TraceCollector",
    "StatsAggregator",
    "TraceStats",
    "AnomalyDetector",
    "Anomaly",
    "EvolutionTrigger",
    "EvolutionTask",
    "TraceBasedMemoryWriter",
]
```

- [ ] **Step 3: Create test file**

```python
"""Tests for memory writer"""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def workspace(tmp_path):
    return tmp_path / "test_workspace"


@pytest.mark.asyncio
async def test_write_anomaly_insight(workspace):
    from nanobot.analysis.memory_writer import TraceBasedMemoryWriter

    writer = TraceBasedMemoryWriter(workspace)

    with patch('nanobot.analysis.memory_writer.PersistSelfImprovementTool') as MockTool:
        mock_instance = AsyncMock()
        MockTool.return_value = mock_instance

        source_id = await writer.write_anomaly_insight(
            anomaly_type="slow_tool",
            description="read_file is slow",
            context={"tool_name": "read_file", "avg_duration": 5000},
            recommended_action="Add caching",
            confidence=0.85
        )

        mock_instance.execute.assert_called_once()
        call_kwargs = mock_instance.execute.call_args[1]
        assert call_kwargs["source_type"] == "self_improve_pattern"
        assert "slow_tool" in call_kwargs["content"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd .. && python -m pytest tests/test_memory_writer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/analysis/memory_writer.py nanobot/analysis/__init__.py tests/test_memory_writer.py
git commit -m "feat(self-evolution): add trace-based memory writer"
```

---

### Task 9: Create Trace Analysis Service

**Files:**
- Create: `nanobot/services/trace_analysis_service.py`
- Modify: `nanobot/tracing/__init__.py` (export analysis components)
- Test: `tests/test_trace_analysis_service.py`

- [ ] **Step 1: Create TraceAnalysisService**

```python
"""Main trace analysis service coordinating all components"""
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.analysis import (
    TraceCollector,
    StatsAggregator,
    AnomalyDetector,
    EvolutionTrigger,
    TraceBasedMemoryWriter,
)


class TraceAnalysisService:
    """Orchestrates trace analysis and self-improvement"""

    def __init__(
        self,
        workspace: Path,
        trace_dir: Path | None = None,
        agent_id: str | None = None
    ):
        self.collector = TraceCollector(trace_dir)
        self.stats_aggregator = StatsAggregator()
        self.anomaly_detector = AnomalyDetector()
        self.evolution_trigger = EvolutionTrigger()
        self.memory_writer = TraceBasedMemoryWriter(workspace, agent_id)

    async def analyze_window(
        self,
        window_hours: int = 1,
        auto_evolve: bool = True
    ) -> dict[str, Any]:
        """Analyze traces in the given time window"""
        end_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ts = int((datetime.now(timezone.utc) - timedelta(hours=window_hours)).timestamp() * 1000)

        logger.info(f"Analyzing traces from {start_ts} to {end_ts}")

        # Collect spans
        spans = list(self.collector.collect_window(start_ts, end_ts))
        logger.info(f"Collected {len(spans)} spans")

        if not spans:
            return {"status": "no_data", "spans_collected": 0}

        # Aggregate statistics
        stats = self.stats_aggregator.aggregate(spans)

        # Detect anomalies
        anomalies = self.anomaly_detector.detect(spans, stats)

        # Generate evolution tasks
        evolution_tasks = []
        if auto_evolve:
            evolution_tasks = self.evolution_trigger.evaluate(anomalies)

        # Execute evolution tasks
        memories_created = 0
        for task in evolution_tasks:
            try:
                await self.memory_writer.write_anomaly_insight(
                    anomaly_type=task.anomaly_type,
                    description=task.description,
                    context=task.context,
                    recommended_action=task.recommended_action,
                    confidence=task.confidence
                )
                memories_created += 1
                logger.info(f"Created memory for evolution task: {task.task_id}")
            except Exception as e:
                logger.error(f"Failed to create memory for {task.task_id}: {e}")

        return {
            "status": "success",
            "window_hours": window_hours,
            "spans_collected": len(spans),
            "stats": stats.to_dict(),
            "anomalies_found": len(anomalies),
            "evolution_tasks_generated": len(evolution_tasks),
            "memories_created": memories_created,
            "anomalies": [
                {
                    "type": a.anomaly_type,
                    "severity": a.severity,
                    "description": a.description,
                    "recommended_action": a.recommended_action
                }
                for a in anomalies
            ]
        }

    async def analyze_trace(self, trace_id: str) -> dict[str, Any]:
        """Analyze a specific trace by ID"""
        # This would be used for deep-dive analysis
        spans = list(self.collector.collect_window(0, int(datetime.now().timestamp() * 1000), trace_id))

        if not spans:
            return {"status": "not_found", "trace_id": trace_id}

        stats = self.stats_aggregator.aggregate(spans)
        anomalies = self.anomaly_detector.detect(spans, stats)

        # Build trace chain
        trace_chain = sorted(spans, key=lambda s: s.get("seq", 0))

        return {
            "status": "success",
            "trace_id": trace_id,
            "spans": trace_chain,
            "stats": stats.to_dict(),
            "anomalies": [
                {
                    "type": a.anomaly_type,
                    "description": a.description,
                    "context_span_id": a.context.get("span_id")
                }
                for a in anomalies
            ]
        }
```

- [ ] **Step 2: Create test file**

```python
"""Tests for trace analysis service"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from nanobot.services.trace_analysis_service import TraceAnalysisService


@pytest.fixture
def service(tmp_path):
    return TraceAnalysisService(workspace=tmp_path)


@pytest.mark.asyncio
async def test_analyze_window_no_data(service):
    """Test analysis with no spans returns no_data status"""
    with patch.object(service.collector, 'collect_window', return_value=iter([])):
        result = await service.analyze_window(window_hours=1)

        assert result["status"] == "no_data"
        assert result["spans_collected"] == 0


@pytest.mark.asyncio
async def test_analyze_window_with_data(service):
    """Test analysis with spans returns stats and anomalies"""
    mock_spans = [
        {"trace_id": "tr_1", "name": "llm.call", "status": "ok", "duration_ms": 100, "seq": 1,
         "attrs": {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}},
        {"trace_id": "tr_1", "name": "tool.execute", "status": "error", "duration_ms": 50, "seq": 2,
         "attrs": {"tool_name": "read_file", "error_type": "FileNotFound"}},
        {"trace_id": "tr_1", "name": "tool.execute", "status": "error", "duration_ms": 60, "seq": 3,
         "attrs": {"tool_name": "read_file", "error_type": "FileNotFound"}},
    ]

    with patch.object(service.collector, 'collect_window', return_value=iter(mock_spans)):
        with patch.object(service.memory_writer, 'write_anomaly_insight', new_callable=AsyncMock):
            result = await service.analyze_window(window_hours=1, auto_evolve=True)

        assert result["status"] == "success"
        assert result["spans_collected"] == 3
        assert "stats" in result
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd .. && python -m pytest tests/test_trace_analysis_service.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add nanobot/services/trace_analysis_service.py tests/test_trace_analysis_service.py
git commit -m "feat(service): add trace analysis service"
```

---

### Task 10: Integrate with CLI/Cron

**Files:**
- Modify: `nanobot/cli/` (add trace analysis command)
- Create: `nanobot/cron/trace_analysis_task.py`

- [ ] **Step 1: Check existing CLI structure**

```bash
ls -la nanobot/cli/
```

- [ ] **Step 2: Add trace analysis CLI command**

Create a new file or add to existing CLI:

```python
"""CLI commands for trace analysis"""
import click
from pathlib import Path


@click.group()
def trace():
    """Trace analysis commands"""
    pass


@trace.command("analyze")
@click.option("--hours", default=1, help="Analysis window in hours")
@click.option("--no-auto-evolve", is_flag=True, help="Skip auto evolution")
@click.option("--workspace", type=click.Path(), default=".", help="Workspace path")
def analyze_traces(hours: int, no_auto_evolve: bool, workspace: str):
    """Analyze recent traces"""
    from nanobot.services.trace_analysis_service import TraceAnalysisService
    import asyncio

    service = TraceAnalysisService(workspace=Path(workspace))

    result = asyncio.run(service.analyze_window(
        window_hours=hours,
        auto_evolve=not no_auto_evolve
    ))

    click.echo(f"\n=== Trace Analysis Report ({hours}h window) ===")
    click.echo(f"Status: {result['status']}")
    click.echo(f"Spans analyzed: {result.get('spans_collected', 0)}")

    if "stats" in result:
        stats = result["stats"]
        click.echo(f"\nStatistics:")
        click.echo(f"  Total traces: {stats['total_traces']}")
        click.echo(f"  Success rate: {stats['success_rate']:.1%}")
        click.echo(f"  Avg duration: {stats['avg_duration_ms']:.0f}ms")

        click.echo(f"\nToken Usage:")
        tokens = stats["tokens"]
        click.echo(f"  Prompt: {tokens['prompt']:,}")
        click.echo(f"  Completion: {tokens['completion']:,}")
        click.echo(f"  Total: {tokens['total']:,}")

    if result.get("anomalies_found", 0) > 0:
        click.echo(f"\nAnomalies detected: {result['anomalies_found']}")
        for anomaly in result.get("anomalies", [])[:5]:
            click.echo(f"  [{anomaly['severity'].upper()}] {anomaly['type']}: {anomaly['description']}")

    if result.get("memories_created", 0) > 0:
        click.echo(f"\nEvolution: Created {result['memories_created']} memories")


@trace.command("inspect")
@click.argument("trace_id")
@click.option("--workspace", type=click.Path(), default=".", help="Workspace path")
def inspect_trace(trace_id: str, workspace: str):
    """Inspect a specific trace"""
    from nanobot.services.trace_analysis_service import TraceAnalysisService
    import asyncio

    service = TraceAnalysisService(workspace=Path(workspace))
    result = asyncio.run(service.analyze_trace(trace_id))

    if result["status"] == "not_found":
        click.echo(f"Trace not found: {trace_id}")
        return

    click.echo(f"\n=== Trace {trace_id} ===")
    click.echo(f"Spans: {len(result.get('spans', []))}")

    for span in result.get("spans", []):
        name = span.get("name", "unknown")
        duration = span.get("duration_ms", 0)
        status = span.get("status", "unknown")
        click.echo(f"  [{status:6}] {name:20} {duration:6}ms")


def register(cli_group):
    """Register CLI commands"""
    cli_group.add_command(trace)
```

- [ ] **Step 3: Create cron task for periodic analysis**

```python
"""Periodic trace analysis cron task"""
from datetime import datetime, timezone
from loguru import logger

from nanobot.services.trace_analysis_service import TraceAnalysisService


async def run_trace_analysis(workspace: "Path", window_hours: int = 1):
    """Run trace analysis as a scheduled task"""
    logger.info("Starting scheduled trace analysis")

    service = TraceAnalysisService(workspace=workspace)

    try:
        result = await service.analyze_window(
            window_hours=window_hours,
            auto_evolve=True
        )

        if result["status"] == "success":
            logger.info(
                f"Trace analysis complete: {result['spans_collected']} spans, "
                f"{result['anomalies_found']} anomalies, "
                f"{result['memories_created']} memories created"
            )
        else:
            logger.warning(f"Trace analysis returned: {result['status']}")

    except Exception as e:
        logger.error(f"Trace analysis failed: {e}")
        raise


# For cron registration
CRON_SPEC = {
    "name": "trace_analysis",
    "schedule": "0 * * * *",  # Every hour
    "handler": run_trace_analysis,
    "args": {"window_hours": 1},
    "description": "Analyze recent traces and trigger self-improvement"
}
```

- [ ] **Step 4: Commit**

```bash
git add nanobot/cli/trace.py nanobot/cron/trace_analysis_task.py
git commit -m "feat(cli): add trace analysis CLI and cron task"
```

---

## Summary

### Task Checklist

| # | Task | Phase | Status |
|---|------|-------|--------|
| 1 | Create Trace Types Module | 1 | - [ ] |
| 2 | Enhance Span Class | 1 | - [ ] |
| 3 | Integrate Tool Span in Agent Loop | 1 | - [ ] |
| 4 | Integrate Subagent Span | 1 | - [ ] |
| 5 | Create Analysis Module | 2 | - [ ] |
| 6 | Create Anomaly Detector | 2 | - [ ] |
| 7 | Create Evolution Trigger | 2 | - [ ] |
| 8 | Create Memory Writer | 3 | - [ ] |
| 9 | Create Trace Analysis Service | 3 | - [ ] |
| 10 | Integrate with CLI/Cron | 3 | - [ ] |

### Verification Plan

After each task:
1. Run unit tests: `pytest tests/test_*.py -v`
2. Manual verification with trace CLI if available

After all tasks:
1. Full test suite: `pytest tests/ -v`
2. Integration test with real agent loop
3. Verify memory is created correctly in SQLite

---

## Next Steps

After completing this plan:

1. **Commit all changes** to feature branch
2. **Run integration tests** with actual agent loop
3. **Verify memory persistence** by checking `.nanobot/chat.db`
4. **Review coverage** - ensure >95% of calls are traced
