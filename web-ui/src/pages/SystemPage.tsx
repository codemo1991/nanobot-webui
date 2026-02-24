import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Input, Modal, Spin, message, Typography, Switch, InputNumber, Tooltip } from 'antd'
import { FolderOutlined, FileTextOutlined, DownloadOutlined, ReloadOutlined, SwapOutlined, UploadOutlined, SettingOutlined } from '@ant-design/icons'
import { api } from '../api'
import { formatUptime } from '../utils/formatUptime'
import type { SystemStatus, MemoryConfig } from '../types'
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
  const [memoryConfig, setMemoryConfig] = useState<MemoryConfig | null>(null)
  const [memoryConfigLoading, setMemoryConfigLoading] = useState(false)
  const [memoryConfigSaving, setMemoryConfigSaving] = useState(false)
  const importInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    loadStatus()
    loadMemoryConfig()
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

  const handleOpenWorkspaceModal = () => {
    setWorkspaceInput(status?.web.workspace ?? '')
    setWorkspaceModalVisible(true)
  }

  // 内部函数，带 copyDb 参数
  const switchWorkspaceWithOptions = async (copyDb?: boolean) => {
    const path = workspaceInput.trim()
    if (!path) {
      message.warning(t('system.enterWorkspacePath'))
      return
    }
    try {
      setWorkspaceSwitching(true)
      const result = await api.switchWorkspace(path, copyDb)

      // 检查是否需要用户选择数据库操作
      if ('needPrompt' in result && result.needPrompt) {
        // 弹出选择对话框
        const dbOptions = [
          {
            key: 'copy',
            label: result.hasDefaultDb
              ? t('system.copyExistingDb')
              : t('system.noExistingDb'),
            disabled: !result.hasDefaultDb,
          },
          {
            key: 'new',
            label: t('system.createNewDb'),
          },
        ]

        // 如果只有一个选项，直接使用
        const enabledOptions = dbOptions.filter((o) => !o.disabled)
        if (enabledOptions.length === 1) {
          await switchWorkspaceWithOptions(enabledOptions[0].key === 'copy')
          return
        }

        // 弹出确认框让用户选择
        Modal.confirm({
          title: t('system.chooseDbOption'),
          content: t('system.chooseDbOptionDesc'),
          okText: dbOptions[0].label,
          cancelText: dbOptions[1].label,
          onOk: async () => {
            await switchWorkspaceWithOptions(true)
          },
          onCancel: async () => {
            await switchWorkspaceWithOptions(false)
          },
        })
        return
      }

      message.success(t('system.workspaceSwitched'))
      setWorkspaceModalVisible(false)
      loadStatus()
    } catch (err) {
      message.error(err instanceof Error ? err.message : t('system.switchWorkspaceFailed'))
    } finally {
      setWorkspaceSwitching(false)
    }
  }

  // Modal 按钮调用的版本（不带参数）
  const handleSwitchWorkspace = () => switchWorkspaceWithOptions()

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

  const loadMemoryConfig = async () => {
    try {
      setMemoryConfigLoading(true)
      const data = await api.getMemoryConfig()
      setMemoryConfig(data)
    } catch (err) {
      console.error('Failed to load memory config:', err)
    } finally {
      setMemoryConfigLoading(false)
    }
  }

  const handleSaveMemoryConfig = async () => {
    if (!memoryConfig) return
    try {
      setMemoryConfigSaving(true)
      const updated = await api.updateMemoryConfig(memoryConfig)
      setMemoryConfig(updated)
      message.success(t('system.saveSuccess') || '保存成功')
    } catch (err) {
      message.error(err instanceof Error ? err.message : t('system.saveFailed') || '保存失败')
    } finally {
      setMemoryConfigSaving(false)
    }
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
          <h2>
            <SettingOutlined style={{ marginRight: 8 }} />
            {t('system.memoryConfig') || '记忆系统配置'}
          </h2>
          {memoryConfigLoading ? (
            <Spin size="small" />
          ) : memoryConfig ? (
            <div className="info-list">
              <div className="info-item">
                <span className="info-label">
                  <Tooltip title={t('system.autoIntegrateEnabledTip') || '自动从聊天记录中提取长期记忆'}>
                    {t('system.autoIntegrateEnabled') || '自动记忆整合'}
                  </Tooltip>
                </span>
                <Switch
                  checked={memoryConfig.auto_integrate_enabled}
                  onChange={(checked) => setMemoryConfig({ ...memoryConfig, auto_integrate_enabled: checked })}
                />
              </div>

              <div className="info-item">
                <span className="info-label">
                  <Tooltip title={t('system.autoIntegrateIntervalTip') || '自动整合的执行间隔'}>
                    {t('system.autoIntegrateInterval') || '整合间隔 (分钟)'}
                  </Tooltip>
                </span>
                <InputNumber
                  min={1}
                  max={1440}
                  value={memoryConfig.auto_integrate_interval_minutes}
                  onChange={(value) => setMemoryConfig({ ...memoryConfig, auto_integrate_interval_minutes: value || 30 })}
                  style={{ width: 100 }}
                />
              </div>

              <div className="info-item">
                <span className="info-label">
                  <Tooltip title={t('system.lookbackMinutesTip') || '每次整合时回溯的时间窗口'}>
                    {t('system.lookbackMinutes') || '回溯窗口 (分钟)'}
                  </Tooltip>
                </span>
                <InputNumber
                  min={1}
                  max={10080}
                  value={memoryConfig.lookback_minutes}
                  onChange={(value) => setMemoryConfig({ ...memoryConfig, lookback_minutes: value || 60 })}
                  style={{ width: 100 }}
                />
              </div>

              <div className="info-item">
                <span className="info-label">
                  <Tooltip title={t('system.maxMessagesTip') || '每次整合最多处理的消息数'}>
                    {t('system.maxMessages') || '每次最大消息数'}
                  </Tooltip>
                </span>
                <InputNumber
                  min={1}
                  max={1000}
                  value={memoryConfig.max_messages}
                  onChange={(value) => setMemoryConfig({ ...memoryConfig, max_messages: value || 100 })}
                  style={{ width: 100 }}
                />
              </div>

              <div className="info-item">
                <span className="info-label">
                  <Tooltip title={t('system.maxEntriesTip') || '长期记忆最大条数，超过时触发LLM总结'}>
                    {t('system.maxEntries') || '长期记忆上限 (条)'}
                  </Tooltip>
                </span>
                <InputNumber
                  min={10}
                  max={10000}
                  value={memoryConfig.max_entries}
                  onChange={(value) => setMemoryConfig({ ...memoryConfig, max_entries: value || 200 })}
                  style={{ width: 100 }}
                />
              </div>

              <div className="info-item">
                <span className="info-label">
                  <Tooltip title={t('system.maxCharsTip') || '长期记忆最大字符数，超过时触发LLM总结'}>
                    {t('system.maxChars') || '长期记忆上限 (字符)'}
                  </Tooltip>
                </span>
                <InputNumber
                  min={1024}
                  max={10 * 1024 * 1024}
                  step={1024}
                  value={memoryConfig.max_chars}
                  onChange={(value) => setMemoryConfig({ ...memoryConfig, max_chars: value || 204800 })}
                  style={{ width: 120 }}
                  formatter={(value) => `${(Number(value) / 1024).toFixed(0)} KB`}
                  parser={(value) => Number(value?.replace(' KB', '')) * 1024}
                />
              </div>

              <div className="info-item">
                <span className="info-label">
                  <Tooltip title={t('system.readMaxEntriesTip') || '读取时若超过此条数则全量读取'}>
                    {t('system.readMaxEntries') || '读取阈值 (条)'}
                  </Tooltip>
                </span>
                <InputNumber
                  min={1}
                  max={10000}
                  value={memoryConfig.read_max_entries}
                  onChange={(value) => setMemoryConfig({ ...memoryConfig, read_max_entries: value || 80 })}
                  style={{ width: 100 }}
                />
              </div>

              <div className="info-item">
                <span className="info-label">
                  <Tooltip title={t('system.readMaxCharsTip') || '读取时若超过此字符数则截断'}>
                    {t('system.readMaxChars') || '读取截断 (字符)'}
                  </Tooltip>
                </span>
                <InputNumber
                  min={1024}
                  max={10 * 1024 * 1024}
                  step={1024}
                  value={memoryConfig.read_max_chars}
                  onChange={(value) => setMemoryConfig({ ...memoryConfig, read_max_chars: value || 25600 })}
                  style={{ width: 120 }}
                  formatter={(value) => `${(Number(value) / 1024).toFixed(0)} KB`}
                  parser={(value) => Number(value?.replace(' KB', '')) * 1024}
                />
              </div>

              <div className="memory-config-actions">
                <Button
                  type="primary"
                  onClick={handleSaveMemoryConfig}
                  loading={memoryConfigSaving}
                >
                  {t('system.save') || '保存'}
                </Button>
              </div>
            </div>
          ) : (
            <div>{t('system.loadMemoryConfigFailed') || '加载记忆配置失败'}</div>
          )}
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
