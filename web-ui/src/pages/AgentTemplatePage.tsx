import { useEffect, useState } from 'react'
import {
  Card, Button, Table, Space, Tag, Modal, Form, Input, Select, Switch,
  Row, Col, message, Popconfirm, Upload, Typography, Tooltip, Segmented
} from 'antd'
import {
  PlusOutlined, EditOutlined, DeleteOutlined, UploadOutlined,
  DownloadOutlined, ReloadOutlined, LockOutlined, FileTextOutlined, EyeOutlined
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import type { AgentTemplate } from '../types'
import { api } from '../api'

const { TextArea } = Input
const { Text } = Typography

export default function AgentTemplatePage() {
  const [templates, setTemplates] = useState<AgentTemplate[]>([])
  const [validTools, setValidTools] = useState<{ name: string; description: string }[]>([])
  const [loading, setLoading] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [editingTemplate, setEditingTemplate] = useState<AgentTemplate | null>(null)
  const [form] = Form.useForm()
  const [importModalVisible, setImportModalVisible] = useState(false)
  const [importContent, setImportContent] = useState('')
  const [importConflict, setImportConflict] = useState<'skip' | 'replace' | 'rename'>('skip')
  const [markdownPreview, setMarkdownPreview] = useState<'edit' | 'preview'>('edit')
  const [systemPromptValue, setSystemPromptValue] = useState('')
  const [modelValue, setModelValue] = useState('')

  useEffect(() => {
    loadTemplates()
    loadValidTools()
  }, [])

  const loadTemplates = async () => {
    setLoading(true)
    try {
      const data = await api.getAgentTemplates()
      setTemplates(data)
    } catch (error) {
      message.error('加载 Agent 模板失败')
    } finally {
      setLoading(false)
    }
  }

  const loadValidTools = async () => {
    try {
      const tools = await api.getValidTools()
      setValidTools(tools)
    } catch (error) {
      console.error('Failed to load valid tools:', error)
    }
  }

  const handleCreate = () => {
    setEditingTemplate(null)
    form.resetFields()
    setSystemPromptValue('')
    setModelValue('')
    form.setFieldsValue({
      tools: ['read_file', 'write_file'],
      rules: ['遵循项目规范'],
      enabled: true
    })
    setMarkdownPreview('edit')
    setModalVisible(true)
  }

  const handleEdit = (record: AgentTemplate) => {
    setEditingTemplate(record)
    setSystemPromptValue(record.system_prompt)
    setModelValue(record.model || '')
    form.setFieldsValue({
      name: record.name,
      description: record.description,
      tools: record.tools,
      rules: record.rules,
      enabled: record.enabled
    })
    setMarkdownPreview('edit')
    setModalVisible(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      values.system_prompt = systemPromptValue
      values.model = modelValue || null

      if (!systemPromptValue || !systemPromptValue.trim()) {
        message.error('请输入系统提示词')
        return
      }

      if (editingTemplate) {
        await api.updateAgentTemplate(editingTemplate.name, values)
        message.success('模板已更新')
      } else {
        await api.createAgentTemplate(values)
        message.success('模板已创建')
      }

      setModalVisible(false)
      loadTemplates()
    } catch (error) {
      console.error('Save failed:', error)
    }
  }

  const handleDelete = async (name: string) => {
    try {
      await api.deleteAgentTemplate(name)
      message.success('模板已删除')
      loadTemplates()
    } catch (error) {
      message.error('删除失败')
    }
  }

  const handleImport = async () => {
    if (!importContent.trim()) {
      message.error('请输入 YAML 内容')
      return
    }

    try {
      const result = await api.importAgentTemplates(importContent, importConflict)

      if (result.imported.length > 0) {
        message.success(`成功导入 ${result.imported.length} 个模板`)
      }
      if (result.skipped.length > 0) {
        message.warning(`跳过 ${result.skipped.length} 个已存在模板`)
      }
      if (result.errors.length > 0) {
        result.errors.forEach((err: string) => message.error(err))
      }

      setImportModalVisible(false)
      setImportContent('')
      loadTemplates()
    } catch (error) {
      message.error('导入失败')
    }
  }

  const handleExport = async (record?: AgentTemplate) => {
    try {
      const result = await api.exportAgentTemplates(record ? [record.name] : undefined)
      const blob = new Blob([result.content], { type: 'text/yaml' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `agent-templates-${record ? record.name : 'all'}.yaml`
      a.click()
      URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch (error) {
      message.error('导出失败')
    }
  }

  const handleFileSelect = (file: File) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      setImportContent(e.target?.result as string)
    }
    reader.readAsText(file)
    return false
  }

  const handleReload = async () => {
    try {
      await api.reloadAgentTemplates()
      message.success('已重新加载')
      loadTemplates()
    } catch (error) {
      message.error('重载失败')
    }
  }

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: AgentTemplate) => (
        <Space>
          {record.is_builtin && <LockOutlined style={{ color: '#1890ff' }} />}
          <Text strong={!record.is_builtin}>{name}</Text>
        </Space>
      )
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      render: (source: string) => (
        <Tag color={source === 'builtin' ? 'blue' : 'green'}>
          {source === 'builtin' ? '系统内置' : '用户定义'}
        </Tag>
      )
    },
    {
      title: '模型',
      dataIndex: 'model',
      key: 'model',
      render: (model: string) => model ? <Tag color="purple">{model}</Tag> : <Text type="secondary">-</Text>
    },
    {
      title: '工具',
      dataIndex: 'tools',
      key: 'tools',
      render: (tools: string[]) => (
        <Space size={4} wrap>
          {tools.slice(0, 3).map(t => <Tag key={t}>{t}</Tag>)}
          {tools.length > 3 && <Tag>+{tools.length - 3}</Tag>}
        </Space>
      )
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_: unknown, record: AgentTemplate) => (
        <Space size="small">
          <Tooltip title={record.is_builtin ? '查看详情' : '编辑'}>
            <Button
              type="text"
              icon={record.is_builtin ? <FileTextOutlined /> : <EditOutlined />}
              onClick={() => handleEdit(record)}
            />
          </Tooltip>
          <Tooltip title="导出">
            <Button
              type="text"
              icon={<DownloadOutlined />}
              onClick={() => handleExport(record)}
            />
          </Tooltip>
          {!record.is_builtin && (
            <Popconfirm
              title="确认删除？"
              onConfirm={() => handleDelete(record.name)}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button type="text" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      )
    }
  ]

  return (
    <div style={{ padding: 24 }}>
      <Card
        title="Agent 模板管理"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={handleReload}>
              重载
            </Button>
            <Button icon={<UploadOutlined />} onClick={() => setImportModalVisible(true)}>
              导入
            </Button>
            <Button icon={<DownloadOutlined />} onClick={() => handleExport()}>
              导出全部
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
              新建模板
            </Button>
          </Space>
        }
      >
        <Table
          columns={columns}
          dataSource={templates}
          rowKey="name"
          loading={loading}
          pagination={false}
          size="middle"
        />
      </Card>

      {/* 编辑/创建模态框 */}
      <Modal
        title={editingTemplate ? (editingTemplate.is_builtin ? '查看模板' : '编辑模板') : '新建模板'}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={editingTemplate?.is_builtin ? undefined : handleSubmit}
        width={800}
        okButtonProps={{ style: { display: editingTemplate?.is_builtin ? 'none' : 'inline-block' } }}
        className="agent-template-modal"
      >
        <Form
          form={form}
          layout="vertical"
          disabled={editingTemplate?.is_builtin}
        >
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="name"
                label="模板名称"
                rules={[{ required: true, message: '请输入名称' }]}
              >
                <Input placeholder="例如: my-coder" disabled={!!editingTemplate} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="description"
                label="描述"
              >
                <Input placeholder="简短描述" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="模型 (可选)"
                tooltip="指定此模板使用的模型，留空则使用默认模型"
              >
                <Input
                  placeholder="例如: qwen-turbo"
                  value={modelValue}
                  onChange={(e) => setModelValue(e.target.value)}
                />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            name="tools"
            label="可用工具"
            rules={[{ required: true, message: '至少选择一个工具' }]}
          >
            <Select
              mode="multiple"
              placeholder="选择工具"
              options={validTools.map(t => ({ label: `${t.name} - ${t.description}`, value: t.name }))}
            />
          </Form.Item>

          <Form.Item
            name="rules"
            label="规则"
            rules={[{ required: true, message: '至少输入一条规则' }]}
          >
            <Select
              mode="tags"
              placeholder="输入规则，按回车添加"
              tokenSeparators={['\n']}
            />
          </Form.Item>

          <Form.Item
            label="系统提示词"
            tooltip="支持 Markdown 格式，可切换预览查看渲染效果"
          >
            <div>
              <div style={{ marginBottom: 8 }}>
                <Segmented
                  value={markdownPreview}
                  onChange={(val) => setMarkdownPreview(val as 'edit' | 'preview')}
                  options={[
                    { value: 'edit', label: <span><EditOutlined /> 编辑</span> },
                    { value: 'preview', label: <span><EyeOutlined /> 预览</span> }
                  ]}
                />
              </div>
              {markdownPreview === 'edit' ? (
                <TextArea
                  rows={12}
                  placeholder="使用 {task}, {all_rules}, {workspace} 作为占位符"
                  style={{ fontFamily: 'monospace', maxHeight: '400px', overflowY: 'auto' }}
                  value={systemPromptValue}
                  onChange={(e) => setSystemPromptValue(e.target.value)}
                />
              ) : (
                <div
                  style={{
                    border: '1px solid #d9d9d9',
                    borderRadius: '6px',
                    padding: '16px',
                    minHeight: '288px',
                    maxHeight: '400px',
                    overflowY: 'auto',
                    backgroundColor: '#fafafa'
                  }}
                  className="markdown-preview"
                >
                  <ReactMarkdown
                    components={{
                      h1: ({ children }) => <h1 style={{ fontSize: '1.5em', borderBottom: '1px solid #eee', paddingBottom: '8px' }}>{children}</h1>,
                      h2: ({ children }) => <h2 style={{ fontSize: '1.3em', borderBottom: '1px solid #eee', paddingBottom: '6px' }}>{children}</h2>,
                      h3: ({ children }) => <h3 style={{ fontSize: '1.1em' }}>{children}</h3>,
                      code: ({ children }) => {
                        return (
                          <code style={{ backgroundColor: '#f5f5f5', padding: '2px 6px', borderRadius: '4px' }}>{children}</code>
                        )
                      },
                      pre: ({ children }) => <pre style={{ backgroundColor: '#f5f5f5', padding: '12px', borderRadius: '6px', overflow: 'auto' }}>{children}</pre>,
                      ul: ({ children }) => <ul style={{ paddingLeft: '20px' }}>{children}</ul>,
                      ol: ({ children }) => <ol style={{ paddingLeft: '20px' }}>{children}</ol>,
                      li: ({ children }) => <li style={{ marginBottom: '4px' }}>{children}</li>,
                    }}
                  >
                    {systemPromptValue || '*暂无内容*'}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          </Form.Item>

          <Form.Item
            name="enabled"
            valuePropName="checked"
          >
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 导入模态框 */}
      <Modal
        title="导入 Agent 模板"
        open={importModalVisible}
        onCancel={() => {
          setImportModalVisible(false)
          setImportContent('')
        }}
        onOk={handleImport}
        width={700}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="large">
          <Upload
            beforeUpload={handleFileSelect}
            accept=".yaml,.yml"
            maxCount={1}
            showUploadList={false}
          >
            <Button icon={<UploadOutlined />}>选择 YAML 文件</Button>
          </Upload>

          <TextArea
            rows={10}
            placeholder="粘贴 YAML 内容..."
            value={importContent}
            onChange={e => setImportContent(e.target.value)}
          />

          <div>
            <Text>冲突处理策略：</Text>
            <Select
              value={importConflict}
              onChange={setImportConflict}
              style={{ width: 200, marginLeft: 8 }}
            >
              <Select.Option value="skip">跳过已存在</Select.Option>
              <Select.Option value="replace">覆盖已存在</Select.Option>
              <Select.Option value="rename">自动重命名</Select.Option>
            </Select>
          </div>
        </Space>
      </Modal>
    </div>
  )
}
