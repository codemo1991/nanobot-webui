# Trace 监控页面设计方案

**日期**: 2026-03-30
**状态**: 已批准
**负责人**: linjifeng

## 1. 概述

在 Web UI 中新增 Trace 监控页面，展示 trace 聚合指标、最近请求列表、异常告警，并支持实时更新。

Trace 数据来源为 nanobot tracing 模块（`nanobot/tracing/`），通过新增后端 API 接口提供数据，前端通过轮询/SSE 获取。

## 2. 后端 API 设计

### 2.1 接口列表

| 接口 | 方法 | 说明 |
|------|------|------|
| `/traces/summary` | GET | 聚合指标（从 emitter buffer 实时计算） |
| `/traces/recent` | GET | 最近 spans 列表（分页，默认 50 条） |
| `/traces/:trace_id` | GET | 单个 trace 详情（完整 span 树） |
| `/traces/anomalies` | GET | 当前异常告警列表 |
| `/traces/stream` | SSE | 实时推送新 span 事件 |

### 2.2 数据结构

```typescript
// GET /traces/summary 返回
interface TraceSummary {
  total_spans: number;           // 时间窗口内总 span 数
  by_type: Record<string, SpanMetrics>;
  by_tool: Record<string, SpanMetrics>;
  by_template: Record<string, SpanMetrics>;
  recent_success_rate: number;  // 最近 5 分钟成功率
  recent_avg_duration_ms: number;
}

// GET /traces/recent 返回
interface RecentSpan {
  trace_id: string;
  span_id: string;
  name: string;
  span_type: string;
  status: 'ok' | 'error' | 'running';
  duration_ms: number | null;
  created_at: string;  // ISO 时间
}

// GET /traces/:trace_id 返回
interface TraceDetail {
  trace_id: string;
  spans: Span[];
  // Span 结构同 nanobot/tracing/spans.py:Span.to_dict()
}

// GET /traces/anomalies 返回
interface Anomaly {
  anomaly_type: string;
  span_type: string;
  group_key: string;
  actual_value: number;
  threshold: number;
  severity: number;
  suggestion: string;
}
```

### 2.3 实现要点

- 使用 `TraceEmitter` 实例的 buffer（内存）获取实时数据，避免频繁读文件
- 聚合指标窗口：最近 1000 个 span 或最近 1 小时（取较小值）
- SSE 流推送新 span 事件，格式：`data: {type: "span", ...}\n\n`

## 3. 前端页面设计

### 3.1 页面路由

- 路径: `/trace`
- 导航入口: Layout 侧边栏新增 "🔍 Trace" 菜单

### 3.2 页面布局

```
┌─────────────────────────────────────────────────────────────┐
│  🔍 Trace 监控                                                │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│  │ 总请求数 │ │ 成功率   │ │ 平均延迟 │ │ 活跃请求 │         │
│  │  1,234   │ │  98.5%   │ │  234ms   │ │    3     │         │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘         │
├─────────────────────────────────────────────────────────────┤
│  [概览] [最近请求] [异常告警]  ← Tabs 切换                    │
├─────────────────────────────────────────────────────────────┤
│  内容区域                                                    │
│  - 概览: 工具/模板/类型分布图表                               │
│  - 最近请求: 表格，trace_id + 操作名 + 状态 + 延迟 + 时间     │
│  - 异常告警: 列表，显示告警类型 + 阈值 + 建议                 │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 组件结构

```
pages/TracePage.tsx          # 主页面
├── components/
│   ├── TraceSummary.tsx     # 聚合指标卡片
│   ├── TraceTabs.tsx       # Tab 切换
│   ├── TraceOverview.tsx   # 概览（图表）
│   ├── TraceRecentList.tsx # 最近请求表格
│   └── TraceAnomalies.tsx  # 异常告警列表
└── api/trace.ts            # API 调用
```

### 3.4 技术选型

- UI 组件: Ant Design（与现有页面一致）
- 图表: Ant Design Charts 或 recharts（轻量）
- 实时更新: SSE 订阅 + React useEffect

## 4. 文件变更

### 后端 (nanobot/)

| 文件 | 变更 |
|------|------|
| `nanobot/web/api.py` | 新增 trace 相关路由注册 |
| `nanobot/web/routes/trace.py` | 新增 trace API 路由实现（新建） |

### 前端 (web-ui/)

| 文件 | 变更 |
|------|------|
| `web-ui/src/App.tsx` | 添加 `/trace` 路由 |
| `web-ui/src/components/Layout.tsx` | 添加 Trace 导航入口 |
| `web-ui/src/pages/TracePage.tsx` | 主页面（新建） |
| `web-ui/src/api.ts` | 添加 trace API 调用方法 |
| `web-ui/src/i18n/index.ts` | 添加国际化 key |

## 5. 实施步骤

1. 后端: 创建 trace 路由，暴露 `/traces/*` API
2. 后端: 实现 `/traces/summary`、`/traces/recent`、`/traces/:trace_id`、`/traces/anomalies`
3. 后端: 实现 SSE `/traces/stream`
4. 前端: 添加 API 方法
5. 前端: 创建 TracePage 组件
6. 前端: 添加路由和导航入口
7. 前端: 添加国际化

## 6. 依赖

- 后端: `nanobot/tracing/` 模块（已有）
- 前端: antd, react-router-dom（已有）
