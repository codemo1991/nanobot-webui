# Trace 监控页面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Web UI 中新增 Trace 监控页面，展示聚合指标、最近请求列表、异常告警，支持实时更新。

**Architecture:** 后端在 `api.py` 中新增 trace 相关路由，调用 `TraceEmitter` 的 buffer 获取实时数据。前端新增 TracePage 组件，使用 Ant Design 展示数据，通过轮询/SSE 获取更新。

**Tech Stack:** React + TypeScript + Ant Design (前端), Python (后端)

---

## 文件变更概览

| 文件 | 变更 |
|------|------|
| `nanobot/web/api.py` | 新增 trace API 路由 |
| `nanobot/tracing/emitter.py` | 新增 `get_recent_spans()` 和 `get_summary()` 方法 |
| `web-ui/src/types.ts` | 新增 Trace 类型定义 |
| `web-ui/src/api.ts` | 新增 trace API 调用方法 |
| `web-ui/src/pages/TracePage.tsx` | 主页面（新建） |
| `web-ui/src/App.tsx` | 添加 `/trace` 路由 |
| `web-ui/src/components/Layout.tsx` | 添加 Trace 导航入口 |
| `web-ui/src/i18n/locales/zh-CN.json` | 添加中文国际化 |
| `web-ui/src/i18n/locales/en.json` | 添加英文国际化 |

---

## Task 1: 后端 - 扩展 TraceEmitter

**Files:**
- Modify: `nanobot/tracing/emitter.py`

- [ ] **Step 1: 添加 `get_recent_spans()` 方法**

在 `TraceEmitter` 类中添加方法，获取最近的 spans：

```python
def get_recent_spans(self, limit: int = 100) -> list[dict[str, Any]]:
    """返回最近的 spans（从 buffer）。"""
    with self._lock:
        return list(self._buffer)[-limit:]
```

- [ ] **Step 2: 添加 `get_summary()` 方法**

```python
def get_summary(self) -> dict[str, Any]:
    """从 buffer 计算聚合指标摘要。"""
    with self._lock:
        spans = list(self._buffer)

    if not spans:
        return {
            "total_spans": 0,
            "by_type": {},
            "by_tool": {},
            "recent_success_rate": 1.0,
            "recent_avg_duration_ms": 0.0,
        }

    from nanobot.tracing.analysis import aggregate_spans
    metrics = aggregate_spans(spans)

    # 计算最近 100 个 span 的成功率
    recent = spans[-100:] if len(spans) > 100 else spans
    recent_ok = sum(1 for s in recent if s.get("status") == "ok")
    recent_success_rate = recent_ok / len(recent) if recent else 1.0

    # 计算平均延迟
    durations = [s.get("duration_ms") for s in recent if s.get("duration_ms")]
    avg_duration = sum(durations) / len(durations) if durations else 0.0

    return {
        "total_spans": len(spans),
        "by_type": {k: v.to_dict() for k, v in metrics.by_type.items()},
        "by_tool": {k: v.to_dict() for k, v in metrics.by_tool.items()},
        "recent_success_rate": recent_success_rate,
        "recent_avg_duration_ms": avg_duration,
    }
```

- [ ] **Step 3: 添加 `add_observer()` 方法用于 SSE 推送**

```python
def add_observer(self, callback: Callable[[dict[str, Any]], None]) -> None:
    """添加 span 观察者，新 span 产生时调用。"""
    with self._lock:
        if not hasattr(self, "_observers"):
            self._observers = []
        self._observers.append(callback)

def remove_observer(self, callback: Callable[[dict[str, Any]], None]) -> None:
    """移除观察者。"""
    with self._lock:
        if hasattr(self, "_observers") and callback in self._observers:
            self._observers.remove(callback)

def _notify_observers(self, span: dict[str, Any]) -> None:
    """通知所有观察者。"""
    if hasattr(self, "_observers"):
        for cb in self._observers:
            try:
                cb(span)
            except Exception:
                pass
```

- [ ] **Step 4: 修改 `_flush_unlocked()` 通知观察者**

在 `_flush_unlocked()` 方法中，emit span 后调用 `_notify_observers()`：

```python
# 在 _flush_unlocked 方法中，emit 后添加：
for record in batch:
    self._notify_observers(record)
```

- [ ] **Step 5: 提交**

```bash
git add nanobot/tracing/emitter.py
git commit -m "feat(tracing): add get_recent_spans, get_summary, and observer support"
```

---

## Task 2: 后端 - 新增 Trace API 路由

**Files:**
- Modify: `nanobot/web/api.py` (在 `do_GET` 方法中添加路由)

- [ ] **Step 1: 添加 `/api/v1/traces/summary` 路由**

在 `do_GET` 方法中添加（位置：在 `/api/v1/health` 之后）：

```python
if path == "/api/v1/traces/summary":
    from nanobot.tracing import get_emitter
    emitter = get_emitter()
    if emitter is None:
        self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, _err("TRACING_NOT_INITIALIZED", "Tracing 未初始化"))
        return
    summary = emitter.get_summary()
    self._write_json(HTTPStatus.OK, _ok(summary))
    return
```

- [ ] **Step 2: 添加 `/api/v1/traces/recent` 路由**

```python
if path == "/api/v1/traces/recent":
    limit = int(query.get("limit", ["50"])[0])
    limit = max(1, min(limit, 200))
    from nanobot.tracing import get_emitter
    emitter = get_emitter()
    if emitter is None:
        self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, _err("TRACING_NOT_INITIALIZED", "Tracing 未初始化"))
        return
    spans = emitter.get_recent_spans(limit)
    # 格式化返回
    result = [{
        "trace_id": s.get("trace_id", ""),
        "span_id": s.get("span_id", ""),
        "name": s.get("name", ""),
        "span_type": s.get("span_type", ""),
        "status": s.get("status", "ok"),
        "duration_ms": s.get("duration_ms"),
        "created_at": s.get("start_ms"),
    } for s in spans]
    self._write_json(HTTPStatus.OK, _ok(result))
    return
```

- [ ] **Step 3: 添加 `/api/v1/traces/:trace_id` 路由**

```python
# GET /api/v1/traces/{trace_id}
if len(parts) == 4 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "traces" and parts[3]:
    trace_id = parts[3]
    from nanobot.tracing import get_emitter
    emitter = get_emitter()
    if emitter is None:
        self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, _err("TRACING_NOT_INITIALIZED", "Tracing 未初始化"))
        return
    spans = emitter.query_by_trace_id(trace_id, limit=200)
    self._write_json(HTTPStatus.OK, _ok({
        "trace_id": trace_id,
        "spans": spans,
    }))
    return
```

- [ ] **Step 4: 添加 `/api/v1/traces/anomalies` 路由**

```python
if path == "/api/v1/traces/anomalies":
    from nanobot.tracing import get_emitter
    emitter = get_emitter()
    if emitter is None:
        self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, _err("TRACING_NOT_INITIALIZED", "Tracing 未初始化"))
        return
    # 从 emitter buffer 获取 spans 进行异常检测
    spans = emitter.get_recent_spans(limit=1000)
    from nanobot.tracing.analysis import aggregate_spans
    metrics = aggregate_spans(spans)
    from nanobot.tracing.anomaly import AnomalyDetector
    detector = AnomalyDetector()
    anomalies = detector.detect(metrics)
    result = [a.to_dict() for a in anomalies]
    self._write_json(HTTPStatus.OK, _ok(result))
    return
```

- [ ] **Step 5: 添加 `/api/v1/traces/stream` SSE 路由**

```python
if path == "/api/v1/traces/stream":
    from nanobot.tracing import get_emitter
    emitter = get_emitter()
    if emitter is None:
        self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, _err("TRACING_NOT_INITIALIZED", "Tracing 未初始化"))
        return

    # SSE headers
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
    self.send_header("Cache-Control", "no-cache")
    self.send_header("Connection", "keep-alive")
    self.send_header("Access-Control-Allow-Origin", "*")
    self.end_headers()

    # 创建队列和观察者
    evt_queue = queue.Queue()

    def observer(span):
        evt_queue.put({"type": "span", "data": span})

    emitter.add_observer(observer)

    try:
        heartbeat_interval = 30
        last_heartbeat = time.time()
        while True:
            try:
                evt = evt_queue.get(timeout=1.0)
                payload = _sse_json_dumps(evt)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except queue.Empty:
                now = time.time()
                if now - last_heartbeat >= heartbeat_interval:
                    try:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        last_heartbeat = now
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break
    except Exception as e:
        logger.warning("Trace stream error: %s", e)
    finally:
        emitter.remove_observer(observer)
    return
```

- [ ] **Step 6: 提交**

```bash
git add nanobot/web/api.py
git commit -m "feat(api): add trace monitoring endpoints"
```

---

## Task 3: 前端 - 添加类型定义

**Files:**
- Modify: `web-ui/src/types.ts`

- [ ] **Step 1: 添加 Trace 类型定义**

在文件末尾（`AgentTemplate` 类型之后）添加：

```typescript
// ==================== Trace Types ====================

export interface SpanMetrics {
  count: number
  ok_count: number
  error_count: number
  success_rate: number
  error_rate: number
  avg_duration_ms: number | null
  p50_duration_ms: number | null
  p95_duration_ms: number | null
  p99_duration_ms: number | null
}

export interface TraceSummary {
  total_spans: number
  by_type: Record<string, SpanMetrics>
  by_tool: Record<string, SpanMetrics>
  recent_success_rate: number
  recent_avg_duration_ms: number
}

export interface RecentSpan {
  trace_id: string
  span_id: string
  name: string
  span_type: string
  status: 'ok' | 'error' | 'running'
  duration_ms: number | null
  created_at: number
}

export interface TraceDetail {
  trace_id: string
  spans: any[]
}

export interface Anomaly {
  anomaly_type: string
  span_type: string
  group_key: string
  actual_value: number
  threshold: number
  severity: number
  suggestion: string
  span_count: number
}
```

- [ ] **Step 2: 提交**

```bash
git add web-ui/src/types.ts
git commit -m "feat(types): add Trace-related type definitions"
```

---

## Task 4: 前端 - 添加 API 调用

**Files:**
- Modify: `web-ui/src/api.ts`

- [ ] **Step 1: 添加 trace API 调用方法**

在 `api` 对象中添加：

```typescript
// ==================== Trace APIs ====================

getTraceSummary: () =>
  request<TraceSummary>('/traces/summary'),

getTraceRecent: (limit = 50) =>
  request<RecentSpan[]>(`/traces/recent?limit=${limit}`),

getTraceDetail: (traceId: string) =>
  request<TraceDetail>(`/traces/${traceId}`),

getTraceAnomalies: () =>
  request<Anomaly[]>('/traces/anomalies'),

/** 订阅 Trace SSE 流 */
subscribeTraceStream: (
  onEvent: (evt: { type: string; data: any }) => void,
  signal?: AbortSignal
): Promise<void> => {
  const res = await fetch(`${API_BASE}/traces/stream`, {
    headers: { Accept: 'text/event-stream' },
    signal,
  })
  if (!res.ok) throw new Error('Trace stream failed')
  const reader = res.body?.getReader()
  if (!reader) throw new Error('Stream not supported')
  const dec = new TextDecoder()
  let buf = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      const lines = buf.split('\n\n')
      buf = lines.pop() ?? ''
      for (const line of lines) {
        const dataLine = line.split('\n').find(l => l.startsWith('data:'))
        if (!dataLine) continue
        const dataStr = dataLine.slice(5).trim()
        if (!dataStr) continue
        try {
          const evt = JSON.parse(dataStr)
          onEvent(evt)
        } catch { /* skip */ }
      }
    }
  } finally {
    reader.releaseLock()
  }
},
```

注意：`request` 是 `async` 的，但 `subscribeTraceStream` 返回 `Promise<void>`，需要在调用前使用 `api.subscribeTraceStream(...)`。

- [ ] **Step 2: 提交**

```bash
git add web-ui/src/api.ts
git commit -m "feat(api): add trace monitoring API methods"
```

---

## Task 5: 前端 - 创建 TracePage 组件

**Files:**
- Create: `web-ui/src/pages/TracePage.tsx`

- [ ] **Step 1: 创建 TracePage 组件**

```tsx
import { useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Card, Row, Col, Statistic, Table, Tag, Tabs, Button, Space, Spin, Alert, Empty } from 'antd'
import { ReloadOutlined, ClockCircleOutlined, CheckCircleOutlined, ExclamationCircleOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { TraceSummary, RecentSpan, Anomaly } from '../types'

function TracePage() {
  const { t } = useTranslation()
  const [summary, setSummary] = useState<TraceSummary | null>(null)
  const [recentSpans, setRecentSpans] = useState<RecentSpan[]>([])
  const [anomalies, setAnomalies] = useState<Anomaly[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('overview')
  const sseRef = useRef<AbortController | null>(null)

  const loadData = async () => {
    try {
      setLoading(true)
      const [summaryData, recentData, anomalyData] = await Promise.all([
        api.getTraceSummary(),
        api.getTraceRecent(100),
        api.getTraceAnomalies(),
      ])
      setSummary(summaryData)
      setRecentSpans(recentData)
      setAnomalies(anomalyData)
    } catch (err) {
      console.error('Failed to load trace data:', err)
    } finally {
      setLoading(false)
    }
  }

  // SSE 订阅
  const subscribeStream = () => {
    const controller = new AbortController()
    sseRef.current = controller

    api.subscribeTraceStream(
      (evt) => {
        if (evt.type === 'span') {
          // 更新 recentSpans
          setRecentSpans(prev => {
            const newSpan = {
              trace_id: evt.data.trace_id,
              span_id: evt.data.span_id,
              name: evt.data.name,
              span_type: evt.data.span_type,
              status: evt.data.status,
              duration_ms: evt.data.duration_ms,
              created_at: evt.data.start_ms,
            }
            return [...prev, newSpan].slice(-100)
          })
        }
      },
      controller.signal
    ).catch(console.error)
  }

  useEffect(() => {
    loadData()
    subscribeStream()
    return () => {
      sseRef.current?.abort()
    }
  }, [])

  const statusIcon = (status: string) => {
    switch (status) {
      case 'ok': return <CheckCircleOutlined style={{ color: '#52c41a' }} />
      case 'error': return <ExclamationCircleOutlined style={{ color: '#ff4d4f' }} />
      default: return <ClockCircleOutlined style={{ color: '#1890ff' }} />
    }
  }

  const recentColumns = [
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (s: string) => statusIcon(s) },
    { title: '操作', dataIndex: 'name', key: 'name', ellipsis: true },
    { title: '类型', dataIndex: 'span_type', key: 'span_type', width: 100,
      render: (t: string) => <Tag>{t || '-'}</Tag> },
    { title: '延迟', dataIndex: 'duration_ms', key: 'duration_ms', width: 100,
      render: (d: number) => d != null ? `${d}ms` : '-' },
    { title: 'Trace ID', dataIndex: 'trace_id', key: 'trace_id', width: 120, ellipsis: true },
  ]

  const anomalyColumns = [
    { title: '类型', dataIndex: 'anomaly_type', key: 'anomaly_type', width: 120,
      render: (t: string) => <Tag color="error">{t}</Tag> },
    { title: '范围', key: 'scope', render: (_: any, r: Anomaly) => `${r.span_type}/${r.group_key}` },
    { title: '实际值', dataIndex: 'actual_value', key: 'actual_value', width: 120,
      render: (v: number, r: Anomaly) => r.anomaly_type.includes('rate') ? `${(v*100).toFixed(1)}%` : `${v.toFixed(0)}ms` },
    { title: '阈值', dataIndex: 'threshold', key: 'threshold', width: 120,
      render: (v: number, r: Anomaly) => r.anomaly_type.includes('rate') ? `${(v*100).toFixed(1)}%` : `${v.toFixed(0)}ms` },
    { title: '严重度', dataIndex: 'severity', key: 'severity', width: 80 },
    { title: '建议', dataIndex: 'suggestion', key: 'suggestion', ellipsis: true },
  ]

  const tabItems = [
    { key: 'overview', label: '概览', children: (
      <Row gutter={16}>
        {summary?.by_tool && Object.entries(summary.by_tool).map(([name, metrics]) => (
          <Col span={8} key={name}>
            <Card size="small" title={name}>
              <Statistic
                title="成功率"
                value={metrics.success_rate * 100}
                suffix="%"
                precision={1}
                valueStyle={{ color: metrics.success_rate > 0.9 ? '#52c41a' : '#ff4d4f' }}
              />
              <div>平均延迟: {metrics.avg_duration_ms?.toFixed(0) || 0}ms</div>
            </Card>
          </Col>
        ))}
      </Row>
    )},
    { key: 'recent', label: '最近请求', children: (
      <Table
        dataSource={recentSpans}
        columns={recentColumns}
        rowKey="span_id"
        size="small"
        pagination={{ pageSize: 10 }}
      />
    )},
    { key: 'anomalies', label: '异常告警', children: (
      anomalies.length === 0 ? <Empty description="暂无异常" /> : (
        <Table
          dataSource={anomalies}
          columns={anomalyColumns}
          rowKey={(r) => `${r.span_type}-${r.group_key}`}
          size="small"
        />
      )
    )},
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1>🔍 Trace 监控</h1>
        <Button icon={<ReloadOutlined />} onClick={loadData}>刷新</Button>
      </div>

      <Spin spinning={loading}>
        <Row gutter={16} style={{ marginBottom: 24 }}>
          <Col span={6}>
            <Card><Statistic title="总请求数" value={summary?.total_spans || 0} /></Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="成功率"
                value={(summary?.recent_success_rate || 0) * 100}
                suffix="%"
                precision={1}
                valueStyle={{ color: (summary?.recent_success_rate || 0) > 0.9 ? '#52c41a' : '#ff4d4f' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card><Statistic title="平均延迟" value={summary?.recent_avg_duration_ms?.toFixed(0) || 0} suffix="ms" /></Card>
          </Col>
          <Col span={6}>
            <Card><Statistic title="活跃请求" value={recentSpans.filter(s => s.status === 'running').length} /></Card>
          </Col>
        </Row>

        {anomalies.length > 0 && (
          <Alert
            message={`检测到 ${anomalies.length} 个异常`}
            description={anomalies[0]?.suggestion}
            type="warning"
            style={{ marginBottom: 16 }}
            showIcon
          />
        )}

        <Card>
          <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
        </Card>
      </Spin>
    </div>
  )
}

export default TracePage
```

- [ ] **Step 2: 提交**

```bash
git add web-ui/src/pages/TracePage.tsx
git commit -m "feat(trace): add TracePage component"
```

---

## Task 6: 前端 - 添加路由和导航

**Files:**
- Modify: `web-ui/src/App.tsx`
- Modify: `web-ui/src/components/Layout.tsx`

- [ ] **Step 1: 添加 TracePage 到 App.tsx**

```tsx
import TracePage from './pages/TracePage'

// 在 Routes 中添加
<Route path="trace" element={<TracePage />} />
```

- [ ] **Step 2: 添加导航入口到 Layout.tsx**

在 sidebar-nav 中添加：

```tsx
<NavLink to="/trace" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
  🔍 {t('nav.trace')}
</NavLink>
```

- [ ] **Step 3: 提交**

```bash
git add web-ui/src/App.tsx web-ui/src/components/Layout.tsx
git commit -m "feat(trace): add route and navigation for trace page"
```

---

## Task 7: 前端 - 添加国际化

**Files:**
- Modify: `web-ui/src/i18n/locales/zh-CN.json`
- Modify: `web-ui/src/i18n/locales/en.json`

- [ ] **Step 1: 添加中文翻译**

在 `zh-CN.json` 中添加：

```json
{
  "nav": {
    "trace": "Trace 监控"
  },
  "trace": {
    "title": "Trace 监控",
    "totalSpans": "总请求数",
    "successRate": "成功率",
    "avgLatency": "平均延迟",
    "activeRequests": "活跃请求",
    "overview": "概览",
    "recentRequests": "最近请求",
    "anomalies": "异常告警",
    "noAnomalies": "暂无异常",
    "anomaliesDetected": "检测到 {count} 个异常"
  }
}
```

- [ ] **Step 2: 添加英文翻译**

在 `en.json` 中添加：

```json
{
  "nav": {
    "trace": "Trace"
  },
  "trace": {
    "title": "Trace Monitor",
    "totalSpans": "Total Spans",
    "successRate": "Success Rate",
    "avgLatency": "Avg Latency",
    "activeRequests": "Active",
    "overview": "Overview",
    "recentRequests": "Recent",
    "anomalies": "Anomalies",
    "noAnomalies": "No anomalies",
    "anomaliesDetected": "{count} anomalies detected"
  }
}
```

- [ ] **Step 3: 提交**

```bash
git add web-ui/src/i18n/locales/zh-CN.json web-ui/src/i18n/locales/en.json
git commit -m "feat(i18n): add trace page translations"
```

---

## Task 8: 验证

- [ ] **Step 1: 启动后端服务**

```bash
cd nanobot-webui
python -m nanobot web
```

- [ ] **Step 2: 启动前端服务**

```bash
cd web-ui
npm run dev
```

- [ ] **Step 3: 访问 Trace 页面**

打开浏览器访问 `http://localhost:5173/trace`，验证：
1. 页面正常加载
2. 指标卡片显示数据
3. Tabs 切换正常
4. SSE 实时更新正常（如果有新请求）
