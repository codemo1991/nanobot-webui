import React, { useState, useEffect } from 'react'
import { List, Switch, Input, Tag, Empty, Button, message, Popconfirm } from 'antd'
import { PlusOutlined, StopOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type { Provider } from '../../types'
import { PROVIDER_TYPE_COLORS, PROVIDER_TYPE_ICONS } from './constants'

interface ProviderListProps {
  providers: Provider[]
  onProvidersChange: (providers: Provider[]) => void
  onSelect: (p: Provider) => void
  selectedId?: string
  onRefresh: () => void
  onAddClick: () => void
}

export const ProviderList: React.FC<ProviderListProps> = ({ providers, onProvidersChange, onSelect, selectedId, onRefresh, onAddClick }) => {
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await api.getProviders()
      onProvidersChange(data || [])
    } catch {
      message.error('加载 Provider 列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleToggle = async (provider: Provider, checked: boolean) => {
    await api.updateProvider(provider.id, { ...provider, enabled: checked })
    onRefresh()
  }

  const handleBatchDisable = async () => {
    const enabled = providers.filter(p => p.enabled)
    if (enabled.length === 0) {
      message.warning('没有已启用的 Provider')
      return
    }
    try {
      const ids = enabled.map(p => p.id)
      await api.batchDisableProviders(ids)
      message.success(`已禁用 ${ids.length} 个 Provider`)
      onRefresh()
    } catch (e: any) {
      console.error('[BatchDisable] error:', e)
      message.error(`批量禁用失败: ${e.message}`)
    }
  }

  const filtered = providers.filter(p => {
    const name = p.displayName || p.name || ''
    const type = p.providerType || p.type || ''
    const s = search.toLowerCase()
    return name.toLowerCase().includes(s) || p.id.toLowerCase().includes(s) || type.toLowerCase().includes(s)
  })

  return (
    <div style={{ borderRight: '1px solid #f0f0f0', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '8px 12px', borderBottom: '1px solid #f0f0f0', display: 'flex', gap: 8, alignItems: 'center' }}>
        <Input.Search
          placeholder="搜索 Provider..."
          onChange={e => setSearch(e.target.value)}
          style={{ flex: 1 }}
          allowClear
        />
        <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
          <Button
            type="primary"
            size="small"
            icon={<PlusOutlined />}
            onClick={onAddClick}
          >
            添加
          </Button>
          <Popconfirm
            title="确认禁用"
            description="确定要一键禁用所有已启用的 Provider 吗？"
            onConfirm={handleBatchDisable}
            okText="确定"
            cancelText="取消"
          >
            <Button
              size="small"
              danger
              icon={<StopOutlined />}
            >
              一键禁用
            </Button>
          </Popconfirm>
        </div>
      </div>
      <div style={{ flex: 1, overflow: 'auto' }}>
        <List
          loading={loading}
          locale={{ emptyText: <Empty description="暂无 Provider" /> }}
          dataSource={filtered}
          renderItem={(p) => {
            const pt = p.providerType || p.type || 'openai'
            const color = PROVIDER_TYPE_COLORS[pt] || '#666'
            return (
              <List.Item
                style={{
                  cursor: 'pointer',
                  background: selectedId === p.id ? '#f0f7ff' : undefined,
                  padding: '10px 14px',
                  borderLeft: selectedId === p.id ? `3px solid ${color}` : '3px solid transparent',
                }}
                onClick={() => onSelect(p)}
                actions={[
                  <Switch
                    key="sw"
                    size="small"
                    checked={p.enabled}
                    onChange={(checked) => handleToggle(p, checked)}
                  />
                ]}
              >
                <List.Item.Meta
                  avatar={
                    <span style={{ fontSize: 20 }}>
                      {PROVIDER_TYPE_ICONS[pt] || '🔗'}
                    </span>
                  }
                  title={
                    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      {p.displayName || p.name || p.id}
                      {p.isSystem && <Tag color="blue" style={{ fontSize: 10, margin: 0 }}>系统</Tag>}
                      {!p.enabled && <Tag color="default" style={{ fontSize: 10, margin: 0 }}>已禁用</Tag>}
                    </span>
                  }
                  description={
                    <span style={{ fontSize: 11, color: '#999' }}>{pt}</span>
                  }
                />
              </List.Item>
            )
          }}
        />
      </div>
    </div>
  )
}
