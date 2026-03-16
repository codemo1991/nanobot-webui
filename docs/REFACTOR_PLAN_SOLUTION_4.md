# 方案四：纳博 Agent 架构优化 - 详细技术改动方案

> 版本：1.0  
> 基于 RIPER-5 协议，整合工具选择、并行决策、职责拆分、CrewAI 子 agent 迁移等所有讨论内容。

---

## 一、改动总览

| 阶段 | 改动项 | 新增文件 | 修改文件 | 优先级 |
|------|--------|----------|----------|--------|
| 1 | execute_in_thread_pool 修复 | 0 | 1 | P0 |
| 2 | ToolContext 统一注入 | 1 | 6 | P0 |
| 3 | 工具选择 LLM 语义化 | 1 | 4 | P0 |
| 4 | 并行决策规则优先 | 1 | 2 | P0 |
| 5 | AgentLoop 职责拆分 | 3 | 1 | P1 |
| 6 | 配置与 API 扩展 | 0 | 5 | P1 |
| 7 | CrewAI 子 agent 可选迁移 | 1 | 2 | P2 |

---

## 二、阶段 1：execute_in_thread_pool 修复（P0）

### 2.1 问题分析

**文件**：`nanobot/agent/tools/registry.py`

**当前代码**（约 107-111 行）：

```python
loop = asyncio.get_event_loop()
return await loop.run_in_executor(
    effective_executor,
    lambda: asyncio.run(tool.execute(**params))  # 错误：在主 loop 内嵌套 asyncio.run()
)
```

**问题**：主线程已在运行 asyncio event loop，`asyncio.run()` 会创建新 loop 并导致 `RuntimeError: This event loop is already running` 或不可预测行为。

### 2.2 修复方案

**方案 A：同步包装 + run_until_complete**（推荐）

```python
def _run_async_tool_in_thread() -> str:
    """在线程内创建独立 event loop 运行异步工具。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(tool.execute(**params))
    finally:
        loop.close()

return await loop.run_in_executor(effective_executor, _run_async_tool_in_thread)
```

**方案 B：仅对 sync 工具用线程池**

若 `exec`、`spawn`、`claude_code` 可在执行时改为同步包装（内部 `asyncio.run`），则 `execute_in_thread_pool` 仅调用同步函数。需逐工具评估。

### 2.3 改动点

| 文件 | 行号 | 改动 |
|------|------|------|
| `nanobot/agent/tools/registry.py` | 107-111 | 替换为方案 A 实现 |

---

## 三、阶段 2：ToolContext 统一注入（P0）

### 3.1 新增 `nanobot/agent/tool_context.py`

```python
"""工具执行上下文，单次请求内共享。"""

from dataclasses import dataclass


@dataclass
class ToolContext:
    """工具执行上下文。"""
    channel: str
    chat_id: str
    session_key: str
    media: list[str]
    batch_id: str

    @classmethod
    def from_message(cls, channel: str, chat_id: str, session_key: str, media: list | None = None, batch_id: str = "") -> "ToolContext":
        return cls(
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            media=media or [],
            batch_id=batch_id or "",
        )
```

### 3.2 修改 `nanobot/agent/tools/registry.py`

| 改动 | 说明 |
|------|------|
| `from nanobot.agent.tool_context import ToolContext` | 新增导入 |
| `_context: ToolContext \| None = None` | 新增实例属性 |
| `def set_context(self, ctx: ToolContext) -> None` | 设置上下文 |
| `def get_context(self) -> ToolContext \| None` | 获取上下文 |

### 3.3 修改 `nanobot/agent/tools/base.py`

```python
# Tool 基类新增可选方法
def get_context_hint(self) -> str | None:
    """
    若工具需要上下文，返回需要的字段，如 'channel,chat_id,media,batch_id'。
    返回 None 表示不需要上下文。
    """
    return None
```

### 3.4 修改需要上下文的工具

| 工具 | 实现 get_context_hint | 修改 execute |
|------|----------------------|--------------|
| `spawn.py` | `return "channel,chat_id,media,batch_id"` | 从 `registry.get_context()` 或注入获取 |
| `message.py` | `return "channel,chat_id"` | 同上 |
| `get_subagent_results.py` | `return "channel,chat_id"` | 同上 |
| `cron.py` | `return "channel,chat_id"` | 同上 |
| `claude_code.py` | `return "channel,chat_id"` | 同上 |

**实现方式**：工具通过 `execute(tool_context: ToolContext | None = None, **kwargs)` 或从注册时的 closure 获取。推荐：Registry 在 `execute` 调用前将 `get_context()` 注入到工具（通过 `set_context` 或工具 constructor 传入 registry 引用）。

**简化方案**：工具保持 `set_context(channel, chat_id)` 等接口，AgentLoop 仅改为统一调用 `registry.set_context(ToolContext(...))`，由 Registry 在 `execute` 时分发到各已注册工具的 `set_context`。这样改动最小。

### 3.5 修改 `nanobot/agent/loop.py`

**位置**：`_process_message` 内，约 993-1016 行

**原逻辑**：多处 `spawn_tool.set_context()`、`set_media()`、`set_batch_id()` 等

**新逻辑**：

```python
ctx = ToolContext.from_message(
    channel=msg.channel,
    chat_id=msg.chat_id,
    session_key=msg.session_key,
    media=msg.media or [],
    batch_id=str(uuid.uuid4())[:12],
)
self.tools.set_context(ctx)
```

删除对 `message_tool.set_context`、`spawn_tool.set_context`、`set_media`、`set_batch_id`、`get_subagent_results_tool.set_context`、`cron_tool.set_context`、`claude_code_tool.set_context` 的单独调用。

**Registry 职责**：`set_context(ctx)` 内部遍历 `_tools`，对实现 `set_context` 或 `get_context_hint` 的工具调用其 `set_context(ctx.channel, ctx.chat_id)` 等（保持现有工具接口兼容）。

---

## 四、阶段 3：工具选择 LLM 语义化（P0）

### 4.1 新增 `nanobot/agent/tool_selector.py`

```python
"""工具选择器：LLM 语义选择 + 关键词回退。"""

from typing import Any

from loguru import logger

from nanobot.agent.tools.registry import ToolRegistry
from nanobot.providers.base import LLMProvider


TOOL_SELECTION_PROMPT = """根据用户消息，选择最相关的工具。只返回工具名列表的 JSON，格式：["tool1", "tool2"]。

可用工具及描述：
{tool_list}

用户消息：{message}

要求：1) 必须包含 read_file, write_file, exec, remember, spawn；2) 根据语义补充其他相关工具；3) 最多 12 个；4) 只返回 JSON 数组。"""

# 回退用：原有 ESSENTIAL_TOOLS
FALLBACK_ESSENTIAL = ["read_file", "write_file", "exec", "remember", "spawn"]


class ToolSelector:
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        use_llm: bool = True,
        model: str | None = None,
        max_tools: int = 12,
        fallback_keywords: dict[str, list[str]] | None = None,
    ):
        self.provider = provider
        self.registry = registry
        self.use_llm = use_llm
        self.model = model
        self.max_tools = max_tools
        self._fallback_keywords = fallback_keywords or {}

    async def select_tools(self, message: str) -> list[dict[str, Any]]:
        """
        返回 OpenAI 格式的工具定义列表。
        失败时回退到关键词匹配。
        """
        if not self.use_llm:
            return self._fallback_select(message)

        tool_list = "\n".join(
            f"- {t.name}: {t.description[:80]}..." if len(t.description) > 80 else f"- {t.name}: {t.description}"
            for t in self.registry._tools.values()
        )
        prompt = TOOL_SELECTION_PROMPT.format(tool_list=tool_list, message=message[:500])

        try:
            resp = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model or "",
                max_tokens=300,
                temperature=0.1,
            )
            names = self._parse_json_array(resp.content)
            if names:
                return self._definitions_for_names(names)
        except Exception as e:
            logger.warning(f"Tool selection LLM failed: {e}, using fallback")

        return self._fallback_select(message)

    def _parse_json_array(self, content: str) -> list[str]:
        import json, re
        content = content.strip()
        m = re.search(r'\[[\s\S]*?\]', content)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return []

    def _definitions_for_names(self, names: list[str]) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            t = self.registry.get(n)
            if t:
                result.append(t.to_schema())
            if len(result) >= self.max_tools:
                break
        return result

    def _fallback_select(self, message: str) -> list[dict[str, Any]]:
        """关键词回退（与原 _select_tools_default 逻辑一致）。"""
        selected = set(FALLBACK_ESSENTIAL)
        msg_lower = message.lower()
        for tool_name, keywords in self._fallback_keywords.items():
            if tool_name in selected:
                continue
            if any(kw in msg_lower for kw in keywords):
                selected.add(tool_name)
                if len(selected) >= self.max_tools:
                    break
        for name in self.registry.tool_names:
            if name.startswith("mcp_"):
                selected.add(name)
                if len(selected) >= self.max_tools:
                    break
        return self._definitions_for_names(list(selected))
```

### 4.2 修改 `nanobot/agent/loop.py`

| 改动 | 说明 |
|------|------|
| 导入 | `from nanobot.agent.tool_selector import ToolSelector` |
| `__init__` 参数 | `tool_selector_model: str \| None = None`, `tool_selection_use_llm: bool = True` |
| 初始化 | `self._tool_selector = ToolSelector(provider, self.tools, use_llm=tool_selection_use_llm, model=tool_selector_model or model, fallback_keywords=TOOL_KEYWORDS)` |
| `_select_tools_for_message` | 改为 `async def`，内部 `return await self._tool_selector.select_tools(message)` |
| 调用处 | `selected_tools = await self._select_tools_for_message(msg.content)` |

### 4.3 配置扩展

**`nanobot/config/schema.py`** - `AgentDefaults` 新增：

```python
tool_selector_model: str = ""
tool_selection_use_llm: bool = True
```

---

## 五、阶段 4：并行决策规则优先（P0）

### 5.1 新增 `nanobot/services/parallel_dependency_analyzer.py`

```python
"""基于工具参数的并行依赖分析，无需 LLM。"""

import json
from typing import Any

from loguru import logger

WRITE_TOOLS = {"write_file", "edit_file", "exec", "spawn", "claude_code", "cron", "remember"}
READ_TOOLS = {"read_file", "list_dir", "web_search", "web_fetch"}
MUST_SERIAL_TOOLS = {"message"}


def _extract_paths(args: dict) -> set[str]:
    """提取工具参数中的文件路径。"""
    paths = set()
    for k in ("path", "file_path", "target", "file"):
        v = args.get(k)
        if isinstance(v, str) and v:
            paths.add(v.strip())
    return paths


def analyze(tool_calls: list) -> dict[str, Any]:
    """
    分析工具调用依赖。
    返回: { "can_parallel": bool, "groups": list[list], "reason": str, "need_llm": bool }
    """
    if len(tool_calls) <= 1:
        return {"can_parallel": False, "groups": [tool_calls] if tool_calls else [], "reason": "单个工具", "need_llm": False}

    infos = []
    for tc in tool_calls:
        name = tc.name if hasattr(tc, "name") else tc.get("name", "")
        args = tc.arguments if hasattr(tc, "arguments") else tc.get("arguments", {})
        if not isinstance(args, dict):
            args = {}
        infos.append((name, args))

    names = [n for n, _ in infos]

    # message 必须串行
    if any(n in MUST_SERIAL_TOOLS for n in names):
        return {"can_parallel": False, "groups": [tool_calls], "reason": "message 需串行", "need_llm": False}

    # 同文件写操作冲突
    write_paths = set()
    for name, args in infos:
        if name in WRITE_TOOLS:
            for p in _extract_paths(args):
                if p in write_paths:
                    return {"can_parallel": False, "groups": [tool_calls], "reason": "同文件写冲突", "need_llm": False}
                write_paths.add(p)

    # 仅读操作可并行
    if all(n in READ_TOOLS for n in names):
        return {"can_parallel": True, "groups": [tool_calls], "reason": "仅读操作", "need_llm": False}

    # 均为后台型可并行
    background = {"exec", "spawn", "claude_code", "web_search", "web_fetch", "read_file", "list_dir"}
    if all(n in background for n in names):
        return {"can_parallel": True, "groups": [tool_calls], "reason": "无依赖后台工具", "need_llm": False}

    # 无法确定，交 LLM
    return {"can_parallel": True, "groups": [tool_calls], "reason": "需 LLM 判断", "need_llm": True}
```

### 5.2 修改 `nanobot/services/smart_parallel_decider.py`

在 `should_parallel` 开头插入：

```python
from nanobot.services.parallel_dependency_analyzer import analyze

async def should_parallel(self, tool_calls: list) -> dict[str, Any]:
    if len(tool_calls) <= 1:
        return {"parallel": False, "groups": [tool_calls] if tool_calls else [], "reason": "单个工具"}

    # 规则优先
    rule_result = analyze(tool_calls)
    if not rule_result.get("need_llm", True):
        return {
            "parallel": rule_result["can_parallel"],
            "groups": rule_result["groups"],
            "reason": rule_result["reason"],
        }

    # 原有 LLM 逻辑
    ...
```

---

## 六、阶段 5：AgentLoop 职责拆分（P1）

### 6.1 新增 `nanobot/agent/preprocessor.py`

```python
"""消息预处理：/clear、继续/扩容、build_messages、图片/音频处理。"""

from typing import Any

from nanobot.bus.events import InboundMessage
from nanobot.session.manager import SessionManager, Session

# 常量从 loop 移入或导入
LIMIT_REACHED_MARKER = "<!-- LIMIT_REACHED -->"
CONTINUE_KEYWORDS = {"继续", "continue", "重置", "reset", "继续执行", "继续任务", "go on", "proceed"}
EXPAND_KEYWORDS = {"扩容", "扩大容量", "增加工具数", "扩大工具上限"}
EXPAND_RATIO = 1.2


@dataclass
class PreprocessResult:
    current_message: str
    messages: list[dict[str, Any]]
    image_files: list[str]
    audio_files: list[str]
    expanded_iterations: bool = False  # 是否触发了扩容


class MessagePreprocessor:
    def __init__(self, loop_ref: "AgentLoop"):
        self._loop = loop_ref

    async def preprocess(self, msg: InboundMessage, session: Session) -> PreprocessResult:
        """
        执行预处理，返回 (current_message, messages, image_files, audio_files)。
        包含：/clear、继续/扩容、build_messages、图片/音频分离、inline 识别（主模型不支持视觉时）。
        """
        # 1. 继续/扩容检测
        current_message = msg.content or ""
        if self._handle_continue_expand(msg, session, current_message):
            current_message = "请继续执行上一条消息中未完成的任务..."
            # 或 expanded_iterations = True

        # 2. build_messages
        messages = self._loop.context.build_messages(...)

        # 3. 图片/音频分离
        image_files = [...]
        audio_files = [...]

        # 4. 主模型不支持视觉时的 inline 识别
        if image_files and not self._loop._is_vision_model(...):
            ...

        return PreprocessResult(current_message, messages, image_files, audio_files, ...)
```

**抽取范围**：`_process_message` 中约 983-1115 行（/clear 到 build_messages、图片处理结束）。

### 6.2 新增 `nanobot/agent/loop_runner.py`

```python
"""纯 LLM↔工具循环，无预处理/后处理。"""

from typing import Any, Callable

from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry


@dataclass
class LoopRunnerResult:
    final_content: str | None
    tool_steps: list[dict[str, Any]]
    usage_acc: dict[str, int]
    exit_reason: str | None  # "time"|"iterations"|"loop"|None


class LoopRunner:
    def __init__(
        self,
        provider: LLMProvider,
        context: ContextBuilder,
        tools: ToolRegistry,
        tool_selector: "ToolSelector",
        smart_parallel_decider: Any | None,
        max_iterations: int,
        max_execution_time: int,
        tool_result_max_length: int,
        status_service: Any | None,
        # 需传入的辅助方法引用
        execute_tool_parallel: Callable,
        deduplicate_tool_calls: Callable,
        add_assistant_message: Callable,
        add_tool_result: Callable,
        check_cancelled: Callable,
        ...
    ):
        ...

    async def run(
        self,
        messages: list[dict],
        session_key: str,
        image_files: list[str],
        progress_cb: Callable | None,
    ) -> LoopRunnerResult:
        """执行主循环，返回 (final_content, tool_steps, usage_acc, exit_reason)。"""
        ...
```

**抽取范围**：`_process_message` 中约 1117-1370 行（while 循环、LLM 调用、工具执行、循环检测）。

### 6.3 新增 `nanobot/agent/postprocessor.py`

```python
"""响应后处理：limit notice、session 保存、兜底图片识别。"""

LIMIT_REACHED_MARKER = "<!-- LIMIT_REACHED -->"
EXPAND_RATIO = 1.2


class ResponsePostprocessor:
    def __init__(self, loop_ref: "AgentLoop"):
        self._loop = loop_ref

    def append_limit_notice(self, final_content: str | None, exit_reason: str | None, loop_start: float) -> str:
        """追加 limit 提示。"""
        ...

    def save_session(self, session, msg, final_content, tool_steps, usage_acc, user_message_kwargs) -> None:
        """保存 user/assistant 消息、token 统计。"""
        ...
```

**抽取范围**：约 1466-1542 行（limit notice、session 保存）。

### 6.4 修改 `nanobot/agent/loop.py`

`_process_message` 简化为：

```python
async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
    self._reset_cancel_event(msg.session_key)
    if msg.channel == "system":
        return await self._process_system_message(msg)
    if self.claude_code_manager.resolve_decision(msg.session_key, msg.content):
        return None

    session = self.sessions.get_or_create(msg.session_key)
    ctx = ToolContext.from_message(...)
    self.tools.set_context(ctx)

    # MCP reload 等
    ...

    # 预处理
    pre = await self.preprocessor.preprocess(msg, session)

    # 主循环
    result = await self.loop_runner.run(
        messages=pre.messages,
        session_key=msg.session_key,
        image_files=pre.image_files,
        progress_cb=msg.metadata.get("progress_callback"),
    )

    # 后处理
    final_content = self.postprocessor.append_limit_notice(
        result.final_content, result.exit_reason, loop_start
    )
    self.postprocessor.save_session(session, msg, final_content, result.tool_steps, result.usage_acc, ...)

    # 兜底图片识别
    ...

    return OutboundMessage(...)
```

---

## 七、阶段 6：配置与 API 扩展（P1）

### 7.1 `nanobot/config/schema.py`

```python
# AgentDefaults 新增
tool_selector_model: str = ""
tool_selection_use_llm: bool = True
parallel_use_rule_first: bool = True  # 已在 SmartParallelDecider 实现
```

### 7.2 `nanobot/web/api.py`

- 创建 AgentLoop 时传入 `tool_selector_model`、`tool_selection_use_llm`
- 配置 API 若存在，增加对应字段读写

### 7.3 `nanobot/cli/commands.py`

- AgentLoop 构造参数补充

### 7.4 `nanobot/storage/status_repository.py`、`web-ui`

- 若状态/配置持久化包含这些项，同步更新

---

## 八、阶段 7：CrewAI 子 Agent 可选迁移（P2）

### 8.1 新增 `nanobot/agent/crewai_adapter.py`

```python
"""纳博子 agent 模板 → CrewAI Agent 适配器。"""

from typing import Any, Callable

from nanobot.config.agent_templates import AgentTemplateConfig

# 依赖: pip install crewai

def template_to_crewai_agent(
    template: AgentTemplateConfig,
    workspace: str,
    llm: Any = None,
    tool_factories: dict[str, Callable] | None = None,
) -> "Agent":
    """将纳博模板转为 CrewAI Agent。"""
    from crewai import Agent

    tools = []
    for name in template.tools:
        if name in (tool_factories or {}):
            tools.append(tool_factories[name](workspace=workspace))

    rules_text = "\n".join(f"{i+1}. {r}" for i, r in enumerate(template.rules))
    backstory = f"{template.description}\n\nRules:\n{rules_text}"

    system_static = template.system_prompt.replace("{task}", "[See task description]")
    system_static = system_static.replace("{all_rules}", rules_text)
    system_static = system_static.replace("{workspace}", "(See task description)")

    return Agent(
        role=template.description,
        goal="Complete the assigned task and provide a clear summary.",
        backstory=backstory,
        tools=tools,
        llm=llm,
        system_template=system_static,
    )


def create_crewai_task(agent: "Agent", task: str, workspace: str) -> "Task":
    from crewai import Task
    return Task(description=f"{task}\n\nWorkspace: {workspace}", agent=agent)
```

### 8.2 工具适配

需实现 `NanobotToolAdapter(BaseTool)`，将 nanobot 的 `Tool.execute(**kwargs)` 包装为 CrewAI `_run`。每个 `VALID_TOOLS` 对应一个适配器或工厂。

### 8.3 SubagentManager 集成

- 配置项 `use_crewai_subagent: bool = False`
- `_run_subagent` 中：若 `use_crewai_subagent`，则用 `template_to_crewai_agent` + `Crew().kickoff()` 替代当前 native LLM 循环；否则保持原逻辑

---

## 九、实施顺序与验收

### 9.1 建议顺序

1. **阶段 1**（thread_pool 修复）→ 跑现有用例，确认无 event loop 错误
2. **阶段 2**（ToolContext）→ 全渠道回归，确认消息正常
3. **阶段 3**（工具选择）→ 对比关键词 vs LLM 选择结果   
4. **阶段 4**（并行规则）→ 验证多工具调用延迟下降
5. **阶段 5**（职责拆分）→ 单元测试 LoopRunner
6. **阶段 6**（配置）→ 配置页与 CLI 验证
7. **阶段 7**（CrewAI）→ 可选，单独开关测试

### 9.2 验收标准

| 阶段 | 验收 |
|------|------|
| 1 | `exec`/`spawn`/`claude_code` 在线程池执行无 RuntimeError |
| 2 | Web/飞书/CLI 发送消息，spawn 结果正确回传 |
| 3 | “查这个 url 内容” 能选出 web_fetch |
| 4 | 多 read_file 并行时不再额外调用 LLM |
| 5 | `LoopRunner` 可单测，`_process_message` 行为不变 |
| 6 | 新配置项可保存、读取并生效 |
| 7 | `use_crewai_subagent=True` 时子 agent 可完成简单任务 |

---

## 十、文件改动清单汇总

| 操作 | 路径 |
|------|------|
| 新建 | `nanobot/agent/tool_context.py` |
| 新建 | `nanobot/agent/tool_selector.py` |
| 新建 | `nanobot/services/parallel_dependency_analyzer.py` |
| 新建 | `nanobot/agent/preprocessor.py` |
| 新建 | `nanobot/agent/loop_runner.py` |
| 新建 | `nanobot/agent/postprocessor.py` |
| 新建 | `nanobot/agent/crewai_adapter.py`（P2） |
| 修改 | `nanobot/agent/tools/registry.py` |
| 修改 | `nanobot/agent/tools/base.py` |
| 修改 | `nanobot/agent/tools/spawn.py` |
| 修改 | `nanobot/agent/tools/message.py` |
| 修改 | `nanobot/agent/tools/get_subagent_results.py` |
| 修改 | `nanobot/agent/tools/cron.py` |
| 修改 | `nanobot/agent/tools/claude_code.py` |
| 修改 | `nanobot/agent/loop.py` |
| 修改 | `nanobot/services/smart_parallel_decider.py` |
| 修改 | `nanobot/config/schema.py` |
| 修改 | `nanobot/web/api.py` |
| 修改 | `nanobot/cli/commands.py` |

---

## 十一、向后兼容

| 改动 | 兼容策略 |
|------|----------|
| 工具选择 | `tool_selection_use_llm=False` 时使用关键词回退 |
| 并行决策 | 规则无法判断时仍调用 SmartParallelDecider |
| ToolContext | 旧工具保留 `set_context` 接口，Registry 内部转发 |
| 职责拆分 | `process_direct`、`_process_message` 对外行为不变 |
| CrewAI | 默认关闭，不影响现有子 agent |
