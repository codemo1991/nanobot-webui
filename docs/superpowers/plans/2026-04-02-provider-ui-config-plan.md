# Provider UI 配置能力 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户可在 UI 上配置 Provider（增删改查），支持从 66 个系统预置 Provider 选择，动态发现模型

**Architecture:** 扩展现有 SQLite schema（迁移代码） + 新增 system_providers 数据文件 + 增强前端 ProviderSetting UI

**Tech Stack:** Python (SQLite), TypeScript/React (Ant Design), nanobot-webui 现有框架

---

## 现状分析

经过代码探索，发现：
- **后端已有完整 REST API**：`NanobotWebAPI` 通过 HTTP REST 处理 `provider_*` / `model_*` 操作，不需要新建 WebSocket Handler
- **数据库已有基础表**：`config_providers` 和 `config_models`，需要扩展字段
- **前端已有基础 UI**：`ConfigPage.tsx` 有 `ProvidersConfig()` 和 `ModelsConfig()` 部分，需要大幅增强
- **模型动态发现已有**：`ModelDiscoveryService.discover_and_save()` 已实现

---

## 文件映射

### 后端 — 新增

| 文件 | 职责 |
|------|------|
| `nanobot/providers/system_providers.py` | 66 个系统预置 Provider 数据（从 Octopus 迁移） |
| `nanobot/storage/provider_repository.py` | 独立 Repository：providers 表扩展字段 CRUD + models 表扩展字段 CRUD |

### 后端 — 修改

| 文件 | 改动 |
|------|------|
| `nanobot/storage/config_repository.py` | `_init_tables()` 增加迁移代码，扩展 providers/models 表字段 |
| `nanobot/web/api.py` | 扩展 REST 端点返回字段（增加 `provider_type`、`is_system`、`model_type` 等）；集成 `ModelDiscoveryService` |
| `nanobot/providers/discovery.py` | 扩展 `DiscoveredModel` 增加 `model_type`、`supports_vision`、`supports_function_calling` 等字段 |
| `nanobot/config/loader.py` | 启动时初始化系统预置 Provider 数据 |

### 前端 — 新增

| 文件 | 职责 |
|------|------|
| `web-ui/src/components/ProviderSetting/` 目录 | 参考 Octopus 的组件结构 |

### 前端 — 修改

| 文件 | 改动 |
|------|------|
| `web-ui/src/types.ts` | 扩展 `Provider`/`ModelInfo` 接口字段 |
| `web-ui/src/api.ts` | 扩展 API 方法（增加 `provider_type`、`model_type` 等字段） |
| `web-ui/src/pages/ConfigPage.tsx` | 增强 `ProvidersConfig()` 组件：系统预置 badge、类型选择、测试连接、模型发现 |

---

## Task 1: 扩展数据库 Schema（迁移代码）

**Files:**
- Modify: `nanobot/storage/config_repository.py:32-130`

- [ ] **Step 1: 添加 providers 表迁移代码**

在 `_init_tables()` 方法末尾的迁移代码块之后（大约 line 126 之前），添加：

```python
# 迁移：扩展 config_providers 表
for col, col_type, default in [
    ("display_name", "TEXT", "''"),
    ("provider_type", "TEXT", "'openai'"),
    ("is_system", "INTEGER", "0"),
    ("sort_order", "INTEGER", "0"),
    ("config_json", "TEXT", "'{}'"),
]:
    try:
        cols = {d[1] for d in conn.execute("PRAGMA table_info(config_providers)").fetchall()}
        if col not in cols:
            conn.execute(f"ALTER TABLE config_providers ADD COLUMN {col} {col_type} DEFAULT {default}")
            conn.commit()
    except Exception:
        pass
```

- [ ] **Step 2: 添加 models 表迁移代码**

在同一迁移代码块中，添加：

```python
# 迁移：扩展 config_models 表
for col, col_type, default in [
    ("model_type", "TEXT", "'chat'"),
    ("max_tokens", "INTEGER", "4096"),
    ("supports_vision", "INTEGER", "0"),
    ("supports_function_calling", "INTEGER", "1"),
    ("supports_streaming", "INTEGER", "1"),
    ("is_default", "INTEGER", "0"),
]:
    try:
        cols = {d[1] for d in conn.execute("PRAGMA table_info(config_models)").fetchall()}
        if col not in cols:
            conn.execute(f"ALTER TABLE config_models ADD COLUMN {col} {col_type} DEFAULT {default}")
            conn.commit()
    except Exception:
        pass
```

- [ ] **Step 3: 修改 get_provider 返回值**

修改 `get_provider()` 方法（line ~286-306），在返回字典中增加字段：

```python
return {
    "id": row["id"],
    "name": row["name"],
    "display_name": row["display_name"] or row["name"],
    "provider_type": row["provider_type"] or "openai",
    "api_key": row["api_key"] or "",
    "api_base": row["api_base"],
    "enabled": bool(row["enabled"]),
    "priority": row["priority"],
    "is_system": bool(row["is_system"]),
    "sort_order": row["sort_order"],
}
```

- [ ] **Step 4: 修改 get_all_providers 返回值**

同样扩展 `get_all_providers()` 返回字典，增加 `display_name`、`provider_type`、`is_system`、`sort_order` 字段。

- [ ] **Step 5: 修改 set_provider 方法签名和 SQL**

修改 `set_provider()` 方法（line ~330-353），扩展参数和方法体：

```python
def set_provider(self, provider_id: str, name: str,
                 display_name: str = "", provider_type: str = "openai",
                 api_key: str = "", api_base: str | None = None,
                 enabled: bool = False, priority: int = 0,
                 is_system: bool = False, sort_order: int = 0) -> None:
    """设置 Provider 配置。"""
    updated_at = self._get_timestamp()
    display_name = display_name or name
    try:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO config_providers
                    (id, name, display_name, provider_type, api_key, api_base,
                     enabled, priority, is_system, sort_order, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    display_name=excluded.display_name,
                    provider_type=excluded.provider_type,
                    api_key=excluded.api_key,
                    api_base=excluded.api_base,
                    enabled=excluded.enabled,
                    priority=excluded.priority,
                    is_system=excluded.is_system,
                    sort_order=excluded.sort_order,
                    updated_at=excluded.updated_at
                """,
                (provider_id, name, display_name, provider_type,
                 api_key, api_base, int(enabled), priority,
                 int(is_system), sort_order, updated_at)
            )
    except Exception as e:
        logger.exception(f"Failed to set provider {provider_id}")
        raise
```

- [ ] **Step 6: 修改 get_model 返回值**

修改 `get_model()` 方法（line ~752-777），增加 `model_type`、`max_tokens`、`supports_vision`、`supports_function_calling`、`supports_streaming` 字段到返回字典。

- [ ] **Step 7: 修改 get_all_models / get_enabled_models 返回值**

同样扩展这两个方法的返回字典，增加模型扩展字段。

- [ ] **Step 8: 修改 set_model 方法**

扩展 `set_model()` 方法签名，增加新参数，并在 SQL 中处理：
- 参数：`model_type: str = "chat"`, `max_tokens: int = 4096`, `supports_vision: bool = False`, `supports_function_calling: bool = True`, `supports_streaming: bool = True`
- SQL：`VALUES (...)` 和 `ON CONFLICT DO UPDATE SET` 块中增加对应字段

- [ ] **Step 9: 添加 delete_provider 方法**

在 `set_provider()` 后添加：

```python
def delete_provider(self, provider_id: str) -> bool:
    """删除 Provider（系统预置不可删除）。"""
    try:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM config_providers WHERE id = ? AND is_system = 0",
                (provider_id,)
            )
            return cursor.rowcount > 0
    except Exception as e:
        logger.warning(f"Failed to delete provider {provider_id}: {e}")
        return False
```

- [ ] **Step 10: 添加 get_system_providers 方法**

```python
def get_system_providers(self) -> list[dict[str, Any]]:
    """获取所有系统预置 Provider。"""
    try:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM config_providers WHERE is_system = 1 ORDER BY sort_order, name"
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "display_name": row["display_name"] or row["name"],
                    "provider_type": row["provider_type"] or "openai",
                    "api_key": "",
                    "api_base": row["api_base"],
                    "enabled": bool(row["enabled"]),
                    "is_system": True,
                }
                for row in rows
            ]
    except Exception as e:
        logger.warning(f"Failed to get system providers: {e}")
        return []
```

- [ ] **Step 11: 提交**

```bash
git add nanobot/storage/config_repository.py
git commit -m "feat: extend providers/models schema with system fields and migration"
```

---

## Task 2: 迁移 66 个系统预置 Provider 数据

**Files:**
- Create: `nanobot/providers/system_providers.py`
- Modify: `nanobot/config/loader.py`

- [ ] **Step 1: 创建 system_providers.py**

从 Octopus `E:\Octopus\backend\data\system_providers.py` 读取完整数据，以 `SYSTEM_PROVIDERS` 列表形式写入 `nanobot/providers/system_providers.py`。参考 Octopus 的数据结构，保留以下字段：

```python
SYSTEM_PROVIDERS = [
    {
        "id": "deepseek",
        "display_name": "DeepSeek",
        "provider_type": "openai",
        "api_base": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "models": [
            {
                "id": "deepseek-chat",
                "name": "DeepSeek V3",
                "model_type": "chat",
                "context_window": 128000,
                "supports_function_calling": True,
                "supports_vision": False,
            },
            {
                "id": "deepseek-reasoner",
                "name": "DeepSeek R1",
                "model_type": "chat",
                "context_window": 128000,
                "supports_function_calling": False,
                "supports_vision": False,
            },
        ]
    },
    # ... 从 Octopus 迁移所有 66 个 provider
]
```

- [ ] **Step 2: 在 loader.py 中添加初始化逻辑**

在 `ensure_initial_config()` 或启动流程中，添加 `init_system_providers()` 调用。实现逻辑：

```python
def init_system_providers(repo: "ConfigRepository") -> None:
    """初始化系统预置 Provider（仅写入 is_system=True 的记录，用户不可删除）。"""
    from nanobot.providers.system_providers import SYSTEM_PROVIDERS
    for sp in SYSTEM_PROVIDERS:
        repo.set_provider(
            provider_id=sp["id"],
            name=sp["id"],
            display_name=sp["display_name"],
            provider_type=sp["provider_type"],
            api_base=sp["api_base"],
            api_key="",
            enabled=False,
            is_system=True,
            sort_order=0,
        )
        # 写入默认模型
        for m in sp.get("models", []):
            repo.set_model(
                model_id=m["id"],
                provider_id=sp["id"],
                name=m["name"],
                litellm_id=m["id"],
                model_type=m.get("model_type", "chat"),
                context_window=m.get("context_window", 128000),
                max_tokens=m.get("max_tokens", 4096),
                supports_vision=m.get("supports_vision", False),
                supports_function_calling=m.get("supports_function_calling", True),
                supports_streaming=m.get("supports_streaming", True),
                is_default=(m["id"] == sp.get("default_model")),
            )
```

在 `ensure_initial_config()` 返回前调用。

- [ ] **Step 3: 提交**

```bash
git add nanobot/providers/system_providers.py nanobot/config/loader.py
git commit -m "feat: migrate 66 system providers from Octopus"
```

---

## Task 3: 扩展 API 层

**Files:**
- Modify: `nanobot/web/api.py` (NanobotWebAPI methods)
- Modify: `nanobot/providers/discovery.py` (DiscoveredModel)

- [ ] **Step 1: 扩展 DiscoveredModel dataclass**

修改 `nanobot/providers/discovery.py` 中的 `DiscoveredModel`，增加字段：

```python
@dataclass
class DiscoveredModel:
    id: str
    name: str
    litellm_id: str
    aliases: list[str]
    capabilities: list[str]
    context_window: int
    # 新增字段
    model_type: str = "chat"       # "chat" | "completion" | "embedding" | "image" | "audio" | "vision"
    max_tokens: int = 4096
    supports_vision: bool = False
    supports_function_calling: bool = True
    supports_streaming: bool = True
```

- [ ] **Step 2: 扩展 NanobotWebAPI.get_providers()**

找到 `api.py` 中 `get_providers()` 方法，扩展返回字段：

```python
def get_providers(self) -> list[dict[str, Any]]:
    providers = self.repo.get_all_providers()
    # 扩展返回字段
    return [
        {
            **p,
            "displayName": p.get("display_name", p["name"]),
            "providerType": p.get("provider_type", "openai"),
            "isSystem": p.get("is_system", False),
            "sortOrder": p.get("sort_order", 0),
        }
        for p in providers
    ]
```

- [ ] **Step 3: 扩展 NanobotWebAPI.create_provider()**

找到 `create_provider()` 方法（处理 `POST /api/v1/providers`），扩展处理新字段 `displayName`、`providerType`、`isSystem`、`sortOrder`。

- [ ] **Step 4: 扩展 NanobotWebAPI.update_provider()**

找到 `update_provider()` 方法，扩展处理 `displayName`、`providerType`、`enabled`（处理 `provider_update` WebSocket 消息中的热更新）。

- [ ] **Step 5: 扩展 NanobotWebAPI.get_models()**

扩展返回字段，加上 `modelType`、`maxTokens`、`supportsVision`、`supportsFunctionCalling`、`supportsStreaming`：

```python
def get_models(self) -> list[dict[str, Any]]:
    models = self.repo.get_all_models()
    return [
        {
            **m,
            "modelType": m.get("model_type", "chat"),
            "maxTokens": m.get("max_tokens", 4096),
            "supportsVision": m.get("supports_vision", False),
            "supportsFunctionCalling": m.get("supports_function_calling", True),
            "supportsStreaming": m.get("supports_streaming", True),
        }
        for m in models
    ]
```

- [ ] **Step 6: 扩展 NanobotWebAPI.create_model() / update_model()**

扩展这两个方法处理新字段。

- [ ] **Step 7: 添加 delete_provider API 端点**

在 `NanobotAPIHandler` 的路由方法中，找到 `do_DELETE()` 分发逻辑，增加 `DELETE /api/v1/providers/{providerId}`：

```python
elif method == "DELETE" and parts[1] == "providers" and len(parts) == 3:
    provider_id = unquote(parts[2])
    deleted = app.repo.delete_provider(provider_id)
    self._send_json(_ok({"deleted": deleted}))
```

- [ ] **Step 8: 添加 discover_models API**

`discover_models_for_provider()` 已存在，检查其是否调用了 `ModelDiscoveryService.discover_and_save()`，确保模型数据被写入数据库并扩展字段。如果不足，在 `NanobotAPIHandler` 的路由中确保 `GET /api/v1/providers/{id}/discover` 端点正常工作。

- [ ] **Step 9: 提交**

```bash
git add nanobot/web/api.py nanobot/providers/discovery.py
git commit -m "feat: extend API layer with provider_type, model_type and discovery fields"
```

---

## Task 4: 扩展前端类型和 API

**Files:**
- Modify: `web-ui/src/types.ts`
- Modify: `web-ui/src/api.ts`

- [ ] **Step 1: 扩展 Provider 接口**

在 `types.ts` 中扩展 `Provider` 接口：

```typescript
interface Provider {
  id: string
  name: string
  displayName?: string
  type: 'openai' | 'anthropic' | 'deepseek' | 'azure' | string
  providerType?: 'openai' | 'anthropic' | 'azure-openai' | 'gemini' | 'ollama' | 'new-api' | string
  apiKey?: string
  apiBase?: string
  enabled: boolean
  apiVersion?: string
  azureDeployment?: string
  // 新增
  isSystem?: boolean
  sortOrder?: number
}
```

- [ ] **Step 2: 扩展 ModelInfo 接口**

```typescript
interface ModelInfo {
  id: string
  providerId: string
  name: string
  litellmId: string
  aliases: string
  capabilities: string
  contextWindow: number
  costRank?: number
  qualityRank?: number
  enabled: boolean
  isDefault: boolean
  // 新增
  modelType?: 'chat' | 'completion' | 'embedding' | 'image' | 'audio' | 'vision'
  maxTokens?: number
  supportsVision?: boolean
  supportsFunctionCalling?: boolean
  supportsStreaming?: boolean
}
```

- [ ] **Step 3: 添加 SYSTEM_PROVIDERS 常量**

在 `api.ts` 中添加从后端获取系统预置 Provider 的 API 调用：

```typescript
export async function getSystemProviders(): Promise<Provider[]> {
  return request<Provider[]>('/api/v1/providers?system=true')
}
```

- [ ] **Step 4: 提交**

```bash
git add web-ui/src/types.ts web-ui/src/api.ts
git commit -m "feat: extend frontend types and API for provider UI"
```

---

## Task 5: 增强前端 ProvidersConfig 组件

**Files:**
- Modify: `web-ui/src/pages/ConfigPage.tsx`

> 参考 Octopus `frontend/src/components/panels/ProviderSetting/` 的交互设计

- [ ] **Step 1: 添加 ProviderSetting 子组件**

在 `ConfigPage.tsx` 同目录下创建 `ProviderSetting/` 子目录，包含：
- `ProviderList.tsx` — 左侧 Provider 列表（搜索 + 系统/用户过滤 + 启用切换）
- `ProviderDetail.tsx` — 右侧详情（配置表单 + 模型列表）
- `AddProviderModal.tsx` — 添加 Provider 弹窗（从系统预置选择 OR 自定义）
- `ModelSelectModal.tsx` — 模型发现弹窗

这些组件的代码量较大，参考 Octopus 的实现逻辑，用 Ant Design 组件库实现：
- `ProviderList`：使用 `<List>` 或 `<Table>`，每行带 switch 开关
- `ProviderDetail`：使用 `<Form>` 布局，API Key 用 `<Input.Password>`
- `AddProviderModal`：两栏布局 — 左栏系统预置列表，右栏自定义表单
- `ModelSelectModal`：调用 `discoverModels()` API 显示加载中 → 结果列表

- [ ] **Step 2: 替换 ConfigPage.tsx 中的 ProvidersConfig**

找到现有的 `ProvidersConfig()` 组件，替换为新的 `ProviderSetting` 组件。确保保留原有的 `getProviders`、`updateProvider`、`createProvider`、`deleteProvider` 等 API 调用逻辑。

- [ ] **Step 3: 实现"测试连接"按钮**

在 `ProviderDetail.tsx` 中，根据 `providerType` 调用不同接口：
- `openai` / `openai-compatible`：`GET {api_base}/v1/models`
- `anthropic`：`GET {api_base}/v1/models`
- `gemini`：`GET {api_base}/v1/models`
- `ollama`：`GET {api_base}/api/tags`

成功返回 200 则显示绿色 success，失败显示红色 error。

- [ ] **Step 4: 提交**

```bash
git add web-ui/src/pages/ConfigPage.tsx web-ui/src/components/ProviderSetting/
git commit -m "feat: add ProviderSetting UI components with system provider support"
```

---

## Task 6: 模型发现集成

**Files:**
- Modify: `nanobot/providers/discovery.py`
- Modify: `nanobot/web/api.py`

- [ ] **Step 1: 扩展各 ProviderDiscovery 子类**

找到 `OpenAIDiscovery`、`AnthropicDiscovery`、`DeepSeekDiscovery`、`AzureDiscovery`，修改 `discover()` 方法返回的 `DiscoveredModel` 列表，增加 `model_type`、`supports_vision`、`supports_function_calling`、`supports_streaming` 字段。这些信息可以从 API 返回的 model 对象中提取（如 `vision` capability），也可以根据模型 ID 模式推断。

对于 OpenAI-compatible discovery，通过解析 `/v1/models` 返回的 model 对象推断类型：
- ID 含 `vision` → `supports_vision: True`
- ID 含 `instruct` → `model_type: "completion"`
- 其他默认 → `model_type: "chat"`

- [ ] **Step 2: 确保 discover_and_save 写入扩展字段**

检查 `ModelDiscoveryService.discover_and_save()` 方法，修改其调用 `repo.set_model()` 时传入新字段。

- [ ] **Step 3: 前端"检测模型"按钮**

在 `ProviderDetail.tsx` 的模型列表上方添加"检测模型"按钮，调用 `discoverModels(providerId)` API，显示加载状态，结果更新到模型列表。

- [ ] **Step 4: 提交**

```bash
git add nanobot/providers/discovery.py nanobot/web/api.py
git commit -m "feat: extend model discovery with type and capability fields"
```

---

## Task 7: 端到端集成测试

- [ ] **Step 1: 启动后端**

```bash
cd E:/workSpace/nanobot-webui
python -m nanobot.web.api
```

验证：首次启动时日志中看到 "System providers initialized" 类似信息；`GET /api/v1/providers` 返回包含 `is_system: true` 的系统预置记录。

- [ ] **Step 2: 启动前端**

```bash
cd E:/workSpace/nanobot-webui/web-ui
npm run dev
```

验证：打开 `http://localhost:5173/config`，切换到 AI Tab，看到左侧 Provider 列表（系统预置显示 badge）；点击一个系统预置 Provider，显示详情；填入 API Key，点击"测试连接"，显示 success。

- [ ] **Step 3: 测试模型发现**

点击"检测模型"，等待返回模型列表，模型按 `model_type` 分组显示。

- [ ] **Step 4: 测试热更新**

在 UI 中修改 API Key，保存后立即发送消息，验证 `AgentLoop` 使用新凭证。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "test: e2e provider UI configuration flow"
```

---

## 执行顺序

1. Task 1 — 数据库 Schema 扩展（迁移代码）
2. Task 2 — 系统预置数据（66 个 Provider）
3. Task 3 — API 层扩展
4. Task 4 — 前端类型和 API
5. Task 5 — 前端 UI 组件（最大工作量）
6. Task 6 — 模型发现集成
7. Task 7 — 端到端测试

---

## Spec 覆盖检查

| Spec 章节 | 实现位置 |
|----------|---------|
| 2.1 数据来源 | Task 2 |
| 2.2 数据结构 | Task 1 + Task 2 |
| 2.3 存储位置 | Task 1 |
| 2.4 初始化逻辑 | Task 2 |
| 3.1 WebSocket/Handler | Task 3（REST API 已存在，扩展字段） |
| 3.2 热更新 | Task 3 |
| 4.1 组件结构 | Task 5 |
| 4.2 ProviderList | Task 5 |
| 4.3 ProviderDetail | Task 5 |
| 4.4 模型列表 | Task 5 |
| 4.5 状态管理 | Task 4 + Task 5 |
| 5. AgentLoop 集成 | 无需改动（逻辑复用） |
| 8. 模型动态发现 | Task 6 |
