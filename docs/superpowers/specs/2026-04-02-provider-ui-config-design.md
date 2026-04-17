# Provider UI 配置能力 — 设计文档

**日期：** 2026-04-02
**状态：** 设计中

## 1. 背景与目标

nanobot-webui 目前 Provider 配置通过代码硬编码（`Config` 对象），用户切换模型/Provider 需要改代码。参考 Octopus 项目，补全一套完整的 Provider UI 配置能力。

**目标：**
- 用户可在 UI 上配置 Provider（增删改查）
- 支持从 66 个系统预置 Provider 中快速选择
- 支持配置 API Key、Base URL
- 支持模型选择

**约束：** 保留现有 4 个 Provider 类（OpenAI/Anthropic/DeepSeek/Azure），不做 UnifiedProvider 合并。

---

## 2. 系统预置 Provider 数据

### 2.1 数据来源

从 Octopus `backend/data/system_providers.py` 迁移 66 个系统预置 Provider 到 nanobot-webui 的 `nanobot/providers/discovery.py`。

### 2.2 数据结构

```python
@dataclass
class SystemProvider:
    id: str                    # "deepseek"
    display_name: str          # "DeepSeek"
    provider_type: str         # "openai" | "anthropic" | "azure-openai" | "gemini" | "ollama"
    api_base: str              # "https://api.deepseek.com/v1"
    default_model: str         # "deepseek-chat"

@dataclass
class SystemModel:
    id: str                    # "deepseek-chat"
    display_name: str          # "DeepSeek Chat"
    provider_id: str           # "deepseek"
    model_type: str            # "chat" | "completion" | "embedding" | "image" | "audio" | "vision"
    context_window: int        # 128000
    supports_tools: bool      # True
    supports_vision: bool      # False
```

### 2.3 存储位置

新增 `nanobot/storage/provider_repository.py`：

- `ProviderRepository`: providers 表 CRUD
- `ModelRepository`: models 表 CRUD

SQLite 表结构参考 Octopus `providers` / `models` 表。

### 2.4 初始化逻辑

应用首次启动时，将 66 个系统预置 Provider 写入数据库（`is_system=True`，用户不可删除）。

---

## 3. 后端 Handler

### 3.1 WebSocket Handler

新增 `nanobot/web/provider_handler.py`，处理以下消息类型：

| 消息类型 | 操作 |
|---------|------|
| `provider_get_all` | 获取所有 Provider（含系统预置） |
| `provider_get` | 获取单个 Provider 详情 |
| `provider_add` | 用户新增 Provider |
| `provider_update` | 更新 Provider（API Key / Base URL / 启用状态） |
| `provider_delete` | 删除用户 Provider（系统预置不可删） |
| `model_get_all` | 获取某 Provider 下的所有模型 |
| `model_add` | 新增模型 |
| `model_update` | 更新模型 |
| `model_delete` | 删除模型 |

### 3.2 热更新

`ProviderManager.update_provider_config()` 在 `provider_update` 时被调用，实现运行时凭证热更新，`AgentLoop` 无需重启。

---

## 4. 前端组件

### 4.1 组件结构

```
nnanobot/components/ProviderSetting/
├── ProviderSetting.jsx    # 主容器，状态管理，WebSocket 消息
├── ProviderList.jsx       # 左侧列表（搜索 / 过滤 / 启用切换）
├── ProviderDetail.jsx     # 右侧详情（配置表单 + 模型列表）
├── AddProviderPopup.jsx   # 添加 Provider 弹窗（从系统预置选择或自定义）
└── ModelSelectPopup.jsx   # 选择模型弹窗
```

### 4.2 ProviderList

- 左侧列表，显示所有 Provider
- 每项：图标 + 显示名 + 类型 + 启用开关
- 顶部搜索框，支持按名称过滤
- 系统预置 Provider 右上角显示 badge

### 4.3 ProviderDetail

- Provider 基本信息（名称、类型、API Base URL）
- API Key 输入框（密码模式，支持显示/隐藏）
- "测试连接" 按钮（各类型调用 `/models` 或等价接口验证）
- 启用/禁用开关
- 下方模型列表

### 4.4 模型列表

- 按 `model_type` 分组（Chat / Completion / Embedding / Image / Audio / Vision）
- 每项：模型 ID + 显示名 + 上下文窗口 + 是否支持 tools
- 支持设为默认模型

### 4.5 状态管理

使用 nanobot-webui 现有的 React hooks 模式，在 `ProviderSetting.jsx` 中管理状态，通过 props 传入的 `sendWSMessage` 与后端通信。

---

## 5. AgentLoop 集成

现有 `AgentLoop` 通过 `ModelRouter` 获取 `ModelHandle`，逻辑不变。

新增 `AgentConfigService`：优先从数据库读取用户配置的 Provider，fallback 到系统预置 Provider。

---

## 6. 实现顺序

1. **后端数据层**：新增 `provider_repository.py`（SQLite 表 + CRUD）
2. **系统预置数据**：迁移 66 个 Provider 到 `discovery.py`，实现初始化逻辑
3. **WebSocket Handler**：新增 `provider_handler.py`，实现 `provider_*` / `model_*` 消息处理
4. **ProviderManager 集成**：Handler 调用 `ProviderManager.update_provider_config()` 实现热更新
5. **前端 ProviderSetting 组件**：参照 Octopus 结构实现 `ProviderList` / `ProviderDetail` / 弹窗组件
6. **前端集成**：在现有设置面板中嵌入 `ProviderSetting`

---

## 7. 待确认

- [x] ~~前端 WebSocket 消息前缀约定~~ → 参考 Octopus，使用 `provider_*` / `model_*`
- [x] ~~系统预置 Provider 的模型元数据~~ → **动态获取**：配置 Provider 后调用 `/models` 接口实时拉取模型列表和能力信息
- [x] ~~ProviderSetting 面板放在哪个 Tab~~ → 现有设置页面的 **AI (AI Provider)** Tab

---

## 8. 模型动态发现

每个 Provider 配置完成后，调用其 `/models` 接口获取真实模型列表：

| Provider 类型 | 调用接口 |
|--------------|---------|
| OpenAI / OpenAI-compatible | `GET /v1/models` |
| Anthropic | `GET /v1/models` |
| Gemini | `GET /v1/models` |
| Azure OpenAI | `GET /providers/{endpoint}/models` |
| Ollama | `GET /api/tags` |

获取到的模型元数据写入 `models` 表，覆盖或补充系统预置的静态数据。

> 首次初始化时使用静态数据填充，用户"检测模型"时用动态数据更新。
