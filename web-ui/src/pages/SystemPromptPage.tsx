import { useEffect, useState } from 'react'
import { Card, Button, Form, Input, Space, message, Popconfirm, Segmented } from 'antd'
import { SaveOutlined, UndoOutlined, EditOutlined, EyeOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import { api } from '../api'

const { TextArea } = Input

const DEFAULT_IDENTITY_PLACEHOLDER = `# nanobot 🐈

You are nanobot, a helpful AI assistant. Customize this file to define your agent's identity.

## Behavior Guidelines

- Be helpful, accurate, and concise
- Use tools when needed, explain what you're doing
- When user says "记住/remember", call the remember tool to persist the information
- For normal conversation, respond with text directly. Only use the 'message' tool for cross-channel messaging.

## Media Handling

When receiving media content, choose the spawn template by media type:
- **Images only** (photos, screenshots, [图片]): Use \`template=vision\` with \`attach_media=true\`. Vision is for image analysis, NOT for audio.
- **Audio/voice only** ([语音], .mp3/.wav/.ogg): Use \`template=voice\` with \`attach_media=true\`. Voice is for speech-to-text, NOT for images.

**CRITICAL**: Never use \`voice\` for images. Never use \`vision\` for audio only. Match template to media type. Always set \`attach_media=true\` when using vision or voice templates.`

export default function SystemPromptPage() {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [identityContent, setIdentityContent] = useState('')
  const [updatedAt, setUpdatedAt] = useState('')
  const [markdownPreview, setMarkdownPreview] = useState<'edit' | 'preview'>('edit')

  useEffect(() => {
    loadPrompt()
  }, [])

  const loadPrompt = async () => {
    setLoading(true)
    try {
      const data = await api.getMainAgentPrompt()
      setIdentityContent(data.identity_content || '')
      setUpdatedAt(data.updated_at || '')
    } catch (error) {
      message.error('加载主 Agent 系统提示词失败')
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.updateMainAgentPrompt(identityContent)
      message.success('已保存')
      loadPrompt()
    } catch (error) {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    try {
      await api.resetMainAgentPrompt()
      message.success('已恢复默认')
      loadPrompt()
    } catch (error) {
      message.error('恢复默认失败')
    }
  }

  const displayContent = identityContent || DEFAULT_IDENTITY_PLACEHOLDER
  const isUsingDefault = !identityContent.trim()

  return (
    <div style={{ padding: 24 }}>
      <Card
        title="主 Agent 系统提示词"
        loading={loading}
        extra={
          <Space>
            <Popconfirm
              title="确认恢复默认？"
              description="将清除当前自定义配置，主 Agent 将使用内置默认身份描述。"
              onConfirm={handleReset}
              okText="恢复"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button icon={<UndoOutlined />} disabled={isUsingDefault}>
                恢复默认
              </Button>
            </Popconfirm>
            <Button type="primary" icon={<SaveOutlined />} onClick={handleSave} loading={saving} htmlType="button">
              保存
            </Button>
          </Space>
        }
      >
        <div style={{ marginBottom: 12 }}>
          <Space>
            <span style={{ color: '#666', fontSize: 13 }}>
              定义主 Agent 的身份、行为规范和媒体处理方式。与子 Agent 模板类似，此处配置会存入 SQLite 数据库。
            </span>
          </Space>
          {updatedAt && (
            <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
              上次更新: {updatedAt}
            </div>
          )}
        </div>

        <Form layout="vertical">
          <Form.Item
            label="Identity 内容"
            tooltip="留空则使用内置默认。系统会自动追加当前时间、工作目录和 Memory 路径等运行时信息。"
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
                {isUsingDefault && (
                  <span style={{ marginLeft: 12, color: '#faad14', fontSize: 12 }}>
                    当前使用内置默认
                  </span>
                )}
              </div>
              {markdownPreview === 'edit' ? (
                <TextArea
                  rows={16}
                  placeholder={DEFAULT_IDENTITY_PLACEHOLDER}
                  style={{ fontFamily: 'monospace' }}
                  value={identityContent}
                  onChange={(e) => setIdentityContent(e.target.value)}
                />
              ) : (
                <div
                  style={{
                    border: '1px solid #d9d9d9',
                    borderRadius: '6px',
                    padding: '16px',
                    minHeight: '400px',
                    maxHeight: '500px',
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
                      code: ({ children }) => (
                        <code style={{ backgroundColor: '#f5f5f5', padding: '2px 6px', borderRadius: '4px' }}>{children}</code>
                      ),
                      pre: ({ children }) => <pre style={{ backgroundColor: '#f5f5f5', padding: '12px', borderRadius: '6px', overflow: 'auto' }}>{children}</pre>,
                      ul: ({ children }) => <ul style={{ paddingLeft: '20px' }}>{children}</ul>,
                      ol: ({ children }) => <ol style={{ paddingLeft: '20px' }}>{children}</ol>,
                      li: ({ children }) => <li style={{ marginBottom: '4px' }}>{children}</li>,
                    }}
                  >
                    {displayContent || '*暂无内容*'}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}
