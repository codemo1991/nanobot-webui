# Subagent Backend 注册表重构技术方案

## 1. 文档信息

| 项目 | 说明 |
|------|------|
| 版本 | v1.0 |
| 创建日期 | 2025-03-05 |
| 状态 | 待实施 |
| 参考 | OpenClaw Plugin/Skill 架构、RIPER-5 协议 |

---

## 2. 背景与目标

### 2.1 现状问题

1. **硬编码路由**：`subagent.py` 中 `if backend == "claude_code"` 直接分支，违反开闭原则
2. **耦合紧密**：SubagentManager 直接依赖 ClaudeCodeManager，新增 backend 需修改核心代码
3. **扩展成本高**：每次引入新执行能力（如 Cursor Agent、其他 CLI）都需改动 subagent.py

### 2.2 设计目标

1. **开闭原则**：新增 backend 不修改 subagent.py，仅新增模块并注册
2. **职责分离**：Skill = 说明与配置，Backend 模块 = 实现与注册
3. **配置驱动**：Template 声明 backend 偏好，解析逻辑不写死 backend 名
4. **参考 OpenClaw**：Plugin 注册 Tool，Skill 教 Agent 使用

---

## 3. 目标架构

### 3.1 模块关系

```
┌─────────────────────────────────────────────────────────────────┐
│  nanobot/agent/subagent.py (SubagentManager)                     │
│  - 注入: backend_registry, backend_resolver                      │
│  - resolved = resolver.resolve(template, param, template_config)  │
│  - runner = registry.get(resolved) or registry.get("native")     │
│  - await runner(...)                                             │
│  - 不包含任何 backend 名称的 if/else                              │
└─────────────────────────────────────────────────────────────────┘
        ▲                                    ▲
        │ 注入                                │ 查表
        │                                    │
┌───────┴──────────┐              ┌─────────┴─────────────────────┐
│ BackendResolver  │              │ BackendRegistry               │
│ - 只读配置        │              │ - register(name, runner, check)│
│ - 无 backend 字面量│              │ - get(name) -> runner | None   │
└──────────────────┘              │ - 由各 backend 模块在 import 时填充│
                                  └─────────────────────────────────┘
                                                    ▲
                                                    │ 注册
                                  ┌─────────────────┴─────────────────┐
                                  │ nanobot/agent/backends/            │
                                  │ - native.py      (内置，始终存在)   │
                                  │ - claude_code.py (import 时注册)   │
                                  │ - [未来] xxx.py                    │
                                  └───────────────────────────────────┘
```

### 3.2 数据流

```
spawn(task, template="coder", backend="auto")
        │
        ▼
BackendResolver.resolve(template, "auto", template_config)
        │ 规则: param 显式指定 > template.backend > "native"
        ▼
resolved_backend = "claude_code"
        │
        ▼
runner = BackendRegistry.get("claude_code")
        │ 若不可用则 get("native")
        ▼
await runner(task_id, task, label, origin, template, batch_id)
```

---

## 4. 详细设计

### 4.1 BackendRegistry

**路径**：`nanobot/agent/backend_registry.py`（新建）

**职责**：维护 backend 名称到执行器的映射，提供查表接口。

```python
# 伪代码
from typing import Callable, Awaitable, Any

Runner = Callable[..., Awaitable[None]]  # (task_id, task, label, origin, template, batch_id) -> None
AvailabilityCheck = Callable[[], bool]

class BackendRegistry:
    _backends: dict[str, tuple[Runner, AvailabilityCheck]] = {}
    
    @classmethod
    def register(cls, name: str, runner: Runner, available_check: AvailabilityCheck) -> None:
        """注册 backend。由各 backend 模块在 import 时调用。"""
        cls._backends[name] = (runner, available_check)
    
    @classmethod
    def get(cls, name: str) -> Runner | None:
        """获取可用的 runner，不可用时返回 None。"""
        entry = cls._backends.get(name)
        if not entry:
            return None
        runner, check = entry
        if not check():
            return None
        return runner
    
    @classmethod
    def list_available(cls) -> list[str]:
        """列出所有已注册且当前可用的 backend 名称。"""
        return [n for n, (_, c) in cls._backends.items() if c()]
```

### 4.2 BackendResolver

**路径**：`nanobot/agent/backend_resolver.py`（新建）

**职责**：根据 spawn 参数、template 配置解析出最终 backend 名称。不包含任何具体 backend 名的硬编码逻辑。

```python
# 伪代码
class BackendResolver:
    def __init__(self, agent_template_manager, backend_registry):
        self._template_manager = agent_template_manager
        self._registry = backend_registry
    
    def resolve(self, template: str, spawn_param: str) -> str:
        """
        解析规则（按优先级）：
        1. spawn_param in ("native", "claude_code", ...) 且非 "auto" -> 使用 spawn_param
        2. template_config.backend 存在且 registry 中可用 -> 使用 template_config.backend
        3. 兼容：template 名含 "coder"/"claude" 且 "claude_code" 可用 -> "claude_code"（迁移期）
        4. 默认 -> "native"
        """
        available = set(self._registry.list_available())
        if spawn_param != "auto" and spawn_param in available:
            return spawn_param
        if spawn_param == "native":
            return "native"
        
        cfg = self._template_manager.get_template(template) if self._template_manager else None
        if cfg and hasattr(cfg, "backend") and cfg.backend and cfg.backend in available:
            return cfg.backend
        
        # 向后兼容：旧 template 无 backend 字段时，按 template 名推断
        if ("coder" in template.lower() or "claude" in template.lower()) and "claude_code" in available:
            return "claude_code"
        
        return "native"
```

### 4.3 Backend 模块

**路径**：`nanobot/agent/backends/`（新建目录）

#### 4.3.1 native.py（内置）

```python
# nanobot/agent/backends/native.py
# native 为默认 backend，不注册也可在 SubagentManager 中特殊处理
# 或：注册为 "native"，available_check 恒为 True
from nanobot.agent.backend_registry import BackendRegistry

def _available() -> bool:
    return True

# 注意：native 的 runner 是 SubagentManager._run_subagent 中的现有 native 逻辑
# 需要将 native 路径提取为独立函数，或由 SubagentManager 在 registry.get 返回 None 时走内置 native
# 方案：native 不注册，SubagentManager 在 runner is None 时走内置 native 分支
```

#### 4.3.2 claude_code.py

```python
# nanobot/agent/backends/claude_code.py
"""Claude Code CLI backend. 在 import 时向 BackendRegistry 注册。"""
from nanobot.agent.backend_registry import BackendRegistry

# 延迟导入，避免循环依赖
_claude_code_manager = None

def _set_manager(mgr):
    global _claude_code_manager
    _claude_code_manager = mgr

def _available() -> bool:
    if _claude_code_manager is None:
        return False
    return _claude_code_manager.check_claude_available()

async def _run(task_id, task, label, origin, template="", batch_id=None):
    # 调用 SubagentManager._run_via_claude_code 的逻辑
    # 需要 SubagentManager 将 _run_via_claude_code 抽成可注入的 runner，或
    # claude_code backend 模块持有 ClaudeCodeManager 引用，自行实现 runner
    ...

def register(claude_code_manager):
    """由 AgentLoop 在初始化时调用，传入 ClaudeCodeManager。"""
    _set_manager(claude_code_manager)
    BackendRegistry.register("claude_code", _run, _available)
```

**说明**：claude_code 的 runner 实现依赖 ClaudeCodeManager，需在 AgentLoop 创建 SubagentManager 之前完成注册。可选方案：

- **方案 A**：BackendRegistry.register 时传入 runner，runner 为闭包，捕获 ClaudeCodeManager
- **方案 B**：AgentLoop 先创建 ClaudeCodeManager，再调用 `backends.claude_code.register(manager)`，再创建 SubagentManager
- **方案 C（推荐）**：Backend 模块不持有 runner 实现，而是注册时传入 `(manager, "method_name")`，由 Registry 在 get 时返回 `lambda *a, **k: getattr(manager, method)(*a, **k)`。这样 `_run_via_claude_code` 仍保留在 SubagentManager 中，仅通过注册表间接调用，无需大范围重构。

### 4.4 AgentTemplateConfig 扩展

**路径**：`nanobot/config/agent_templates.py`

**变更**：新增 `backend` 字段。

```python
@dataclass
class AgentTemplateConfig:
    # ... 现有字段 ...
    backend: Optional[str] = None  # "auto" | "native" | "claude_code" | 未来其他
```

**builtin_templates_data.py**：为 coder、claude-coder 添加 `backend: "claude_code"`。

### 4.5 SubagentManager 改造

**路径**：`nanobot/agent/subagent.py`

**变更要点**：

1. **构造函数**：
   - 保留 `claude_code_manager` 参数（用于传递给 claude_code backend 模块注册）
   - 新增 `backend_registry`、`backend_resolver` 参数；若未传入则使用默认实例
   - 在 `__init__` 末尾调用 `backends.claude_code.register(self._claude_code_manager)`（或由 AgentLoop 统一注册）

2. **移除**：
   - `_resolve_backend` 方法（逻辑迁移到 BackendResolver）
   - `if backend == "claude_code"` 分支

3. **新增/修改**：
   - `_run_subagent` 开头：
     ```python
     resolved = self._backend_resolver.resolve(template, backend)
     runner = self._backend_registry.get(resolved)
     if runner is not None:
         await runner(task_id, task, label, origin, template=template, batch_id=batch_id)
         return
     # runner 为 None 或 resolved 为 "native"：走内置 native 路径
     ```

4. **native 路径**：保持现有 `_run_subagent` 中 native LLM 循环逻辑不变，作为默认分支。

### 4.6 AgentLoop 改造

**路径**：`nanobot/agent/loop.py`

**变更**：

1. 创建 BackendRegistry、BackendResolver 实例
2. 先创建 ClaudeCodeManager
3. 调用 `backends.claude_code.register(claude_code_manager)` 完成 claude_code 注册
4. 创建 SubagentManager 时传入 `backend_registry`、`backend_resolver`

### 4.7 SpawnTool 改造

**路径**：`nanobot/agent/tools/spawn.py`

**变更**：`backend` 的 `enum` 可改为动态从 `BackendRegistry.list_available()` 获取，或保持 `["auto", "native", "claude_code"]` 以兼容现有模型输出。建议首版保持静态 enum，后续再动态化。

---

## 5. 文件变更清单

| 操作 | 路径 | 说明 |
|------|------|------|
| 新建 | `nanobot/agent/backend_registry.py` | BackendRegistry 类 |
| 新建 | `nanobot/agent/backend_resolver.py` | BackendResolver 类 |
| 新建 | `nanobot/agent/backends/__init__.py` | 包初始化，触发 backend 模块 import |
| 新建 | `nanobot/agent/backends/native.py` | native 占位或说明（native 可内置不注册） |
| 新建 | `nanobot/agent/backends/claude_code.py` | claude_code 注册与 runner |
| 修改 | `nanobot/config/agent_templates.py` | AgentTemplateConfig 增加 backend 字段 |
| 修改 | `nanobot/config/builtin_templates_data.py` | coder、claude-coder 增加 backend |
| 修改 | `nanobot/agent/subagent.py` | 注入 registry/resolver，移除硬编码分支 |
| 修改 | `nanobot/agent/loop.py` | 创建 registry、resolver，注册 claude_code，传入 subagent |

---

## 6. 迁移与兼容

### 6.1 向后兼容

1. **Template 无 backend 字段**：BackendResolver 保留「template 名含 coder/claude 则用 claude_code」的兼容逻辑
2. **spawn 参数**：保持 `backend: "auto" | "native" | "claude_code"` 不变
3. **数据库**：已有 template 的 config_json 无 backend 时，`from_json` 默认 `backend=None`

### 6.2 迁移步骤

1. 部署新代码
2. 对 coder、claude-coder 等 template 执行 DB 迁移，写入 `backend: "claude_code"`
3. 兼容逻辑可保留 1～2 个版本后移除

---

## 7. 新增 Backend 流程（重构后）

| 步骤 | 操作 | 是否修改 subagent.py |
|------|------|----------------------|
| 1 | 新建 `nanobot/agent/backends/xxx.py`，实现 runner 和 available_check | 否 |
| 2 | 在模块中调用 `BackendRegistry.register("xxx", runner, check)` | 否 |
| 3 | 在 `backends/__init__.py` 中 `import .xxx` 触发注册 | 否 |
| 4 | 在 AgentLoop 中传入 backend 所需依赖（若有）并完成注册 | 否 |
| 5 | 新建或更新 Skill（SKILL.md），说明使用方式 | 否 |
| 6 | 配置 template 的 `backend: "xxx"` | 否 |

**结论**：新增 backend 无需修改 subagent.py，符合开闭原则。

---

## 8. 测试要点

1. **单元测试**：BackendResolver.resolve 各分支（spawn_param、template.backend、兼容逻辑、默认）
2. **集成测试**：spawn(template="coder", backend="auto") 正确路由到 claude_code
3. **降级测试**：Claude Code 不可用时自动回退到 native
4. **兼容测试**：旧 template（无 backend 字段）仍能正确解析

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 注册顺序导致 claude_code 不可用 | AgentLoop 显式在创建 SubagentManager 前完成注册 |
| 循环导入 | 使用延迟导入、闭包注入依赖 |
| 性能 | BackendResolver 使用缓存（若需要），Registry 查表为 O(1) |

---

## 10. 附录：Runner 签名

所有 backend runner 需统一签名：

```python
async def runner(
    task_id: str,
    task: str,
    label: str,
    origin: dict[str, str],
    template: str = "",
    batch_id: str | None = None,
    # 若需要 SubagentManager 的其他上下文，可通过闭包或依赖注入传递
) -> None:
    ...
```

SubagentManager 调用时需传入 `self` 引用或通过其他方式传递 workspace、bus 等依赖。可选：runner 注册时传入 `(manager, method)` 或 factory，由 Registry 在 get 时绑定。

---

## 11. 实施检查清单

```
[ ] 1. 新建 nanobot/agent/backend_registry.py
[ ] 2. 新建 nanobot/agent/backend_resolver.py
[ ] 3. 新建 nanobot/agent/backends/ 目录及 __init__.py
[ ] 4. 新建 nanobot/agent/backends/claude_code.py，实现注册逻辑
[ ] 5. 修改 AgentTemplateConfig，增加 backend 字段
[ ] 6. 修改 builtin_templates_data，为 coder/claude-coder 添加 backend
[ ] 7. 修改 SubagentManager：注入 registry/resolver，移除 _resolve_backend 和硬编码分支
[ ] 8. 修改 AgentLoop：创建 registry、resolver，注册 claude_code，传入 subagent
[ ] 9. 数据库迁移：为已有 coder/claude-coder 模板补充 backend 字段（可选，兼容逻辑可覆盖）
[ ] 10. 更新 SpawnTool 的 backend enum 描述（可选）
[ ] 11. 单元测试 BackendResolver
[ ] 12. 集成测试 spawn -> claude_code 路由
```
