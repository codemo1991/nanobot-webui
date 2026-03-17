# 微内核动态阈值 + 失败累积 技术方案

> 版本：1.1  
> 状态：已实现  
> 目标：将固定阈值改为动态阈值，并引入失败累积机制，使微内核委托更智能、更早响应卡壳场景

---

## 1. 现状与问题

### 1.1 当前实现

- **固定阈值**：`microkernel_escalation_threshold = 10`，当 `len(tool_steps) >= 10` 且仍有 `tool_calls` 时触发委托
- **无失败感知**：不区分成功/失败步骤，10 次成功与 10 次全失败触发时机相同
- **无上下文适应**：简单任务和复杂任务使用同一阈值

### 1.2 问题

| 问题 | 影响 |
|------|------|
| 阈值一刀切 | 简单任务可能过早升级；复杂任务可能过晚升级 |
| 失败无感知 | 连续失败时仍要等到 10 次才升级，浪费 token 与时间 |
| 无收敛判断 | 无法识别「正在取得进展」vs「原地打转」 |

---

## 2. 设计目标

1. **动态阈值**：基础阈值可随任务特征调整（如 spawn 多、失败多则降低）
2. **失败累积**：连续或累计失败达到一定数量即提前触发
3. **向后兼容**：保留固定阈值模式作为 fallback，配置可切换
4. **可观测**：日志/指标中记录触发原因（count / failure / score）

---

## 3. 核心设计

### 3.1 失败判定规则

从 `tool_steps` 中识别失败步骤，规则如下：

```python
def _is_step_failed(step: dict) -> bool:
    """判断单步是否为失败。"""
    result = step.get("result", "")
    if not result or not isinstance(result, str):
        return False
    # 1. 标准化错误前缀
    if result.startswith("[ERROR]") or result.startswith("[RETRYABLE]"):
        return True
    # 2. 传统 Error: 前缀
    if result.strip().lower().startswith("error:"):
        return True
    # 3. 常见失败关键词（可配置）
    fail_keywords = ("failed", "失败", "exception", "timeout", "超时")
    if any(kw in result.lower() for kw in fail_keywords):
        return True
    return False
```

### 3.2 失败累积指标

| 指标 | 含义 | 用途 |
|------|------|------|
| `failure_count` | 本回合累计失败步数 | 失败过多即触发 |
| `consecutive_failures` | 最近连续失败步数 | 连续卡壳即触发 |
| `failure_ratio` | 失败数 / 总步数 | 动态调整阈值 |

### 3.3 动态阈值公式（已实现）

**工具复杂度基础阈值**：

- 简单工具（read_file, list_dir）：15
- 中等工具（exec, web_search, write_file 等）：10
- 复杂工具（spawn）：5

**失败累积系数**：

- 0–2 次失败：不降
- 3–5 次失败：降 50%（×0.5）
- 6+ 次失败：降 75%（×0.25）

```
effective_threshold = base_threshold × (1 - failure_reduction)
```

**示例**：

| 场景 | 工具类型 | base | 失败数 | effective | 触发步数 |
|------|----------|------|--------|-----------|----------|
| 全成功 | 简单 | 15 | 0 | 15 | 15 |
| 全成功 | 复杂 | 5 | 0 | 5 | 5 |
| 4 失败 | 中等 | 10 | 4 | 5 | 5 |
| 6 失败 | 简单 | 15 | 6 | 3 | 3 |

### 3.4 独立失败触发（短路）

除动态阈值外，增加**失败短路**：满足任一条即立即委托，不等步数：

```
触发条件（OR）：
1. 动态阈值：len(tool_steps) >= effective_threshold 且 has_tool_calls
2. 失败短路：consecutive_failures >= failure_shortcut_threshold（默认 5）
3. 失败短路：failure_count >= failure_count_shortcut（默认 6）
```

---

## 4. 配置设计

### 4.1 新增/扩展配置项

```python
# nanobot/config/schema.py - AgentDefaults

# 微内核委托配置（扩展）
microkernel_escalation_enabled: bool = True
microkernel_escalation_mode: str = "dynamic"  # "fixed" | "dynamic"
microkernel_escalation_threshold: int = 10     # 基础阈值（fixed 模式直接使用）

# 动态阈值参数（仅 dynamic 模式生效）
microkernel_failure_weight: int = 1          # 每失败 1 步降低的阈值
microkernel_failure_max_penalty: int = 5       # 失败惩罚上限
microkernel_consecutive_penalty: int = 2       # 连续失败>=3 时的额外惩罚
microkernel_consecutive_penalty_heavy: int = 4  # 连续失败>=5 时的额外惩罚

# 失败短路（任一满足即触发，不等步数）
microkernel_failure_shortcut_consecutive: int = 5   # 连续失败 N 步即触发
microkernel_failure_shortcut_count: int = 6         # 累计失败 N 步即触发
```

### 4.2 配置示例

```yaml
agents:
  defaults:
    microkernel_escalation_enabled: true
    microkernel_escalation_mode: dynamic   # 使用动态阈值
    microkernel_escalation_threshold: 10

    # 动态阈值
    microkernel_failure_weight: 1
    microkernel_failure_max_penalty: 5
    microkernel_consecutive_penalty: 2
    microkernel_consecutive_penalty_heavy: 4

    # 失败短路
    microkernel_failure_shortcut_consecutive: 5
    microkernel_failure_shortcut_count: 6
```

---

## 5. 实现规范

### 5.1 新增方法

```python
# nanobot/agent/loop.py

def _is_step_failed(self, step: dict) -> bool:
    """判断单步是否为失败。"""
    # 见 3.1 节

def _compute_escalation_metrics(self, tool_steps: list[dict]) -> dict:
    """计算升级相关指标。"""
    failure_count = sum(1 for s in tool_steps if self._is_step_failed(s))
    consecutive = 0
    for s in reversed(tool_steps):
        if self._is_step_failed(s):
            consecutive += 1
        else:
            break
    return {
        "failure_count": failure_count,
        "consecutive_failures": consecutive,
        "total_steps": len(tool_steps),
        "failure_ratio": failure_count / len(tool_steps) if tool_steps else 0,
    }

def _compute_effective_threshold(self, tool_steps: list[dict]) -> int:
    """计算动态有效阈值。"""
    if self._microkernel_escalation_mode == "fixed":
        return self._microkernel_escalation_threshold

    m = self._compute_escalation_metrics(tool_steps)
    base = self._microkernel_escalation_threshold
    fw = getattr(self, "_microkernel_failure_weight", 1)
    max_p = getattr(self, "_microkernel_failure_max_penalty", 5)
    cp = getattr(self, "_microkernel_consecutive_penalty", 2)
    cph = getattr(self, "_microkernel_consecutive_penalty_heavy", 4)

    failure_penalty = min(m["failure_count"] * fw, max_p)
    consecutive_penalty = 0
    if m["consecutive_failures"] >= 5:
        consecutive_penalty = cph
    elif m["consecutive_failures"] >= 3:
        consecutive_penalty = cp

    effective = max(1, base - failure_penalty - consecutive_penalty)
    return effective

def _should_escalate_to_microkernel(
    self, tool_steps: list[dict], response_has_tool_calls: bool
) -> tuple[bool, str]:
    """
    判断是否应委托微内核。
    Returns: (should_escalate, reason)
    """
    if not self._microkernel_escalation_enabled or not response_has_tool_calls:
        return False, ""

    m = self._compute_escalation_metrics(tool_steps)
    shortcut_consecutive = getattr(self, "_microkernel_failure_shortcut_consecutive", 5)
    shortcut_count = getattr(self, "_microkernel_failure_shortcut_count", 6)

    # 失败短路
    if m["consecutive_failures"] >= shortcut_consecutive:
        return True, f"consecutive_failures={m['consecutive_failures']}"
    if m["failure_count"] >= shortcut_count:
        return True, f"failure_count={m['failure_count']}"

    # 动态/固定阈值
    effective = self._compute_effective_threshold(tool_steps)
    if len(tool_steps) >= effective:
        return True, f"steps={len(tool_steps)}>=effective_threshold={effective}"

    return False, ""
```

### 5.2 主循环调用处修改

**原逻辑**（`loop.py` 约 1696-1716 行）：

```python
if (
    self._microkernel_escalation_enabled
    and len(tool_steps) >= self._microkernel_escalation_threshold
):
```

**新逻辑**：

```python
should_escalate, reason = self._should_escalate_to_microkernel(
    tool_steps, response.has_tool_calls
)
if should_escalate:
    logger.info(
        "[AgentLoop] Microkernel escalation triggered: %s (steps=%d)",
        reason, len(tool_steps),
    )
    # ... 原有 delegate 逻辑 ...
```

### 5.3 日志与可观测性

触发时记录：

- `escalation_reason`：`consecutive_failures` / `failure_count` / `steps>=effective_threshold`
- `tool_steps_count`、`failure_count`、`consecutive_failures`、`effective_threshold`

便于后续分析调参。

---

## 6. 实施清单

| 序号 | 任务 | 文件 |
|------|------|------|
| 1 | 扩展 AgentDefaults 配置项 | `nanobot/config/schema.py` |
| 2 | AgentLoop 构造函数读取新配置 | `nanobot/agent/loop.py` |
| 3 | 实现 `_is_step_failed` | `nanobot/agent/loop.py` |
| 4 | 实现 `_compute_escalation_metrics` | `nanobot/agent/loop.py` |
| 5 | 实现 `_compute_effective_threshold` | `nanobot/agent/loop.py` |
| 6 | 实现 `_should_escalate_to_microkernel` | `nanobot/agent/loop.py` |
| 7 | 替换主循环中的阈值判断逻辑 | `nanobot/agent/loop.py` |
| 8 | API/CLI 传入新参数（若需要） | `nanobot/web/api.py`, `nanobot/cli/commands.py` |
| 9 | 前端配置项（可选） | `web-ui` |

---

## 7. 与现有复杂度分数的关系

`main-agent-microkernel-optimized-spec.md` 中已有 `calc_escalation_score` 设计（含 `repeated_failures`）。本方案与之一致且可并存：

- **本方案**：在「工具次数 + 失败」维度做动态阈值与短路，实现简单、易调参
- **分数模式**：可作后续扩展，将 `failure_count` / `consecutive_failures` 作为 `calc_escalation_score` 的输入，实现 `score` 模式

建议先落地本方案，再视需要接入分数模式。

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 失败误判（成功但含 "failed" 等词） | 优先依赖 `[ERROR]`/`[RETRYABLE]`/`Error:` 前缀，关键词作为补充，可配置关闭 |
| 过早升级 | 保留 `fixed` 模式，短路阈值可调高 |
| 配置膨胀 | 新参数均有合理默认值，多数场景零配置可用 |
