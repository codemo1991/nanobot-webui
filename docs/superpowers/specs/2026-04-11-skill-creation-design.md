# Skill 创建功能设计

**日期**: 2026-04-11
**状态**: 已批准
**参考**: Hermes-Agent 自进化系统

---

## 背景

nanobot-webui 已有 SkillsLoader（只读加载器），但缺少动态创建 Skill 的能力。对标 Hermes-Agent 的 `skill_manage` 工具，实现 Agent 自主创建 Skill 的自进化闭环。

**现状差距**:
- nanobot: `nanobot/agent/skills.py` 仅支持加载，无创建工具
- Hermes-Agent: `skill_manage(action='create|edit|delete|patch|write_file')` + `~/.hermes/skills/`

**目标**: 让 Agent 在复杂任务完成后，通过提示词引导 → 工具调用 → 文件系统持久化，实现跨会话的 Skill 复用。

---

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 存储位置 | `{workspace}/skills/` | workspace-first 架构，每个工作区独立 |
| 新 Skill 可见时机 | 下个会话（冻结快照） | 防止同会话内无限自举；workspace 内文件少，扫描成本低 |
| 冻结快照范围 | session 级 skills 索引 | 多会话并发，每个 session 独立快照 |
| 缓存失效策略 | `skill_manage` 成功后清除所有 session 快照 | skills 目录全局共享 |
| 管理动作 | create + edit + delete + patch + write_file | 完整集合，与 Hermes 兼容 |
| 反馈格式 | 仅 JSON 结果 | 保持 nanobot 简洁风格 |
| 安全扫描 | 写入前扫描威胁模式 | 与 Hermes 一致 |

---

## 架构

### 文件结构

```
workspace/
└── skills/                      # Skill 根目录（workspace 内）
    ├── {skill_name}/
    │   ├── SKILL.md             # 主文件
    │   └── references/           # 辅助文件（可选）
    └── ...

nanobot/agent/tools/
├── skill_manager.py              # 新文件
└── registry.py                   # 修改（注册 skill_manage）
```

### 与 Self-Improve 的关系

| 工具 | 存储位置 | 用途 | 格式 |
|------|---------|------|------|
| `persist_self_improvement` | SQLite `scope=self_improve` | 经验教训沉淀 | 自然语言摘要 |
| `skill_manage` | `{workspace}/skills/` | 可复用工作流 | 结构化 SKILL.md |

两者独立互补：自我改进产生结论 → LLM 判断是否值得封装为 Skill。

---

## 工具定义

### Schema

```python
SKILL_SCHEMA = {
    "name": "skill_manage",
    "description": (
        "创建、编辑、删除工作区内的 Skill。\n"
        "触发时机：完成复杂任务（5+工具调用）、修复错误、发现可复用工作流。\n"
        "Skill 存储在 {workspace}/skills/，所有会话共享。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "edit", "delete", "patch", "write_file"],
                "description": "动作"
            },
            "name": {
                "type": "string",
                "pattern": r"^[a-z0-9][a-z0-9_-]{0,63}$",
                "description": "Skill 名称（小写、字母数字、-_. ，最大64字符）"
            },
            "content": {
                "type": "string",
                "description": "SKILL.md 内容（create/edit 必须包含 YAML frontmatter）"
            },
            "file_path": {
                "type": "string",
                "description": "辅助文件路径（write_file 时必填）"
            },
            "file_content": {
                "type": "string",
                "description": "辅助文件内容（write_file 时必填）"
            },
            "old_string": {
                "type": "string",
                "description": "patch 的目标字符串"
            },
            "new_string": {
                "type": "string",
                "description": "patch 的替换字符串"
            },
        },
        "required": ["action", "name"]
    }
}
```

### SKILL.md 格式

```yaml
---
name: my-skill
description: 简短描述（最大80字符）
category: dev
version: 1.0.0
---

# 技能标题

完整指令内容，支持 Markdown...
```

---

## 核心模块

### skill_manager.py

```python
# 入口
def skill_manage(action, name, content=None, file_path=None,
                 file_content=None, old_string=None, new_string=None):
    """统一入口，分发到各 action 处理函数"""

# 核心函数
_create_skill(name, content)      # 创建新 Skill
_edit_skill(name, content)        # 全文替换
_delete_skill(name)                # 删除 Skill 目录
_patch_skill(name, old, new)      # 局部字符串替换
_write_skill_file(name, path, content)  # 辅助文件

# 验证函数
_validate_name(name)              # 名称格式验证
_validate_frontmatter(content)    # YAML frontmatter 验证
_validate_content_size(content)   # 内容大小限制

# 安全
_security_scan(skill_dir)        # 威胁模式扫描
```

### 验证规则

| 字段 | 规则 |
|------|------|
| name | 小写字母数字，可含 `-` `_` `.`，最大 64 字符，不含空格 |
| content | 必须以 `---` 开头，含 `---` 闭合，含 `name` 和 `description` |
| description | 最大 80 字符 |
| content 大小 | 最大 50,000 字符 |
| file_path | 不能包含 `..`（路径穿越防护） |
| file_path | 必须在 skill 目录内 |

### 安全扫描模式

```python
_THREAT_PATTERNS = [
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET...)', "exfil_curl"),
    (r'ignore\s+(?:\w+\s+)*(previous|all|above)...', "prompt_injection"),
    (r'crontab\b', "persistence"),
    (r'\bnc\s+-[lp]', "reverse_shell"),
    (r'authorized_keys', "ssh_backdoor"),
]
```

扫描失败时回滚（删除已创建的目录或恢复原内容）。

---

## 冻结快照机制

### 快照结构

```python
# ApiHandler 层
_skill_index_snapshots: dict[str, str]  # {session_id: skill_index_text}
```

### 失效策略

| 触发 | 失效范围 | 理由 |
|------|---------|------|
| `skill_manage` 成功 | 所有 session 快照 | skills 目录全局共享 |
| `/reset` / 新 session | 仅该 session | 全新对话流 |

### 流程

```
skill_manage(action='create', ...)
    │
    └─▶ _create_skill() → 写入文件
              │
              └─▶ _invalidate_all_skill_snapshots()
                       │
                       └─▶ self._skill_index_snapshots.clear()
    │
    └─▶ 返回 {"success": true, "path": "skills/xxx"}

下一轮对话
    │
    └─▶ _build_skill_index() → 缓存未命中 → 重新扫描 skills/ → 新 Skill 可见
```

---

## SKILLS_GUIDANCE

注入位置：`ContextBuilder.build_system_prompt()`

```python
SKILLS_GUIDANCE = (
    "After completing a complex task (5+ tool calls), fixing a tricky error, "
    "or discovering a non-trivial workflow, save the approach as a "
    "skill with skill_manage so you can reuse it next time.\n"
    "When using a skill and finding it outdated, incomplete, or wrong, "
    "patch it immediately with skill_manage(action='patch') — don't wait to be asked."
)
```

条件注入：`if "skill_manage" in valid_tool_names: prompt_parts.append(SKILLS_GUIDANCE)`

---

## 实现计划

### 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `nanobot/agent/tools/skill_manager.py` | 新增 | 工具实现 |
| `nanobot/agent/tools/registry.py` | 修改 | 注册 skill_manage |
| `nanobot/agent/context.py` | 修改 | 注入 SKILLS_GUIDANCE |
| `nanobot/web/api.py` | 修改 | 添加快照缓存 + 失效机制 |
| `tests/agent/tools/test_skill_manager.py` | 新增 | 单元测试 |

### 依赖

- 无新增外部依赖
- 复用现有的 `SkillsLoader`（读取）和 `filesystem` 工具（辅助文件写入）

### 风险

| 风险 | 缓解 |
|------|------|
| 并发写入同一 skill | 原子写入 + 异常回滚 |
| 安全扫描绕过 | 定期人工审查 + 威胁模式库维护 |
| 快照失效不及时 | skill_manage 成功后同步清除 |

---

## 验收标准

1. Agent 可通过 `skill_manage(action='create', ...)` 创建 Skill
2. Skill 存储在 `{workspace}/skills/{name}/SKILL.md`
3. 新 Skill 在**下一轮对话**中出现在 skills 索引中
4. `edit` / `delete` / `patch` / `write_file` 均可正常工作
5. 安全扫描阻止恶意内容写入
6. 多会话并发下，skill 创建对所有 session 立即可见（下一轮）
7. 现有 `persist_self_improvement` 不受影响
