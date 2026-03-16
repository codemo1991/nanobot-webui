# Subagent 增强实现方案

本文档基于 subagent 架构讨论整理，涵盖模板配置、技能、模型与图片识别等能力的实现规划。

**文档版本**: 1.2  
**创建日期**: 2026-02  
**更新日期**: 2026-02

---

## 1. 当前架构概览

### 1.1 Subagent 核心组件

| 组件 | 位置 | 职责 |
|------|------|------|
| `SubagentTemplate` / `AgentTemplateConfig` | `prompts.py` / `config/agent_templates.py` | 定义 tools、rules、system_prompt |
| `SubagentManager` | `agent/subagent.py` | 管理子 agent 并发、执行、会话 |
| `SpawnTool` | `agent/tools/spawn.py` | 主 agent 调用，spawn 子 agent |
| `AgentTemplateManager` | `config/agent_templates.py` | 配置化模板管理（YAML 加载、热重载，可选迁移至 SQLite 见 2.4）|

### 1.2 系统提示变量来源

| 占位符 | 来源 |
|--------|------|
| `{task}` | 主 agent 调用 spawn 时传入的任务描述 |
| `{all_rules}` | 当前模板的 rules 列表，在 `build_system_prompt()` 中格式化 |
| `{workspace}` | 子 agent 工作区路径 |

### 1.3 当前能力边界

- **主 Agent**：ContextBuilder + SkillsLoader + bootstrap + memory
- **子 Agent**：仅 tools + rules，**无 skills 注入**
- **模型**：全局 `subagent_model`（config.agents.defaults），所有子 agent 共用
- **图片**：Inline 预处理 + spawn 时 `attach_media`，DashScope 视觉走 `_dashscope_direct_call` 使用 `self.model`

### 1.4 执行后端说明

| 后端 | 适用模板 | LLM 调用 | 是否受模板级 model 影响 |
|------|----------|----------|-------------------------|
| `native` | 任意 | `provider.chat(model=...)` | 是 |
| `claude_code` | 仅 coder | Claude Code CLI（不经过 provider）| **否** |

> 模板级 model 配置仅对 `native` 后端生效；`claude_code` 后端由 Claude Code CLI 自行选择模型。

---

## 2. 实现方案

### 2.1 模板级 Model 配置（P0，推荐优先实现）

**目标**：不同子 agent 模板使用不同模型，实现成本与能力分离。

**模型解析优先级**：`模板 model` > `全局 subagent_model` > `主 agent model`（provider 默认）

#### 2.1.1 数据层改动

- 在 `AgentTemplateConfig` 中增加可选字段 `model: Optional[str] = None`
- 留空时行为与现有一致，使用 SubagentManager 的 `self.model`（即全局 subagent_model 或 provider 默认）

#### 2.1.2 生效范围与例外

- **生效**：`_run_subagent` 中 `backend == "native"` 时的 `provider.chat()` 调用
- **生效**：带 `media` 的 DashScope 直接调用（`_dashscope_direct_call`），需传入 `effective_model` 而非 `self.model`
- **不生效**：`backend == "claude_code"` 时（路由到 `_run_via_claude_code`，不经过 provider）

#### 2.1.3 API Key 预注册

当模板指定 `model` 时，需确保该模型的 API key 已在 provider 中注册。可选方案：

- **方案 A**：启动时或模板加载时，遍历所有模板的 `model`，调用 `provider.ensure_api_key_for_model()`
- **方案 B**：首次 spawn 该模板时按需注册，失败时回退到 `self.model` 并打日志

建议采用 **方案 A**，在 `AgentTemplateManager._load()` 完成后触发一次预注册；或由 `AgentLoop` / Web API 初始化时统一处理。

#### 2.1.4 实现清单

| 序号 | 动作 | 文件 |
|------|------|------|
| 1 | `AgentTemplateConfig` 增加 `model: Optional[str] = None` | `config/agent_templates.py` |
| 2 | `_load_from_file` 解析 `model` 字段 | `config/agent_templates.py` |
| 3 | `update_template` 支持 `model` 更新 | `config/agent_templates.py` |
| 4 | `_save_user_templates`、`to_dict` 包含 `model` | `config/agent_templates.py` |
| 5 | `import_from_yaml` 校验并支持 `model` | `config/agent_templates.py` |
| 6 | `_run_subagent` 中解析 `effective_model`，传入 `provider.chat` | `agent/subagent.py` |
| 7 | `_dashscope_direct_call` 调用处传入 `effective_model` | `agent/subagent.py` |
| 8 | 模板 model 的 API key 预注册（启动/热重载时）| `web/api.py`、`cli/commands.py` |
| 9 | `list_agent_templates`、`get_agent_template` 返回 `model` | `web/api.py` |
| 10 | `create_agent_template`、`update_agent_template` 接收 `model` | `web/api.py` |
| 11 | 前端 `AgentTemplate` 类型、表单、列表展示 `model` | `web-ui/src/types.ts`、`AgentTemplatePage.tsx` |

#### 2.1.5 YAML 示例

```yaml
agents:
  - name: coder
    description: 代码编写任务
    model: anthropic/claude-sonnet-4
    tools: [read_file, write_file, edit_file, list_dir, exec]
    rules: [...]
    system_prompt: "..."

  - name: researcher
    model: openai/gpt-4o-mini   # 研究用便宜模型
    ...
```

#### 2.1.6 风险与注意事项

- **向后兼容**：现有 YAML 无 `model` 字段时，按 `None` 处理，行为不变
- **模板指定无效 model**：建议不做严格校验，由 provider 调用失败时按现有错误处理；可选增加「无效时回退到 self.model」的降级逻辑
- **Spawn 工具 template 枚举**：当前 `spawn.py` 中 `template` 为硬编码 enum，若支持用户自定义模板，需改为从 `AgentTemplateManager` 动态获取或放宽为 string

#### 2.1.7 验收标准

- 模板配置 `model` 后，native 子 agent 使用该模型完成对话
- 模板未配置 `model` 时，行为与现有一致（使用全局 subagent_model）
- 带图片的 DashScope 子 agent 使用模板 model（若配置）完成识别
- 前端可编辑、查看、导入导出包含 `model` 的模板

---

### 2.2 子 Agent Skills（P2，按需实现）

**目标**：让子 agent 能加载 workspace skills，与主 agent 共享项目级约定。

**设计原则**：基于实际需求决策，避免仅因 OpenCLAW 对比而引入。

**方案 A：模板级 skills**

在 `AgentTemplateConfig` 中增加 `skills: list[str]`：

```python
"coder": AgentTemplateConfig(
    skills=["code-review-expert", "python-expert"],
    ...
)
```

**方案 B：spawn 时传入 skills**

spawn 工具增加 `skill_names: list[str] | None`，由主 agent 决定本次子 agent 需要的技能。

**实现要点**：

- 使用 `SkillsLoader.load_skills_for_context(skill_names)` 注入 SKILL.md 内容
- 在 `build_system_prompt` 或 `_run_subagent` 中追加 skills 段落
- 注意 skills 与 tools 的匹配（避免给 researcher 传入需要 web_search 的 skill 却无对应 tool）
- 控制 token 预算，skills 会增加 prompt 长度

**建议**：先观察子 agent 输出质量，若确有 project-level 约定缺失再实现；优先考虑细化 rules 作为低成本替代。

---

### 2.3 图片识别策略（P1，文档与 prompt 指引）

**目标**：兼顾简单看图与复杂图像分析，控制成本。

**策略**：

| 场景 | 处理方式 |
|------|----------|
| 主模型支持视觉 | 不做 inline 预处理，直接传图给主 agent |
| 主模型不支持视觉 + 简单看图 | 保持现有 Inline 预处理 |
| 主模型不支持视觉 + 复杂分析 | Inline 做简要描述后，主 agent 判断是否 spawn 带 `attach_media` 的子 agent |
| 与问题无关的图片 | 可选：启发式跳过（如消息不含「图/看/分析」等）|

**可选优化**：

- 增加「跳过视觉」的简单启发：纯闲聊或明确无关问题时跳过 inline
- 视觉子 agent 使用模板级 `model`，如 `dashscope/qwen-vl-plus`（实现 2.1 后自动支持）

**无需新增**：现有 Inline + `attach_media` 已覆盖主要场景，混合策略主要通过主 agent 的决策逻辑实现。

---

### 2.4 Agent 模板存储迁移至 SQLite（可选）

**目标**：将 Agent 模板的用户配置从 YAML 文件迁移到 SQLite，与 channels、providers、mcps 等配置共用同一数据库文件（`~/.nanobot/chat.db` 或通过 `get_db_path()` 获取的路径）。

**当前存储**：

- **Builtin 模板**：`nanobot/config/agents.yaml`（包内只读）
- **用户模板**：`{workspace}/data/agents.yaml`（按 workspace 隔离）

**迁移后**：

- **Builtin 模板**：继续从包内 YAML 加载，或首次启动时写入 DB 作为种子数据
- **用户模板**：存入 SQLite `config_agent_templates` 表，与 channels 同一 DB

#### 2.4.1 表结构设计

```sql
CREATE TABLE IF NOT EXISTS config_agent_templates (
    name TEXT NOT NULL,                    -- 模板名称（主键之一）
    workspace_path TEXT NOT NULL DEFAULT '', -- workspace 路径，用于多 workspace 隔离
    source TEXT NOT NULL DEFAULT 'user_yaml', -- builtin | user_yaml
    config_json TEXT NOT NULL,             -- 模板完整配置 JSON（name, description, tools, rules, system_prompt, model, ...）
    enabled INTEGER NOT NULL DEFAULT 1,    -- 是否启用
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (name, workspace_path)
);
```

> 说明：`workspace_path` 与当前设计一致，用户模板按 workspace 隔离；`config_json` 存完整 `AgentTemplateConfig` 序列化，便于扩展字段。

#### 2.4.2 数据流

| 操作 | 当前（YAML） | 迁移后（SQLite） |
|------|--------------|------------------|
| 加载 builtin | 读 `nanobot/config/agents.yaml` | 不变，或从 DB 种子加载 |
| 加载 user | 读 `{workspace}/data/agents.yaml` | `ConfigRepository.get_agent_templates(workspace_path)` |
| 创建/更新/删除 user | 写 `{workspace}/data/agents.yaml` | `ConfigRepository.set_agent_template(...)` |
| 热重载 | 重新读 YAML | 重新从 DB 查询 |

#### 2.4.3 实现要点

1. **ConfigRepository 扩展**：在 `nanobot/storage/config_repository.py` 中增加 `get_agent_templates(workspace_path)`、`set_agent_template(...)`、`delete_agent_template(...)`，并在 `_init_tables` 中创建 `config_agent_templates` 表。
2. **AgentTemplateManager 改造**：`AgentTemplateManager` 接收 `config_repository: ConfigRepository` 和 `workspace: Path`，user 模板的读写改为走 DB，不再使用 `workspace/data/agents.yaml`。
3. **DB 路径**：与 channels 一致，使用 `get_config_repository()` 得到的 `ConfigRepository`（db 路径为 `get_db_path()`）。
4. **迁移与兼容**：首次启用时，若存在 `{workspace}/data/agents.yaml`，可自动导入到 DB，并可选保留/删除原 YAML 作为备份。
5. **YAML 导入/导出**：保留现有 API，导入时写入 DB，导出时从 DB 读取并生成 YAML 字符串，行为对前端透明。

#### 2.4.4 影响文件

- `nanobot/storage/config_repository.py` — 新表、新方法
- `nanobot/config/agent_templates.py` — `AgentTemplateManager` 改为读写 DB
- `nanobot/config/loader.py` — 若需 workspace 维度的 DB 路径则调整（当前 channels 用全局 db）
- `nanobot/web/api.py`、`nanobot/cli/commands.py` — 初始化 `AgentTemplateManager` 时传入 `ConfigRepository`

#### 2.4.5 与 2.1 模板级 Model 的关系

- 2.4 为**存储层**迁移，2.1 为**字段**扩展；二者可独立实现。
- 迁移至 SQLite 后，`model` 字段存入 `config_json`，2.1 的实现逻辑不变，仅数据来源从 YAML 变为 DB。

---

## 3. 实现优先级

| 优先级 | 项目 | 收益 | 复杂度 | 建议 |
|--------|------|------|--------|------|
| P0 | 模板级 model 配置 | 高：成本与能力分离 | 低 | 立即实现 |
| P1 | 图片识别混合策略说明 | 中：设计清晰 | 低 | 更新文档与 prompt 指引 |
| P2 | 子 agent skills | 中：项目约定一致 | 中 | 有明确需求再实现 |
| P2 | Agent 模板 YAML → SQLite 迁移 | 中：配置统一、易备份 | 中 | 与 channels 同库，可选实现 |

---

## 4. 附录

### 4.1 与 OpenCLAW 对比

| 维度 | OpenCLAW | Nanobot |
|------|----------|---------|
| 子 agent skills | `sessions_spawn` 有 `skills` 参数 | 当前无，可扩展 |
| 子 agent model | `sessions_spawn` 有 `model` 参数 | 仅全局 `subagent_model`，可增加模板级 model |
| 设计哲学 | 按 spawn 细粒度配置 | 按模板粗粒度 + 可选 spawn 覆盖 |

实现模板级 model 后，nanobot 在模型选择上可与 OpenCLAW 对齐，skills 按实际需求决定是否引入。

### 4.2 相关代码路径

| 功能 | 文件路径 |
|------|----------|
| 模板定义（内置）| `nanobot/agent/prompts.py` |
| 配置化模板 | `nanobot/config/agent_templates.py` |
| 子 agent 执行 | `nanobot/agent/subagent.py` |
| spawn 工具 | `nanobot/agent/tools/spawn.py` |
| Inline 图片识别 | `nanobot/agent/loop.py`（`_inline_image_recognition`）|
| DashScope 图片子 agent | `nanobot/agent/subagent.py`（`_dashscope_direct_call`）|
| 主 agent skills | `nanobot/agent/skills.py`、`nanobot/agent/context.py` |
| 全局 subagent_model | `nanobot/config/schema.py`（`agents.defaults.subagent_model`）|
| Channels / 配置 SQLite 存储 | `nanobot/storage/config_repository.py` |
| 配置 DB 路径 | `nanobot/config/loader.py`（`get_db_path()`→`~/.nanobot/chat.db`）|
