# nanobot-webui 升级设计：去除 litellm，使用原生 SDK

**日期:** 2026-04-02
**状态:** 已批准
**目标:** 移除 litellm 依赖，用原生 OpenAI / Anthropic / DeepSeek / Azure OpenAI SDK 替代

---

## 背景

参考 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 的极简 SDK 路线，重构 nanobot-webui 的 LLM Provider 层。

**决策：**
- 保留 Microkernel 核心差异化
- Provider 架构：各 SDK 独立注入，无统一运行时调度
- `LLMProvider` ABC 保留，仅作类型提示，不做运行时多态

---

## Provider 支持范围

| Provider | SDK | 用途 |
|----------|-----|------|
| OpenAI | `openai>=1.0` (AsyncOpenAI) | 通用 GPT 模型 |
| Anthropic | `anthropic>=0.20` (AsyncAnthropic) | Claude 系列 |
| DeepSeek | `openai>=1.0` (AsyncOpenAI, endpoint=api.deepseek.com) | 性价比推理 |
| Azure OpenAI | `openai>=1.0` (AzureOpenAI) | 企业场景 |

---

## Provider 层架构

### 原则

- 各 Provider **独立实现**，不通过统一接口运行时调用
- `ModelRouter` 持有各 Provider 实例引用（依赖注入）
- `get()` 解析 model ID 前缀（`openai/`、`anthropic/`、`deepseek/`、`azure/`）映射到对应 Provider 实例
- 无 litellm 中间层，无动态 API 探测

### 文件变更

| 操作 | 文件路径 |
|------|---------|
| 新增 | `nanobot/providers/openai_provider.py` |
| 新增 | `nanobot/providers/anthropic_provider.py` |
| 新增 | `nanobot/providers/deepseek_provider.py` |
| 新增 | `nanobot/providers/azure_provider.py` |
| 删除 | `nanobot/providers/litellm_provider.py` |
| 修改 | `nanobot/providers/base.py` — 降级为 interface，保留 ABC + 数据类 |
| 重写 | `nanobot/providers/router.py` — Provider 实例注入 + 前缀路由 |
| 重写 | `nanobot/providers/discovery.py` — 改为静态 model list |

### ModelHandle 结构

```python
@dataclass(frozen=True)
class ModelHandle:
    model: str              # 原生 model ID, e.g. "claude-opus-4-6" (非 "anthropic/claude-opus-4-6")
    api_key: str
    api_base: str | None
    provider_id: str         # "openai" | "anthropic" | "deepseek" | "azure"
    capabilities: set[str]  # {"tools", "vision", "thinking"}
    context_window: int
```

### ModelRouter 改造

```python
class ModelRouter:
    def __init__(self, config_repo: ConfigRepository):
        self._providers: dict[str, BaseProvider] = {}
        # 实例化 openai / anthropic / deepseek / azure Provider

    def get(self, profile_or_model: str) -> ModelHandle:
        # 1. Profile ID (smart/fast/coding) -> resolve via model_chain
        # 2. Model ID -> 解析前缀 -> 对应 Provider 实例
        # 3. Alias -> lookup via aliases field
```

---

## 各 Provider 实现要点

### OpenAI Provider

- 使用 `openai>=1.0` SDK，`AsyncOpenAI`
- `chat()` 调用 `client.chat.completions.create()`
- `tools` 参数转为 `tools=tools, tool_choice="auto"`
- `stream=True` 支持流式

### Anthropic Provider

- 使用 `anthropic>=0.20` SDK，`AsyncAnthropic`
- `chat()` 调用 `client.messages.create()`
- Anthropic 原生 `tools` 参数格式（`tools` list + `tool_use` message type）
- `thinking` 能力通过 `thinking={ "type": "enabled", "budget_tokens": ... }` 支持
- `stream=True` 支持流式

### DeepSeek Provider

- 使用 `openai>=1.0` SDK，`AsyncOpenAI(base_url="https://api.deepseek.com")`
- 复用 OpenAI Provider 逻辑，endpoint 不同

### Azure OpenAI Provider

- 使用 `openai>=1.0` SDK，`AzureOpenAI`
- `chat()` 调用 `client.chat.completions.create()`
- `api_version` 参数从环境/配置读取（如 `"2024-12-01-preview"`）
- endpoint 由 `azure_openai_base_url` + `azure_deployment_name` 组合

### 统一返回格式

所有 Provider `chat()` 方法统一返回 `LLMResponse`：

```python
@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCallRequest] | None
    finish_reason: str
    usage: dict[str, int]  # input_tokens, output_tokens, etc.
    thinking: str | None = None  # Anthropic extended thinking
```

---

## Config Schema 改造

### `nanobot/config/schema.py`

**修改 `ProvidersConfig`：**

```python
class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    azure: AzureProviderConfig = Field(default_factory=AzureProviderConfig)  # 新增

class AzureProviderConfig(BaseModel):
    """Azure OpenAI provider configuration."""
    api_key: str = ""
    api_base: str = ""  # e.g. "https://xxx.openai.azure.com"
    api_version: str = "2024-12-01-preview"
    azure_deployment: str = ""  # e.g. "gpt-4o"
```

**简化 `_MODEL_PROVIDER_MAP`：**

```python
_MODEL_PROVIDER_MAP: dict[tuple[str, ...], str] = {
    ("anthropic/", "claude"): "anthropic",
    ("openai/", "gpt"): "openai",
    ("deepseek/", "deepseek"): "deepseek",
    ("azure/", "azure"): "azure",
}

_MODEL_API_BASE_MAP: dict[str, str | None] = {
    "openai": None,
    "anthropic": None,
    "deepseek": "https://api.deepseek.com",
    "azure": None,  # 由用户配置提供
}
```

**移除：** openrouter, groq, zhipu, dashscope, vllm, ollama, gemini, minimax

---

## Model Discovery 改造

### 现有问题

原来 `/api/v1/providers/{id}/discover` 调用 litellm 动态探测。

### 新方案

各 Provider 实现 `list_models()` 方法，返回静态 model 列表（硬编码在代码中）：

```python
def list_models(self) -> list[dict]:
    return [
        {"id": "gpt-4o", "name": "GPT-4o", "context_window": 128000, "capabilities": ["tools", "vision"]},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "context_window": 128000, "capabilities": ["tools"]},
        ...
    ]
```

`ModelDiscoveryService` 改为 façade，委托给对应 Provider。

**硬编码模型列表：**

- **OpenAI:** gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-3.5-turbo
- **Anthropic:** claude-opus-4-6, claude-sonnet-4-7, claude-haiku-4-7
- **DeepSeek:** deepseek-chat, deepseek-reasoner
- **Azure:** 用户配置的 deployment（静态）

---

## Agent Loop / AgentLoop 改造

### `nanobot/agent/loop.py`

- 持有 `ModelRouter` 实例
- `get_provider_and_model()` 调用 `router.get()`，得到 `ModelHandle`
- 根据 `ModelHandle.provider_id` 选择对应 Provider，直接调用 `.chat()`
- 不走 litellm

### `nanobot/agentloop/kernel/`

- Capability 实现（planner、critic、root 等）通过构造函数注入 Provider 实例
- Kernel 本身不感知 LLM 调用细节
- 改动仅限 Capability 层的 Provider 注入点

---

## WebUI / API 改造

### Frontend 类型 — `web-ui/src/types.ts`

```typescript
export interface Provider {
  id: string
  name: string
  type: 'openai' | 'anthropic' | 'deepseek' | 'azure'
  apiKey?: string
  apiBase?: string
  enabled: boolean
}
```

**移除：** openrouter, groq, zhipu, dashscope, gemini, vllm, minimax
**新增：** azure

### ConfigPage — `web-ui/src/pages/ConfigPage.tsx`

- `ProvidersConfig` 组件保留，dropdown 选项调整为 4 种
- UI 展示相应更新
- 删除对已废弃 provider 类型的处理逻辑

### API Discovery 端点 — `nanobot/web/api.py`

- `GET /api/v1/providers/{id}/discover` 改为调用对应 Provider 的 `list_models()`
- 遇到已废弃 provider 类型时，返回友好提示

---

## Storage 改造

### `nanobot/storage/config_repository.py`

- `config_providers` 表：移除废弃 provider 列（openrouter, groq, zhipu, dashscope, vllm, ollama, gemini, minimax）
- 保留 openai, anthropic, deepseek 列
- 新增 azure 列

### 数据库迁移

- 新增 `alembic` 或手动 SQL migration 脚本
- 将废弃 provider 列数据导出或清理
- 保留现有 openai/anthropic/deepseek 数据

---

## 依赖改造

### `pyproject.toml`

**移除：**
```toml
litellm
```

**新增：**
```toml
openai>=1.0
anthropic>=0.20
```

（DeepSeek 复用 openai，Azure 也在 openai 包内）

---

## 迁移步骤

### Phase 1: 基础设施
1. 更新 `pyproject.toml`，安装新依赖
2. 创建 `openai_provider.py`
3. 创建 `anthropic_provider.py`
4. 创建 `deepseek_provider.py`
5. 创建 `azure_provider.py`

### Phase 2: 核心重构
6. 重写 `providers/router.py`（Provider 实例注入）
7. 更新 `providers/base.py`（确认数据类不变）
8. 重写 `providers/discovery.py`（静态 model list）

### Phase 3: 配置层
9. 更新 `config/schema.py`（简化 ProvidersConfig）
10. 更新 `storage/config_repository.py`（列清理）
11. 数据库 migration 脚本

### Phase 4: 调用方迁移
12. 更新 `agent/loop.py`（直接 Provider 调用）
13. 更新 `agentloop/capabilities/`（Provider 注入）

### Phase 5: 前端 & API
14. 更新 `web-ui/src/types.ts`
15. 更新 `web-ui/src/pages/ConfigPage.tsx`
16. 更新 `web/api.py` Discovery 端点

### Phase 6: 清理
17. 删除 `providers/litellm_provider.py`
18. 清理 `config/schema.py` 废弃 MAP 条目
19. 运行测试验证

---

## 不变项

- Microkernel 架构（`nanobot/agentloop/`）
- API endpoint 结构（CRUD 不变）
- Tool System（`nanobot/agent/tools/`）
- MCP 集成（`nanobot/mcp/`）
- Channel 系统（`nanobot/channels/`）
- 消息总线（`nanobot/bus/`）
