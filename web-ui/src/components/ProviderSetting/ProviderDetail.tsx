import React, { useState, useEffect } from 'react'
import { Form, Input, Button, Switch, message, Tag, Radio, Card, Divider, Space, Popconfirm } from 'antd'
import { api } from '../../api'
import type { Provider, ModelInfo } from '../../types'

const MODEL_TYPE_LABELS: Record<string, string> = {
  chat: '对话',
  completion: '补全',
  embedding: '嵌入',
  image: '图像生成',
  audio: '音频',
  vision: '视觉理解',
}

function getDefaultBase(providerType?: string): string {
  const bases: Record<string, string> = {
    openai: 'https://api.openai.com/v1',
    anthropic: 'https://api.anthropic.com',
    deepseek: 'https://api.deepseek.com/v1',
    gemini: 'https://generativelanguage.googleapis.com/v1beta',
    ollama: 'http://localhost:11434',
    vllm: 'http://localhost:8000',
    azure: 'https://YOUR_RESOURCE.openai.azure.com',
    azure_openai: 'https://YOUR_RESOURCE.openai.azure.com',
    openrouter: 'https://openrouter.ai/api',
    together: 'https://api.together.xyz/v1',
    fireworks: 'https://api.fireworks.ai/inference/v1',
  }
  return bases[providerType || 'openai'] || ''
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

  const pt = (provider as any).providerType || (provider as any).type || 'openai'

  useEffect(() => {
    form.setFieldsValue({
      displayName: (provider as any).displayName || provider.name,
      providerType: pt,
      apiBase: provider.apiBase || getDefaultBase(pt),
      apiKey: provider.apiKey || '',
      enabled: provider.enabled,
    })
    loadModels()
  }, [provider.id])

  const loadModels = async () => {
    try {
      const all = await api.getModels()
      // Filter models for this provider; handle both old Model[] and new ModelInfo[]
      const filtered = (all || []).filter((m: any) => m.providerId === provider.id)
      setModels(filtered)
    } catch {
      // models might use a different endpoint; silently fail
      setModels([])
    }
  }

  const handleSave = async (values: any) => {
    setSaving(true)
    try {
      const pt = values.providerType || pt
      await api.updateProvider(provider.id, {
        ...values,
        enabled: values.enabled,
        type: pt, // backend uses `type` field
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

  // Group models by type
  const grouped = models.reduce((acc, m) => {
    const t = (m as any).modelType || 'chat'
    if (!acc[t]) acc[t] = []
    acc[t].push(m)
    return acc
  }, {} as Record<string, ModelInfo[]>)

  return (
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
            type={testingResult === 'success' ? 'default' : testingResult === 'error' ? 'default' : 'default'}
          >
            {testingResult === 'success' ? '✅ 连接成功' : testingResult === 'error' ? '❌ 连接失败' : '测试连接'}
          </Button>
          {!((provider as any).isSystem) && (
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
        <Button onClick={handleDiscover} loading={discovering} size="small">
          检测模型
        </Button>
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
                </div>
              </div>
            )
          })}
        </Card>
      ))}
    </div>
  )
}
