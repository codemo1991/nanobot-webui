import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Input, Modal, Spin, message, Typography } from 'antd'
import { FolderOutlined, FileTextOutlined, DownloadOutlined, ReloadOutlined, SwapOutlined, UploadOutlined } from '@ant-design/icons'
import { api } from '../api'
import { formatUptime } from '../utils/formatUptime'
import type { SystemStatus } from '../types'
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
  const [workspaceModalVisible, setWorkspaceModalVisible] = useState(false)
  const [workspaceInput, setWorkspaceInput] = useState('')
  const [workspaceSwitching, setWorkspaceSwitching] = useState(false)
  const [importing, setImporting] = useState(false)
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

  const handleOpenWorkspaceModal = () => {
    setWorkspaceInput(status?.web.workspace ?? '')
    setWorkspaceModalVisible(true)
  }

  const handleSwitchWorkspace = async () => {
    const path = workspaceInput.trim()
    if (!path) {
      message.warning(t('system.enterWorkspacePath'))
      return
    }
    try {
      setWorkspaceSwitching(true)
      await api.switchWorkspace(path)
      message.success(t('system.workspaceSwitched'))
      setWorkspaceModalVisible(false)
      loadStatus()
    } catch (err) {
      message.error(err instanceof Error ? err.message : t('system.switchWorkspaceFailed'))
    } finally {
      setWorkspaceSwitching(false)
    }
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
        </div>

        <div className="card">
          <h2>
            <FolderOutlined style={{ marginRight: 8 }} />
            {t('system.workspace')}
          </h2>
          <div className="info-list">
            <div className="info-item">
              <span className="info-label">{t('system.workspacePath')}</span>
              <span className="info-value workspace-path">
                {loading ? (
                  <Spin size="small" />
                ) : status?.web.workspace ? (
                  <Typography.Text
                    copyable
                    style={{ fontFamily: 'monospace', fontSize: 13, wordBreak: 'break-all' }}
                  >
                    {status.web.workspace}
                  </Typography.Text>
                ) : (
                  '--'
                )}
              </span>
            </div>
            <div className="workspace-actions">
              <Button
                type="primary"
                icon={<SwapOutlined />}
                onClick={handleOpenWorkspaceModal}
                size="small"
              >
                {t('system.switchWorkspace')}
              </Button>
            </div>
            <div className="workspace-tip">
              <small>
                {t('system.workspaceTip')}
              </small>
            </div>
          </div>
        </div>

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
        title={t('system.switchWorkspaceModal')}
        open={workspaceModalVisible}
        onCancel={() => setWorkspaceModalVisible(false)}
        onOk={handleSwitchWorkspace}
        confirmLoading={workspaceSwitching}
        okText={t('system.switch')}
      >
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', marginBottom: 8 }}>{t('system.workspacePathLabel')}</label>
          <Input
            placeholder={t('system.workspacePathPlaceholder')}
            value={workspaceInput}
            onChange={(e) => setWorkspaceInput(e.target.value)}
            onPressEnter={handleSwitchWorkspace}
          />
          <small style={{ color: '#666', marginTop: 8, display: 'block' }}>
            {t('system.workspacePathHint')}
          </small>
        </div>
      </Modal>

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
