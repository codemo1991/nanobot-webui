-- AgentLoop 微内核 - 系统配置数据库表结构 (system.db)
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;
PRAGMA busy_timeout = 3000;

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
