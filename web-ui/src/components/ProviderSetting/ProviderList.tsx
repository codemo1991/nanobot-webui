import React, { useState, useEffect } from 'react'
import { List, Switch, Input, Tag, Empty, Button } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type { Provider } from '../../types'
import { PROVIDER_TYPE_COLORS, PROVIDER_TYPE_ICONS } from './constants'

interface ProviderListProps {
  onSelect: (p: Provider) => void
  selectedId?: string
  onRefresh: () => void
  onAddClick: () => void
}

export const ProviderList: React.FC<ProviderListProps> = ({ onSelect, selectedId, onRefresh, onAddClick }) => {
  const [providers, setProviders] = useState<Provider[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await api.getProviders()
      setProviders(data || [])
    } catch {
      message.error('加载 Provider 列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleToggle = async (provider: Provider, checked: boolean, e: React.MouseEvent) => {
    e.stopPropagation()
    await api.updateProvider(provider.id, { ...provider, enabled: checked })
    await load()
    onRefresh()
  }

  const filtered = providers.filter(p => {
    const name = p.displayName || p.name || ''
    const type = p.providerType || p.type || ''
    const s = search.toLowerCase()
    return name.toLowerCase().includes(s) || p.id.toLowerCase().includes(s) || type.toLowerCase().includes(s)
  })

  return (
    <div style={{ borderRight: '1px solid #f0f0f0', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '8px 12px', borderBottom: '1px solid #f0f0f0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Input.Search
          placeholder="搜索 Provider..."
          onChange={e => setSearch(e.target.value)}
          style={{ flex: 1, marginRight: 8 }}
          allowClear
        />
        <Button
          type="primary"
          size="small"
          icon={<PlusOutlined />}
          onClick={onAddClick}
        >
          添加
        </Button>
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
                    onChange={(checked, e) => handleToggle(p, checked, e)}
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
                    <span>
                      {p.displayName || p.name || p.id}
                      {p.isSystem && (
                        <Tag color="blue" style={{ marginLeft: 6, fontSize: 10 }}>系统</Tag>
                      )}
                    </span>
                  }
                  description={
                    <span style={{ fontSize: 11, color: '#999' }}>
                      <span style={{ color }}>{pt}</span>
                      {!p.enabled && <Tag style={{ marginLeft: 4, fontSize: 10 }}>已禁用</Tag>}
                    </span>
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
