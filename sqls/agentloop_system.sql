-- ============================================================
-- AgentLoop 微内核 - 系统配置数据库表结构
-- 数据库文件: ~/.nanobot/system.db 或 C:\Users\GYENNO\.nanobot\system.db
-- 执行时机: 应用启动时由 agentloop.db 模块自动初始化
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;
PRAGMA busy_timeout = 3000;

-- ------------------------------------------------------------
-- capability_registry 表：能力注册表（agent / tool / reducer）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentloop_capability_registry (
    capability_name TEXT PRIMARY KEY,
    capability_kind TEXT NOT NULL CHECK(capability_kind IN ('agent', 'tool', 'reducer')),
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK(is_enabled IN (0, 1)),
    max_concurrency INTEGER NOT NULL DEFAULT 1,
    avg_latency_ms INTEGER NOT NULL DEFAULT 0,
    avg_cost_cents INTEGER NOT NULL DEFAULT 0,
    success_rate REAL NOT NULL DEFAULT 1.0,
    cacheable INTEGER NOT NULL DEFAULT 0 CHECK(cacheable IN (0, 1)),
    config_json TEXT,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agentloop_capability_kind ON agentloop_capability_registry(capability_kind);
CREATE INDEX IF NOT EXISTS idx_agentloop_capability_enabled ON agentloop_capability_registry(is_enabled);

-- ------------------------------------------------------------
-- calendar_events 表：日历事件数据（全局公共数据，存于 system.db）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    is_all_day INTEGER NOT NULL DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'medium',
    reminders_json TEXT NOT NULL DEFAULT '[]',
    recurrence_json TEXT,
    recurrence_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_start ON calendar_events(start_time);
CREATE INDEX IF NOT EXISTS idx_calendar_events_recurrence ON calendar_events(recurrence_id);

-- ------------------------------------------------------------
-- calendar_settings 表：日历默认设置
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calendar_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    default_view TEXT NOT NULL DEFAULT 'dayGridMonth',
    default_priority TEXT NOT NULL DEFAULT 'medium',
    sound_enabled INTEGER NOT NULL DEFAULT 1,
    notification_enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO calendar_settings (id, default_view, default_priority, sound_enabled, notification_enabled, updated_at)
VALUES (1, 'dayGridMonth', 'medium', 1, 1, datetime('now'));
