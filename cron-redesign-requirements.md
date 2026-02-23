# Cron 模块改造技术方案

> 本文档为详细技术设计文档，包含完整的实现细节、代码示例和架构决策说明。
>
> 另见需求规格文档：需求摘要见上方原始需求文档。

---

## 1. APScheduler集成方案

### 1.1 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| APScheduler版本 | 3.10+ | 支持Python 3.11+，稳定性好 |
| jobstore | SQLAlchemyJobStore + SQLite | 复用现有SQLite，简化部署 |
| executor | AsyncIOExecutor | 支持异步Job执行，与现有async架构兼容 |
| 触发器 | DateTrigger, IntervalTrigger, CronTrigger | APScheduler内置支持 |

### 1.2 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                      CronService                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              APScheduler (AsyncIOScheduler)          │   │
│  │  ┌─────────────┐  ┌───────────────────────────┐   │   │
│  │  │ JobStore    │  │   Executors                │   │   │
│  │  │ (SQLite)    │  │  ┌─────────────────────┐  │   │   │
│  │  │             │  │  │ AsyncIOExecutor      │  │   │   │
│  │  │ - jobs表    │  │  │ (处理异步job)        │  │   │   │
│  │  └─────────────┘  │  └─────────────────────┘  │   │   │
│  └────────────────────┴───────────────────────────┘   │
│                          ↓                                │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Job执行回调                             │   │
│  │  - 调用agent执行任务                                │   │
│  │  - 记录执行结果到数据库                            │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 1.3 APScheduler初始化配置

```python
# nanobot/cron/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from pathlib import Path

def create_scheduler(db_path: Path) -> AsyncIOScheduler:
    """创建APScheduler调度器"""

    jobstores = {
        'default': SQLAlchemyJobStore(
            url=f'sqlite:///{db_path}',
            tableschema='apscheduler'
        )
    }

    executors = {
        'default': AsyncIOExecutor()
    }

    job_defaults = {
        'coalesce': True,           # 合并多次触发为一次
        'max_instances': 1,         # 同一任务最多实例数
        'misfire_grace_time': 60   # 允许延迟执行的秒数
    }

    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
        timezone='Asia/Shanghai'
    )

    # 添加事件监听器用于记录执行结果
    def job_executed_listener(event):
        """Job执行完成回调"""
        if event.exception:
            logger.error(f"Job {event.job_id} failed: {event.exception}")
        else:
            logger.info(f"Job {event.job_id} executed successfully")

    scheduler.add_listener(job_executed_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    return scheduler
```

### 1.4 额外数据存储

APScheduler的job支持通过`add_job`的`kwargs`参数存储额外数据：

```python
# 存储job时携带额外数据(payload)
scheduler.add_job(
    my_async_func,
    trigger=trigger,
    id=job_id,
    name=job_name,
    kwargs={
        'payload': {
            'message': 'Hello',
            'deliver': True,
            'channel': 'telegram',
            'to': '@user'
        }
    }
)
```

job执行时可通过`job.kwargs`获取payload。

---

## 2. SQLite表结构设计

### 2.1 设计决策

采用**复用APScheduler内置表** + **独立业务表**的方案：
- 使用APScheduler自带的`apscheduler_jobs`表存储调度信息（APScheduler自动管理）
- 新建`cron_jobs`表存储业务数据（payload、状态等）

### 2.2 复用APScheduler表结构

APScheduler的SQLAlchemyJobStore会创建以下表（默认表名：`apscheduler_jobs`）：

```sql
CREATE TABLE apscheduler_jobs (
    id VARCHAR(191) PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    trigger VARCHAR(512) NOT NULL,
    next_run_time FLOAT,
    job_state BLOB NOT NULL
);
```

此表由APScheduler自动管理，我们不直接操作。

### 2.3 业务数据扩展表

```sql
-- Cron任务业务数据表
CREATE TABLE IF NOT EXISTS cron_jobs (
    id VARCHAR(36) PRIMARY KEY,           -- UUID，与APScheduler job id对应

    name VARCHAR(256) NOT NULL,            -- 任务名称
    enabled INTEGER DEFAULT 1,             -- 是否启用 (0/1)

    -- 调度配置 (JSON存储)
    schedule_kind VARCHAR(16) NOT NULL,    -- 'at' | 'every' | 'cron'
    schedule_config TEXT NOT NULL,         -- JSON: {"atMs": xxx, "everyMs": xxx, "expr": "xxx", "tz": "xxx"}

    -- 执行负载 (JSON存储)
    payload_kind VARCHAR(16) DEFAULT 'agent_turn',
    payload_config TEXT NOT NULL,          -- JSON: {"message": "xxx", "deliver": true, "channel": "xxx", "to": "xxx"}

    -- 运行时状态
    next_run_at_ms INTEGER,                -- 下次执行时间(毫秒时间戳)
    last_run_at_ms INTEGER,               -- 上次执行时间
    last_status VARCHAR(16),               -- 'ok' | 'error' | 'skipped'
    last_error TEXT,                      -- 上次错误信息

    -- 元数据
    delete_after_run INTEGER DEFAULT 0,    -- 执行后删除(一次性任务)
    created_at TEXT NOT NULL,             -- ISO格式时间，如 "2024-02-24T10:00:00"
    updated_at TEXT NOT NULL              -- ISO格式时间
);

CREATE INDEX IF NOT EXISTS idx_cron_jobs_enabled ON cron_jobs(enabled);
CREATE INDEX IF NOT EXISTS idx_cron_jobs_next_run ON cron_jobs(next_run_at_ms);
```

### 2.4 数据模型映射

```python
# nanobot/cron/models.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional
import json

@dataclass
class CronSchedule:
    kind: Literal["at", "every", "cron"]
    at_ms: Optional[int] = None
    every_ms: Optional[int] = None
    expr: Optional[str] = None
    tz: Optional[str] = "Asia/Shanghai"

    def to_json(self) -> str:
        return json.dumps({
            "kind": self.kind,
            "atMs": self.at_ms,
            "everyMs": self.every_ms,
            "expr": self.expr,
            "tz": self.tz
        })

    @classmethod
    def from_json(cls, data: str) -> 'CronSchedule':
        d = json.loads(data)
        return cls(
            kind=d["kind"],
            at_ms=d.get("atMs"),
            every_ms=d.get("everyMs"),
            expr=d.get("expr"),
            tz=d.get("tz")
        )

@dataclass
class CronPayload:
    kind: Literal["agent_turn", "system_event"] = "agent_turn"
    message: str = ""
    deliver: bool = False
    channel: Optional[str] = None
    to: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps({
            "kind": self.kind,
            "message": self.message,
            "deliver": self.deliver,
            "channel": self.channel,
            "to": self.to
        })

    @classmethod
    def from_json(cls, data: str) -> 'CronPayload':
        d = json.loads(data)
        return cls(**d)

@dataclass
class CronJobRecord:
    """数据库记录模型"""
    id: str
    name: str
    enabled: bool
    schedule: CronSchedule
    payload: CronPayload
    next_run_at_ms: Optional[int] = None
    last_run_at_ms: Optional[int] = None
    last_status: Optional[Literal["ok", "error", "skipped"]] = None
    last_error: Optional[str] = None
    delete_after_run: bool = False
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: dict) -> 'CronJobRecord':
        return cls(
            id=row["id"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            schedule=CronSchedule.from_json(row["schedule_config"]),
            payload=CronPayload.from_json(row["payload_config"]),
            next_run_at_ms=row["next_run_at_ms"],
            last_run_at_ms=row["last_run_at_ms"],
            last_status=row["last_status"],
            last_error=row["last_error"],
            delete_after_run=bool(row["delete_after_run"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )
```

---

## 3. API路由设计

### 3.1 路由文件位置

在现有`nanobot/web/api.py`的`NanobotWebAPI`类中添加Cron相关方法。

### 3.2 接口定义

#### 3.2.1 列表任务

```
GET /api/v1/cron/jobs
```

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "abc123",
      "name": "每日早报",
      "enabled": true,
      "schedule": {
        "kind": "cron",
        "expr": "0 8 * * *",
        "tz": "Asia/Shanghai"
      },
      "payload": {
        "kind": "agent_turn",
        "message": "请生成今日早报",
        "deliver": true,
        "channel": "telegram",
        "to": "@user"
      },
      "state": {
        "nextRunAtMs": 1706054400000,
        "lastRunAtMs": 1705968000000,
        "lastStatus": "ok",
        "lastError": null
      },
      "deleteAfterRun": false,
      "createdAtMs": 1705900000000,
      "updatedAtMs": 1706050000000
    }
  ]
}
```

#### 3.2.2 创建任务

```
POST /api/v1/cron/jobs
```

**Request Body:**
```json
{
  "name": "每日早报",
  "schedule": {
    "kind": "cron",
    "expr": "0 8 * * *",
    "tz": "Asia/Shanghai"
  },
  "payload": {
    "kind": "agent_turn",
    "message": "请生成今日早报",
    "deliver": true,
    "channel": "telegram",
    "to": "@user"
  },
  "deleteAfterRun": false
}
```

**Response:**
```json
{
  "success": true,
  "data": { "id": "abc123", ... }
}
```

#### 3.2.3 更新任务

```
PATCH /api/v1/cron/jobs/{job_id}
```

**Request Body (部分更新):**
```json
{
  "name": "新名称",
  "enabled": false,
  "schedule": { "kind": "every", "everyMs": 3600000 }
}
```

#### 3.2.4 删除任务

```
DELETE /api/v1/cron/jobs/{job_id}
```

#### 3.2.5 手动执行任务

```
POST /api/v1/cron/jobs/{job_id}/run
```

#### 3.2.6 获取服务状态

```
GET /api/v1/cron/status
```

**Response:**
```json
{
  "success": true,
  "data": {
    "running": true,
    "jobs": 5,
    "nextWakeAtMs": 1706054400000
  }
}
```

### 3.3 API实现模式

在`NanobotWebAPI`类中添加方法：

```python
# nanobot/web/api.py - 在 NanobotWebAPI 类中添加

def list_cron_jobs(self) -> dict[str, Any]:
    """列出所有Cron任务"""
    jobs = self.cron_service.list_jobs(include_disabled=True)
    return _ok([self._serialize_cron_job(j) for j in jobs])

def create_cron_job(self, data: dict[str, Any]) -> dict[str, Any]:
    """创建Cron任务"""
    job = self.cron_service.add_job(
        name=data["name"],
        schedule=data["schedule"],
        payload=data.get("payload", {}),
        delete_after_run=data.get("deleteAfterRun", False)
    )
    return _ok(self._serialize_cron_job(job))

def update_cron_job(self, job_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """更新Cron任务"""
    job = self.cron_service.update_job(job_id, data)
    if job is None:
        return _err("NOT_FOUND", f"Job {job_id} not found")
    return _ok(self._serialize_cron_job(job))

def delete_cron_job(self, job_id: str) -> dict[str, Any]:
    """删除Cron任务"""
    removed = self.cron_service.remove_job(job_id)
    return _ok({"deleted": removed})

def run_cron_job(self, job_id: str) -> dict[str, Any]:
    """手动执行任务"""
    success = asyncio.get_event_loop().run_until_complete(
        self.cron_service.run_job(job_id, force=True)
    )
    return _ok({"executed": success})

def get_cron_status(self) -> dict[str, Any]:
    """获取Cron服务状态"""
    status = self.cron_service.status()
    return _ok(status)

def _serialize_cron_job(self, job) -> dict[str, Any]:
    """序列化CronJob为API响应格式"""
    return {
        "id": job.id,
        "name": job.name,
        "enabled": job.enabled,
        "schedule": {
            "kind": job.schedule.kind,
            "atMs": job.schedule.at_ms,
            "everyMs": job.schedule.every_ms,
            "expr": job.schedule.expr,
            "tz": job.schedule.tz,
        },
        "payload": {
            "kind": job.payload.kind,
            "message": job.payload.message,
            "deliver": job.payload.deliver,
            "channel": job.payload.channel,
            "to": job.payload.to,
        },
        "state": {
            "nextRunAtMs": job.state.next_run_at_ms,
            "lastRunAtMs": job.state.last_run_at_ms,
            "lastStatus": job.state.last_status,
            "lastError": job.state.last_error,
        },
        "deleteAfterRun": job.delete_after_run,
        "createdAtMs": job.created_at_ms,
        "updatedAtMs": job.updated_at_ms,
    }
```

路由匹配（在`_route`方法中）：

```python
# nanobot/web/api.py - 在 NanobotWebAPI._route() 中添加
# 处理 /api/v1/cron/ 开头的请求

if path.startswith("/api/v1/cron/"):
    # 去掉前缀获取剩余路径
    rest = path[len("/api/v1/cron/"):]

    if method == "GET" and rest == "jobs":
        return json.dumps(self.list_cron_jobs())
    if method == "POST" and rest == "jobs":
        return json.dumps(self.create_cron_job(body))
    if method == "GET" and rest == "status":
        return json.dumps(self.get_cron_status())

    # /api/v1/cron/jobs/{id}...
    if rest.startswith("jobs/"):
        job_id = rest[len("jobs/"):].split("/")[0]
        action = rest[len("jobs/" + job_id):].strip("/")

        if method == "GET" and action == "":
            return json.dumps(self.get_cron_job(job_id))
        if method == "PATCH" and action == "":
            return json.dumps(self.update_cron_job(job_id, body))
        if method == "DELETE" and action == "":
            return json.dumps(self.delete_cron_job(job_id))
        if method == "POST" and action == "run":
            return json.dumps(self.run_cron_job(job_id))
```

---

## 4. 前端架构设计

### 4.1 目录结构

```
web-ui/src/
├── api.ts                    # 扩展API调用
├── pages/
│   └── CronPage.tsx         # Cron任务管理页面
├── components/
│   └── cron/
│       ├── CronJobList.tsx  # 任务列表组件
│       ├── CronJobForm.tsx  # 任务创建/编辑表单
│       └── CronJobCard.tsx  # 任务卡片展示
├── store/
│   └── cronStore.ts         # Cron任务状态管理
├── types/
│   └── cron.ts              # TypeScript类型定义
└── i18n/
    └── locales/
        ├── en.json          # 英文翻译
        └── zh-CN.json      # 中文翻译
```

### 4.2 类型定义

```typescript
// web-ui/src/types/cron.ts

export type ScheduleKind = 'at' | 'every' | 'cron';

export interface CronSchedule {
  kind: ScheduleKind;
  atMs?: number;
  everyMs?: number;
  expr?: string;
  tz?: string;
}

export type PayloadKind = 'agent_turn' | 'system_event';

export interface CronPayload {
  kind: PayloadKind;
  message: string;
  deliver: boolean;
  channel?: string;
  to?: string;
}

export interface CronJobState {
  nextRunAtMs: number | null;
  lastRunAtMs: number | null;
  lastStatus: 'ok' | 'error' | 'skipped' | null;
  lastError: string | null;
}

export interface CronJob {
  id: string;
  name: string;
  enabled: boolean;
  schedule: CronSchedule;
  payload: CronPayload;
  state: CronJobState;
  deleteAfterRun: boolean;
  createdAtMs: number;
  updatedAtMs: number;
}

export interface CreateCronJobRequest {
  name: string;
  schedule: CronSchedule;
  payload: CronPayload;
  deleteAfterRun?: boolean;
}
```

### 4.3 API调用封装

```typescript
// web-ui/src/api.ts 扩展

export const api = {
  // ... 现有方法

  // Cron APIs
  getCronJobs: () => request<CronJob[]>('/cron/jobs'),
  getCronJob: (jobId: string) => request<CronJob>(`/cron/jobs/${jobId}`),
  createCronJob: (data: CreateCronJobRequest) =>
    request<CronJob>('/cron/jobs', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateCronJob: (jobId: string, data: Partial<CreateCronJobRequest>) =>
    request<CronJob>(`/cron/jobs/${jobId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteCronJob: (jobId: string) =>
    request<{ deleted: boolean }>(`/cron/jobs/${jobId}`, {
      method: 'DELETE',
    }),
  runCronJob: (jobId: string) =>
    request<{ executed: boolean }>(`/cron/jobs/${jobId}/run`, {
      method: 'POST',
    }),
  getCronStatus: () =>
    request<{ running: boolean; jobs: number; nextWakeAtMs: number | null }>('/cron/status'),
};
```

### 4.4 状态管理

```typescript
// web-ui/src/store/cronStore.ts

import { create } from 'zustand';
import type { CronJob, CreateCronJobRequest } from '../types/cron';
import { api } from '../api';

interface CronStore {
  jobs: CronJob[];
  loading: boolean;
  error: string | null;

  // Actions
  fetchJobs: () => Promise<void>;
  createJob: (data: CreateCronJobRequest) => Promise<CronJob>;
  updateJob: (jobId: string, data: Partial<CreateCronJobRequest>) => Promise<void>;
  deleteJob: (jobId: string) => Promise<void>;
  runJob: (jobId: string) => Promise<void>;
  toggleJob: (jobId: string, enabled: boolean) => Promise<void>;
}

export const useCronStore = create<CronStore>((set, get) => ({
  jobs: [],
  loading: false,
  error: null,

  fetchJobs: async () => {
    set({ loading: true, error: null });
    try {
      const jobs = await api.getCronJobs();
      set({ jobs, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  createJob: async (data) => {
    const job = await api.createCronJob(data);
    set((state) => ({ jobs: [...state.jobs, job] }));
    return job;
  },

  updateJob: async (jobId, data) => {
    const updated = await api.updateCronJob(jobId, data);
    set((state) => ({
      jobs: state.jobs.map((j) => (j.id === jobId ? updated : j)),
    }));
  },

  deleteJob: async (jobId) => {
    await api.deleteCronJob(jobId);
    set((state) => ({
      jobs: state.jobs.filter((j) => j.id !== jobId),
    }));
  },

  runJob: async (jobId) => {
    await api.runCronJob(jobId);
    await get().fetchJobs();
  },

  toggleJob: async (jobId, enabled) => {
    await get().updateJob(jobId, { enabled } as any);
  },
}));
```

### 4.5 CronPage主页面

```tsx
// web-ui/src/pages/CronPage.tsx

import { useEffect, useState } from 'react';
import { Typography, Button, Space, Card, Tag, List, message, Modal, Empty, Switch } from 'antd';
import { PlusOutlined, PlayCircleOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useCronStore } from '../store/cronStore';
import CronJobForm from '../components/cron/CronJobForm';
import type { CronJob } from '../types/cron';
import './CronPage.css';

const { Title, Text } = Typography;

export default function CronPage() {
  const { t } = useTranslation();
  const { jobs, loading, fetchJobs, deleteJob, runJob, toggleJob } = useCronStore();
  const [formModalVisible, setFormModalVisible] = useState(false);
  const [editingJob, setEditingJob] = useState<CronJob | null>(null);

  useEffect(() => {
    fetchJobs();
  }, []);

  const handleAdd = () => {
    setEditingJob(null);
    setFormModalVisible(true);
  };

  const handleEdit = (job: CronJob) => {
    setEditingJob(job);
    setFormModalVisible(true);
  };

  const handleDelete = (jobId: string) => {
    Modal.confirm({
      title: t('cron.confirmDelete'),
      onOk: async () => {
        await deleteJob(jobId);
        message.success(t('cron.deleted'));
      },
    });
  };

  const handleRun = async (jobId: string) => {
    try {
      await runJob(jobId);
      message.success(t('cron.executed'));
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const handleToggle = async (job: CronJob) => {
    await toggleJob(job.id, !job.enabled);
  };

  const getScheduleLabel = (job: CronJob) => {
    const { schedule } = job;
    switch (schedule.kind) {
      case 'at':
        return schedule.atMs ? new Date(schedule.atMs).toLocaleString() : '-';
      case 'every':
        return `${(schedule.everyMs || 0) / 1000}s`;
      case 'cron':
        return schedule.expr || '-';
    }
  };

  const getStatusTag = (job: CronJob) => {
    const { state } = job;
    if (!job.enabled) return <Tag>Disabled</Tag>;
    if (state.lastStatus === 'ok') return <Tag color="success">OK</Tag>;
    if (state.lastStatus === 'error') return <Tag color="error">Error</Tag>;
    return <Tag>Pending</Tag>;
  };

  return (
    <div className="cron-page">
      <div className="page-header">
        <Title level={2}>⏰ {t('cron.title')}</Title>
        <Text type="secondary">{t('cron.subtitle')}</Text>
      </div>

      <div className="cron-actions">
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
          {t('cron.addJob')}
        </Button>
      </div>

      {jobs.length === 0 ? (
        <Empty description={t('cron.empty')} />
      ) : (
        <List
          grid={{ gutter: 16, xs: 1, sm: 1, md: 2, lg: 2, xl: 3 }}
          dataSource={jobs}
          loading={loading}
          renderItem={(job) => (
            <List.Item>
              <Card
                title={job.name}
                extra={
                  <Space>
                    {getStatusTag(job)}
                    <Switch
                      checked={job.enabled}
                      onChange={() => handleToggle(job)}
                      size="small"
                    />
                  </Space>
                }
                actions={[
                  <Button
                    key="run"
                    type="text"
                    icon={<PlayCircleOutlined />}
                    onClick={() => handleRun(job.id)}
                    disabled={!job.enabled}
                  >
                    {t('cron.run')}
                  </Button>,
                  <Button
                    key="edit"
                    type="text"
                    icon={<EditOutlined />}
                    onClick={() => handleEdit(job)}
                  />,
                  <Button
                    key="delete"
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => handleDelete(job.id)}
                  />,
                ]}
              >
                <Space direction="vertical">
                  <Text>
                    <Tag>{job.schedule.kind}</Tag>
                    {getScheduleLabel(job)}
                  </Text>
                  {job.payload.message && (
                    <Text type="secondary" ellipsis>
                      {job.payload.message}
                    </Text>
                  )}
                  {job.state.lastRunAtMs && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t('cron.lastRun')}: {new Date(job.state.lastRunAtMs).toLocaleString()}
                    </Text>
                  )}
                </Space>
              </Card>
            </List.Item>
          )}
        />
      )}

      <Modal
        title={editingJob ? t('cron.editJob') : t('cron.addJob')}
        open={formModalVisible}
        onCancel={() => setFormModalVisible(false)}
        footer={null}
        width={600}
      >
        <CronJobForm
          job={editingJob}
          onSuccess={() => {
            setFormModalVisible(false);
            fetchJobs();
          }}
        />
      </Modal>
    </div>
  );
}
```

### 4.6 CronJobForm组件

```tsx
// web-ui/src/components/cron/CronJobForm.tsx

import { useState } from 'react';
import { Form, Input, Select, Switch, InputNumber, Button, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import type { CronJob, CronSchedule, CronPayload, ScheduleKind } from '../../types/cron';
import { useCronStore } from '../../store/cronStore';

const { TextArea } = Input;

interface Props {
  job?: CronJob | null;
  onSuccess: () => void;
}

export default function CronJobForm({ job, onSuccess }: Props) {
  const { t } = useTranslation();
  const { createJob, updateJob } = useCronStore();
  const [form] = Form.useForm();
  const [scheduleKind, setScheduleKind] = useState<ScheduleKind>(job?.schedule.kind || 'cron');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (values: any) => {
    setLoading(true);
    try {
      const schedule: CronSchedule = {
        kind: scheduleKind,
        ...(scheduleKind === 'at' && { atMs: values.atMs }),
        ...(scheduleKind === 'every' && { everyMs: values.everyMs }),
        ...(scheduleKind === 'cron' && { expr: values.expr, tz: values.tz }),
      };

      const payload: CronPayload = {
        kind: values.payloadKind || 'agent_turn',
        message: values.message || '',
        deliver: values.deliver || false,
        channel: values.channel,
        to: values.to,
      };

      if (job) {
        await updateJob(job.id, { name: values.name, schedule, payload });
      } else {
        await createJob({ name: values.name, schedule, payload });
      }
      onSuccess();
    } finally {
      setLoading(false);
    }
  };

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{
        name: job?.name,
        scheduleKind: job?.schedule.kind,
        expr: job?.schedule.expr,
        everyMs: job?.schedule.everyMs,
        atMs: job?.schedule.atMs,
        tz: job?.schedule.tz || 'Asia/Shanghai',
        message: job?.payload.message,
        deliver: job?.payload.deliver,
        channel: job?.payload.channel,
        to: job?.payload.to,
      }}
      onFinish={handleSubmit}
    >
      <Form.Item name="name" label={t('cron.name')} rules={[{ required: true }]}>
        <Input placeholder={t('cron.namePlaceholder')} />
      </Form.Item>

      <Form.Item label={t('cron.scheduleType')}>
        <Select
          value={scheduleKind}
          onChange={setScheduleKind}
          options={[
            { value: 'cron', label: 'Cron ' + t('cron.expression') },
            { value: 'every', label: t('cron.interval') },
            { value: 'at', label: t('cron.oneTime') },
          ]}
        />
      </Form.Item>

      {scheduleKind === 'cron' && (
        <>
          <Form.Item name="expr" label={t('cron.cronExpr')} rules={[{ required: true }]}>
            <Input placeholder="0 8 * * *" />
          </Form.Item>
          <Form.Item name="tz" label={t('cron.timezone')}>
            <Select
              options={[
                { value: 'Asia/Shanghai', label: 'Asia/Shanghai' },
                { value: 'UTC', label: 'UTC' },
                { value: 'America/New_York', label: 'America/New_York' },
              ]}
            />
          </Form.Item>
        </>
      )}

      {scheduleKind === 'every' && (
        <Form.Item name="everyMs" label={t('cron.intervalMs')}>
          <InputNumber
            min={1000}
            step={1000}
            addonAfter="ms"
            style={{ width: '100%' }}
          />
        </Form.Item>
      )}

      {scheduleKind === 'at' && (
        <Form.Item name="atMs" label={t('cron.runAt')}>
          <Input type="datetime-local" />
        </Form.Item>
      )}

      <Form.Item name="message" label={t('cron.message')}>
        <TextArea rows={3} placeholder={t('cron.messagePlaceholder')} />
      </Form.Item>

      <Form.Item name="deliver" valuePropName="checked" label={t('cron.deliver')}>
        <Switch />
      </Form.Item>

      {form.getFieldValue('deliver') && (
        <Space>
          <Form.Item name="channel" label={t('cron.channel')}>
            <Select
              placeholder={t('cron.channelPlaceholder')}
              options={[
                { value: 'telegram', label: 'Telegram' },
                { value: 'whatsapp', label: 'WhatsApp' },
                { value: 'feishu', label: 'Feishu' },
              ]}
            />
          </Form.Item>
          <Form.Item name="to" label={t('cron.to')}>
            <Input placeholder="@user" />
          </Form.Item>
        </Space>
      )}

      <Form.Item>
        <Button type="primary" htmlType="submit" loading={loading} block>
          {job ? t('cron.update') : t('cron.create')}
        </Button>
      </Form.Item>
    </Form>
  );
}
```

---

## 5. 依赖更新

### 5.1 pyproject.toml

```toml
[project.optional-dependencies]
cron = [
    "apscheduler>=3.10.0",
]
```

---

## 6. 实施步骤

### 第一阶段：后端核心
1. [ ] 创建`nanobot/cron/models.py` - 数据模型
2. [ ] 创建`nanobot/storage/cron_repository.py` - 数据访问层
3. [ ] 修改`nanobot/cron/service.py` - 迁移到APScheduler + SQLite
4. [ ] 修改`nanobot/web/api.py` - 添加Cron API路由

### 第二阶段：前端实现
5. [ ] 创建`web-ui/src/types/cron.ts` - 类型定义
6. [ ] 扩展`web-ui/src/api.ts` - API调用
7. [ ] 创建`web-ui/src/store/cronStore.ts` - 状态管理
8. [ ] 创建`web-ui/src/pages/CronPage.tsx` - 页面
9. [ ] 创建`web-ui/src/components/cron/CronJobForm.tsx` - 表单组件

### 第三阶段：测试与集成
10. [ ] 验证CRUD功能
11. [ ] 验证定时任务执行
12. [ ] 验证状态更新
13. [ ] 旧数据迁移脚本

---

## 7. 兼容性考虑

- **旧JSON数据迁移**: 启动时检测旧JSON文件，自动导入到SQLite
- **保留现有回调机制**: 确保`on_job`回调机制不变
- **API响应格式**: 保持与现有API一致 (`{success, data, error}`)

---

## 8. 风险与约束

1. **SQLite并发**: APScheduler使用SQLite作为jobstore时，写操作会加锁，但当前系统为单实例部署，无问题
2. **Job ID唯一性**: 使用UUID作为job ID，确保不冲突
3. **时区处理**: Cron表达式支持时区，需存储`trigger_tz`
4. **AsyncIOExecutor**: 确保回调函数为async def，否则需包装
