import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Layout, Input, Button, List, Typography, Avatar, Space, Spin, message as antMessage, Empty, Collapse } from 'antd'
import { SendOutlined, PlusOutlined, DeleteOutlined, EditOutlined, RobotOutlined, UserOutlined, StopOutlined, ToolOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { api } from '../api'
import type { Session, Message, ToolStep, TokenUsage } from '../types'
import './ChatPage.css'
import 'highlight.js/styles/github-dark.css'

const { Header, Sider, Content } = Layout
const { TextArea } = Input
const { Text } = Typography

const TOOL_STEPS_COLLAPSE_THRESHOLD = 5

function ToolStepsPanel({ steps, showRunningOnLast }: { steps: ToolStep[]; showRunningOnLast?: boolean }) {
  const { t } = useTranslation()
  const items = useMemo(() => {
    if (!steps?.length) return []
    return steps.map((step, i) => {
    const isRunning = showRunningOnLast && i === steps.length - 1 && !step.result
    const args = typeof step.arguments === 'string'
      ? (() => { try { return JSON.parse(step.arguments as string) } catch { return {} } })()
      : (step.arguments || {}) as Record<string, unknown>
    return {
      key: String(i),
      label: (
        <span className="tool-step-label">
          <ToolOutlined style={{ marginRight: 8 }} />
          {step.name}
          {isRunning && <Spin size="small" style={{ marginLeft: 8 }} />}
        </span>
      ),
      children: (
        <div className="tool-step-detail">
          {Object.keys(args).length > 0 && (
            <div className="tool-step-section">
              <div className="tool-step-subtitle">{t('chat.toolArguments')}</div>
              <pre>{JSON.stringify(args, null, 2)}</pre>
            </div>
          )}
          <div className="tool-step-section">
            <div className="tool-step-subtitle">{t('chat.toolResult')}</div>
            <pre className="tool-step-result">{isRunning ? t('chat.toolRunning') : String(step.result || '')}</pre>
          </div>
        </div>
      ),
    }
  })
  }, [steps, showRunningOnLast, t])

  if (!items.length) return null

  const innerPanel = (
    <Collapse
      ghost
      size="small"
      className="tool-steps-panel"
      items={items}
      defaultActiveKey={[]}
    />
  )

  if (steps.length > TOOL_STEPS_COLLAPSE_THRESHOLD) {
    return (
      <Collapse
        ghost
        size="small"
        className="tool-steps-outer-collapse"
        defaultActiveKey={[]}
        items={[
          {
            key: 'tools',
            label: (
              <span className="tool-step-label">
                <ToolOutlined style={{ marginRight: 8 }} />
                {t('chat.toolStepsCount', { count: steps.length })}
              </span>
            ),
            children: innerPanel,
          },
        ]}
      />
    )
  }

  return innerPanel
}

function formatMessageTime(isoString: string): string {
  try {
    const d = new Date(isoString)
    const y = d.getFullYear()
    const M = String(d.getMonth() + 1).padStart(2, '0')
    const D = String(d.getDate()).padStart(2, '0')
    const h = String(d.getHours()).padStart(2, '0')
    const m = String(d.getMinutes()).padStart(2, '0')
    const s = String(d.getSeconds()).padStart(2, '0')
    return `${y}-${M}-${D} ${h}:${m}:${s}`
  } catch {
    return ''
  }
}

function formatTokenNumber(n: number): string {
  return new Intl.NumberFormat().format(Math.max(0, Math.trunc(n || 0)))
}

function ChatPage() {
  const { t } = useTranslation()
  const [sessions, setSessions] = useState<Session[]>([])
  const [currentSession, setCurrentSession] = useState<Session | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [streamingToolSteps, setStreamingToolSteps] = useState<ToolStep[]>([])
  const [streamingThinking, setStreamingThinking] = useState(false)
  const [sessionTokenUsage, setSessionTokenUsage] = useState<TokenUsage>({
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
  })
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortControllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    loadSessions()
  }, [])

  useEffect(() => {
    if (currentSession) {
      loadMessages(currentSession.id)
      loadSessionTokenUsage(currentSession.id)
    } else {
      setSessionTokenUsage({ promptTokens: 0, completionTokens: 0, totalTokens: 0 })
    }
  }, [currentSession])

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const loadSessions = async () => {
    try {
      setLoadingSessions(true)
      const data = await api.getSessions()
      setSessions(data.items)
      if (data.items.length > 0 && !currentSession) {
        setCurrentSession(data.items[0])
      }
    } catch (error) {
      antMessage.error(t('chat.loadSessionsFailed'))
      console.error(error)
    } finally {
      setLoadingSessions(false)
    }
  }

  const loadMessages = async (sessionId: string) => {
    try {
      const data = await api.getMessages(sessionId)
      setMessages(data)
    } catch (error) {
      antMessage.error(t('chat.loadMessagesFailed'))
      console.error(error)
    }
  }

  const loadSessionTokenUsage = async (sessionId: string) => {
    try {
      const usage = await api.getSessionTokenSummary(sessionId)
      setSessionTokenUsage(usage)
    } catch (error) {
      console.error(error)
      setSessionTokenUsage({ promptTokens: 0, completionTokens: 0, totalTokens: 0 })
    }
  }

  const handleCreateSession = async () => {
    try {
      const session = await api.createSession(t('chat.defaultTitle'))
      setSessions([session, ...sessions])
      setCurrentSession(session)
      setMessages([])
    } catch (error) {
      antMessage.error(t('chat.createSessionFailed'))
      console.error(error)
    }
  }

  const handleDeleteSession = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await api.deleteSession(sessionId)
      const newSessions = sessions.filter(s => s.id !== sessionId)
      setSessions(newSessions)
      if (currentSession?.id === sessionId) {
        setCurrentSession(newSessions[0] || null)
      }
      antMessage.success(t('chat.sessionDeleted'))
    } catch (error) {
      antMessage.error(t('chat.deleteSessionFailed'))
      console.error(error)
    }
  }

  const handleRenameSession = async (sessionId: string) => {
    if (!editTitle.trim()) return
    try {
      await api.renameSession(sessionId, editTitle)
      setSessions(sessions.map(s => s.id === sessionId ? { ...s, title: editTitle } : s))
      setEditingSessionId(null)
      antMessage.success(t('chat.sessionRenamed'))
    } catch (error) {
      antMessage.error(t('chat.renameFailed'))
      console.error(error)
    }
  }

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
      setLoading(false)
      antMessage.info(t('chat.generationStopped'))
    }
  }

  const handleSend = useCallback(async () => {
    if (!input.trim() || !currentSession) return
    if (loading) {
      handleStop()
      return
    }

    const userMessage = input.trim()
    setInput('')
    setLoading(true)
    setStreamingToolSteps([])
    setStreamingThinking(false)

    const controller = new AbortController()
    abortControllerRef.current = controller

    const tempUserMsg: Message = {
      id: `temp-${Date.now()}`,
      sessionId: currentSession.id,
      role: 'user',
      content: userMessage,
      createdAt: new Date().toISOString(),
      sequence: messages.length + 1
    }
    setMessages(prev => [...prev, tempUserMsg])

    const handleStreamEvent = (evt: { type: string; name?: string; arguments?: Record<string, unknown>; result?: string }) => {
      if (evt.type === 'thinking') {
        setStreamingThinking(true)
      } else if (evt.type === 'tool_start' && evt.name) {
        setStreamingThinking(false)
        setStreamingToolSteps(prev => [...prev, { name: evt.name!, arguments: evt.arguments ?? {}, result: '' }])
      } else if (evt.type === 'tool_end' && evt.name) {
        setStreamingToolSteps(prev => {
          const next = [...prev]
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].name === evt.name && !next[i].result) {
              next[i] = { ...next[i], result: evt.result ?? '' }
              break
            }
          }
          return next
        })
      }
    }

    try {
      await api.sendMessageStream(
        currentSession.id,
        userMessage,
        handleStreamEvent,
        controller.signal
      )
      await loadMessages(currentSession.id)
      await loadSessionTokenUsage(currentSession.id)
      void loadSessions()
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return
      antMessage.error(t('chat.sendFailed'))
      if (err instanceof Error) console.error(err)
      setMessages(prev => prev.filter(m => !m.id.startsWith('temp-')))
    } finally {
      setStreamingToolSteps([])
      setStreamingThinking(false)
      if (abortControllerRef.current === controller) {
        setLoading(false)
        abortControllerRef.current = null
      }
    }
  }, [input, loading, messages, currentSession, t])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <Layout className="chat-page">
      <Sider width={280} theme="light" className="chat-sider">
        <div className="sider-header">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleCreateSession}
            block
            size="large"
          >
            {t('chat.newChat')}
          </Button>
        </div>
        <div className="sessions-list">
          {loadingSessions ? (
            <div style={{ textAlign: 'center', padding: '40px 0' }}>
              <Spin />
            </div>
          ) : sessions.length === 0 ? (
            <Empty description={t('chat.noChats')} />
          ) : (
            <List
              dataSource={sessions}
              renderItem={(session) => (
                <div
                  key={session.id}
                  className={`session-item ${currentSession?.id === session.id ? 'active' : ''}`}
                  onClick={() => setCurrentSession(session)}
                >
                  {editingSessionId === session.id ? (
                    <Input
                      value={editTitle}
                      onChange={e => setEditTitle(e.target.value)}
                      onPressEnter={() => handleRenameSession(session.id)}
                      onBlur={() => handleRenameSession(session.id)}
                      autoFocus
                      size="small"
                    />
                  ) : (
                    <>
                      <div className="session-info">
                        <Text ellipsis className="session-title" style={{ display: 'block', marginBottom: 4 }}>
                          {session.title || t('chat.defaultTitle')}
                        </Text>
                        <Text type="secondary" className="session-meta" style={{ display: 'block', fontSize: 12 }}>
                          {session.messageCount}{t('chat.messages')}
                        </Text>
                      </div>
                      <div className="session-actions">
                        <Button
                          type="text"
                          size="small"
                          icon={<EditOutlined />}
                          onClick={(e) => {
                            e.stopPropagation()
                            setEditingSessionId(session.id)
                            setEditTitle(session.title || '')
                          }}
                        />
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={(e) => handleDeleteSession(session.id, e)}
                        />
                      </div>
                    </>
                  )}
                </div>
              )}
            />
          )}
        </div>
      </Sider>

      <Layout>
        <Header className="chat-header">
          <Space>
            <Text strong style={{ fontSize: 16 }}>
              {currentSession?.title || t('chat.selectOrCreate')}
            </Text>
          </Space>
        </Header>

        <Content className="chat-content">
          {!currentSession ? (
            <div className="empty-chat">
              <Empty
                image={<RobotOutlined style={{ fontSize: 64, color: '#1890ff' }} />}
                description={t('chat.selectToStart')}
              />
            </div>
          ) : messages.length === 0 ? (
            <div className="empty-chat">
              <Empty
                image={<RobotOutlined style={{ fontSize: 64, color: '#1890ff' }} />}
                description={t('chat.firstMessage')}
              />
            </div>
          ) : (
            <div className="messages-container">
              {messages.map((message, index) => (
                <div
                  key={`${message.id}-${message.sequence ?? index}`}
                  className={`message-wrapper ${message.role}`}
                >
                  <div className="message-bubble">
                    <div className="message-avatar-row">
                      <Avatar
                        icon={message.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                        className={`message-avatar ${message.role}`}
                      />
                      <div className="message-header">
                        <Text strong>{message.role === 'user' ? t('chat.you') : 'Nanobot'}</Text>
                        {message.createdAt && (
                          <span className="message-time">{formatMessageTime(message.createdAt)}</span>
                        )}
                      </div>
                    </div>
                    <div className="message-content">
                      <div className="message-text">
                        {message.role === 'assistant' ? (
                          <>
                            {message.toolSteps && message.toolSteps.length > 0 && (
                              <ToolStepsPanel steps={message.toolSteps} />
                            )}
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              rehypePlugins={[rehypeHighlight]}
                              className="markdown-body"
                            >
                              {message.content}
                            </ReactMarkdown>
                          </>
                        ) : (
                          <Text>{message.content}</Text>
                        )}
                      </div>
                      {message.tokenUsage && (
                        <div className="message-token-usage">
                          <Text type="secondary">
                            {t('chat.tokenUsageInline', {
                              input: formatTokenNumber(message.tokenUsage.promptTokens),
                              output: formatTokenNumber(message.tokenUsage.completionTokens),
                              total: formatTokenNumber(message.tokenUsage.totalTokens),
                            })}
                          </Text>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
              {loading && (
                <div className="message-wrapper assistant">
                  <div className="message-bubble">
                    <div className="message-avatar-row">
                      <Avatar icon={<RobotOutlined />} className="message-avatar assistant" />
                      <div className="message-header">
                        <Text strong>Nanobot</Text>
                      </div>
                    </div>
                    <div className="message-content">
                      <div className="message-text loading-text">
                        <div className="loading-status">
                          <Spin size="small" />
                          <span>{streamingThinking ? t('chat.thinking') : streamingToolSteps.length > 0 ? t('chat.callingTool') : t('chat.thinkingOrTool')}</span>
                        </div>
                        {streamingToolSteps.length > 0 && (
                          <ToolStepsPanel steps={streamingToolSteps} showRunningOnLast />
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </Content>

        <div className="chat-input-container">
          <div className="chat-token-summary">
            <Text type="secondary">
              {t('chat.tokenUsageSummary', {
                input: formatTokenNumber(sessionTokenUsage.promptTokens),
                output: formatTokenNumber(sessionTokenUsage.completionTokens),
                total: formatTokenNumber(sessionTokenUsage.totalTokens),
              })}
            </Text>
          </div>
          <TextArea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('chat.inputPlaceholder')}
            autoSize={{ minRows: 1, maxRows: 4 }}
            disabled={!currentSession || loading}
            className="chat-input"
          />
          <Button
            type="primary"
            icon={loading ? <StopOutlined /> : <SendOutlined />}
            onClick={loading ? handleStop : handleSend}
            danger={loading}
            disabled={(!currentSession || !input.trim()) && !loading}
            className="send-button"
          >
            {loading ? t('chat.stop') : t('chat.send')}
          </Button>
        </div>
      </Layout>
    </Layout>
  )
}

export default ChatPage
