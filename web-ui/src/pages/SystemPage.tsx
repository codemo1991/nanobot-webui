import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Modal, Spin, message, Row, Col, Statistic, Space, Card } from 'antd'
import { FileTextOutlined, DownloadOutlined, ReloadOutlined, UploadOutlined, DeleteOutlined } from '@ant-design/icons'
import { api } from '../api'
import { formatUptime } from '../utils/formatUptime'
import type { SystemStatus, Metrics } from '../types'
import './SystemPage.css'

function SystemPage() {
  const { t } = useTranslation()
  const [health, setHealth] = useState<{ status: string } | null>(null)
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [logsVisible, setLogsVisible] = useState(false)
  const [logs, setLogs] = useState<string[]>([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [metricsLoading, setMetricsLoading] = useState(false)
  const importInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    loadStatus()
  }, [])

  const loadStatus = async () => {
    try {
      setLoading(true)
      setError(null)
      const healthData = await api.health()
      setHealth(healthData)
      const statusData = await api.getSystemStatus()
      setStatus(statusData)
    } catch (err) {
      console.error('Failed to load status:', err)
      setError(err instanceof Error ? err.message : t('system.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleViewLogs = async () => {
    setLogsVisible(true)
    setLogsLoading(true)
    try {
      const res = await api.getSystemLogs()
      setLogs(res.lines)
    } catch (err) {
      console.error(err)
      message.error(t('system.loadLogsFailed'))
    } finally {
      setLogsLoading(false)
    }
  }

  const handleExportConfig = () => {
    api.exportConfig()
    message.success(t('system.exportStarted'))
  }

  const handleResetGlobalTokens = () => {
    Modal.confirm({
      title: t('system.resetGlobalTokensTitle'),
      content: t('system.resetGlobalTokensConfirm'),
      onOk: async () => {
        try {
          await api.resetGlobalTokenSummary()
          message.success(t('system.resetGlobalTokensSuccess'))
          loadStatus()
        } catch (err) {
          message.error(t('system.resetGlobalTokensFailed'))
        }
      },
    })
  }

  const handleImportConfig = () => {
    importInputRef.current?.click()
  }

  const handleImportConfigFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    const reader = new FileReader()
    reader.onload = async () => {
      try {
        const text = reader.result as string
        const config = JSON.parse(text) as object
        setImporting(true)
        await api.importConfig(config, true)
        message.success(t('system.importSuccess'))
        loadStatus()
      } catch (err) {
        message.error(err instanceof Error ? err.message : t('system.importFailed'))
      } finally {
        setImporting(false)
      }
    }
    reader.readAsText(file, 'utf-8')
  }

  const loadMetrics = async () => {
    try {
      setMetricsLoading(true)
      const data = await api.getMetrics()
      setMetrics(data)
    } catch (err) {
      console.error('Failed to load metrics:', err)
    } finally {
      setMetricsLoading(false)
    }
  }

  const handleResetMetrics = async () => {
    try {
      await api.resetMetrics()
      message.success(t('config.metrics.resetSuccess') || '重置成功')
      loadMetrics()
    } catch (err) {
      console.error('Failed to reset metrics:', err)
      message.error(t('config.metrics.resetFailed') || '重置失败')
    }
  }

  // Load metrics on mount
  useEffect(() => {
    loadMetrics()
  }, [])

  return (
    <div className="system-page">
      <div className="page-header">
        <h1>📊 {t('system.title')}</h1>
        <p className="page-description">{t('system.description')}</p>
      </div>

      <div className="system-content">
        {error && (
          <div className="card error-card">
            <p>⚠️ {error}</p>
          </div>
        )}

        <div className="status-grid">
          <div className="card status-card">
            <div className="status-icon">🟢</div>
            <div>
              <h3>{t('system.apiService')}</h3>
              <p className="status-value">
                {loading ? t('system.checking') : health?.status === 'ok' ? t('system.running') : t('system.abnormal')}
              </p>
            </div>
          </div>

          <div className="card status-card">
            <div className="status-icon">🔄</div>
            <div>
              <h3>{t('system.uptime')}</h3>
              <p className="status-value">
                {loading ? t('system.loading') : status ? formatUptime(status.web.uptime) : '--'}
              </p>
            </div>
          </div>

          <div className="card status-card">
            <div className="status-icon">💬</div>
            <div>
              <h3>{t('system.sessions')}</h3>
              <p className="status-value">
                {loading ? '加载中...' : status ? status.stats.sessions : '--'}
              </p>
            </div>
          </div>

          <div className="card status-card">
            <div className="status-icon">📦</div>
            <div>
              <h3>{t('system.skills')}</h3>
              <p className="status-value">
                {loading ? '加载中...' : status ? status.stats.skills : '0'}
              </p>
            </div>
          </div>

          <div className="card status-card">
            <div className="status-icon">🧮</div>
            <div>
              <h3>{t('system.tokens')}</h3>
              <p className="status-value">
                {loading ? t('system.loading') : status ? new Intl.NumberFormat().format(status.stats.tokens.totalTokens) : '--'}
              </p>
            </div>
          </div>
        </div>

        {/* 监控指标 */}
        <Card
          title={t('config.metrics.title')}
          extra={
            <Space>
              <Button icon={<ReloadOutlined />} size="small" onClick={loadMetrics}>
                {t('config.metrics.refresh')}
              </Button>
              <Button danger icon={<DeleteOutlined />} size="small" onClick={handleResetMetrics}>
                {t('config.metrics.reset')}
              </Button>
            </Space>
          }
          loading={metricsLoading}
          style={{ marginBottom: 16 }}
        >
          <Row gutter={16}>
            <Col span={8}>
              <Statistic title={t('config.metrics.totalToolCalls')} value={metrics?.total_tool_calls || 0} />
            </Col>
            <Col span={8}>
              <Statistic title={t('config.metrics.parallelToolCalls')} value={metrics?.parallel_tool_calls || 0} valueStyle={{ color: '#3f8600' }} />
            </Col>
            <Col span={8}>
              <Statistic title={t('config.metrics.serialToolCalls')} value={metrics?.serial_tool_calls || 0} valueStyle={{ color: '#cf1322' }} />
            </Col>
          </Row>
          <Row gutter={16} style={{ marginTop: 16 }}>
            <Col span={8}>
              <Statistic title={t('config.metrics.failedToolCalls')} value={metrics?.failed_tool_calls || 0} valueStyle={{ color: '#cf1322' }} />
            </Col>
            <Col span={8}>
              <Statistic title={t('config.metrics.totalSubagentSpawns')} value={metrics?.total_subagent_spawns || 0} />
            </Col>
            <Col span={8}>
              <Statistic title={t('config.metrics.llmCallCount')} value={metrics?.llm_call_count || 0} />
            </Col>
          </Row>
          <Row gutter={16} style={{ marginTop: 16 }}>
            <Col span={8}>
              <Statistic title={t('config.metrics.avgToolExecutionTime')} value={metrics?.avg_tool_execution_time?.toFixed(2) || 0} suffix="s" />
            </Col>
            <Col span={8}>
              <Statistic title={t('config.metrics.maxConcurrentTools')} value={metrics?.max_concurrent_tools || 0} valueStyle={{ color: '#1890ff' }} />
            </Col>
            <Col span={8}>
              <Statistic title={t('config.metrics.totalTokenUsage')} value={metrics?.total_token_usage || 0} />
            </Col>
          </Row>
        </Card>

        <div className="card">
          <h2>{t('system.systemInfo')}</h2>
          <div className="info-list">
            <div className="info-item">
              <span className="info-label">{t('system.version')}</span>
              <span className="info-value">{status?.web.version ?? '0.1.0'}</span>
            </div>
            <div className="info-item">
              <span className="info-label">{t('system.apiEndpoint')}</span>
              <span className="info-value">/api/v1</span>
            </div>
            <div className="info-item">
              <span className="info-label">{t('system.database')}</span>
              <span className="info-value">~/.nanobot/chat.db</span>
            </div>
            {status?.environment && (
              <>
                <div className="info-item">
                  <span className="info-label">Python</span>
                  <span className="info-value">{status.environment.python}</span>
                </div>
                <div className="info-item">
                  <span className="info-label">{t('system.platform')}</span>
                  <span className="info-value">{status.environment.platform}</span>
                </div>
              </>
            )}
          </div>
        </div>

        <div className="card">
          <h2>{t('system.quickActions')}</h2>
          <div className="action-buttons">
            <Button type="primary" icon={<ReloadOutlined />} onClick={loadStatus} loading={loading}>
              {t('system.refresh')}
            </Button>
            <Button icon={<FileTextOutlined />} onClick={handleViewLogs}>
              {t('system.viewLogs')}
            </Button>
            <Button icon={<DownloadOutlined />} onClick={handleExportConfig}>
              {t('system.exportConfig')}
            </Button>
            <Button
              icon={<UploadOutlined />}
              onClick={handleImportConfig}
              loading={importing}
            >
              {t('system.importConfig')}
            </Button>
            <Button danger onClick={handleResetGlobalTokens}>
              {t('system.resetGlobalTokens')}
            </Button>
          </div>
          <input
            ref={importInputRef}
            type="file"
            accept=".json,application/json"
            style={{ display: 'none' }}
            onChange={handleImportConfigFile}
          />
        </div>
      </div>

      <Modal
        title={t('system.logsTitle')}
        open={logsVisible}
        onCancel={() => setLogsVisible(false)}
        width={900}
        footer={[
          <Button key="refresh" onClick={handleViewLogs} loading={logsLoading}>
            {t('system.refreshLogs')}
          </Button>,
          <Button key="close" type="primary" onClick={() => setLogsVisible(false)}>
            {t('system.close')}
          </Button>,
        ]}
      >
        <div
          className="logs-content"
          style={{
            maxHeight: '60vh',
            overflowY: 'auto',
            background: '#001529',
            color: '#fff',
            padding: '16px',
            borderRadius: '8px',
            fontFamily: 'monospace',
            fontSize: '12px',
            lineHeight: 1.5,
          }}
        >
          {logsLoading ? (
            <div style={{ textAlign: 'center', padding: '20px' }}>
              <Spin tip={t('system.loadingLogs')} />
            </div>
          ) : logs.length === 0 ? (
            <div style={{ color: '#999' }}>{t('system.noLogs')}</div>
          ) : (
            logs.map((line, i) => (
              <div key={i} style={{ borderBottom: '1px solid #1f1f1f', padding: '2px 0' }}>
                {line}
              </div>
            ))
          )}
        </div>
      </Modal>
    </div>
  )
}

export default SystemPage
