import React, { useState, useEffect } from 'react'
import { Form, Input, Button, Switch, message, Tag, Radio, Card, Divider, Popconfirm, Modal, Select, InputNumber, Space } from 'antd'
import { api } from '../../api'
import type { Provider, ModelInfo } from '../../types'
import { DEFAULT_API_BASES } from './constants'

const MODEL_TYPE_LABELS: Record<string, string> = {
  chat: '对话',
  completion: '补全',
  embedding: '嵌入',
  image: '图像生成',
  audio: '音频',
  vision: '视觉理解',
}

const MODEL_TYPE_OPTIONS = [
  { value: 'chat', label: '对话' },
  { value: 'completion', label: '补全' },
  { value: 'embedding', label: '嵌入' },
  { value: 'image', label: '图像生成' },
  { value: 'audio', label: '音频' },
  { value: 'vision', label: '视觉理解' },
]

const CAPABILITY_OPTIONS = [
  { value: 'tools', label: 'Tools (函数调用)' },
  { value: 'vision', label: 'Vision (视觉理解)' },
  { value: 'streaming', label: 'Streaming' },
]

function getDefaultBase(providerType?: string): string {
  return DEFAULT_API_BASES[providerType || 'openai'] ?? DEFAULT_API_BASES.default ?? ''
}

interface ProviderDetailProps {
  provider: Provider
  onUpdate: () => void
}

export const ProviderDetail: React.FC<ProviderDetailProps> = ({ provider, onUpdate }) => {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testingResult, setTestingResult] = useState<'idle' | 'success' | 'error'>('idle')
  const [models, setModels] = useState<ModelInfo[]>([])
  const [discovering, setDiscovering] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [addModelVisible, setAddModelVisible] = useState(false)
  const [addModelSaving, setAddModelSaving] = useState(false)
  const [addModelForm] = Form.useForm()

  const pt = provider.providerType || (provider as any).type || 'openai'

  useEffect(() => {
    form.setFieldsValue({
      displayName: provider.displayName || provider.name,
      providerType: pt,
      apiBase: provider.apiBase || getDefaultBase(pt),
      apiKey: provider.apiKey || '',
      enabled: provider.enabled,
    })
    loadModels()
  }, [provider])

  const loadModels = async () => {
    try {
      const all = await api.getModels(provider.id)
      setModels((all || []))
    } catch {
      // models might use a different endpoint; silently fail
      message.error('加载模型列表失败')
      setModels([])
    }
  }

  const handleSave = async (values: any) => {
    setSaving(true)
    try {
      const ptype = values.providerType || pt
      await api.updateProvider(provider.id, {
        ...values,
        enabled: values.enabled,
        type: ptype, // backend uses `type` field
      })
      message.success('保存成功')
      onUpdate()
    } catch (e: any) {
      message.error(`保存失败: ${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestingResult('idle')
    try {
      const base = form.getFieldValue('apiBase') || getDefaultBase(pt)
      const key = form.getFieldValue('apiKey')
      const headers: Record<string, string> = {}
      if (key) headers['Authorization'] = `Bearer ${key}`

      const resp = await fetch(`${base}/models`, { headers })
      if (resp.ok) {
        setTestingResult('success')
        message.success('连接成功')
      } else {
        setTestingResult('error')
        message.error(`连接失败: HTTP ${resp.status}`)
      }
    } catch {
      setTestingResult('error')
      message.error('连接失败，请检查 API 地址和 Key')
    } finally {
      setTesting(false)
    }
  }

  const handleDelete = async () => {
    try {
      await api.deleteProvider(provider.id)
      message.success('已删除')
      onUpdate()
    } catch (e: any) {
      message.error(`删除失败: ${e.message}`)
    }
  }

  const handleDiscover = async () => {
    setDiscovering(true)
    try {
      await api.discoverModels(provider.id)
      message.success('模型检测完成')
      await loadModels()
    } catch (e: any) {
      message.error(`模型检测失败: ${e.message}`)
    } finally {
      setDiscovering(false)
    }
  }

  const handleSetDefault = async (modelId: string) => {
    try {
      await api.setDefaultModel(modelId)
      await loadModels()
    } catch (e: any) {
      message.error(`设置失败: ${e.message}`)
    }
  }

  const handleAddModel = async (values: any) => {
    setAddModelSaving(true)
    try {
      const modelId = (values.id || values.name || '').trim().replace(/\s+/g, '-')
      if (!modelId) {
        message.error('请输入模型 ID')
        return
      }
      const caps = Array.isArray(values.capabilities) ? values.capabilities.join(',') : (values.capabilities || '')
      await (api.createModel as any)({
        id: modelId,
        name: values.name || modelId,
        providerId: provider.id,
        litellmId: values.id || modelId,
        aliases: '',
        capabilities: caps,
        contextWindow: values.contextWindow || 128000,
        costRank: null,
        qualityRank: null,
        enabled: true,
        isDefault: false,
        modelType: values.modelType || 'chat',
      })
      message.success('模型已添加')
      setAddModelVisible(false)
      addModelForm.resetFields()
      await loadModels()
    } catch (e: any) {
      message.error(`添加失败: ${e.message}`)
    } finally {
      setAddModelSaving(false)
    }
  }

  const handleDeleteModel = async (modelId: string) => {
    try {
      await api.deleteModel(modelId)
      message.success('已删除')
      await loadModels()
    } catch (e: any) {
      message.error(`删除失败: ${e.message}`)
    }
  }

  // Group models by type
  const grouped = models.reduce((acc, m) => {
    const t = (m as any).modelType || 'chat'
    if (!acc[t]) acc[t] = []
    acc[t].push(m)
    return acc
  }, {} as Record<string, ModelInfo[]>)

  return (
    <>
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      <Form form={form} layout="vertical" onFinish={handleSave}>
        <Form.Item name="displayName" label="显示名称">
          <Input placeholder="Provider 显示名称" />
        </Form.Item>
        <Form.Item name="providerType" label="类型">
          <Input disabled />
        </Form.Item>
        <Form.Item name="apiBase" label="API Base URL">
          <Input placeholder="https://api.openai.com/v1" />
        </Form.Item>
        <Form.Item name="apiKey" label="API Key">
          <Input.Password
            placeholder="sk-..."
            visibilityToggle={{ visible: showApiKey, onVisibleChange: setShowApiKey }}
          />
        </Form.Item>
        <Form.Item name="enabled" label="启用" valuePropName="checked">
          <Switch />
        </Form.Item>
        <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
          <Button type="primary" htmlType="submit" loading={saving}>保存</Button>
          <Button
            onClick={handleTest}
            loading={testing}
          >
            {testingResult === 'success' ? '✅ 连接成功' : testingResult === 'error' ? '❌ 连接失败' : '测试连接'}
          </Button>
          {!provider.isSystem && (
            <Popconfirm
              title="确认删除此 Provider？"
              onConfirm={handleDelete}
              okText="删除"
              cancelText="取消"
            >
              <Button danger>删除</Button>
            </Popconfirm>
          )}
        </div>
      </Form>

      <Divider style={{ margin: '16px 0' }}>模型列表</Divider>
      <div style={{ marginBottom: 12 }}>
        <Space>
          <Button onClick={handleDiscover} loading={discovering} size="small">
            检测模型
          </Button>
          <Button size="small" onClick={() => { addModelForm.resetFields(); setAddModelVisible(true) }}>
            添加模型
          </Button>
        </Space>
      </div>

      {Object.keys(grouped).length === 0 && (
        <div style={{ color: '#999', fontSize: 13, padding: '8px 0' }}>
          暂未配置模型，点击「检测模型」自动发现
        </div>
      )}

      {Object.entries(grouped).map(([type, ms]) => (
        <Card key={type} size="small" title={MODEL_TYPE_LABELS[type] || type} style={{ marginBottom: 8 }}>
          {(ms as ModelInfo[]).map(m => {
            const caps: string[] = typeof m.capabilities === 'string'
              ? m.capabilities.split(',').map((c: string) => c.trim())
              : (m.capabilities || [])
            return (
              <div
                key={m.id}
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0' }}
              >
                <div>
                  <span style={{ fontWeight: m.isDefault ? 600 : 400 }}>{m.name}</span>
                  <span style={{ color: '#999', fontSize: 12, marginLeft: 8 }}>
                    上下文: {m.contextWindow ? m.contextWindow.toLocaleString() : '—'}
                  </span>
                </div>
                <div>
                  {caps.includes('tools') && <Tag color="green" style={{ marginRight: 4, fontSize: 10 }}>Tools</Tag>}
                  {(m as any).supportsVision && <Tag color="blue" style={{ marginRight: 4, fontSize: 10 }}>Vision</Tag>}
                  <Radio checked={m.isDefault} onChange={() => handleSetDefault(m.id)} />
                  <Button size="small" danger onClick={() => handleDeleteModel(m.id)} style={{ marginLeft: 4 }}>删除</Button>
                </div>
              </div>
            )
          })}
        </Card>
      ))}
    </div>

    <Modal
      title="添加模型"
      open={addModelVisible}
      onCancel={() => setAddModelVisible(false)}
      footer={null}
      width={480}
      destroyOnClose
    >
      <Form form={addModelForm} layout="vertical" onFinish={handleAddModel}>
        <Form.Item name="id" label="模型 ID" rules={[{ required: true, message: '请输入模型 ID' }]}>
          <Input placeholder="gpt-4o-mini" />
        </Form.Item>
        <Form.Item name="name" label="显示名称">
          <Input placeholder="同 ID 则留空" />
        </Form.Item>
        <Form.Item name="modelType" label="模型类型" initialValue="chat">
          <Select options={MODEL_TYPE_OPTIONS} />
        </Form.Item>
        <Form.Item name="contextWindow" label="上下文窗口">
          <InputNumber placeholder="128000" style={{ width: '100%' }} min={0} />
        </Form.Item>
        <Form.Item name="capabilities" label="能力">
          <Select mode="multiple" placeholder="选择能力（可选）" options={CAPABILITY_OPTIONS} allowClear />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={addModelSaving}>添加</Button>
      </Form>
    </Modal>
    </>
  )
}
