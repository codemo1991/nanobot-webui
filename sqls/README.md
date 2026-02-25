# Nanobot 数据库结构说明

> 数据库文件位置: `{workspace}/.nanobot/chat.db` 或 `~/.nanobot/chat.db`

## 目录

- [1. 会话管理表 (Chat)](#1-会话管理表-chat)
- [2. 配置表 (Config)](#2-配置表-config)
- [3. 系统状态表 (System)](#3-系统状态表-system)
- [4. 记忆表 (Memory)](#4-记忆表-memory)
- [5. Mirror Room 表](#5-mirror-room-表)
- [6. Cron 任务表](#6-cron-任务表)
- [7. 日历表](#7-日历表)

---

## 1. 会话管理表 (Chat)

### 1.1 chat_sessions - 会话主表

存储会话元数据。

| 字段 | 类型 | 说明 |
|-----|------|------|
| key | TEXT | 会话唯一标识 (主键) |
| created_at | TEXT | 创建时间 (ISO格式) |
| updated_at | TEXT | 更新时间 (ISO格式) |
| metadata_json | TEXT | 元数据 JSON (默认 '{}') |

**索引:**
- `idx_chat_sessions_updated_at` - 按更新时间降序

### 1.2 chat_messages - 消息表

存储会话中的每条消息。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | INTEGER | 消息 ID (自增主键) |
| session_key | TEXT | 所属会话 key (外键) |
| role | TEXT | 角色: user / assistant / system |
| content | TEXT | 消息内容 |
| timestamp | TEXT | 时间戳 |
| sequence | INTEGER | 消息顺序 |
| extras_json | TEXT | 额外信息 JSON (默认 '{}') |

**索引:**
- `idx_chat_messages_session_sequence` - 按会话和顺序

### 1.3 chat_token_totals - Token 统计 (全量)

记录 API Token 使用情况。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | INTEGER | ID (主键) |
| prompt_tokens | INTEGER | Prompt tokens |
| completion_tokens | INTEGER | Completion tokens |
| total_tokens | INTEGER | 总 tokens |
| updated_at | TEXT | 更新时间 |

### 1.4 chat_session_token_totals - Token 统计 (按会话)

每个会话的 token 消耗。

| 字段 | 类型 | 说明 |
|-----|------|------|
| session_key | TEXT | 会话 key (主键) |
| prompt_tokens | INTEGER | Prompt tokens |
| completion_tokens | INTEGER | Completion tokens |
| total_tokens | INTEGER | 总 tokens |
| updated_at | TEXT | 更新时间 |

---

## 2. 配置表 (Config)

### 2.1 config - 主配置表

存储应用配置。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | INTEGER | ID (主键) |
| category | TEXT | 分类 |
| key | TEXT | 键 |
| value | TEXT | 值 |
| value_type | TEXT | 值类型 |
| updated_at | TEXT | 更新时间 |

**索引:**
- `idx_config_category` - 按分类

### 2.2 config_providers - Provider 配置

AI 模型供应商配置。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | TEXT | Provider ID (如 openai, anthropic) (主键) |
| name | TEXT | 显示名称 |
| api_key | TEXT | API Key |
| api_base | TEXT | API Base URL |
| enabled | INTEGER | 是否启用 (默认 1) |
| priority | INTEGER | 优先级 (默认 0) |
| updated_at | TEXT | 更新时间 |

**索引:**
- `idx_config_providers_enabled` - 按启用状态

### 2.3 config_channels - Channel 配置

通讯渠道配置。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | TEXT | Channel ID (如 telegram, feishu) (主键) |
| enabled | INTEGER | 是否启用 (默认 0) |
| config_json | TEXT | 渠道配置 JSON |
| updated_at | TEXT | 更新时间 |

**索引:**
- `idx_config_channels_enabled` - 按启用状态

### 2.4 config_mcps - MCP 配置

MCP 服务器配置。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | TEXT | MCP ID (主键) |
| name | TEXT | 显示名称 |
| transport | TEXT | 传输方式 (stdio, http, sse) |
| command | TEXT | 命令 (用于 stdio) |
| args_json | TEXT | 参数 JSON (用于 stdio, 默认 '[]') |
| url | TEXT | URL (用于 http/sse) |
| enabled | INTEGER | 是否启用 (默认 0) |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

### 2.5 config_tools - Tool 配置

工具配置。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | INTEGER | ID (主键) |
| tool_type | TEXT | 工具类型 |
| key | TEXT | 键 |
| value | TEXT | 值 |
| updated_at | TEXT | 更新时间 |

---

## 3. 系统状态表 (System)

### 3.1 system_status - 系统状态

存储运行时状态、并发配置和监控指标。

| 字段 | 类型 | 说明 |
|-----|------|------|
| key | TEXT | 键 (主键) |
| value | TEXT | 值 |
| updated_at | TEXT | 更新时间 |

**系统状态 key:**
- `start_time` - 系统启动时间戳

**并发配置 key (格式: `concurrency_{config_key}`):**

| config_key | 类型 | 说明 | 默认值 |
|-----------|------|------|--------|
| max_parallel_tool_calls | int | 最大并行工具调用数 | 5 |
| max_concurrent_subagents | int | 最大并发子Agent数 | 10 |
| enable_parallel_tools | bool | 是否启用并行工具调用 | true |
| thread_pool_size | int | 线程池大小 | 4 |
| enable_subagent_parallel | bool | 是否启用子Agent并行执行 | true |
| claude_code_max_concurrent | int | Claude Code 最大并发数 | 3 |

**监控指标 key (格式: `metric_{metric_key}`):**

| metric_key | 类型 | 说明 |
|-----------|------|------|
| total_tool_calls | int | 总工具调用次数 |
| parallel_tool_calls | int | 并行工具调用次数 |
| serial_tool_calls | int | 串行工具调用次数 |
| failed_tool_calls | int | 失败工具调用次数 |
| total_subagent_spawns | int | 子Agent spawn 总次数 |
| avg_tool_execution_time | float | 平均工具执行时间 (秒) |
| max_concurrent_tools | int | 最大并发工具数 |
| llm_call_count | int | LLM 调用次数 |
| total_token_usage | int | 总 token 使用量 |

**索引:**
- `idx_system_status_updated_at` - 按更新时间降序

---

## 4. 记忆表 (Memory)

### 4.1 memory_entries - 长期记忆

存储需要长期保存的信息。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | INTEGER | 记忆 ID (自增主键) |
| agent_id | TEXT | Agent ID (可选，用于隔离) |
| scope | TEXT | 作用域: global, mirror-wu 等 (默认 'global') |
| content | TEXT | 记忆内容 |
| source_type | TEXT | 来源类型: remember, auto_integrate, daily_merge |
| source_id | TEXT | 来源 ID |
| entry_date | TEXT | 记录日期 (YYYY-MM-DD) |
| entry_time | TEXT | 记录时间 (HH:MM) |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

**索引:**
- `idx_memory_scope` - 按作用域和 agent_id
- `idx_memory_date` - 按日期降序
- `idx_memory_created` - 按创建时间降序
- `idx_memory_source` - 按来源类型和来源 ID

### 4.2 daily_notes - 每日笔记

存储当天的对话摘要。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | INTEGER | 笔记 ID (自增主键) |
| agent_id | TEXT | Agent ID |
| scope | TEXT | 作用域 (默认 'global') |
| note_date | TEXT | 笔记日期 (YYYY-MM-DD) |
| content | TEXT | 笔记内容 |
| is_processed | INTEGER | 是否已处理 (默认 0) |
| processed_at | TEXT | 处理时间 |
| created_at | TEXT | 创建时间 |

**索引:**
- `idx_daily_notes_scope` - 按作用域、agent_id 和日期
- `idx_daily_notes_processed` - 按处理状态和日期

### 4.3 memory_migration_status - 记忆迁移状态

记录文件迁移进度。

| 字段 | 类型 | 说明 |
|-----|------|------|
| key | TEXT | 迁移键 (主键) |
| migrated_at | TEXT | 迁移完成时间 |
| details | TEXT | 详情 |

### 4.4 memory_fts - FTS5 全文搜索

用于快速检索记忆。

| 字段 | 类型 | 说明 |
|-----|------|------|
| content | TEXT | 记忆内容 |
| content_rowid | INTEGER | 关联的 memory_entries id |

**分词器:** `porter unicode61`

---

## 5. Mirror Room 表

### 5.1 mirror_shang_records - Mirror 记录

存储"辩"对话记录。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | TEXT | 记录 ID (主键) |
| record_date | TEXT | 记录日期 |
| topic | TEXT | 主题 |
| image_a_url | TEXT | 图片 A URL |
| image_b_url | TEXT | 图片 B URL |
| description_a | TEXT | 描述 A |
| description_b | TEXT | 描述 B |
| choice | TEXT | 选择 |
| attribution | TEXT | 作者归属 |
| analysis_json | TEXT | 分析 JSON |
| status | TEXT | 状态 |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

**索引:**
- `idx_shang_status` - 按状态
- `idx_shang_date` - 按日期降序

### 5.2 mirror_profiles - Mirror Profile

存储"吾"角色卡片。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | INTEGER | Profile ID (主键) |
| profile_json | TEXT | Profile JSON |
| update_time | TEXT | 更新时间 |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

### 5.3 mirror_profile_snapshots - Mirror Profile 快照

存储历史 Profile。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | INTEGER | 快照 ID (主键) |
| snapshot_date | TEXT | 快照日期 |
| profile_json | TEXT | Profile JSON |
| created_at | TEXT | 创建时间 |

**索引:**
- `idx_profile_snapshots_date` - 按日期降序

---

## 6. Cron 任务表

### 6.1 cron_jobs - Cron 定时任务

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | TEXT | 任务 ID (主键) |
| name | TEXT | 任务名称 |
| enabled | INTEGER | 是否启用 (默认 1) |
| is_system | INTEGER | 是否为系统任务 (默认 0) |
| trigger_type | TEXT | 触发类型: interval, date, cron |
| trigger_date_ms | INTEGER | 定时触发时间 (毫秒) |
| trigger_interval_seconds | INTEGER | 间隔触发秒数 |
| trigger_cron_expr | TEXT | Cron 表达式 |
| trigger_tz | TEXT | 时区 |
| payload_kind | TEXT | 负载类型 (默认 'agent_turn') |
| payload_message | TEXT | 负载消息 |
| payload_deliver | INTEGER | 负载投递方式 (默认 0) |
| payload_channel | TEXT | 负载渠道 |
| payload_to | TEXT | 负载接收者 |
| next_run_at_ms | INTEGER | 下次运行时间 (毫秒) |
| last_run_at_ms | INTEGER | 上次运行时间 (毫秒) |
| last_status | TEXT | 上次状态 |
| last_error | TEXT | 上次错误 |
| delete_after_run | INTEGER | 运行后是否删除 (默认 0) |
| created_at_ms | INTEGER | 创建时间 (毫秒) |
| updated_at_ms | INTEGER | 更新时间 (毫秒) |

**索引:**
- `idx_cron_jobs_next_run` - 按下次运行时间
- `idx_cron_jobs_enabled` - 按启用状态

---

## 7. 日历表

### 7.1 calendar_events - 日历事件表

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | TEXT | 事件 ID (主键) |
| title | TEXT | 标题 |
| description | TEXT | 描述 |
| start_time | TEXT | 开始时间 (ISO 8601) |
| end_time | TEXT | 结束时间 (ISO 8601) |
| is_all_day | INTEGER | 是否全天 (默认 0) |
| priority | TEXT | 优先级: high/medium/low (默认 'medium') |
| reminders_json | TEXT | Reminder[] JSON (默认 '[]') |
| recurrence_json | TEXT | RecurrenceRule JSON |
| recurrence_id | TEXT | 原始重复事件 ID |
| created_at | TEXT | 创建时间 |
| updated_at | TEXT | 更新时间 |

**索引:**
- `idx_calendar_events_start` - 按开始时间
- `idx_calendar_events_recurrence` - 按重复事件 ID

### 7.2 calendar_settings - 日历设置表

单行配置表。

| 字段 | 类型 | 说明 |
|-----|------|------|
| id | INTEGER | ID (默认 1) |
| default_view | TEXT | 默认视图 (默认 'dayGridMonth') |
| default_priority | TEXT | 默认优先级 (默认 'medium') |
| sound_enabled | INTEGER | 声音启用 (默认 1) |
| notification_enabled | INTEGER | 通知启用 (默认 1) |
| updated_at | TEXT | 更新时间 |

---

## 使用示例

### 读取并发配置

```sql
SELECT key, value FROM system_status
WHERE key LIKE 'concurrency_%';
```

### 读取监控指标

```sql
SELECT key, value FROM system_status
WHERE key LIKE 'metric_%';
```

### 更新并发配置

```sql
INSERT INTO system_status (key, value, updated_at)
VALUES ('concurrency_max_parallel_tool_calls', '10', datetime('now'))
ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;
```

### 重置监控指标

```sql
DELETE FROM system_status WHERE key LIKE 'metric_%';
```
