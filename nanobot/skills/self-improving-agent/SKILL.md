---
name: self-improving-agent
description: "Multi-memory self-improve: patterns, episodic log, skill updates."
short_description: "Learn from tasks, update skills"
keywords: "self-improve, 自我进化, lifelong, memory, pattern, retrospective, 教训, skill update, semantic"
category: "meta"
metadata: {"nanobot":{"emoji":"🔄"}}
---

# Self-Improving Agent（nanobot 内置版）

> 从每次技能使用中提炼经验，写入语义/情景记忆，并用可追溯的标记更新相关 `SKILL.md`。

## nanobot 中的行为

nanobot **不会**自动执行本目录下 `hooks/*.sh`，也没有 Claude Code 的 PreToolUse/PostToolUse。实际用法是：**在任务结束或出错后**，由你主动触发（例如说「自我进化」「按 self-improving-agent 总结」），代理按下面流程用工具完成：用 `read_file` / `write_file` / `edit_file` 读写本技能与目标技能；用 `exec` 跑测试或 `git`（若工作区允许）。

## Overview

- **多记忆结构**：语义（`memory/semantic-patterns.json`）+ 情景（`memory/episodic/`）+ 工作记忆（`memory/working/`）
- **自纠**：依据失败命令、测试失败或用户纠正，更新技能条文并打 **Correction** 标记
- **演进标记**：对技能文件的修改使用 `<!-- Evolution: ... -->` / `<!-- Correction: ... -->`

## 何时启用

**手动（nanobot 默认）**：用户提到「自我进化」「self-improve」「从经验中学习」「总结教训」「分析今天的经验」等。

**可选自动化**：若在 **Claude Code** 等环境中配置 hooks，可参考本目录 `hooks/` 与 `references/appendix.md` 中的外部产品说明；与 nanobot 主循环无关。

## 研究背景（摘要）

| 来源 | 要点 | 用途 |
|------|------|------|
| [SimpleMem](https://arxiv.org/html/2601.02553v1) | 高效终身记忆 | 模式积累 |
| [Multi-Memory Survey](https://dl.acm.org/doi/10.1145/3748302) | 语义 + 情景 | 规则与实例 |
| [Lifelong Learning](https://arxiv.org/html/2501.07278v1) | 持续任务流 | 多轮技能学习 |

## 改进循环（概念）

```
Skill / 任务结束 → 抽取经验 → 抽象模式 → 更新目标 SKILL.md + memory
```

## 演进优先级（示例）

当发现可复用知识时，优先把规则写回**实际存在的** nanobot 技能（如 `code-review-expert`、`skill-creator`）或工作区 `skills/`，不要虚构技能名。

| 触发 | 目标 | 优先级 |
|------|------|--------|
| 调试结论 | 相关调试/重构类技能 | 高 |
| Review 漏项 | `code-review-expert` | 高 |
| API/架构取舍 | 架构或 API 类技能 | 高 |
| React/状态模式 | 前端/调试类技能 | 中 |
| 测试策略 | 测试类技能 | 中 |

## 多记忆布局（路径以系统 Skills 列表为准）

系统提示里每条技能会给出 **`SKILL.md` 绝对路径** 与 **`dir:`**（该文件所在目录，即技能根目录）。例如：`.../SKILL.md | dir: .../self-improving-agent` —— 则：

- `memory/semantic-patterns.json` → `.../self-improving-agent/memory/semantic-patterns.json`
- `memory/episodic/`、`memory/working/`、`templates/`、`references/` → 均在 `.../self-improving-agent/` 下同名子目录

用 `read_file` / `write_file` / `edit_file` 时务必使用上述完整路径（勿假设只在用户 `workspace/skills/` 下）。

### 1. 语义记忆：`memory/semantic-patterns.json`

抽象规则与模式，JSON 结构见文件内示例；更新时用 `read_file` 读全文件，`write_file` 或 `edit_file` 合并。

### 2. 情景记忆：`memory/episodic/`（建议 `memory/episodic/YYYY/YYYY-MM-DD-{topic}.json`）

单次会话的处境、根因、教训、用户反馈（若有）。

### 3. 工作记忆：`memory/working/`

`current_session.json`、`last_error.json`（自纠时填写）、`session_end.json` 等当前会话上下文。

## 流程

### Phase 1：经验抽取

从刚完成的任务中整理：用了哪个技能、任务目标、结果（成功/部分/失败）、做得好与不好、根因、用户评分（若有）。

### Phase 2：模式抽象

把具体事件写成可复用的一条模式，对照 `memory/semantic-patterns.json` 是否已有同类；决定是新增 `patterns` 条目还是提高 `confidence` / `applications`。JSON 内 `meta.nanobot_target_skills_note` 与每条 `target_skills` 已与 **nanobot 内置技能名**对齐；若新增模式，只能引用 `nanobot/skills/` 下或 `workspace/skills/` 下真实存在的目录名。

抽象规则（摘要）：

- 同类问题重复 **≥3 次**：考虑写入目标技能的「易错点」
- 方案有效：写入「最佳实践」
- 用户评分高：强化该策略；评分低：写入「避免」并降置信

### Phase 3：更新技能

在目标 `SKILL.md` 中加入带日期的段落，并保留标记：

```markdown
<!-- Evolution: 2025-01-12 | source: ep-2025-01-12-001 | skill: code-review-expert -->
```

纠错示例：

```markdown
<!-- Correction: 2025-01-12 | was: "..." | reason: 导致陈旧数据 -->
```

### Phase 4：固化

1. 更新 `memory/semantic-patterns.json`
2. 新建或追加 `memory/episodic/...json`
3. 调整置信度；长期未验证的低置信模式可删减（需简要记录原因）
4. **SQLite（nanobot）— 必须执行**：每条要进入主 Agent Memory 的结论，**在改完文件后**必须调用工具 **`persist_self_improvement`** 写入 `.nanobot/chat.db`（`scope=self_improve`）。**只改 JSON/SKILL 不会进 SQLite**；跳过则后续对话里看不到这条总结。
   - `source_type`：`self_improve_episode` / `self_improve_pattern` / `self_improve_correction`
   - **去重键**：`(source_id, source_type)`；同 id 不同类型可并存。
   - `source_id`：与 episodic、`semantic-patterns`、`<!-- Evolution -->` 一致（如 `ep-…`、`pat-…`）。
   - `content`：简短摘要（≤16KB）。子 Agent 开独立记忆时写入对应 `agent_id`。

## 自纠（`exec` 非零 / 测试失败 / 用户否定）

1. 把错误上下文写入 `memory/working/last_error.json`（或会话小结）
2. 区分：技能写错、理解偏差、步骤不全
3. 用 `edit_file` 改正对应 `SKILL.md`，写 **Correction** 标记，并同步语义 memory
4. 必要时用 `exec` 重跑验证，并请用户确认

## 校验

使用 `references/appendix.md` 与 `templates/validation-template.md` 做发版前检查。

## 外部产品：Claude Code hooks（可选）

仅在 Claude Code 等支持 `~/.claude/settings.json` hooks 的环境中使用；将 `${SKILLS_DIR}` 换为实际技能根目录，并指向本仓库内 `nanobot/skills/self-improving-agent/hooks/` 下的脚本（需 bash 环境）。

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${SKILLS_DIR}/self-improving-agent/hooks/pre-tool.sh \"$TOOL_NAME\" \"$TOOL_INPUT\""
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${SKILLS_DIR}/self-improving-agent/hooks/post-bash.sh \"$TOOL_OUTPUT\" \"$EXIT_CODE\""
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${SKILLS_DIR}/self-improving-agent/hooks/session-end.sh"
          }
        ]
      }
    ]
  }
}
```

## 工具与引用

- **必读**：本文件；详细模板见 `templates/`；结构说明见 `references/appendix.md`
- **读写技能**：通过 `read_file` 打开目标路径（系统提示里会列出各技能 `SKILL.md` 路径）
- **搜索代码**：`exec` 使用 `rg`（若可用）或仓库约定的方式

## 原则

**应做**：从多次交互中抽象；更新多个相关技能；记录置信度与应用次数；大范围改写前先验证。

**避免**：单次个案过度泛化；无上下文的矛盾规则；破坏现有流程的「大改」。

## 快速开始（nanobot）

用户或你在任务末尾 **主动请求** 执行本流程：分析 → 抽取模式 → 更新 `memory/` 与相关 `SKILL.md` → 简短向用户汇报变更摘要。

## 参考链接

- [SimpleMem](https://arxiv.org/html/2601.02553v1)
- [Memory Mechanisms Survey](https://dl.acm.org/doi/10.1145/3748302)
- [Lifelong Learning of LLM Agents](https://arxiv.org/html/2501.07278v1)
