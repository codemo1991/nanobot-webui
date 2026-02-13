import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Input, Spin, Empty, Avatar, Modal, message as antMessage } from 'antd'
import { BulbOutlined, PlusOutlined, SendOutlined, UserOutlined, RobotOutlined, LockOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../../api'
import type { MirrorSession, MirrorMessage, StreamEvent } from '../../types'

const { TextArea } = Input

function WuTab() {
  const { t } = useTranslation()
  const [sessions, setSessions] = useState<MirrorSession[]>([])
  const [currentSession, setCurrentSession] = useState<MirrorSession | null>(null)
  const [messages, setMessages] = useState<MirrorMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [sending, setSending] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    loadSessions()
  }, [])

  useEffect(() => {
    if (currentSession) {
      loadMessages(currentSession.id)
    }
  }, [currentSession])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const loadSessions = async () => {
    setLoadingSessions(true)
    try {
      const data = await api.getMirrorSessions('wu')
      setSessions(data.items)
    } catch {
      antMessage.error(t('mirror.loadFailed'))
    } finally {
      setLoadingSessions(false)
    }
  }

  const loadMessages = async (sessionId: string) => {
    setLoading(true)
    try {
      const msgs = await api.getMirrorMessages(sessionId)
      setMessages(msgs)
    } catch {
      antMessage.error(t('mirror.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleStartWu = async () => {
    try {
      const session = await api.createMirrorSession('wu')
      setSessions((prev) => [session, ...prev])
      setCurrentSession(session)
    } catch {
      antMessage.error(t('mirror.loadFailed'))
    }
  }

  const handleSend = useCallback(async () => {
    if (!input.trim() || !currentSession || sending) return
    const content = input.trim()
    setInput('')
    setSending(true)

    // Optimistic: add user message
    const tempUserMsg: MirrorMessage = {
      id: `temp-${Date.now()}`,
      sessionId: currentSession.id,
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
      sequence: messages.length + 1,
    }
    setMessages((prev) => [...prev, tempUserMsg])

    const abortCtrl = new AbortController()
    abortRef.current = abortCtrl

    try {
      let assistantContent = ''
      const tempAssistantId = `temp-assistant-${Date.now()}`

      await api.sendMirrorMessageStream(
        currentSession.id,
        content,
        (evt: StreamEvent) => {
          if (evt.type === 'thinking') {
            // Show placeholder
            setMessages((prev) => {
              const existing = prev.find((m) => m.id === tempAssistantId)
              if (!existing) {
                return [
                  ...prev,
                  {
                    id: tempAssistantId,
                    sessionId: currentSession.id,
                    role: 'assistant' as const,
                    content: '...',
                    createdAt: new Date().toISOString(),
                    sequence: prev.length + 1,
                  },
                ]
              }
              return prev
            })
          } else if (evt.type === 'done' && 'content' in evt) {
            assistantContent = evt.content ?? ''
            setMessages((prev) => {
              const found = prev.some((m) => m.id === tempAssistantId)
              if (found) {
                return prev.map((m) =>
                  m.id === tempAssistantId ? { ...m, content: assistantContent } : m
                )
              }
              return [
                ...prev,
                {
                  id: tempAssistantId,
                  sessionId: currentSession.id,
                  role: 'assistant' as const,
                  content: assistantContent,
                  createdAt: new Date().toISOString(),
                  sequence: prev.length + 1,
                },
              ]
            })
          }
        },
        abortCtrl.signal
      )
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        antMessage.error(t('chat.sendFailed'))
      }
    } finally {
      setSending(false)
      abortRef.current = null
    }
  }, [input, currentSession, sending, messages.length, t])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSeal = () => {
    if (!currentSession) return
    Modal.confirm({
      title: t('mirror.endWu'),
      content: t('mirror.endConfirm'),
      onOk: async () => {
        try {
          const sealed = await api.sealMirrorSession(currentSession.id)
          setCurrentSession(sealed)
          setSessions((prev) =>
            prev.map((s) => (s.id === sealed.id ? sealed : s))
          )
        } catch {
          antMessage.error(t('mirror.loadFailed'))
        }
      },
    })
  }

  const isSealed = currentSession?.status === 'sealed'

  return (
    <div className="mirror-split-layout">
      {/* 左栏：会话列表 */}
      <div className="mirror-sidebar">
        <div className="mirror-sidebar-header">
          <h3>{t('mirror.wu')}</h3>
          <Button
            type="primary"
            size="small"
            icon={<PlusOutlined />}
            onClick={handleStartWu}
          >
            {t('mirror.newSession')}
          </Button>
        </div>
        <div className="mirror-sidebar-list">
          {loadingSessions ? (
            <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
          ) : sessions.length === 0 ? (
            <Empty
              description={t('mirror.noSessions')}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ) : (
            sessions.map((s) => (
              <div
                key={s.id}
                className={`mirror-session-item ${currentSession?.id === s.id ? 'active' : ''} ${s.status === 'sealed' ? 'sealed' : ''}`}
                onClick={() => setCurrentSession(s)}
              >
                <div className="session-title">
                  {s.title || s.topic || t('chat.defaultTitle')}
                </div>
                <div className="session-meta">
                  <span>{new Date(s.createdAt).toLocaleDateString()}</span>
                  <span className={`session-status ${s.status}`}>
                    {s.status === 'sealed' ? (
                      <><LockOutlined /> {t('mirror.sessionSealed')}</>
                    ) : (
                      t('mirror.sessionActive')
                    )}
                  </span>
                </div>
                {s.insight && (
                  <div style={{ fontSize: 11, color: '#999', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {s.insight}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      {/* 右栏：对话区 或 空态 */}
      <div className="mirror-main">
        {!currentSession ? (
          <div className="mirror-empty-state">
            <BulbOutlined className="mirror-logo" />
            <div className="mirror-empty-title">{t('mirror.wuStart')}</div>
            <div className="mirror-empty-hint">{t('mirror.wuStartHint')}</div>
            <Button
              type="primary"
              size="large"
              className="mirror-start-btn"
              icon={<BulbOutlined />}
              onClick={handleStartWu}
            >
              {t('mirror.wuStart')}
            </Button>
          </div>
        ) : (
          <div className="mirror-chat-area">
            {/* 消息列表 */}
            <div className="mirror-chat-messages">
              {loading ? (
                <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
              ) : (
                messages.map((msg) => (
                  <div key={msg.id} className={`mirror-message ${msg.role}`}>
                    <Avatar
                      className="message-avatar"
                      size={32}
                      icon={msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                      style={{ background: msg.role === 'user' ? '#1890ff' : '#87d068' }}
                    />
                    <div className="message-bubble">
                      {msg.role === 'assistant' ? (
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                      ) : (
                        msg.content
                      )}
                    </div>
                  </div>
                ))
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* 输入区 */}
            {!isSealed ? (
              <>
                <div className="mirror-chat-input-area">
                  <TextArea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={t('chat.inputPlaceholder')}
                    autoSize={{ minRows: 1, maxRows: 4 }}
                    disabled={sending}
                  />
                  <Button
                    type="primary"
                    icon={<SendOutlined />}
                    onClick={handleSend}
                    loading={sending}
                  />
                </div>
                <div className="mirror-chat-footer">
                  <Button danger onClick={handleSeal}>
                    <LockOutlined /> {t('mirror.endWu')}
                  </Button>
                </div>
              </>
            ) : (
              <div className="mirror-chat-footer" style={{ justifyContent: 'center', padding: 16 }}>
                <span style={{ color: '#999', display: 'flex', alignItems: 'center', gap: 6 }}>
                  <LockOutlined /> {t('mirror.sessionSealed')}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default WuTab
