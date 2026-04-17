-- ============================================================
-- AgentLoop 微内核 - 业务数据库表结构
-- 数据库文件: {workspace}/.nanobot/chat.db 或 E:\workSpace\.nanobot\chat.db
-- 执行时机: 应用启动时由 agentloop.db 模块自动初始化
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;
PRAGMA busy_timeout = 3000;

-- ------------------------------------------------------------
-- 1. traces 表：一次用户请求的完整执行链路
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentloop_traces (
    trace_id TEXT PRIMARY KEY,
    root_task_id TEXT,
    user_input TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('NEW', 'RUNNING', 'DONE', 'FAILED', 'CANCELED')),
    success_criteria TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    finished_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_agentloop_traces_status ON agentloop_traces(status);
CREATE INDEX IF NOT EXISTS idx_agentloop_traces_created ON agentloop_traces(created_at DESC);

-- ------------------------------------------------------------
-- 2. tasks 表：可调度的工作单元，形成任务树
-- 注：result_artifact_id 无 FK 约束，避免与 artifacts 循环依赖
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentloop_tasks (
    task_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_task_id TEXT,
    task_kind TEXT NOT NULL CHECK(task_kind IN ('ROOT', 'AGENT', 'TOOL', 'REDUCER')),
    capability_name TEXT NOT NULL,
    intent TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'NEW',
        'READY',
        'LEASED',
        'RUNNING',
        'WAITING_CHILDREN',
        'WAITING_ARTIFACTS',
        'REDUCING',
        'DONE',
        'FAILED',
        'CANCELED',
        'STALE'
    )),
    priority INTEGER NOT NULL DEFAULT 100,
    depth INTEGER NOT NULL DEFAULT 0,
    budget_tokens INTEGER NOT NULL DEFAULT 0,
    budget_millis INTEGER NOT NULL DEFAULT 0,
    budget_cost_cents INTEGER NOT NULL DEFAULT 0,
    deadline_ts INTEGER,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 1,
    expected_children INTEGER NOT NULL DEFAULT 0,
    finished_children INTEGER NOT NULL DEFAULT 0,
    join_policy TEXT NOT NULL DEFAULT 'ALL' CHECK(join_policy IN ('ALL', 'ANY', 'QUORUM')),
    quorum_n INTEGER,
    input_schema TEXT,
    output_schema TEXT,
    request_payload TEXT,
    result_artifact_id TEXT,
    error_code TEXT,
    error_message TEXT,
    lease_owner TEXT,
    lease_until INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER,
    FOREIGN KEY(trace_id) REFERENCES agentloop_traces(trace_id),
    FOREIGN KEY(parent_task_id) REFERENCES agentloop_tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_agentloop_tasks_trace_state_priority
ON agentloop_tasks(trace_id, state, priority);

CREATE INDEX IF NOT EXISTS idx_agentloop_tasks_parent
ON agentloop_tasks(parent_task_id);

CREATE INDEX IF NOT EXISTS idx_agentloop_tasks_lease
ON agentloop_tasks(state, lease_until);

-- ------------------------------------------------------------
-- 3. artifacts 表：任务产出的中间结果或最终结果
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentloop_artifacts (
    artifact_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    producer_task_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'READY', 'STALE', 'DELETED')),
    storage_kind TEXT NOT NULL CHECK(storage_kind IN ('INLINE', 'FILE')),
    payload_text TEXT,
    payload_path TEXT,
    payload_hash TEXT,
    confidence REAL,
    metadata_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY(trace_id) REFERENCES agentloop_traces(trace_id),
    FOREIGN KEY(producer_task_id) REFERENCES agentloop_tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_agentloop_artifacts_trace_status
ON agentloop_artifacts(trace_id, status);

CREATE INDEX IF NOT EXISTS idx_agentloop_artifacts_producer
ON agentloop_artifacts(producer_task_id);

-- ------------------------------------------------------------
-- 4. task_artifact_deps 表：任务对 artifact 的读写依赖
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentloop_task_artifact_deps (
    dep_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('READ', 'WRITE')),
    required INTEGER NOT NULL DEFAULT 1 CHECK(required IN (0, 1)),
    alias TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(task_id, artifact_id, mode, alias),
    FOREIGN KEY(task_id) REFERENCES agentloop_tasks(task_id),
    FOREIGN KEY(artifact_id) REFERENCES agentloop_artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_agentloop_deps_task
ON agentloop_task_artifact_deps(task_id);

CREATE INDEX IF NOT EXISTS idx_agentloop_deps_artifact
ON agentloop_task_artifact_deps(artifact_id);

-- ------------------------------------------------------------
-- 4.1 task_pending_deps 表：WAITING_ARTIFACTS 任务等待的、尚未创建的 artifact（无 FK 到 artifacts）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentloop_task_pending_deps (
    task_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (task_id, artifact_id),
    FOREIGN KEY (task_id) REFERENCES agentloop_tasks(task_id)
);
CREATE INDEX IF NOT EXISTS idx_agentloop_pending_artifact ON agentloop_task_pending_deps(artifact_id);

-- ------------------------------------------------------------
-- 5. events 表：内核运行过程中的状态变化与审计日志
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agentloop_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    task_id TEXT,
    parent_task_id TEXT,
    event_type TEXT NOT NULL,
    event_payload TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(trace_id) REFERENCES agentloop_traces(trace_id),
    FOREIGN KEY(task_id) REFERENCES agentloop_tasks(task_id),
    FOREIGN KEY(parent_task_id) REFERENCES agentloop_tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_agentloop_events_trace
ON agentloop_events(trace_id, created_at);

