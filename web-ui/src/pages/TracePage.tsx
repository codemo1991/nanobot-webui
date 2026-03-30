import { useEffect, useState, useRef } from 'react'
import { Card, Row, Col, Statistic, Table, Tag, Tabs, Button, Spin, Alert, Empty } from 'antd'
import { ReloadOutlined, ClockCircleOutlined, CheckCircleOutlined, ExclamationCircleOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { TraceSummary, RecentSpan, Anomaly } from '../types'

function TracePage() {
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
          setRecentSpans(prev => {
            const newSpan: RecentSpan = {
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
    { title: '范围', key: 'scope', render: (_: unknown, r: Anomaly) => `${r.span_type}/${r.group_key}` },
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
        <h1>Trace 监控</h1>
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
