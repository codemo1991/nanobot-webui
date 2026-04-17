import React, { useState, useEffect } from 'react'
import { Modal, Tabs, List, Input, Form, Select, Button, message, Tag, Space } from 'antd'
import { api } from '../../api'
import { PROVIDER_TYPE_ICONS } from './constants'

const SYSTEM_PROVIDERS: Array<{
  id: string
  displayName: string
  providerType: string
  apiBase: string
  description?: string
}> = [
  { id: 'openai', displayName: 'OpenAI', providerType: 'openai', apiBase: 'https://api.openai.com/v1', description: 'GPT-4o, GPT-4o-mini, o1 系列' },
  { id: 'anthropic', displayName: 'Anthropic', providerType: 'anthropic', apiBase: 'https://api.anthropic.com', description: 'Claude 3.5 Sonnet, Claude 3 Opus' },
  { id: 'deepseek', displayName: 'DeepSeek', providerType: 'deepseek', apiBase: 'https://api.deepseek.com/v1', description: 'DeepSeek V3, DeepSeek R1' },
  { id: 'gemini', displayName: 'Google Gemini', providerType: 'gemini', apiBase: 'https://generativelanguage.googleapis.com/v1beta', description: 'Gemini 2.0 Flash, Gemini 1.5 Pro' },
  { id: 'ollama', displayName: 'Ollama (本地)', providerType: 'ollama', apiBase: 'http://localhost:11434', description: '本地运行的开源模型' },
  { id: 'vllm', displayName: 'vLLM (本地)', providerType: 'vllm', apiBase: 'http://localhost:8000/v1', description: '本地部署的高性能推理服务' },
  { id: 'azure-openai', displayName: 'Azure OpenAI', providerType: 'azure_openai', apiBase: 'https://YOUR_RESOURCE.openai.azure.com', description: 'Azure 托管的 OpenAI 模型' },
  { id: 'openrouter', displayName: 'OpenRouter', providerType: 'openrouter', apiBase: 'https://openrouter.ai/api/v1', description: '聚合多个模型供应商' },
  { id: 'together', displayName: 'Together AI', providerType: 'together', apiBase: 'https://api.together.xyz/v1', description: '开源模型聚合平台' },
  { id: 'fireworks', displayName: 'Fireworks AI', providerType: 'fireworks', apiBase: 'https://api.fireworks.ai/inference/v1', description: '高速推理服务' },
  { id: 'new-api', displayName: 'New API (自定义)', providerType: 'new_api', apiBase: '', description: '兼容 OpenAI API 的任意服务' },
]



interface AddProviderModalProps {
  open: boolean
  onClose: () => void
  onAdded: () => void
}

export const AddProviderModal: React.FC<AddProviderModalProps> = ({ open, onClose, onAdded }) => {
  const [tab, setTab] = useState('system')
  const [systemProviders, setSystemProviders] = useState<typeof SYSTEM_PROVIDERS>([])
  const [search, setSearch] = useState('')
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [addingId, setAddingId] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setSystemProviders(SYSTEM_PROVIDERS)
    }
  }, [open])

  const filtered = systemProviders.filter(p =>
    p.displayName.toLowerCase().includes(search.toLowerCase()) ||
    p.id.toLowerCase().includes(search.toLowerCase())
  )

  const handleAddSystem = async (sp: typeof SYSTEM_PROVIDERS[0]) => {
    setAddingId(sp.id)
    try {
      await api.createProvider({
        id: sp.id,
        name: sp.displayName,
        displayName: sp.displayName,
        providerType: sp.providerType,
        apiBase: sp.apiBase,
        apiKey: '',
        enabled: false,
      })
      message.success(`已添加 ${sp.displayName}`)
      onAdded()
      onClose()
    } catch (e: any) {
      message.error(`添加失败: ${e.message}`)
    } finally {
      setAddingId(null)
    }
  }

  const handleAddCustom = async (values: any) => {
    setSaving(true)
    try {
      const providerType = values.providerType || 'openai'
      await api.createProvider({
        id: values.id || values.name.toLowerCase().replace(/\s+/g, '-'),
        name: values.name,
        displayName: values.displayName || values.name,
        providerType,
        apiBase: values.apiBase || '',
        apiKey: values.apiKey || '',
        enabled: false,
      })
      message.success('已添加自定义 Provider')
      onAdded()
      onClose()
      form.resetFields()
    } catch (e: any) {
      message.error(`添加失败: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="添加 Provider"
      open={open}
      onCancel={onClose}
      footer={null}
      width={600}
      destroyOnClose
    >
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'system',
            label: '从系统预置选择',
            children: (
              <>
                <Input.Search
                  placeholder="搜索..."
                  onChange={e => setSearch(e.target.value)}
                  style={{ marginBottom: 12 }}
                  allowClear
                />
                <List
                  dataSource={filtered}
                  locale={{ emptyText: '未找到匹配的预置 Provider' }}
                  renderItem={(p) => (
                    <List.Item
                      style={{ cursor: 'pointer' }}
                      actions={[
                        <Button
                          key="add"
                          size="small"
                          type="primary"
                          loading={addingId === p.id}
                          onClick={() => handleAddSystem(p)}
                        >
                          添加
                        </Button>
                      ]}
                    >
                      <List.Item.Meta
                        avatar={
                          <span style={{ fontSize: 20 }}>
                            {PROVIDER_TYPE_ICONS[p.providerType] || '🔗'}
                          </span>
                        }
                        title={<Space>{p.displayName}<Tag style={{ fontSize: 10 }}>{p.providerType}</Tag></Space>}
                        description={
                          <Space direction="vertical" size={0}>
                            <span style={{ color: '#666', fontSize: 12 }}>{p.description}</span>
                          </Space>
                        }
                      />
                    </List.Item>
                  )}
                />
              </>
            )
          },
          {
            key: 'custom',
            label: '自定义',
            children: (
              <Form
                form={form}
                onFinish={handleAddCustom}
                layout="vertical"
                style={{ marginTop: 16 }}
                initialValues={{ providerType: 'openai' }}
              >
                <Form.Item name="name" label="Provider ID" rules={[{ required: true, message: '请输入 Provider ID' }]}>
                  <Input placeholder="my-provider" />
                </Form.Item>
                <Form.Item name="displayName" label="显示名称">
                  <Input placeholder="My Provider" />
                </Form.Item>
                <Form.Item name="providerType" label="类型" rules={[{ required: true }]}>
                  <Select options={[
                    { value: 'openai', label: 'OpenAI (兼容)' },
                    { value: 'anthropic', label: 'Anthropic' },
                    { value: 'deepseek', label: 'DeepSeek' },
                    { value: 'gemini', label: 'Gemini' },
                    { value: 'azure_openai', label: 'Azure OpenAI' },
                    { value: 'ollama', label: 'Ollama (本地)' },
                    { value: 'vllm', label: 'vLLM (本地)' },
                    { value: 'new_api', label: 'New API (自定义)' },
                    { value: 'openrouter', label: 'OpenRouter' },
                    { value: 'together', label: 'Together AI' },
                    { value: 'fireworks', label: 'Fireworks AI' },
                  ]} />
                </Form.Item>
                <Form.Item name="apiBase" label="API Base URL" rules={[{ required: true, message: '请输入 API Base URL' }]}>
                  <Input placeholder="https://api.example.com/v1" />
                </Form.Item>
                <Form.Item name="apiKey" label="API Key">
                  <Input.Password placeholder="sk-..." />
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={saving}>添加</Button>
              </Form>
            )
          }
        ]}
      />
    </Modal>
  )
}
