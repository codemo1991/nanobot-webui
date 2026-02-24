-- ============================================================
-- nanobot 数据库升级脚本
-- 此文件用于已有数据库的热升级
-- 执行方式：sqlite3 {database} < sqls/upgrade_memory.sql
-- ============================================================

-- ------------------------------------------------------------
-- 日历表 (v1)
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
