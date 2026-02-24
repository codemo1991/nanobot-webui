-- ============================================================
-- nanobot 初始数据库结构
-- 数据库文件: {workspace}/.nanobot/chat.db 或 ~/.nanobot/chat.db
-- ============================================================

-- ------------------------------------------------------------
-- 1. 会话管理表 (Chat)
-- ------------------------------------------------------------

-- 会话主表：存储会话元数据
CREATE TABLE IF NOT EXISTS chat_sessions (
    key TEXT PRIMARY KEY,                      -- 会话唯一标识
    created_at TEXT NOT NULL,                   -- 创建时间
    updated_at TEXT NOT NULL,                   -- 更新时间
    metadata_json TEXT NOT NULL DEFAULT '{}'    -- 元数据 JSON
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated_at ON chat_sessions(updated_at DESC);

-- 消息表：存储会话中的每条消息
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,        -- 消息 ID
    session_key TEXT NOT NULL,                   -- 所属会话 key
    role TEXT NOT NULL,                          -- 角色：user / assistant / system
    content TEXT NOT NULL,                       -- 消息内容
    timestamp TEXT NOT NULL,                     -- 时间戳
    sequence INTEGER NOT NULL,                   -- 消息顺序
    extras_json TEXT NOT NULL DEFAULT '{}',      -- 额外信息 JSON
    FOREIGN KEY(session_key) REFERENCES chat_sessions(key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_sequence ON chat_messages(session_key, sequence);

-- Token 统计（全量）：记录 API token 使用情况
CREATE TABLE IF NOT EXISTS chat_token_totals (
    id INTEGER PRIMARY KEY,                      -- ID
    prompt_tokens INTEGER,                       -- Prompt tokens
    completion_tokens INTEGER,                   -- Completion tokens
    total_tokens INTEGER,                        -- 总 tokens
    updated_at TEXT NOT NULL                    -- 更新时间
);

-- Token 统计（按会话）：每个会话的 token 消耗
CREATE TABLE IF NOT EXISTS chat_session_token_totals (
    session_key TEXT PRIMARY KEY,                -- 会话 key
    prompt_tokens INTEGER,                       -- Prompt tokens
    completion_tokens INTEGER,                   -- Completion tokens
    total_tokens INTEGER,                        -- 总 tokens
    updated_at TEXT NOT NULL                    -- 更新时间
);

-- ------------------------------------------------------------
-- 2. 配置表 (Config)
-- ------------------------------------------------------------

-- 主配置表：存储应用配置
CREATE TABLE IF NOT EXISTS config (
    id INTEGER PRIMARY KEY,                      -- ID
    category TEXT NOT NULL,                      -- 分类
    key TEXT NOT NULL,                          -- 键
    value TEXT NOT NULL,                        -- 值
    value_type TEXT NOT NULL,                   -- 值类型
    updated_at TEXT NOT NULL                    -- 更新时间
);

CREATE INDEX IF NOT EXISTS idx_config_category ON config(category);

-- Provider 配置：AI 模型供应商配置
CREATE TABLE IF NOT EXISTS config_providers (
    id TEXT PRIMARY KEY,                        -- Provider ID (如 openai, anthropic)
    name TEXT NOT NULL,                         -- 显示名称
    api_key TEXT,                               -- API Key
    api_base TEXT,                              -- API Base URL
    enabled INTEGER NOT NULL DEFAULT 1,         -- 是否启用
    priority INTEGER NOT NULL DEFAULT 0,        -- 优先级
    updated_at TEXT NOT NULL                    -- 更新时间
);

CREATE INDEX IF NOT EXISTS idx_config_providers_enabled ON config_providers(enabled);

-- Channel 配置：通讯渠道配置
CREATE TABLE IF NOT EXISTS config_channels (
    id TEXT PRIMARY KEY,                        -- Channel ID (如 telegram, feishu)
    enabled INTEGER NOT NULL DEFAULT 0,         -- 是否启用
    config_json TEXT NOT NULL,                  -- 渠道配置 JSON
    updated_at TEXT NOT NULL                    -- 更新时间
);

CREATE INDEX IF NOT EXISTS idx_config_channels_enabled ON config_channels(enabled);

-- MCP 配置：MCP 服务器配置
CREATE TABLE IF NOT EXISTS config_mcps (
    id TEXT PRIMARY KEY,                        -- MCP ID
    name TEXT NOT NULL,                         -- 显示名称
    transport TEXT NOT NULL,                    -- 传输方式 (stdio, http, sse)
    command TEXT,                               -- 命令 (用于 stdio)
    args_json TEXT NOT NULL DEFAULT '[]',      -- 参数 JSON (用于 stdio)
    url TEXT,                                   -- URL (用于 http/sse)
    enabled INTEGER NOT NULL DEFAULT 0,         -- 是否启用
    created_at TEXT NOT NULL,                   -- 创建时间
    updated_at TEXT NOT NULL                    -- 更新时间
);

-- Tool 配置：工具配置
CREATE TABLE IF NOT EXISTS config_tools (
    id INTEGER PRIMARY KEY,                      -- ID
    tool_type TEXT NOT NULL,                    -- 工具类型
    key TEXT NOT NULL,                          -- 键
    value TEXT NOT NULL,                        -- 值
    updated_at TEXT NOT NULL                    -- 更新时间
);

-- ------------------------------------------------------------
-- 3. 系统状态表 (System)
-- ------------------------------------------------------------

-- 系统状态：存储运行时状态
CREATE TABLE IF NOT EXISTS system_status (
    key TEXT PRIMARY KEY,                       -- 键
    value TEXT NOT NULL,                        -- 值
    updated_at TEXT NOT NULL                    -- 更新时间
);

CREATE INDEX IF NOT EXISTS idx_system_status_updated_at ON system_status(updated_at);

-- ------------------------------------------------------------
-- 4. 记忆表 (Memory)
-- ------------------------------------------------------------

-- 长期记忆：存储需要长期保存的信息
CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,        -- 记忆 ID
    agent_id TEXT,                              -- Agent ID (可选，用于隔离)
    scope TEXT DEFAULT 'global',                -- 作用域：global, mirror-wu 等
    content TEXT NOT NULL,                     -- 记忆内容
    source_type TEXT,                          -- 来源类型：remember, auto_integrate, daily_merge
    source_id TEXT,                             -- 来源 ID
    entry_date TEXT,                           -- 记录日期 (YYYY-MM-DD)
    entry_time TEXT,                           -- 记录时间 (HH:MM)
    created_at TEXT NOT NULL,                  -- 创建时间
    updated_at TEXT NOT NULL                   -- 更新时间
);

CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_entries(scope, agent_id);
CREATE INDEX IF NOT EXISTS idx_memory_date ON memory_entries(entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_source ON memory_entries(source_type, source_id);

-- 每日笔记：存储当天的对话摘要
CREATE TABLE IF NOT EXISTS daily_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,        -- 笔记 ID
    agent_id TEXT,                              -- Agent ID
    scope TEXT DEFAULT 'global',                -- 作用域
    note_date TEXT NOT NULL,                    -- 笔记日期 (YYYY-MM-DD)
    content TEXT NOT NULL,                      -- 笔记内容
    is_processed INTEGER DEFAULT 0,             -- 是否已处理（合并到长期记忆）
    processed_at TEXT,                          -- 处理时间
    created_at TEXT NOT NULL                    -- 创建时间
);

CREATE INDEX IF NOT EXISTS idx_daily_notes_scope ON daily_notes(scope, agent_id, note_date);
CREATE INDEX IF NOT EXISTS idx_daily_notes_processed ON daily_notes(is_processed, note_date);

-- 记忆迁移状态：记录文件迁移进度
CREATE TABLE IF NOT EXISTS memory_migration_status (
    key TEXT PRIMARY KEY,                       -- 迁移键
    migrated_at TEXT,                          -- 迁移完成时间
    details TEXT                                -- 详情
);

-- FTS5 全文搜索：用于快速检索记忆
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    content,                                    -- 记忆内容
    content_rowid=id,                          -- 关联的 memory_entries id
    tokenize='porter unicode61'
);

-- ------------------------------------------------------------
-- 5. Mirror Room 表
-- ------------------------------------------------------------

-- Mirror 记录：存储"辩"对话记录
CREATE TABLE IF NOT EXISTS mirror_shang_records (
    id TEXT PRIMARY KEY,                        -- 记录 ID
    record_date TEXT,                          -- 记录日期
    topic TEXT,                                -- 主题
    image_a_url TEXT,                          -- 图片 A URL
    image_b_url TEXT,                          -- 图片 B URL
    description_a TEXT,                         -- 描述 A
    description_b TEXT,                         -- 描述 B
    choice TEXT,                               -- 选择
    attribution TEXT,                          -- 作者归属
    analysis_json TEXT,                        -- 分析 JSON
    status TEXT,                               -- 状态
    created_at TEXT NOT NULL,                   -- 创建时间
    updated_at TEXT NOT NULL                    -- 更新时间
);

CREATE INDEX IF NOT EXISTS idx_shang_status ON mirror_shang_records(status);
CREATE INDEX IF NOT EXISTS idx_shang_date ON mirror_shang_records(record_date DESC);

-- Mirror Profile：存储"吾"角色卡片
CREATE TABLE IF NOT EXISTS mirror_profiles (
    id INTEGER PRIMARY KEY,                      -- Profile ID
    profile_json TEXT NOT NULL,                 -- Profile JSON
    update_time TEXT NOT NULL,                  -- 更新时间
    created_at TEXT NOT NULL,                   -- 创建时间
    updated_at TEXT NOT NULL                    -- 更新时间
);

-- Mirror Profile 快照：存储历史 Profile
CREATE TABLE IF NOT EXISTS mirror_profile_snapshots (
    id INTEGER PRIMARY KEY,                      -- 快照 ID
    snapshot_date TEXT NOT NULL,                -- 快照日期
    profile_json TEXT NOT NULL,                 -- Profile JSON
    created_at TEXT NOT NULL                    -- 创建时间
);

CREATE INDEX IF NOT EXISTS idx_profile_snapshots_date ON mirror_profile_snapshots(snapshot_date DESC);

-- ------------------------------------------------------------
-- 6. Cron 任务表
-- ------------------------------------------------------------

-- Cron 定时任务
CREATE TABLE IF NOT EXISTS cron_jobs (
    id TEXT PRIMARY KEY,                        -- 任务 ID
    name TEXT NOT NULL,                         -- 任务名称
    enabled INTEGER NOT NULL DEFAULT 1,          -- 是否启用
    is_system INTEGER NOT NULL DEFAULT 0,        -- 是否为系统任务 (0=否, 1=是)
    trigger_type TEXT NOT NULL,                 -- 触发类型：interval, date, cron
    trigger_date_ms INTEGER,                    -- 定时触发时间 (毫秒)
    trigger_interval_seconds INTEGER,           -- 间隔触发秒数
    trigger_cron_expr TEXT,                    -- Cron 表达式
    trigger_tz TEXT,                            -- 时区
    payload_kind TEXT DEFAULT 'agent_turn',      -- 负载类型：agent_turn, system_event
    payload_message TEXT,                       -- 负载消息
    payload_deliver INTEGER DEFAULT 0,          -- 负载投递方式
    payload_channel TEXT,                       -- 负载渠道
    payload_to TEXT,                            -- 负载接收者
    next_run_at_ms INTEGER,                     -- 下次运行时间 (毫秒)
    last_run_at_ms INTEGER,                     -- 上次运行时间 (毫秒)
    last_status TEXT,                          -- 上次状态
    last_error TEXT,                            -- 上次错误
    delete_after_run INTEGER DEFAULT 0,        -- 运行后是否删除
    created_at_ms INTEGER NOT NULL,            -- 创建时间 (毫秒)
    updated_at_ms INTEGER NOT NULL              -- 更新时间 (毫秒)
);

CREATE INDEX IF NOT EXISTS idx_cron_jobs_next_run ON cron_jobs(next_run_at_ms);
CREATE INDEX IF NOT EXISTS idx_cron_jobs_enabled ON cron_jobs(enabled);

-- ------------------------------------------------------------
-- 6. 日历表
-- ------------------------------------------------------------

-- 日历事件表
CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    start_time TEXT NOT NULL,        -- ISO 8601
    end_time TEXT NOT NULL,          -- ISO 8601
    is_all_day INTEGER NOT NULL DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'medium',  -- high/medium/low
    reminders_json TEXT NOT NULL DEFAULT '[]',    -- Reminder[] JSON
    recurrence_json TEXT,                         -- RecurrenceRule JSON
    recurrence_id TEXT,              -- 原始重复事件 ID
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_start ON calendar_events(start_time);
CREATE INDEX IF NOT EXISTS idx_calendar_events_recurrence ON calendar_events(recurrence_id);

-- 日历设置表（单行配置）
CREATE TABLE IF NOT EXISTS calendar_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,  -- 始终只有一行
    default_view TEXT NOT NULL DEFAULT 'dayGridMonth',
    default_priority TEXT NOT NULL DEFAULT 'medium',
    sound_enabled INTEGER NOT NULL DEFAULT 1,
    notification_enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

-- 初始化默认日历设置
INSERT OR IGNORE INTO calendar_settings (id, default_view, default_priority, sound_enabled, notification_enabled, updated_at)
VALUES (1, 'dayGridMonth', 'medium', 1, 1, datetime('now'));

-- ------------------------------------------------------------
-- 7. SQLite 内部表
-- ------------------------------------------------------------

-- SQLite sequence：自增主键计数器
CREATE TABLE IF NOT EXISTS sqlite_sequence (
    name TEXT PRIMARY KEY,                      -- 表名
    seq INTEGER NOT NULL                        -- 序列值
);
