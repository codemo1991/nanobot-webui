# 纳博 Agent 聚焦改造方案（零外部框架）

> 版本：1.0  
> 聚焦：主 agent 意图识别、子 agent 聚合、ToolContext 统一注入、并行决策规则优先。  
> **不引入** LangChain、CrewAI 等外部框架。

---

## 一、改动总览

| 阶段 | 改动项 | 新增文件 | 修改文件 | 目的 |
|------|--------|----------|----------|------|
| 1 | LLM 工具选择 | 1 | 2 | 意图识别增强 |
| 2 | 主 agent 系统提示增强 | 0 | 1 | 何时 spawn、如何用子 agent 结果 |
| 3 | 子 agent 聚合优化 | 0 | 1 | batch 汇总与 announce 质量 |
| 4 | ToolContext 统一注入 | 1 | 7 | 上下文注入简化 |
| 5 | 并行决策规则优先 | 1 | 1 | 减少 LLM 调用、降延迟 |
| 6 | execute_in_thread_pool 修复 | 0 | 1 | 避免 event loop 嵌套问题 |

---

## 二、阶段 1：LLM 工具选择

### 2.1 新增 `nanobot/agent/tool_selector.py`

```python
"""工具选择器：LLM 语义选择 + 关键词回退。无外部依赖。"""

from typing import Any
import json
import re

from loguru import logger

from nanobot.agent.tools.registry import ToolRegistry
from nanobot.providers.base import LLMProvider


TOOL_SELECTION_PROMPT = """根据用户消息，选择最相关的工具。只返回 JSON 数组，格式：["tool1", "tool2"]。

可用工具及描述：
{tool_list}

用户消息：{message}

要求：
1. 必须包含：read_file, write_file, exec, remember, spawn
2. 根据语义补充其他相关工具（如：查 url/网页→web_fetch，搜索→web_search，编辑文件→edit_file）
3. 最多 12 个工具
4. 只返回 JSON 数组，不要其他内容"""

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
        """返回 OpenAI 格式的工具定义列表。失败时回退到关键词匹配。"""
        if not self.use_llm:
            return self._fallback_select(message)

        tools = list(self.registry._tools.values())
        tool_list = "\n".join(
            f"- {t.name}: {(t.description or '')[:100]}"
            for t in tools
        )
        prompt = TOOL_SELECTION_PROMPT.format(tool_list=tool_list, message=(message or "")[:500])

        try:
            resp = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model or "",
                max_tokens=300,
                temperature=0.1,
            )
            names = self._parse_json_array(resp.content or "")
            if names:
                return self._definitions_for_names(names)
        except Exception as e:
            logger.warning(f"Tool selection LLM failed: {e}, using fallback")

        return self._fallback_select(message)

    def _parse_json_array(self, content: str) -> list[str]:
        content = content.strip()
        m = re.search(r'\[[\s\S]*?\]', content)
        if m:
            try:
                arr = json.loads(m.group())
                return [str(x) for x in arr] if isinstance(arr, list) else []
            except json.JSONDecodeError:
                pass
        return []

    def _definitions_for_names(self, names: list[str]) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for n in names:
            n = str(n).strip()
            if not n or n in seen:
                continue
            seen.add(n)
            t = self.registry.get(n)
            if t:
                result.append(t.to_schema())
            if len(result) >= self.max_tools:
                break
        return result

    def _fallback_select(self, message: str) -> list[dict[str, Any]]:
        selected = set(FALLBACK_ESSENTIAL)
        msg_lower = (message or "").lower()
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

### 2.2 修改 `nanobot/agent/loop.py`

| 位置 | 改动 |
|------|------|
| 导入 | `from nanobot.agent.tool_selector import ToolSelector` |
| `__init__` | 新增参数 `tool_selector_model: str \| None = None`、`tool_selection_use_llm: bool = True`；在 `self.tools` 初始化后创建 `self._tool_selector = ToolSelector(provider, self.tools, use_llm=tool_selection_use_llm, model=tool_selector_model or self.model, fallback_keywords=TOOL_KEYWORDS)` |
| `_select_tools_for_message` | 改为 `async def _select_tools_for_message(self, message: str) -> list[dict[str, Any]]`，内部 `return await self._tool_selector.select_tools(message or "")` |
| 调用处（约 1197 行） | `selected_tools = await self._select_tools_for_message(msg.content)` |

### 2.3 配置（可选）

**`nanobot/config/schema.py`** - `AgentDefaults` 新增：

```python
tool_selector_model: str = ""
tool_selection_use_llm: bool = True
```

在 `nanobot/web/api.py`、`nanobot/cli/commands.py` 创建 AgentLoop 时传入对应参数。

---

## 三、阶段 2：主 Agent 系统提示增强

### 3.1 修改 `nanobot/agent/context.py`

在 `DEFAULT_IDENTITY_CONTENT` 中，`## Subagent Templates` 之前插入：

```python
DEFAULT_IDENTITY_CONTENT = """# nanobot 🐈

You are nanobot, a helpful AI assistant.

## Behavior Guidelines

- Be helpful, accurate, and concise
- Use tools when needed, explain what you're doing
- When user says "记住/remember", call the remember tool to persist the information
- For normal conversation, respond with text directly. Only use the 'message' tool for cross-channel messaging.

## When to Use spawn (Subagent)

- **Use spawn** for tasks that need dedicated capability or longer processing: image/voice analysis (vision, voice template), deep research (researcher), code implementation (coder), data analysis (analyst)
- **Do NOT spawn** for simple one-off operations: reading a file, running a command, web search — do these yourself
- **Avoid duplicate spawn**: only spawn once per logical task; do not create similar subagents for the same request
- **Use subagent results**: when you receive [Subagent completed] or batch summary, synthesize the results in your reply; do not ignore or misrepresent them

## Subagent Templates

Use spawn tool to delegate tasks to subagents. Available templates are listed in the tool description."""
```

若用户已通过 Web 配置覆盖 `identity_content`，则需在 **首次部署或升级说明** 中提示：可将上述「When to Use spawn」段落加入自定义 identity。

### 3.2 兼容已有配置

- 从 DB 或 IDENTITY.md 读取的 identity 保持不变
- 仅调整 `DEFAULT_IDENTITY_CONTENT`（未自定义 identity 时生效）
- 可选：在 `runtime_suffix` 中追加「When to Use spawn」的精简版，确保即使用户 identity 未包含，也有基础指引

---

## 四、阶段 3：子 Agent 聚合优化

### 4.1 优化 `_generate_batch_summary`（Web 渠道 batch 汇总）

**文件**：`nanobot/agent/subagent.py`，约 1069-1094 行

**原 prompt**：

```python
summary_prompt = (
    "以下是多个子任务的执行结果（含任务指令与结果），请用 2-4 句话综合总结，自然回复用户。"
    "不要逐条复述，要提炼关键结论。不要提 subagent、task_id 等技术细节。\n\n"
    f"{combined_preview}"
)
```

**改为**：

```python
BATCH_SUMMARY_SYSTEM = """你是协调子任务的主 agent，负责综合多子任务结果并向用户汇报。

输出要求：
1. 总体结论：1-2 句概括所有任务完成情况
2. 分任务要点：按任务逐一列出关键结论（每项 1-2 句）
3. 建议下一步：如有未完成或需用户决策的内容，简要说明

要求：简洁、结构化，不要提及 subagent、task_id 等技术细节。"""

summary_prompt = f"""以下是多个子任务的执行结果，请按「总体结论 → 分任务要点 → 建议下一步」的结构综合回复用户。

{combined_preview}"""

messages = [
    {"role": "system", "content": BATCH_SUMMARY_SYSTEM},
    {"role": "user", "content": summary_prompt},
]
```

### 4.2 优化 `_announce_result`（单条子 agent 完成通知）

**文件**：`nanobot/agent/subagent.py`，约 1293-1301 行

**原**：

```python
announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""
```

**改为**：

```python
result_preview = (result or "")[:800] + ("..." if len(result or "") > 800 else "")

announce_content = f"""[子 agent 完成] {label}

**任务**：{task[:200]}{'...' if len(task) > 200 else ''}

**结果摘要**：
{result_preview}

请基于以上结果，用自然语言向用户汇报（1-3 句）。若有多个子 agent 结果，需综合后统一回复。不要提及 subagent、task_id 等技术细节。"""
```

### 4.3 优化 `_announce_batch_result`（非 Web 渠道 batch 通知）

**文件**：`nanobot/agent/subagent.py`，约 1135 行

**原**：

```python
content += "\n\nSummarize the above subagent results naturally for the user in 2-4 sentences. Do not mention technical details."
```

**改为**：

```python
content += """

请按「总体结论 → 各任务要点」结构，用 2-4 句话向用户汇报。不要逐条复述，要提炼关键结论。不要提及 subagent、task_id 等技术细节。"""
```

---

## 五、阶段 4：ToolContext 统一注入

### 5.1 新增 `nanobot/agent/tool_context.py`

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
    def from_message(
        cls,
        channel: str,
        chat_id: str,
        session_key: str,
        media: list[str] | None = None,
        batch_id: str = "",
    ) -> "ToolContext":
        return cls(
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            media=media or [],
            batch_id=batch_id or "",
        )
```

### 5.2 修改 `nanobot/agent/tools/registry.py`

| 改动 | 说明 |
|------|------|
| 导入 | `from nanobot.agent.tool_context import ToolContext` |
| 属性 | `self._context: ToolContext | None = None` |
| 方法 | `def set_context(self, ctx: ToolContext) -> None` |
| 方法 | `def get_context(self) -> ToolContext | None` |

`set_context` 实现（简化方案，保持工具原有接口）：

```python
def set_context(self, ctx: ToolContext) -> None:
    """设置当前请求的上下文，并分发给需要上下文的工具。"""
    self._context = ctx
    for tool in self._tools.values():
        if hasattr(tool, "set_context"):
            tool.set_context(ctx.channel, ctx.chat_id)
        if hasattr(tool, "set_media"):
            tool.set_media(ctx.media)  # SpawnTool
        if hasattr(tool, "set_batch_id"):
            tool.set_batch_id(ctx.batch_id)  # SpawnTool
```

### 5.3 修改各工具的 `set_context` 兼容性

确保以下工具的 `set_context(channel, chat_id)` 保持不变，由 Registry 在 `set_context(ctx)` 时调用：
- `message.py`：已有 `set_context(channel, chat_id)`
- `get_subagent_results.py`：已有
- `cron.py`：已有
- `claude_code.py`：已有

`spawn.py` 除 `set_context` 外，还有 `set_media`、`set_batch_id`，Registry 的 `set_context(ctx)` 需依次调用这三者。

### 5.4 修改 `nanobot/agent/loop.py`

**位置**：`_process_message` 内，约 992-1015 行

**删除**：对 `message_tool.set_context`、`spawn_tool.set_context`、`spawn_tool.set_media`、`spawn_tool.set_batch_id`、`get_subagent_results_tool.set_context`、`cron_tool.set_context`、`claude_code_tool.set_context` 的单独调用。

**替换为**：

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

**`_process_system_message` 内**（约 1709-1730 行）：同样替换为 ToolContext 统一注入：

```python
session_key = f"{origin_channel}:{origin_chat_id}"
ctx = ToolContext.from_message(
    channel=origin_channel,
    chat_id=origin_chat_id,
    session_key=session_key,
    media=[],  # system 消息无 media
    batch_id="",
)
self.tools.set_context(ctx)
```

删除对 message_tool、spawn_tool、get_subagent_results_tool、cron_tool、claude_code_tool 的单独 set_context 调用。

---

## 六、阶段 5：并行决策规则优先

### 6.1 新增 `nanobot/services/parallel_dependency_analyzer.py`

```python
"""基于工具参数的并行依赖分析，无需 LLM。"""

from typing import Any

WRITE_TOOLS = {"write_file", "edit_file", "exec", "spawn", "claude_code", "cron", "remember"}
READ_TOOLS = {"read_file", "list_dir", "web_search", "web_fetch"}
MUST_SERIAL_TOOLS = {"message"}
BACKGROUND_TOOLS = {"exec", "spawn", "claude_code", "web_search", "web_fetch", "read_file", "list_dir"}


def _extract_paths(args: dict) -> set[str]:
    paths = set()
    for k in ("path", "file_path", "target", "file"):
        v = args.get(k)
        if isinstance(v, str) and v:
            paths.add(v.strip())
    return paths


def analyze(tool_calls: list) -> dict[str, Any]:
    """
    分析工具调用依赖。
    返回: {"can_parallel": bool, "groups": list, "reason": str, "need_llm": bool}
    """
    if len(tool_calls) <= 1:
        return {
            "can_parallel": False,
            "groups": [tool_calls] if tool_calls else [],
            "reason": "单个工具",
            "need_llm": False,
        }

    infos = []
    for tc in tool_calls:
        name = tc.name if hasattr(tc, "name") else tc.get("name", "")
        args = tc.arguments if hasattr(tc, "arguments") else tc.get("arguments", {})
        if not isinstance(args, dict):
            args = {}
        infos.append((name, args))

    names = [n for n, _ in infos]

    if any(n in MUST_SERIAL_TOOLS for n in names):
        return {"can_parallel": False, "groups": [tool_calls], "reason": "message 需串行", "need_llm": False}

    write_paths = set()
    for name, args in infos:
        if name in WRITE_TOOLS:
            for p in _extract_paths(args):
                if p in write_paths:
                    return {"can_parallel": False, "groups": [tool_calls], "reason": "同文件写冲突", "need_llm": False}
                write_paths.add(p)

    if all(n in READ_TOOLS for n in names):
        return {"can_parallel": True, "groups": [tool_calls], "reason": "仅读操作", "need_llm": False}

    if all(n in BACKGROUND_TOOLS for n in names):
        return {"can_parallel": True, "groups": [tool_calls], "reason": "无依赖后台工具", "need_llm": False}

    return {"can_parallel": True, "groups": [tool_calls], "reason": "需 LLM 判断", "need_llm": True}
```

### 6.2 修改 `nanobot/services/smart_parallel_decider.py`

在 `should_parallel` 开头，单个工具判断之后、message 检查之前插入：

```python
from nanobot.services.parallel_dependency_analyzer import analyze as analyze_dependencies

async def should_parallel(self, tool_calls: list) -> dict[str, Any]:
    if len(tool_calls) <= 1:
        return {
            "parallel": False,
            "groups": [tool_calls] if tool_calls else [],
            "reason": "单个工具无需并行",
        }

    rule_result = analyze_dependencies(tool_calls)
    if not rule_result.get("need_llm", True):
        return {
            "parallel": rule_result["can_parallel"],
            "groups": rule_result["groups"],
            "reason": rule_result["reason"],
        }

    # 原有 message 检查、_quick_check_parallel、_llm_decide 逻辑保持不变
    ...
```

---

## 七、阶段 6：execute_in_thread_pool 修复

### 7.1 修改 `nanobot/agent/tools/registry.py`

**位置**：约 107-111 行

**原**：

```python
loop = asyncio.get_event_loop()
return await loop.run_in_executor(
    effective_executor,
    lambda: asyncio.run(tool.execute(**params))
)
```

**改为**：

```python
loop = asyncio.get_running_loop()
params_copy = dict(params)

def _run_async_in_thread() -> str:
    thread_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(thread_loop)
    try:
        return thread_loop.run_until_complete(tool.execute(**params_copy))
    finally:
        thread_loop.close()

return await loop.run_in_executor(effective_executor, _run_async_in_thread)
```

---

## 八、实施顺序与验收

### 8.1 建议实施顺序

1. **阶段 6**（thread_pool 修复）— 风险小，先消除潜在 bug  
2. **阶段 5**（并行规则）— 无新依赖，易验证  
3. **阶段 4**（ToolContext）— 为后续维护打基础  
4. **阶段 1**（工具选择）— 核心意图识别提升  
5. **阶段 2**（主 agent prompt）— 配置即生效  
6. **阶段 3**（子 agent 聚合）— 直接改善聚合体验  

### 8.2 验收标准

| 阶段 | 验收 |
|------|------|
| 1 | “查这个 url 内容”能选出 web_fetch |
| 2 | 新 identity 中「When to Use spawn」生效，spawn 决策更合理 |
| 3 | 多子 agent batch 完成时，汇总更结构化、主 agent 回复更自然 |
| 4 | 各渠道消息正常，spawn 结果正确回传 |
| 5 | 多 read_file 并行时不再额外调用 LLM |
| 6 | exec/spawn/claude_code 在线程池执行无 RuntimeError |

---

## 九、文件改动清单

| 操作 | 路径 |
|------|------|
| 新建 | `nanobot/agent/tool_selector.py` |
| 新建 | `nanobot/agent/tool_context.py` |
| 新建 | `nanobot/services/parallel_dependency_analyzer.py` |
| 修改 | `nanobot/agent/loop.py` |
| 修改 | `nanobot/agent/context.py` |
| 修改 | `nanobot/agent/tools/registry.py` |
| 修改 | `nanobot/agent/subagent.py` |
| 修改 | `nanobot/services/smart_parallel_decider.py` |
| 修改 | `nanobot/config/schema.py`（可选） |
| 修改 | `nanobot/web/api.py`（可选，传 tool_selector 参数） |
| 修改 | `nanobot/cli/commands.py`（可选） |

---

## 十、向后兼容

| 改动 | 兼容策略 |
|------|----------|
| 工具选择 | `tool_selection_use_llm=False` 时使用关键词回退 |
| 主 agent prompt | 仅改默认 identity；用户自定义 identity 不受影响 |
| 子 agent 聚合 | 仅改 prompt 文本，接口不变 |
| ToolContext | 工具 `set_context` 等接口不变，由 Registry 转发 |
| 并行决策 | 规则无法判断时仍走 SmartParallelDecider 的 LLM 逻辑 |
