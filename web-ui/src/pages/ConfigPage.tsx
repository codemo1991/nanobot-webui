import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Form, Input, InputNumber, Switch, Button, Modal, Select, Card, Space, Tag, List, message, Tabs, Spin, Typography, Row, Col, Table, Alert, Tooltip, AutoComplete } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, SettingOutlined, FolderOpenOutlined, UploadOutlined, SwapOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { ChannelsConfig, Provider, InstalledSkill, McpServer, AgentConfig, WebConcurrencyConfig, WebMemoryConfig } from '../types'
import AgentTemplatePage from './AgentTemplatePage'
import SystemPromptPage from './SystemPromptPage'
import './ConfigPage.css'

const { Title, Text } = Typography
const { TextArea } = Input

export default function ConfigPage() {
  const [activeTab, setActiveTab] = useState('providers')

  const { t } = useTranslation()
  const TabContentWrapper = ({ children }: { children: React.ReactNode }) => (
    <div className="config-tab-content">{children}</div>
  )
  const items = [
    { key: 'channels', label: t('config.channels'), children: <TabContentWrapper><ChannelsConfig /></TabContentWrapper> },
    { key: 'providers', label: t('config.providers'), children: <TabContentWrapper><ProvidersConfig /></TabContentWrapper> },
    { key: 'models', label: t('config.models'), children: <TabContentWrapper><ModelsConfig /></TabContentWrapper> },
    { key: 'mcps', label: t('config.mcps'), children: <TabContentWrapper><McpConfig /></TabContentWrapper> },
    { key: 'skills', label: t('config.skills'), children: <TabContentWrapper><SkillsConfig /></TabContentWrapper> },
    { key: 'agent-templates', label: 'Agent 模板', children: <TabContentWrapper><AgentTemplatePage /></TabContentWrapper> },
    { key: 'system-prompt', label: '主 Agent 提示词', children: <TabContentWrapper><SystemPromptPage /></TabContentWrapper> },
    { key: 'system', label: t('config.system'), children: <TabContentWrapper><SystemConfig /></TabContentWrapper> },
  ]

  return (
    <div className="config-page">
      <div className="page-header">
        <Title level={2} style={{ margin: 0 }}>⚙️ {t('config.title')}</Title>
        <Text type="secondary">{t('config.subtitle')}</Text>
      </div>

      <div className="config-content-wrapper">
        <Tabs 
          activeKey={activeTab} 
          onChange={setActiveTab} 
          items={items} 
          type="card"
          className="config-tabs-container"
        />
      </div>
    </div>
  )
}

// --- Channels (IM) Configuration ---

function ChannelsConfig() {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [channels, setChannels] = useState<ChannelsConfig | null>(null)
  const [editModalVisible, setEditModalVisible] = useState(false)
  const [currentChannel, setCurrentChannel] = useState<'whatsapp' | 'telegram' | 'feishu' | 'discord' | 'qq' | 'dingtalk' | null>(null)
  const [form] = Form.useForm()

  useEffect(() => {
    loadChannels()
  }, [])

  const loadChannels = async () => {
    try {
      setLoading(true)
      const data = await api.getChannels()
      setChannels(data)
    } catch (error) {
      message.error(t('config.loadChannelsFailed'))
      console.error(error)
    } finally {
      setLoading(false)
    }
  }

  const handleEdit = (channelKey: 'whatsapp' | 'telegram' | 'feishu' | 'discord' | 'qq' | 'dingtalk') => {
    if (!channels) return
    setCurrentChannel(channelKey)
    form.setFieldsValue(channels[channelKey])
    setEditModalVisible(true)
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      if (!currentChannel) return

      await api.updateChannels({
        [currentChannel]: values
      })
      
      message.success(t('config.configUpdated'))
      setEditModalVisible(false)
      loadChannels()
    } catch (error) {
      console.error(error)
      message.error(t('config.updateFailed'))
    }
  }

  if (loading && !channels) return <div className="loading-container"><Spin size="large" /></div>

  if (!channels) return null

  return (
    <div className="config-panel">
      <div className="channels-scroll-container">
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        {/* Gateway Status */}
        <Card size="small">
          <Space>
            <Text strong>Gateway Process:</Text>
            {channels.gateway ? (
              <Tag color={channels.gateway.running ? 'success' : 'default'}>
                {channels.gateway.running ? t('config.gatewayRunning') : t('config.gatewayStopped')}
              </Tag>
            ) : (
             <Tag>{t('config.gatewayUnknown')}</Tag>
            )}
            <Text type="secondary" style={{ fontSize: 12 }}>({t('config.gatewayHint')})</Text>
          </Space>
        </Card>

        {/* WhatsApp */}
        <Card 
          title={<Space><SettingOutlined /> WhatsApp</Space>} 
          extra={<Button type="link" onClick={() => handleEdit('whatsapp')}>{t('config.configure')}</Button>}
        >
          <Space direction="vertical">
            <Text>状态: <Tag color={channels.whatsapp.enabled ? 'success' : 'default'}>{channels.whatsapp.enabled ? t('config.enabled') : t('config.notEnabled')}</Tag></Text>
            <Text type="secondary">Bridge URL: {channels.whatsapp.bridgeUrl || t('config.notSet')}</Text>
          </Space>
        </Card>

        {/* Telegram */}
        <Card 
          title={<Space><SettingOutlined /> Telegram</Space>} 
          extra={<Button type="link" onClick={() => handleEdit('telegram')}>{t('config.configure')}</Button>}
        >
          <Space direction="vertical">
            <Text>状态: <Tag color={channels.telegram.enabled ? 'success' : 'default'}>{channels.telegram.enabled ? t('config.enabled') : t('config.notEnabled')}</Tag></Text>
            <Text type="secondary">Bot Token: {channels.telegram.token ? '************' : t('config.notSet')}</Text>
          </Space>
        </Card>

        {/* Feishu */}
        <Card 
          title={<Space><SettingOutlined /> Feishu (飞书)</Space>} 
          extra={<Button type="link" onClick={() => handleEdit('feishu')}>{t('config.configure')}</Button>}
        >
          <Space direction="vertical">
            <Text>状态: <Tag color={channels.feishu.enabled ? 'success' : 'default'}>{channels.feishu.enabled ? t('config.enabled') : t('config.notEnabled')}</Tag></Text>
            <Text type="secondary">App ID: {channels.feishu.appId || t('config.notSet')}</Text>
          </Space>
        </Card>

        {/* Discord */}
        <Card 
          title={<Space><SettingOutlined /> Discord</Space>} 
          extra={<Button type="link" onClick={() => handleEdit('discord')}>{t('config.configure')}</Button>}
        >
          <Space direction="vertical">
            <Text>状态: <Tag color={channels.discord?.enabled ? 'success' : 'default'}>{channels.discord?.enabled ? t('config.enabled') : t('config.notEnabled')}</Tag></Text>
            <Text type="secondary">Bot Token: {channels.discord?.token ? '************' : t('config.notSet')}</Text>
          </Space>
        </Card>

        {/* QQ */}
        <Card 
          title={<Space><SettingOutlined /> QQ (单聊)</Space>} 
          extra={<Button type="link" onClick={() => handleEdit('qq')}>{t('config.configure')}</Button>}
        >
          <Space direction="vertical">
            <Text>状态: <Tag color={channels.qq?.enabled ? 'success' : 'default'}>{channels.qq?.enabled ? t('config.enabled') : t('config.notEnabled')}</Tag></Text>
            <Text type="secondary">App ID: {channels.qq?.appId || t('config.notSet')}</Text>
          </Space>
        </Card>

        {/* DingTalk */}
        <Card 
          title={<Space><SettingOutlined /> DingTalk (钉钉)</Space>} 
          extra={<Button type="link" onClick={() => handleEdit('dingtalk')}>{t('config.configure')}</Button>}
        >
          <Space direction="vertical">
            <Text>状态: <Tag color={channels.dingtalk?.enabled ? 'success' : 'default'}>{channels.dingtalk?.enabled ? t('config.enabled') : t('config.notEnabled')}</Tag></Text>
            <Text type="secondary">Client ID: {channels.dingtalk?.clientId || t('config.notSet')}</Text>
          </Space>
        </Card>
      </Space>
      </div>

      <Modal
        title={`配置 ${currentChannel === 'feishu' ? 'Feishu (飞书)' : currentChannel === 'discord' ? 'Discord' : currentChannel === 'qq' ? 'QQ' : currentChannel === 'dingtalk' ? 'DingTalk (钉钉)' : currentChannel === 'telegram' ? 'Telegram' : currentChannel === 'whatsapp' ? 'WhatsApp' : currentChannel}`}
        open={editModalVisible}
        onOk={handleSave}
        onCancel={() => setEditModalVisible(false)}
        width={600}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="enabled" valuePropName="checked" label={t('config.channel.enabled')}>
            <Switch />
          </Form.Item>
          
          {currentChannel === 'whatsapp' && (
            <>
              <Form.Item name="bridgeUrl" label="Bridge URL" rules={[{ required: true }]}>
                <Input placeholder="ws://localhost:3001" />
              </Form.Item>
            </>
          )}

          {currentChannel === 'telegram' && (
            <>
              <Form.Item name="token" label="Bot Token" rules={[{ required: true }]}>
                <Input.Password placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11" />
              </Form.Item>
              <Form.Item name="proxy" label="Proxy URL">
                <Input placeholder="http://127.0.0.1:7890" />
              </Form.Item>
            </>
          )}

          {currentChannel === 'feishu' && (
            <>
              <Form.Item name="appId" label="App ID" rules={[{ required: true }]}>
                <Input placeholder="cli_..." />
              </Form.Item>
              <Form.Item name="appSecret" label="App Secret" rules={[{ required: true }]}>
                <Input.Password />
              </Form.Item>
              <Form.Item name="encryptKey" label="Encrypt Key">
                <Input.Password />
              </Form.Item>
              <Form.Item name="verificationToken" label="Verification Token">
                <Input.Password />
              </Form.Item>
            </>
          )}

          {currentChannel === 'discord' && (
            <>
              <Form.Item name="token" label="Bot Token" rules={[{ required: true }]}>
                <Input.Password placeholder="MToxxx..." />
              </Form.Item>
              <Text type="secondary" style={{ fontSize: 12 }}>需开启 MESSAGE CONTENT INTENT</Text>
            </>
          )}

          {currentChannel === 'qq' && (
            <>
              <Form.Item name="appId" label="App ID" rules={[{ required: true }]}>
                <Input placeholder="机器人 ID" />
              </Form.Item>
              <Form.Item name="secret" label="App Secret" rules={[{ required: true }]}>
                <Input.Password placeholder="机器人密钥" />
              </Form.Item>
              <Text type="secondary" style={{ fontSize: 12 }}>QQ 开放平台 q.qq.com 创建应用</Text>
            </>
          )}

          {currentChannel === 'dingtalk' && (
            <>
              <Form.Item name="clientId" label="Client ID (AppKey)" rules={[{ required: true }]}>
                <Input placeholder="AppKey" />
              </Form.Item>
              <Form.Item name="clientSecret" label="Client Secret (AppSecret)" rules={[{ required: true }]}>
                <Input.Password placeholder="AppSecret" />
              </Form.Item>
              <Text type="secondary" style={{ fontSize: 12 }}>钉钉开放平台，Stream 模式</Text>
            </>
          )}

          <Form.Item name="allowFrom" label={t('config.channel.allowFrom')}>
            <Select mode="tags" style={{ width: '100%' }} placeholder={t('config.channel.allowFromPlaceholder')} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

// --- Providers (AI) Configuration ---


function ProvidersConfig() {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [providers, setProviders] = useState<Provider[]>([])
  const [modalVisible, setModalVisible] = useState(false)
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null)
  const [form] = Form.useForm()

  useEffect(() => {
    loadProviders()
  }, [])
  
  // ... (loadProviders, handleCreate, handleEdit, handleDelete same as before)
  const loadProviders = async () => {
    try {
      setLoading(true)
      const data = await api.getProviders()
      setProviders(data)
    } catch (error) {
      message.error(t('config.provider.loadFailed'))
      console.error(error)
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = () => {
    setEditingProviderId(null)
    form.resetFields()
    form.setFieldsValue({ type: 'openai' })
    setModalVisible(true)
  }

  const handleEdit = (provider: Provider) => {
    setEditingProviderId(provider.id)
    form.setFieldsValue({
      type: provider.type,
      name: provider.name,
      apiKey: provider.apiKey || '',
      apiBase: provider.apiBase
    })
    setModalVisible(true)
  }

  const handleDelete = (id: string) => {
    Modal.confirm({
      title: t('config.provider.confirmDisable'),
      content: t('config.provider.confirmDisableContent'),
      onOk: async () => {
        try {
          await api.deleteProvider(id)
          message.success(t('config.provider.disabled'))
          loadProviders()
        } catch (error) {
          message.error(t('config.provider.opFailed'))
        }
      }
    })
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      const providerData = {
        ...values,
        enabled: true
      } as any
      
      if (editingProviderId) {
        await api.updateProvider(editingProviderId, providerData)
      } else {
        await api.createProvider(providerData)
      }
      message.success(t('config.provider.saveSuccess'))
      setModalVisible(false)
      loadProviders()
    } catch (error) {
      console.error(error)
      message.error(t('config.provider.saveFailed'))
    }
  }

  const providerOptions = [
    { value: 'anthropic', label: 'Anthropic' },
    { value: 'openai', label: 'OpenAI' },
    { value: 'openrouter', label: 'OpenRouter' },
    { value: 'deepseek', label: 'DeepSeek' },
    { value: 'minimax', label: 'Minimax' },
    { value: 'groq', label: 'Groq' },
    { value: 'zhipu', label: 'Zhipu (智谱)' },
    { value: 'dashscope', label: 'Qwen (通义 / DashScope)' },
    { value: 'gemini', label: 'Gemini' },
    { value: 'vllm', label: 'vLLM' },
    { value: 'ollama', label: 'Ollama (本地)' },
    { value: 'moonshot', label: 'Moonshot (Kimi)' },
  ]

  return (
    <div className="config-panel">
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <Text type="secondary">支持: {providerOptions.map(p => p.label).join('、')}</Text>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>{t('config.provider.add')}</Button>
      </div>

      <List
        grid={{ gutter: 16, column: 2 }}
        dataSource={providers}
        loading={loading}
        renderItem={(item: Provider) => (
          <List.Item>
            <Card 
              title={item.name} 
              extra={
                <Space>
                  <Button type="text" icon={<EditOutlined />} onClick={() => handleEdit(item)} />
                  <Button type="text" danger icon={<DeleteOutlined />} onClick={() => handleDelete(item.id)} />
                </Space>
              }
            >
              <Space direction="vertical" style={{ width: '100%' }}>
                <Text>Type: <Tag>{item.type}</Tag></Text>
                <Text>Status: <Tag color={item.enabled ? 'success' : 'default'}>{item.enabled ? 'Enabled' : 'Disabled'}</Tag></Text>
                <Text type="secondary">API Key: {item.apiKey ? '已配置' : '未配置'}</Text>
                {item.apiBase && <Text type="secondary" ellipsis>Base URL: {item.apiBase}</Text>}
              </Space>
            </Card>
          </List.Item>
        )}
      />

      <Modal
        title={editingProviderId ? t('config.provider.edit') : t('config.provider.add')}
        open={modalVisible}
        onOk={handleSave}
        onCancel={() => setModalVisible(false)}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="type" label="Provider Type" rules={[{ required: true }]}>
            <Select
              disabled={!!editingProviderId}
              placeholder="选择类型 (如 DeepSeek、Zhipu、Qwen)"
              options={providerOptions}
            />
          </Form.Item>
          <Form.Item name="name" label="名称">
            <Input placeholder="例如: My OpenAI" />
          </Form.Item>
          <Form.Item name="apiKey" label="API Key">
            <Input.Password placeholder="sk-..." />
          </Form.Item>
          <Form.Item name="apiBase" label="Base URL (可选)">
            <Input placeholder="https://api.example.com/v1" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

function SystemConfig() {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [agentLoading, setAgentLoading] = useState(false)
  const [concurrencyLoading, setConcurrencyLoading] = useState(false)
  const [memoryLoading, setMemoryLoading] = useState(false)
  const [workspaceModalVisible, setWorkspaceModalVisible] = useState(false)
  const [workspaceValue, setWorkspaceValue] = useState('')
  const [currentWorkspace, setCurrentWorkspace] = useState('')
  const [agentForm] = Form.useForm()
  const [concurrencyForm] = Form.useForm()
  const [memoryForm] = Form.useForm()

  useEffect(() => {
    loadAgent()
    loadConcurrency()
    loadMemory()
    loadCurrentWorkspace()
  }, [])

  const loadCurrentWorkspace = async () => {
    try {
      const status = await api.getSystemStatus()
      if (status?.web?.workspace) {
        setCurrentWorkspace(status.web.workspace)
      }
    } catch (error) {
      console.error('Failed to load workspace:', error)
    }
  }

  const loadAgent = async () => {
    try {
      setAgentLoading(true)
      const data = await api.getConfig()
      const agent = data?.agent ?? { maxToolIterations: 40, maxExecutionTime: 600 }
      agentForm.setFieldsValue({
        maxToolIterations: agent.maxToolIterations,
        maxExecutionTime: agent.maxExecutionTime,
        microkernelEscalationEnabled: agent.microkernelEscalationEnabled ?? true,
        microkernelEscalationThreshold: agent.microkernelEscalationThreshold ?? 10,
      })
    } catch (error) {
      console.error(error)
    } finally {
      setAgentLoading(false)
    }
  }

  const loadConcurrency = async () => {
    try {
      setConcurrencyLoading(true)
      const data = await api.getConcurrencyConfig()
      concurrencyForm.setFieldsValue({
        maxParallelToolCalls: data.max_parallel_tool_calls || 5,
        maxConcurrentSubagents: data.max_concurrent_subagents || 10,
        enableParallelTools: data.enable_parallel_tools !== false,
        threadPoolSize: data.thread_pool_size || 4,
        enableSubagentParallel: data.enable_subagent_parallel !== false,
        claudeCodeMaxConcurrent: data.claude_code_max_concurrent || 3,
        claudeCodePermissionMode: data.claude_code_permission_mode || 'auto',
      })
    } catch (error) {
      console.error(error)
    } finally {
      setConcurrencyLoading(false)
    }
  }

  const loadMemory = async () => {
    try {
      setMemoryLoading(true)
      const data = await api.getMemoryConfig()
      memoryForm.setFieldsValue({
        autoIntegrateEnabled: data.auto_integrate_enabled !== false,
        autoIntegrateIntervalMinutes: data.auto_integrate_interval_minutes || 60,
        lookbackMinutes: data.lookback_minutes || 60,
        maxMessages: data.max_messages || 100,
        maxEntries: data.max_entries || 100,
        maxChars: data.max_chars || 100000,
        readMaxEntries: data.read_max_entries || 10,
        readMaxChars: data.read_max_chars || 50000,
      })
    } catch (error) {
      console.error(error)
    } finally {
      setMemoryLoading(false)
    }
  }

  const handleAgentSave = async (values: Partial<AgentConfig>) => {
    try {
      setLoading(true)
      await api.updateAgentConfig({
        maxToolIterations: values.maxToolIterations != null ? Number(values.maxToolIterations) : undefined,
        maxExecutionTime: values.maxExecutionTime != null ? Number(values.maxExecutionTime) : undefined,
        microkernelEscalationEnabled: values.microkernelEscalationEnabled,
        microkernelEscalationThreshold: values.microkernelEscalationThreshold != null ? Number(values.microkernelEscalationThreshold) : undefined,
      })
      message.success(t('config.agent.saveSuccess'))
    } catch (error) {
      console.error(error)
      message.error(t('config.agent.saveFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleConcurrencySave = async (values: WebConcurrencyConfig) => {
    try {
      setLoading(true)
      // 发送 camelCase 格式以匹配后端期望
      await api.updateConcurrencyConfig({
        maxParallelToolCalls: Number(values.maxParallelToolCalls),
        maxConcurrentSubagents: Number(values.maxConcurrentSubagents),
        enableParallelTools: values.enableParallelTools,
        threadPoolSize: Number(values.threadPoolSize),
        enableSubagentParallel: values.enableSubagentParallel,
        claudeCodeMaxConcurrent: Number(values.claudeCodeMaxConcurrent),
        claudeCodePermissionMode: values.claudeCodePermissionMode || 'auto',
      })
      message.success(t('config.saveSuccess'))
    } catch (error) {
      console.error(error)
      message.error(t('config.saveFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleMemorySave = async (values: WebMemoryConfig) => {
    try {
      setLoading(true)
      await api.updateMemoryConfig({
        auto_integrate_enabled: values.autoIntegrateEnabled,
        auto_integrate_interval_minutes: Number(values.autoIntegrateIntervalMinutes),
        lookback_minutes: Number(values.lookbackMinutes),
        max_messages: Number(values.maxMessages),
        max_entries: Number(values.maxEntries),
        max_chars: Number(values.maxChars),
        read_max_entries: Number(values.readMaxEntries),
        read_max_chars: Number(values.readMaxChars),
      })
      message.success(t('config.saveSuccess'))
    } catch (error) {
      console.error(error)
      message.error(t('config.saveFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleWorkspaceSwitch = async () => {
    if (!workspaceValue.trim()) {
      message.error(t('config.workspace.pathRequired'))
      return
    }
    try {
      setLoading(true)
      await api.switchWorkspace(workspaceValue.trim())
      message.success(t('config.workspace.switchSuccess'))
      setWorkspaceModalVisible(false)
      setWorkspaceValue('')
      // Reload page to apply new workspace
      window.location.reload()
    } catch (error) {
      console.error(error)
      message.error(t('config.workspace.switchFailed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="config-panel">
      <Spin spinning={loading}>
        {/* Agent 配置 */}
        <Card
          title={t('config.system')}
          extra={<Text type="secondary" style={{ fontSize: 12 }}>{t('config.systemSubtitle')}</Text>}
          style={{ marginBottom: 16 }}
        >
          <Form form={agentForm} layout="vertical" onFinish={handleAgentSave}>
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  name="maxToolIterations"
                  label={t('config.agent.maxToolIterations')}
                  rules={[{ required: true }, { type: 'number', min: 1, max: 200 }]}
                  help={t('config.agent.maxToolIterationsHelp')}
                >
                  <InputNumber min={1} max={200} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  name="maxExecutionTime"
                  label={t('config.agent.maxExecutionTime')}
                  rules={[{ required: true }, { type: 'number', min: 0 }]}
                  help={t('config.agent.maxExecutionTimeHelp')}
                >
                  <InputNumber min={0} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  name="microkernelEscalationEnabled"
                  label={t('config.agent.microkernelEscalationEnabled')}
                  valuePropName="checked"
                  help={t('config.agent.microkernelEscalationEnabledHelp')}
                >
                  <Switch />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item noStyle shouldUpdate={(prev, curr) => prev.microkernelEscalationEnabled !== curr.microkernelEscalationEnabled}>
                  {({ getFieldValue }) => (
                    <Form.Item
                      name="microkernelEscalationThreshold"
                      label={t('config.agent.microkernelEscalationThreshold')}
                      rules={[{ type: 'number', min: 1, max: 50 }]}
                      help={t('config.agent.microkernelEscalationThresholdHelp')}
                    >
                      <InputNumber min={1} max={50} style={{ width: '100%' }} disabled={!getFieldValue('microkernelEscalationEnabled')} />
                    </Form.Item>
                  )}
                </Form.Item>
              </Col>
            </Row>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={agentLoading}>{t('config.save')}</Button>
            </Form.Item>
          </Form>
        </Card>

        {/* 并发/线程池配置 */}
        <Card
          title={t('config.concurrency.title') || '并发配置'}
          style={{ marginBottom: 16 }}
        >
          <Form form={concurrencyForm} layout="vertical" onFinish={handleConcurrencySave}>
            <Row gutter={16}>
              <Col span={8}>
                <Form.Item
                  name="threadPoolSize"
                  label={t('config.concurrency.threadPoolSize') || '线程池大小'}
                  rules={[{ type: 'number', min: 1, max: 32 }]}
                >
                  <InputNumber min={1} max={32} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="maxParallelToolCalls"
                  label={t('config.concurrency.maxParallelToolCalls') || '最大并行工具数'}
                  rules={[{ type: 'number', min: 1, max: 20 }]}
                >
                  <InputNumber min={1} max={20} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="maxConcurrentSubagents"
                  label={t('config.concurrency.maxConcurrentSubagents') || '最大并行子代理数'}
                  rules={[{ type: 'number', min: 1, max: 50 }]}
                >
                  <InputNumber min={1} max={50} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={8}>
                <Form.Item
                  name="enableParallelTools"
                  label={t('config.concurrency.enableParallelTools') || '启用工具并行'}
                  valuePropName="checked"
                >
                  <Switch />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="enableSubagentParallel"
                  label={t('config.concurrency.enableSubagentParallel') || '启用子代理并行'}
                  valuePropName="checked"
                >
                  <Switch />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  name="claudeCodeMaxConcurrent"
                  label={t('config.concurrency.claudeCodeMaxConcurrent') || 'Claude Code 最大并发'}
                  rules={[{ type: 'number', min: 1, max: 10 }]}
                >
                  <InputNumber min={1} max={10} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  name="claudeCodePermissionMode"
                  label={t('config.concurrency.claudeCodePermissionMode') || 'Claude Code 权限模式'}
                  tooltip={t('config.concurrency.claudeCodePermissionModeHelp')}
                >
                  <Select
                    options={[
                      { value: 'auto', label: 'auto（逐项确认）' },
                      { value: 'bypassPermissions', label: 'bypassPermissions（自动批准）' },
                      { value: 'plan', label: 'plan（先展示计划）' },
                      { value: 'acceptEdits', label: 'acceptEdits（接受编辑）' },
                      { value: 'default', label: 'default（默认）' },
                    ]}
                    placeholder={t('config.concurrency.claudeCodePermissionMode')}
                  />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={concurrencyLoading}>{t('config.save')}</Button>
            </Form.Item>
          </Form>
        </Card>

        {/* Memory 配置 */}
        <Card
          title={t('config.memory.title') || '记忆系统配置'}
          style={{ marginBottom: 16 }}
        >
          <Form form={memoryForm} layout="vertical" onFinish={handleMemorySave}>
            <Row gutter={16}>
              <Col span={8}>
                <Form.Item
                  name="autoIntegrateEnabled"
                  label={t('config.memory.autoIntegrateEnabled') || '启用自动整合'}
                  valuePropName="checked"
                >
                  <Switch />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="autoIntegrateIntervalMinutes"
                  label={t('config.memory.autoIntegrateInterval') || '整合间隔(分钟)'}
                  rules={[{ type: 'number', min: 1 }]}
                >
                  <InputNumber min={1} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="lookbackMinutes"
                  label={t('config.memory.lookbackMinutes') || '回顾时间(分钟)'}
                  rules={[{ type: 'number', min: 1 }]}
                >
                  <InputNumber min={1} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={8}>
                <Form.Item
                  name="maxMessages"
                  label={t('config.memory.maxMessages') || '最大消息数'}
                  rules={[{ type: 'number', min: 10 }]}
                >
                  <InputNumber min={10} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="maxEntries"
                  label={t('config.memory.maxEntries') || '最大长期记忆条目'}
                  rules={[{ type: 'number', min: 1 }]}
                >
                  <InputNumber min={1} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item
                  name="maxChars"
                  label={t('config.memory.maxChars') || '最大字符数'}
                  rules={[{ type: 'number', min: 1000 }]}
                >
                  <InputNumber min={1000} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  name="readMaxEntries"
                  label={t('config.memory.readMaxEntries') || '读取最大条目'}
                  rules={[{ type: 'number', min: 1 }]}
                >
                  <InputNumber min={1} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item
                  name="readMaxChars"
                  label={t('config.memory.readMaxChars') || '读取最大字符'}
                  rules={[{ type: 'number', min: 1000 }]}
                >
                  <InputNumber min={1000} style={{ width: '100%' }} />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={memoryLoading}>{t('config.save')}</Button>
            </Form.Item>
          </Form>
        </Card>

        {/* 工作目录切换 */}
        <Card
          title={t('config.workspace.title') || '工作目录'}
          extra={
            <Button icon={<SwapOutlined />} onClick={() => setWorkspaceModalVisible(true)}>
              {t('config.workspace.switch') || '切换'}
            </Button>
          }
        >
          <Space direction="vertical" size="small">
            <Text type="secondary">{t('config.workspace.current') || '当前工作目录'}:</Text>
            <Text code copyable>{currentWorkspace || '-'}</Text>
          </Space>
        </Card>

        {/* 工作目录切换弹窗 */}
        <Modal
          title={t('config.workspace.switchTitle') || '切换工作目录'}
          open={workspaceModalVisible}
          onOk={handleWorkspaceSwitch}
          onCancel={() => {
            setWorkspaceModalVisible(false)
            setWorkspaceValue('')
          }}
          confirmLoading={loading}
        >
          <Form layout="vertical">
            <Form.Item
              label={t('config.workspace.path') || '路径'}
              required
            >
              <Input
                value={workspaceValue}
                onChange={(e) => setWorkspaceValue(e.target.value)}
                placeholder={t('config.workspace.pathPlaceholder') || '例如: ~/my-workspace'}
              />
            </Form.Item>
          </Form>
        </Modal>
      </Spin>
    </div>
  )
}

function ModelsConfig() {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [litellmOptions, setLitellmOptions] = useState<{ value: string; label: string }[]>([])
  const [litellmOptionsLoading, setLitellmOptionsLoading] = useState(false)
  const [models, setModels] = useState<import('../types').ModelInfo[]>([])
  const [profiles, setProfiles] = useState<import('../types').ModelProfile[]>([])
  const [providers, setProviders] = useState<import('../types').Provider[]>([])
  const [activeTab, setActiveTab] = useState('profiles')
  const [modelModalVisible, setModelModalVisible] = useState(false)
  const [profileModalVisible, setProfileModalVisible] = useState(false)
  const [globalDefaultModalVisible, setGlobalDefaultModalVisible] = useState(false)
  const [editingModel, setEditingModel] = useState<import('../types').ModelInfo | null>(null)
  const [editingProfile, setEditingProfile] = useState<import('../types').ModelProfile | null>(null)
  const [modelForm] = Form.useForm()
  const [profileForm] = Form.useForm()
  const [globalDefaultForm] = Form.useForm()

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    try {
      setLoading(true)
      const [modelsData, profilesData, providersData] = await Promise.all([
        api.getModels().then(m => m as unknown as import('../types').ModelInfo[]),
        api.getModelProfiles(),
        api.getProviders()
      ])
      setModels(modelsData || [])
      setProfiles(profilesData || [])
      setProviders(providersData || [])
    } catch (error) {
      console.error(error)
      message.error('加载配置失败')
    } finally {
      setLoading(false)
    }
  }

  const [discoveredModelsMap, setDiscoveredModelsMap] = useState<Record<string, import('../types').DiscoveredModel>>({})

  const handleDiscoverLitellmOptions = async (providerId: string) => {
    if (!providerId) {
      setLitellmOptions([])
      setDiscoveredModelsMap({})
      return
    }
    try {
      setLitellmOptionsLoading(true)
      const discovered = await api.discoverModels(providerId)
      const map: Record<string, import('../types').DiscoveredModel> = {}
      discovered.forEach(m => { map[m.litellmId] = m })
      setDiscoveredModelsMap(map)
      setLitellmOptions(discovered.map(m => ({ value: m.litellmId, label: `${m.litellmId} (${m.name})` })))
    } catch (error: any) {
      message.error(`${t('config.discoveryFailed')}: ${error.message}`)
      setLitellmOptions([])
      setDiscoveredModelsMap({})
    } finally {
      setLitellmOptionsLoading(false)
    }
  }

  const handleCreateModel = () => {
    setEditingModel(null)
    modelForm.resetFields()
    setLitellmOptions([])
    modelForm.setFieldsValue({
      enabled: true,
      contextWindow: 128000,
      capabilities: 'tools'
    })
    setModelModalVisible(true)
  }

  const handleEditModel = (model: import('../types').ModelInfo) => {
    setEditingModel(model)
    modelForm.setFieldsValue({
      ...model,
      capabilities: model.capabilities || ''
    })
    handleDiscoverLitellmOptions(model.providerId)
    setModelModalVisible(true)
  }

  const handleSaveModel = async (values: any) => {
    try {
      let litellmId = (values.litellmId || '').trim()
      const providerId = values.providerId
      // ollama、vllm 需带前缀，若用户只输入模型名则自动补全
      if (providerId === 'ollama' && litellmId && !litellmId.startsWith('ollama/')) {
        litellmId = 'ollama/' + litellmId
      } else if (providerId === 'vllm' && litellmId && !litellmId.startsWith('vllm/')) {
        litellmId = 'vllm/' + litellmId
      }
      const modelId = editingModel ? editingModel.id : litellmId.replace(/\//g, '-')
      const modelName = litellmId
      const modelData = {
        ...values,
        id: modelId,
        name: modelName,
        providerId: values.providerId,
        litellmId,
        aliases: '', // 已移除别名字段
        costRank: values.costRank || null,
        qualityRank: values.qualityRank || null
      }

      if (editingModel) {
        await api.updateModel(editingModel.id, modelData)
      } else {
        await api.createModel(modelData)
      }
      message.success(t('config.saveSuccess'))
      setModelModalVisible(false)
      loadData()
    } catch (error: any) {
      message.error(`${t('config.saveFailed')}: ${error.message}`)
    }
  }

  const handleDeleteModel = (modelId: string) => {
    Modal.confirm({
      title: '确认删除',
      content: '删除后无法恢复，是否继续？',
      onOk: async () => {
        try {
          await api.deleteModel(modelId)
          message.success('删除成功')
          loadData()
        } catch (error: any) {
          message.error(`删除失败: ${error.message}`)
        }
      }
    })
  }

  const handleSetDefault = async (modelId: string) => {
    try {
      await api.setDefaultModel(modelId)
      message.success('默认模型已设置')
      loadData()
    } catch (error: any) {
      message.error(`设置失败: ${error.message}`)
    }
  }

  const handleCreateProfile = () => {
    setEditingProfile(null)
    profileForm.resetFields()
    profileForm.setFieldsValue({ enabled: true, modelChain: '' })
    setProfileModalVisible(true)
  }

  const handleEditProfile = (profile: import('../types').ModelProfile) => {
    setEditingProfile(profile)
    // 将逗号分隔的 modelChain 转换为数组
    const modelChainArray = profile.modelChain ? profile.modelChain.split(',').filter(Boolean) : []
    profileForm.setFieldsValue({
      ...profile,
      modelChain: modelChainArray
    })
    setProfileModalVisible(true)
  }

  const handleSaveProfile = async (values: any) => {
    try {
      // 将数组转换为逗号分隔的字符串
      const modelChain = Array.isArray(values.modelChain)
        ? values.modelChain.join(',')
        : values.modelChain

      const data = {
        ...values,
        modelChain
      }

      if (editingProfile) {
        await api.updateModelProfile(editingProfile.id, data)
      } else {
        await api.createModelProfile({ ...data, id: values.id || values.name.toLowerCase().replace(/\s+/g, '-') })
      }
      message.success('保存成功')
      setProfileModalVisible(false)
      loadData()
    } catch (error: any) {
      message.error(`保存失败: ${error.message}`)
    }
  }

  const handleDeleteProfile = (profileId: string) => {
    if (['smart', 'fast', 'coding', 'summarize'].includes(profileId)) {
      message.error('不能删除系统预设场景')
      return
    }
    Modal.confirm({
      title: '确认删除',
      content: '删除后无法恢复，是否继续？',
      onOk: async () => {
        try {
          await api.deleteModelProfile(profileId)
          message.success('删除成功')
          loadData()
        } catch (error: any) {
          message.error(`删除失败: ${error.message}`)
        }
      }
    })
  }

  // 打开全局默认设置弹窗
  const handleOpenGlobalDefault = () => {
    globalDefaultForm.resetFields()
    const enabledModels = models.filter(m => m.enabled).map(m => m.id)
    globalDefaultForm.setFieldsValue({
      modelChain: enabledModels.length > 0 ? [enabledModels[0]] : []
    })
    setGlobalDefaultModalVisible(true)
  }

  // 应用全局默认到所有场景
  const handleApplyGlobalDefault = async (values: any) => {
    try {
      const modelChain = values.modelChain.join(',')
      if (!modelChain) {
        message.error('请至少选择一个模型')
        return
      }

      // 应用到所有系统场景
      const systemProfiles = ['smart', 'fast', 'coding', 'summarize']
      for (const profileId of systemProfiles) {
        const profile = profiles.find(p => p.id === profileId)
        if (profile) {
          await api.updateModelProfile(profileId, {
            ...profile,
            modelChain
          })
        }
      }

      message.success(`已将 ${values.modelChain.length} 个模型应用到所有场景`)
      setGlobalDefaultModalVisible(false)
      loadData()
    } catch (error: any) {
      message.error(`应用失败: ${error.message}`)
    }
  }

  const getProviderName = (providerId: string) => {
    const provider = providers.find(p => p.id === providerId)
    return provider?.name || providerId
  }

  const modelColumns = [
    { title: 'Provider', dataIndex: 'providerId', key: 'providerId', render: (v: string) => getProviderName(v) },
    { title: 'LiteLLM ID', dataIndex: 'litellmId', key: 'litellmId' },
    { title: '能力', dataIndex: 'capabilities', key: 'capabilities' },
    {
      title: '默认',
      dataIndex: 'isDefault',
      key: 'isDefault',
      render: (v: boolean, record: import('../types').ModelInfo) =>
        v ? <Tag color="green">默认</Tag> : <Button size="small" onClick={() => handleSetDefault(record.id)}>设为默认</Button>
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      render: (v: boolean) => v ? <Tag color="green">启用</Tag> : <Tag>禁用</Tag>
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: import('../types').ModelInfo) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => handleEditModel(record)}>编辑</Button>
          <Button size="small" danger onClick={() => handleDeleteModel(record.id)}>删除</Button>
        </Space>
      )
    }
  ]

  const profileColumns = [
    { title: 'ID', dataIndex: 'id', key: 'id' },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '描述', dataIndex: 'description', key: 'description' },
    {
      title: '模型链',
      dataIndex: 'modelChain',
      key: 'modelChain',
      render: (v: string) => (
        <Tooltip title={v}>
          <span style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }}>
            {v}
          </span>
        </Tooltip>
      )
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      render: (v: boolean) => v ? <Tag color="green">启用</Tag> : <Tag>禁用</Tag>
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: import('../types').ModelProfile) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => handleEditProfile(record)}>编辑</Button>
          <Button size="small" danger onClick={() => handleDeleteProfile(record.id)}>删除</Button>
        </Space>
      )
    }
  ]

  return (
    <div className="config-panel">
      <Spin spinning={loading}>
        <Tabs activeKey={activeTab} onChange={setActiveTab}>
          <Tabs.TabPane tab="模型场景" key="profiles">
            <Card
              title="模型场景配置 (Profiles)"
              extra={
                <Space>
                  <Button onClick={handleOpenGlobalDefault}>
                    设为全局默认
                  </Button>
                  <Button type="primary" icon={<PlusOutlined />} onClick={handleCreateProfile}>
                    添加场景
                  </Button>
                </Space>
              }
            >
              <Table
                dataSource={profiles}
                columns={profileColumns}
                rowKey="id"
                pagination={false}
                size="small"
              />
              <Alert
                message="关于模型场景"
                description={
                  <div>
                    <p><strong>smart</strong>: 深度思考，适合复杂任务</p>
                    <p><strong>fast</strong>: 快速响应，成本低</p>
                    <p><strong>coding</strong>: 编程专用，支持长上下文</p>
                    <p><strong>summarize</strong>: 总结归纳，适合记忆维护</p>
                    <p>模型链格式: model1,model2,model3 (按优先级顺序)</p>
                  </div>
                }
                type="info"
                style={{ marginTop: 16 }}
              />
            </Card>
          </Tabs.TabPane>

          <Tabs.TabPane tab="模型管理" key="models">
            <Card
              title="模型管理"
              extra={
                <Button type="primary" icon={<PlusOutlined />} onClick={handleCreateModel}>
                  手动添加模型
                </Button>
              }
            >
              <Table
                dataSource={models}
                columns={modelColumns}
                rowKey="id"
                pagination={false}
                size="small"
              />
            </Card>
          </Tabs.TabPane>
        </Tabs>
      </Spin>

      {/* Model Modal */}
      <Modal
        title={editingModel ? '编辑模型' : '添加模型'}
        open={modelModalVisible}
        onOk={() => modelForm.submit()}
        onCancel={() => setModelModalVisible(false)}
        width={600}
      >
        <Form form={modelForm} layout="vertical" onFinish={handleSaveModel}>
          <Form.Item name="providerId" label="Provider" rules={[{ required: true, message: '请选择 Provider' }]}>
            <Select
              placeholder="选择 Provider"
              onChange={(v) => {
                handleDiscoverLitellmOptions(v)
                // ollama、vllm 需带前缀，切换时自动填入
                const prefix = v === 'ollama' ? 'ollama/' : v === 'vllm' ? 'vllm/' : ''
                modelForm.setFieldValue('litellmId', prefix || undefined)
              }}
            >
              {providers.map(p => (
                <Select.Option key={p.id} value={p.id}>{p.name}</Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            name="litellmId"
            label="LiteLLM ID"
            rules={[{ required: true, message: '请输入或选择 LiteLLM ID' }]}
            help="选择 Provider 后自动发现下拉可选，或自行输入，如: anthropic/claude-opus-4-6"
          >
            <AutoComplete
              placeholder="anthropic/claude-opus-4-6"
              options={litellmOptionsLoading ? [] : litellmOptions}
              filterOption={(inputValue, option) =>
                (option?.value ?? '').toLowerCase().includes(inputValue.toLowerCase())
              }
              onSelect={(value: string) => {
                const m = discoveredModelsMap[value]
                if (m) {
                  modelForm.setFieldsValue({
                    contextWindow: m.contextWindow,
                    capabilities: (m.capabilities || []).join(',')
                  })
                }
              }}
            />
          </Form.Item>
          <Form.Item name="capabilities" label="能力标签" help="逗号分隔，如: tools,vision,thinking">
            <Input placeholder="tools,vision" />
          </Form.Item>
          <Form.Item name="contextWindow" label="上下文窗口" rules={[{ required: true }]}>
            <InputNumber min={1000} style={{ width: '100%' }} />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="costRank" label="成本等级 (1-10, 1=便宜)">
                <InputNumber min={1} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="qualityRank" label="质量等级 (1-10, 1=最好)">
                <InputNumber min={1} max={10} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="enabled" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Profile Modal */}
      <Modal
        title={editingProfile ? '编辑场景' : '添加场景'}
        open={profileModalVisible}
        onOk={() => profileForm.submit()}
        onCancel={() => setProfileModalVisible(false)}
        width={600}
      >
        <Form form={profileForm} layout="vertical" onFinish={handleSaveProfile}>
          {!editingProfile && (
            <Form.Item name="id" label="场景 ID" rules={[{ required: true }]} help="唯一标识，如: smart, fast, coding">
              <Input placeholder="my-custom-profile" />
            </Form.Item>
          )}
          <Form.Item name="name" label="显示名称" rules={[{ required: true }]}>
            <Input placeholder="如: 深度思考" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="描述该场景适合什么任务" />
          </Form.Item>
          <Form.Item
            name="modelChain"
            label="模型链"
            rules={[{ required: true, message: '请至少选择一个模型' }]}
            help="按优先级顺序选择模型，排在最前的优先使用"
          >
            <Select
              mode="multiple"
              placeholder="请选择模型（按优先级排序）"
              options={models.filter(m => m.enabled).map(m => ({
                value: m.id,
                label: `${m.name} (${m.id})`,
              }))}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="enabled" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Global Default Modal */}
      <Modal
        title="设为全局默认模型"
        open={globalDefaultModalVisible}
        onOk={() => globalDefaultForm.submit()}
        onCancel={() => setGlobalDefaultModalVisible(false)}
        width={500}
      >
        <Form form={globalDefaultForm} layout="vertical" onFinish={handleApplyGlobalDefault}>
          <Alert
            message="一键设置默认模型"
            description="选择一个或多个模型，应用到 smart、fast、coding、summarize 所有场景。按选择顺序确定优先级。"
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
          />
          <Form.Item
            name="modelChain"
            label="选择模型（按优先级排序）"
            rules={[{ required: true, message: '请至少选择一个模型' }]}
          >
            <Select
              mode="multiple"
              placeholder="请选择模型"
              options={models.filter(m => m.enabled).map(m => ({
                value: m.id,
                label: `${m.name} (${m.id})`,
              }))}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

function McpConfig() {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [mcps, setMcps] = useState<McpServer[]>([])
  const [modalVisible, setModalVisible] = useState(false)
  const [editingMcp, setEditingMcp] = useState<McpServer | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [discoveringToolsId, setDiscoveringToolsId] = useState<string | null>(null)
  const [jsonInput, setJsonInput] = useState('')
  const [jsonModalVisible, setJsonModalVisible] = useState(false)
  const jsonInputRef = useRef<HTMLInputElement>(null)
  const [form] = Form.useForm()

  useEffect(() => {
    loadMcps()
  }, [])

  const loadMcps = async () => {
    try {
      setLoading(true)
      const data = await api.getMcps()
      setMcps(data || [])
    } catch (error) {
      message.error(t('config.mcp.loadFailed'))
      console.error(error)
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = () => {
    setEditingMcp(null)
    form.resetFields()
    form.setFieldsValue({ transport: 'stdio', enabled: true })
    setModalVisible(true)
  }

  const handleEdit = (mcp: McpServer) => {
    setEditingMcp(mcp)
    form.setFieldsValue({
      id: mcp.id,
      name: mcp.name,
      transport: mcp.transport,
      command: mcp.command,
      args: mcp.args?.join(' ') || '',
      envText: (() => {
        const raw = mcp.env
        if (typeof raw === 'string') {
          // Already a JSON string — parse it back to object for display
          try { return Object.entries(JSON.parse(raw)).map(([k, v]) => `${k}=${v}`).join('\n') } catch { return '' }
        }
        return raw ? Object.entries(raw).map(([k, v]) => `${k}=${v}`).join('\n') : ''
      })(),
      headersText: (() => {
        const raw = mcp.headers
        if (typeof raw === 'string') {
          try { return Object.entries(JSON.parse(raw)).map(([k, v]) => `${k}=${v}`).join('\n') } catch { return '' }
        }
        return raw ? Object.entries(raw).map(([k, v]) => `${k}=${v}`).join('\n') : ''
      })(),
      scopeText: mcp.scope?.join('\n') || '',
      url: mcp.url,
      enabled: mcp.enabled,
    })
    setModalVisible(true)
  }

  const handleDelete = (mcp: McpServer) => {
    console.log('Delete clicked for MCP:', mcp)
    Modal.confirm({
      title: t('config.mcp.confirmDelete'),
      content: t('config.mcp.confirmDeleteContent', { name: mcp.name }),
      onOk: async () => {
        try {
          console.log('Calling deleteMcp with id:', mcp.id)
          await api.deleteMcp(mcp.id)
          message.success(t('config.mcp.deleted'))
          loadMcps()
        } catch (error) {
          console.error('Delete MCP error:', error)
          message.error(t('config.mcp.deleteFailed'))
        }
      },
    })
  }

  const handleTest = async (mcpId: string) => {
    try {
      setTestingId(mcpId)
      const result = await api.testMcp(mcpId)
      if (result.connected) {
        message.success(result.message || t('config.mcp.connected'))
      } else {
        message.warning(result.message || t('config.mcp.connectFailed'))
      }
    } catch (error) {
      message.error(t('config.mcp.testFailed'))
    } finally {
      setTestingId(null)
    }
  }

  const mapTypeToTransport = (t: string): McpServer['transport'] => {
    const lower = (t || '').toLowerCase()
    if (lower === 'stdio') return 'stdio'
    if (lower === 'sse') return 'sse'
    if (lower === 'streamablehttp' || lower === 'streamable-http') return 'streamable_http'
    return 'http'
  }

  const normalizeMcpItem = (raw: unknown, explicitId?: string): McpServer | null => {
    if (!raw || typeof raw !== 'object') return null
    const o = raw as Record<string, unknown>
    let id = explicitId || (typeof o.id === 'string' ? o.id.trim() : undefined)
    // Sanitize ID: replace invalid chars with underscore (same as backend)
    if (id) {
      id = id.replace(/[^a-zA-Z0-9._-]/g, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '')
    }
    let name = typeof o.name === 'string' ? o.name.trim() : undefined
    const transportRaw = typeof o.transport === 'string' ? o.transport : typeof o.type === 'string' ? o.type : 'stdio'
    const transport = mapTypeToTransport(transportRaw)
    let command: string | undefined
    let args: string[] | undefined
    let url: string | undefined
    if (transport === 'stdio') {
      command = typeof o.command === 'string' ? o.command.trim() : undefined
      if (!command) return null
      args = Array.isArray(o.args) ? o.args.filter((a): a is string => typeof a === 'string') : undefined
    } else {
      url = typeof o.url === 'string' ? o.url.trim() : undefined
      if (!url) return null
    }
    if (!name) name = id || 'unnamed'
    if (!id) id = name.replace(/\s+/g, '-').toLowerCase().replace(/[^a-z0-9._-]/g, '') || 'mcp'
    // Extract env, headers, and tools
    const env = (o.env && typeof o.env === 'object') ? o.env as Record<string, string> : undefined
    const headers = (o.headers && typeof o.headers === 'object') ? o.headers as Record<string, string> : undefined
    const tools = Array.isArray(o.tools) ? o.tools as { name: string; description?: string }[] : undefined
    const scope = Array.isArray(o.scope) ? o.scope.filter((s): s is string => typeof s === 'string') : undefined
    return { id, name, transport, command, args, url, enabled: o.enabled !== false, env, headers, tools, scope }
  }

  const normOne = (raw: unknown) => normalizeMcpItem(raw)

  const parseJsonToMcps = (parsed: unknown): McpServer[] => {
    if (!parsed || typeof parsed !== 'object') return []
    const obj = parsed as Record<string, unknown>
    if (Array.isArray(parsed)) {
      return parsed.map(normOne).filter((m): m is McpServer => m !== null)
    }
    if ('mcps' in obj && Array.isArray(obj.mcps)) {
      return (obj.mcps as unknown[]).map(normOne).filter((m): m is McpServer => m !== null)
    }
    if ('mcpServers' in obj && obj.mcpServers && typeof obj.mcpServers === 'object') {
      const servers = obj.mcpServers as Record<string, unknown>
      return Object.entries(servers).map(([id, config]) => normalizeMcpItem(config, id)).filter((m): m is McpServer => m !== null)
    }
    return [normalizeMcpItem(parsed)].filter((m): m is McpServer => m !== null)
  }

  const handleJsonImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    const reader = new FileReader()
    reader.onload = async () => {
      try {
        const text = reader.result as string
        const parsed = JSON.parse(text) as unknown
        const normalized = parseJsonToMcps(parsed)
        if (normalized.length === 0) {
          message.warning(t('config.mcp.noValidMcp'))
          return
        }
        setImporting(true)
        const existingList = await api.getMcps()
        const existingIds = new Set(existingList?.map(x => x.id) ?? [])
        let ok = 0
        let fail = 0
        for (const mcp of normalized) {
          try {
            if (existingIds.has(mcp.id)) {
              await api.updateMcp(mcp.id, mcp)
              ok++
            } else {
              await api.createMcp(mcp)
              ok++
              existingIds.add(mcp.id)
            }
          } catch {
            fail++
          }
        }
        if (ok > 0) {
          message.success(fail > 0 ? t('config.mcp.importPartial', { ok, fail }) : t('config.mcp.importSuccess', { ok }))
          loadMcps()
        }
        if (fail > 0 && ok === 0) {
          message.error(t('config.mcp.importFailed', { fail }))
        }
      } catch (err) {
        message.error(t('config.mcp.jsonParseFailed'))
        console.error(err)
      } finally {
        setImporting(false)
      }
    }
    reader.readAsText(file)
  }

  const handleJsonGenerate = async () => {
    const text = jsonInput.trim()
    if (!text) {
      message.warning(t('config.mcp.enterJson'))
      return
    }
    try {
      const parsed = JSON.parse(text) as unknown
      const normalized = parseJsonToMcps(parsed)
      if (normalized.length === 0) {
        message.warning('JSON 中未找到有效的 MCP 配置')
        return
      }
      setImporting(true)
      const existingList = await api.getMcps()
      const existingIds = new Set(existingList?.map(x => x.id) ?? [])
      let ok = 0
      let fail = 0
      for (const mcp of normalized) {
        try {
          if (existingIds.has(mcp.id)) {
            await api.updateMcp(mcp.id, mcp)
            ok++
          } else {
            await api.createMcp(mcp)
            ok++
            existingIds.add(mcp.id)
          }
        } catch {
          fail++
        }
      }
      if (ok > 0) {
        message.success(fail > 0 ? t('config.mcp.generatePartial', { ok, fail }) : t('config.mcp.generateSuccess', { ok }))
        setJsonInput('')
        setJsonModalVisible(false)
        loadMcps()
      }
      if (fail > 0 && ok === 0) {
        message.error(t('config.mcp.generateFailed', { fail }))
      }
    } catch (err) {
      message.error(t('config.mcp.jsonFormatError'))
      console.error(err)
    } finally {
      setImporting(false)
    }
  }

  const handleSave = async () => {
    try {
      setSaving(true)
      const values = await form.validateFields()
      const argsStr = values.args
      const args = typeof argsStr === 'string' && argsStr.trim()
        ? argsStr.trim().split(/\s+/)
        : []
      // Parse env: each line is KEY=value
      const envText = values.envText
      const env: Record<string, string> = {}
      if (typeof envText === 'string' && envText.trim()) {
        for (const line of envText.split('\n')) {
          const trimmed = line.trim()
          if (!trimmed) continue
          const eqIdx = trimmed.indexOf('=')
          if (eqIdx > 0) {
            env[trimmed.slice(0, eqIdx)] = trimmed.slice(eqIdx + 1)
          }
        }
      }
      // Parse headers: each line is KEY=value
      const headersText = values.headersText
      const headers: Record<string, string> = {}
      if (typeof headersText === 'string' && headersText.trim()) {
        for (const line of headersText.split('\n')) {
          const trimmed = line.trim()
          if (!trimmed) continue
          const eqIdx = trimmed.indexOf('=')
          if (eqIdx > 0) {
            headers[trimmed.slice(0, eqIdx)] = trimmed.slice(eqIdx + 1)
          }
        }
      }
      const payload = {
        id: values.id?.trim() || undefined,
        name: values.name?.trim(),
        transport: values.transport,
        command: values.transport === 'stdio' ? values.command : undefined,
        args: values.transport === 'stdio' ? args : undefined,
        url: values.transport !== 'stdio' ? values.url : undefined,
        enabled: values.enabled ?? true,
        env: Object.keys(env).length > 0 ? env : undefined,
        headers: Object.keys(headers).length > 0 ? headers : undefined,
        scope: (() => {
          const text = values.scopeText
          if (typeof text !== 'string' || !text.trim()) return []
          return text.split(/[\n,]/).map(s => s.trim()).filter(Boolean)
        })(),
      }
      if (editingMcp) {
        await api.updateMcp(editingMcp.id, payload)
        message.success(t('config.mcp.updated'))
      } else {
        await api.createMcp(payload)
        message.success(t('config.mcp.created'))
      }
      setModalVisible(false)
      // 保存后快速刷新 MCP 列表（不 discover 工具，避免连接所有服务器）
      const data = await api.getMcps()
      setMcps(data || [])
    } catch (error) {
      console.error(error)
      message.error(t('config.mcp.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="config-panel">
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <Text type="secondary">
          {t('config.mcp.description')}支持 stdio/http/sse/streamable_http、导入 JSON 文件、或从 JSON 生成（含 Cursor 格式 mcpServers）。
        </Text>
        <Space>
          <input
            type="file"
            ref={jsonInputRef}
            accept=".json,application/json"
            style={{ display: 'none' }}
            onChange={handleJsonImport}
          />
          <Button
            icon={<UploadOutlined />}
            loading={importing}
            onClick={() => jsonInputRef.current?.click()}
          >
            {t('config.mcp.importJson')}
          </Button>
          <Button icon={<PlusOutlined />} onClick={() => setJsonModalVisible(true)}>
            {t('config.mcp.jsonGenerate')}
          </Button>
          <Button icon={<ReloadOutlined />} onClick={async () => {
            setLoading(true)
            try {
              const data = await api.getMcpsWithTools()
              setMcps(data || [])
            } catch (e) {
              console.error(e)
              message.error(t('config.mcp.loadFailed'))
            } finally {
              setLoading(false)
            }
          }} loading={loading}>{t('config.refresh')}</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>{t('config.mcp.add')}</Button>
        </Space>
      </div>

      <Modal
        title={t('config.mcp.generateFromJson')}
        open={jsonModalVisible}
        onCancel={() => setJsonModalVisible(false)}
        onOk={handleJsonGenerate}
        okText={t('config.mcp.generateMcp')}
        confirmLoading={importing}
        width={640}
        destroyOnClose
      >
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          <Text type="secondary">
            {t('config.mcp.jsonFormatHint')}
          </Text>
          <TextArea
            value={jsonInput}
            onChange={e => setJsonInput(e.target.value)}
            placeholder={t('config.mcp.jsonPlaceholder')}
            rows={8}
            style={{ fontFamily: 'monospace', fontSize: 12 }}
          />
        </Space>
      </Modal>

      <List
        grid={{ gutter: 16, column: 2 }}
        dataSource={mcps}
        loading={loading}
        locale={{ emptyText: t('config.mcp.emptyText') }}
        renderItem={(item: McpServer) => (
          <List.Item>
            <Card
              title={
                <Space>
                  <span>{item.name}</span>
                  <Tag>{item.transport}</Tag>
                  {item.enabled && <Tag color="success">{t('config.enabled')}</Tag>}
                  {!item.enabled && <Tag>{t('config.disabled')}</Tag>}
                  <Tag color={item.tools && item.tools.length > 0 ? 'blue' : 'default'}>
                    {item.tools?.length ?? 0} 个工具
                  </Tag>
                </Space>
              }
              extra={
                <Space>
                  <Button
                    type="text"
                    size="small"
                    onClick={() => handleTest(item.id)}
                    loading={testingId === item.id}
                  >
                    {t('config.mcp.test')}
                  </Button>
                  <Button type="text" icon={<EditOutlined />} onClick={() => handleEdit(item)} />
                  <Button type="text" danger icon={<DeleteOutlined />} onClick={() => handleDelete(item)} />
                </Space>
              }
            >
              <Space direction="vertical" style={{ width: '100%' }} size="small">
                {item.transport === 'stdio' && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {item.command} {item.args?.join(' ') || ''}
                  </Text>
                )}
                {item.transport !== 'stdio' && item.url && (
                  <Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                    {item.url}
                  </Text>
                )}
              </Space>
            </Card>
          </List.Item>
        )}
      />

      <Modal
        title={editingMcp ? t('config.mcp.edit') : t('config.mcp.add')}
        open={modalVisible}
        onOk={handleSave}
        onCancel={() => setModalVisible(false)}
        confirmLoading={saving}
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="id"
            label="ID"
            help={t('config.mcp.idHelp') || '唯一标识，只支持字母、数字、下划线、连字符、点（留空则自动生成）'}
            rules={[
              {
                pattern: /^$|^[a-zA-Z0-9._-]*$/,
                message: 'ID 只能包含字母、数字、下划线、连字符、点',
              },
            ]}
          >
            <Input
              placeholder="my-mcp-server"
              disabled={!!editingMcp}
              onChange={(e) => {
                // Auto-replace non-ASCII chars in ID field
                const val = e.target.value.replace(/[^a-zA-Z0-9._-]/g, '_')
                e.target.value = val
              }}
            />
          </Form.Item>
          <Form.Item name="name" label={t('config.mcp.nameLabel')} rules={[{ required: true, message: t('config.mcp.nameRequired') }]}>
            <Input placeholder={t('config.mcp.namePlaceholder')} />
          </Form.Item>
          <Form.Item name="transport" label={t('config.mcp.transportLabel')} rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'stdio', label: t('config.mcp.transportStdio') },
                { value: 'http', label: t('config.mcp.transportHttp') },
                { value: 'sse', label: t('config.mcp.transportSse') },
                { value: 'streamable_http', label: t('config.mcp.transportStreamable') },
              ]}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, curr) => prev.transport !== curr.transport}>
            {({ getFieldValue }) =>
              ['stdio'].includes(getFieldValue('transport') || '') ? (
                <>
                  <Form.Item name="command" label="Command" rules={[{ required: true, message: t('config.mcp.commandRequired') }]}>
                    <Input placeholder="npx" />
                  </Form.Item>
                  <Form.Item name="args" label="Args (空格分隔)">
                    <Input placeholder="-y @modelcontextprotocol/server-filesystem" />
                  </Form.Item>
                  <Form.Item
                    name="envText"
                    label="Env (每行 KEY=value)"
                  >
                    <Input.TextArea
                      placeholder={"JIRA_HOST=your-domain.atlassian.net\nJIRA_API_TOKEN=xxx"}
                      rows={3}
                      style={{ fontFamily: 'monospace', fontSize: 12 }}
                    />
                  </Form.Item>
                </>
              ) : (
                <>
                <Form.Item name="url" label="URL" rules={[{ required: true, message: t('config.mcp.urlRequired') }]}>
                    <Input placeholder="http://localhost:6788/mcp" />
                </Form.Item>
                <Form.Item
                  name="headersText"
                  label="Headers (每行 KEY=value)"
                >
                  <Input.TextArea
                    placeholder={"Authorization=Bearer xxx\nX-API-Key=xxx"}
                    rows={3}
                    style={{ fontFamily: 'monospace', fontSize: 12 }}
                  />
                </Form.Item>
                <Form.Item
                  name="scopeText"
                  label={t('config.mcp.scopeLabel', 'Scope 关键词 (触发意图匹配)')}
                  extra="消息中出现这些关键词时激活该 MCP 的工具，每行一个关键词"
                >
                  <Input.TextArea
                    placeholder={"jira\nissue\nbug\ntask"}
                    rows={2}
                    style={{ fontFamily: 'monospace', fontSize: 12 }}
                  />
                </Form.Item>
                </>
              )
            }
          </Form.Item>
          <Form.Item name="enabled" valuePropName="checked" label={t('config.channel.enabled')}>
            <Switch />
          </Form.Item>

          {/* 工具列表展示 - 仅在编辑模式下显示 */}
          {editingMcp && (
            <div style={{ marginTop: 16, padding: 12, background: '#fafafa', borderRadius: 6, border: '1px solid #f0f0f0' }}>
              <Space style={{ marginBottom: 8 }}>
                <Text strong style={{ fontSize: 14 }}>发现工具 ({editingMcp.tools?.length ?? 0}个)</Text>
                <Button
                  size="small"
                  icon={<ReloadOutlined spin={discoveringToolsId === editingMcp.id} />}
                  loading={discoveringToolsId === editingMcp.id}
                  onClick={async () => {
                    if (!editingMcp.id) return
                    setDiscoveringToolsId(editingMcp.id)
                    try {
                      const result = await api.discoverMcpTools(editingMcp.id)
                      setEditingMcp(prev => prev ? { ...prev, tools: result.tools || [] } : null)
                      message.success(t('config.mcp.toolsDiscovered'))
                    } catch (e) {
                      console.error(e)
                      message.error(t('config.mcp.discoverFailed'))
                    } finally {
                      setDiscoveringToolsId(null)
                    }
                  }}
                >
                  {discoveringToolsId === editingMcp.id ? '连接中…' : '刷新'}
                </Button>
              </Space>
              {editingMcp.tools && editingMcp.tools.length > 0 ? (
                <div style={{ maxHeight: 300, overflow: 'auto' }}>
                  {editingMcp.tools.map((tool, index) => (
                    <div key={index} style={{ marginBottom: 10, padding: '8px 10px', background: '#fff', borderRadius: 4, border: '1px solid #f0f0f0' }}>
                      <Text code style={{ fontSize: 12 }}>{tool.name}</Text>
                      {tool.description && (
                        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
                          {tool.description.length > 200 ? tool.description.slice(0, 200) + '...' : tool.description}
                        </Text>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  暂未发现工具（可能是连接超时或 MCP 服务器未启动）
                </Text>
              )}
            </div>
          )}
        </Form>
      </Modal>
    </div>
  )
}

function SkillsConfig() {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [skills, setSkills] = useState<InstalledSkill[]>([])
  const folderInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    loadSkills()
  }, [])

  const loadSkills = async () => {
    try {
      setLoading(true)
      const data = await api.getInstalledSkills()
      setSkills(data || [])
    } catch (error) {
      console.error(error)
      message.error(t('config.skillsPanel.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleFolderSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return
    const formData = new FormData()
    for (let i = 0; i < files.length; i++) {
      const f = files[i]
      const path = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
      formData.append('path', path)
      formData.append('file', f)
    }
    e.target.value = ''
    await doUpload(formData)
  }

  const doUpload = async (formData: FormData) => {
    try {
      setUploading(true)
      await api.uploadSkill(formData)
      message.success(t('config.skillsPanel.uploadSuccess'))
      loadSkills()
    } catch (error) {
      console.error(error)
        message.error(error instanceof Error ? error.message : t('config.skillsPanel.uploadFailed'))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="config-panel skills-config-panel">
      <div style={{ marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <input
          type="file"
          ref={folderInputRef}
          {...({ webkitdirectory: '', directory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
          multiple
          style={{ display: 'none' }}
          onChange={handleFolderSelect}
        />
        <Button
          type="primary"
          icon={<FolderOpenOutlined />}
          loading={uploading}
          onClick={() => folderInputRef.current?.click()}
        >
          {t('config.skillsPanel.selectFolder')}
        </Button>
        <Text type="secondary" style={{ alignSelf: 'center' }}>
          {t('config.skillsPanel.folderHint')}
        </Text>
      </div>
      <div className="skills-list-scroll">
        <List
          grid={{ gutter: 16, column: 2 }}
          dataSource={skills}
          loading={loading}
        renderItem={(item: InstalledSkill) => (
          <List.Item>
            <Card 
              title={
                <Space>
                  <span>{item.name}</span>
                  <Tag>{item.version}</Tag>
                  {item.author && <Tag color="blue">@{item.author}</Tag>}
                </Space>
              }
              extra={<Tag color={item.enabled ? 'success' : 'default'}>{item.enabled ? 'Enabled' : 'Disabled'}</Tag>}
            >
              <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>{item.description}</Text>
              {item.tags && item.tags.length > 0 && (
                <Space size={4} wrap>
                  {item.tags.map((tag: string) => <Tag key={tag} style={{ fontSize: 10 }}>{tag}</Tag>)}
                </Space>
              )}
            </Card>
          </List.Item>
        )}
        />
      </div>
    </div>
  )
}
