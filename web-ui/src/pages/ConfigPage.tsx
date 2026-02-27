import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Form, Input, InputNumber, Switch, Button, Modal, Select, Card, Space, Tag, List, message, Tabs, Spin, Typography, Row, Col } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, SettingOutlined, FolderOpenOutlined, UploadOutlined, SwapOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { ChannelsConfig, Provider, InstalledSkill, McpServer, AgentConfig, WebConcurrencyConfig, WebMemoryConfig } from '../types'
import AgentTemplatePage from './AgentTemplatePage'
import './ConfigPage.css'

const { Title, Text } = Typography
const { TextArea } = Input

export default function ConfigPage() {
  const [activeTab, setActiveTab] = useState('providers')

  const { t } = useTranslation()
  const items = [
    { key: 'channels', label: t('config.channels'), children: <ChannelsConfig /> },
    { key: 'providers', label: t('config.providers'), children: <ProvidersConfig /> },
    { key: 'models', label: t('config.models'), children: <ModelsConfig /> },
    { key: 'mcps', label: t('config.mcps'), children: <McpConfig /> },
    { key: 'skills', label: t('config.skills'), children: <SkillsConfig /> },
    { key: 'agent-templates', label: 'Agent 模板', children: <AgentTemplatePage /> },
    { key: 'system', label: t('config.system'), children: <SystemConfig /> },
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
      apiKey: '', // Don't show existing key
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
        renderItem={item => (
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
        enableSmartParallel: data.enable_smart_parallel !== false,
        smartParallelModel: data.smart_parallel_model || '',
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
        enableSmartParallel: values.enableSmartParallel,
        smartParallelModel: values.smartParallelModel || undefined,
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
              <Col span={8}>
                <Form.Item
                  name="enableSmartParallel"
                  label={t('config.concurrency.enableSmartParallel') || '启用智能并行'}
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
                  name="smartParallelModel"
                  label={t('config.concurrency.smartParallelModel') || '智能并行模型'}
                >
                  <Input placeholder={t('config.concurrency.smartParallelModelPlaceholder') || '留空使用默认模型'} />
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
  const [form] = Form.useForm()

  useEffect(() => {
    loadModel()
  }, [])

  const loadModel = async () => {
    try {
      setLoading(true)
      const data = await api.getModels()
      if (data && data.length > 0) {
        const defaultModel = data.find(m => m.isDefault) || data[0]
        form.setFieldsValue({
          modelName: defaultModel.modelName,
          temperature: defaultModel.parameters?.temperature,
          maxTokens: defaultModel.parameters?.maxTokens,
          qwenImageModel: defaultModel.qwenImageModel ?? '',
          subagentModel: defaultModel.subagentModel ?? '',
        })
      }
    } catch (error) {
      console.error(error)
      message.error(t('config.model.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async (values: any) => {
    try {
      await api.updateModel('default', {
        providerId: values.modelName.split('/')[0] || 'openai',
        modelName: values.modelName,
        parameters: {
          temperature: values.temperature ? Number(values.temperature) : undefined,
          maxTokens: values.maxTokens ? Number(values.maxTokens) : undefined
        },
        enabled: true,
        name: values.modelName,
        qwenImageModel: (values.qwenImageModel ?? '').trim(),
        subagentModel: (values.subagentModel ?? '').trim(),
      })
      message.success(t('config.model.updated'))
      loadModel()
    } catch (error) {
      console.error(error)
      message.error(t('config.updateFailed'))
    }
  }

  return (
    <div className="config-panel">
      <Card title={t('config.model.title')} loading={loading}>
        <Form form={form} layout="vertical" onFinish={handleSave}>
          <Form.Item name="modelName" label={t('config.model.nameLabel')} rules={[{ required: true }]} help={t('config.model.nameHelp')}>
            <Input />
          </Form.Item>

          <Form.Item
            name="subagentModel"
            label="子 Agent 模型"
            help="子 Agent (spawn tool) 使用的模型，留空则与主 Agent 相同。支持视觉模型，如 dashscope/qwen-vl-plus"
          >
            <Input placeholder="留空则与主 Agent 相同，例如：dashscope/qwen-vl-plus" />
          </Form.Item>
          
          <Space>
            <Form.Item name="temperature" label="Temperature">
              <Input type="number" step="0.1" min="0" max="2" />
            </Form.Item>
            <Form.Item name="maxTokens" label="Max Tokens">
              <Input type="number" step="1" min="1" />
            </Form.Item>
          </Space>

          <Form.Item
            name="qwenImageModel"
            label={t('config.model.qwenImageLabel')}
            help={t('config.model.qwenImageHelp')}
          >
            <Input placeholder="qwen-image-plus" />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit">{t('config.save')}</Button>
          </Form.Item>
        </Form>
      </Card>
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
    if (lower === 'streamable_http') return 'streamable_http'
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
    // Extract env and headers
    const env = (o.env && typeof o.env === 'object') ? o.env as Record<string, string> : undefined
    const headers = (o.headers && typeof o.headers === 'object') ? o.headers as Record<string, string> : undefined
    return { id, name, transport, command, args, url, enabled: o.enabled !== false, env, headers }
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
      const values = await form.validateFields()
      const argsStr = values.args
      const args = typeof argsStr === 'string' && argsStr.trim()
        ? argsStr.trim().split(/\s+/)
        : []
      const payload = {
        id: values.id?.trim() || undefined,
        name: values.name?.trim(),
        transport: values.transport,
        command: values.transport === 'stdio' ? values.command : undefined,
        args: values.transport === 'stdio' ? args : undefined,
        url: values.transport !== 'stdio' ? values.url : undefined,
        enabled: values.enabled ?? true,
      }
      if (editingMcp) {
        await api.updateMcp(editingMcp.id, payload)
        message.success(t('config.mcp.updated'))
      } else {
        await api.createMcp(payload)
        message.success(t('config.mcp.created'))
      }
      setModalVisible(false)
      loadMcps()
    } catch (error) {
      console.error(error)
      message.error(t('config.mcp.saveFailed'))
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
        renderItem={item => (
          <List.Item>
            <Card
              title={
                <Space>
                  <span>{item.name}</span>
                  <Tag>{item.transport}</Tag>
                  {item.enabled && <Tag color="success">{t('config.enabled')}</Tag>}
                  {!item.enabled && <Tag>{t('config.disabled')}</Tag>}
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
        width={560}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="id" label="ID" help={t('config.mcp.idHelp')}>
            <Input placeholder="my-mcp-server" disabled={!!editingMcp} />
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
                </>
              ) : (
                <Form.Item name="url" label="URL" rules={[{ required: true, message: t('config.mcp.urlRequired') }]}>
                    <Input placeholder="http://localhost:6788/mcp" />
                </Form.Item>
              )
            }
          </Form.Item>
          <Form.Item name="enabled" valuePropName="checked" label={t('config.channel.enabled')}>
            <Switch />
          </Form.Item>
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
        renderItem={item => (
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
                  {item.tags.map(tag => <Tag key={tag} style={{ fontSize: 10 }}>{tag}</Tag>)}
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
