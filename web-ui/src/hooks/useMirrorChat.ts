import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, message as antMessage } from 'antd'
import { api } from '../api'
import type { MirrorSession, MirrorMessage, AttackLevel, StreamEvent } from '../types'

export type MirrorSessionType = 'wu' | 'bian'

export interface UseMirrorChatOptions {
  attackLevel?: AttackLevel
  customTopic?: string
  firstReplyPlaceholder?: string
  /** 新建会话并完成首次回复后调用（如清空 customTopic） */
  onStartComplete?: () => void
}

export function useMirrorChat(
  sessionType: MirrorSessionType,
  options: UseMirrorChatOptions = {}
) {
  const { t } = useTranslation()
  const { attackLevel = 'medium', customTopic = '', firstReplyPlaceholder = '...', onStartComplete } = options

  const [sessions, setSessions] = useState<MirrorSession[]>([])
  const [currentSession, setCurrentSession] = useState<MirrorSession | null>(null)
  const [messages, setMessages] = useState<MirrorMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [sending, setSending] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const streamingFirstReplyRef = useRef<string | null>(null)

  useEffect(() => {
    const load = async () => {
      setLoadingSessions(true)
      try {
        const data = await api.getMirrorSessions(sessionType)
        setSessions(data.items)
      } catch {
        antMessage.error(t('mirror.loadFailed'))
      } finally {
        setLoadingSessions(false)
      }
    }
    load()
  }, [sessionType, t])

  useEffect(() => {
    if (!currentSession) return
    if (streamingFirstReplyRef.current === currentSession.id) return
    const load = async () => {
      setLoading(true)
      try {
        const msgs = await api.getMirrorMessages(currentSession.id, sessionType)
        setMessages(msgs)
      } catch {
        antMessage.error(t('mirror.loadFailed'))
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [currentSession, sessionType, t])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const createSession = useCallback(async (): Promise<MirrorSession> => {
    if (sessionType === 'wu') {
      return api.createMirrorSession('wu')
    }
    const topic = customTopic?.trim() || undefined
    return api.createMirrorSession('bian', { attackLevel, topic })
  }, [sessionType, attackLevel, customTopic])

  const handleStartSession = useCallback(async () => {
    try {
      const session = await createSession()
      setSessions((prev) => [session, ...prev])
      setCurrentSession(session)
      setMessages([])
      streamingFirstReplyRef.current = session.id
      setSending(true)
      const abortCtrl = new AbortController()
      try {
        await api.getMirrorFirstReplyStream(
          session.id,
          sessionType,
          (evt) => {
            if (evt.type === 'thinking') {
              setMessages((prev) =>
                prev.some((m) => m.id?.startsWith('temp-first'))
                  ? prev
                  : [
                      ...prev,
                      {
                        id: `temp-first-${Date.now()}`,
                        sessionId: session.id,
                        role: 'assistant' as const,
                        content: firstReplyPlaceholder,
                        createdAt: new Date().toISOString(),
                        sequence: prev.length + 1,
                      },
                    ]
              )
            } else if (evt.type === 'done' && 'content' in evt && evt.content) {
              setMessages((prev) => {
                const found = prev.find((m) => m.id?.startsWith('temp-first'))
                if (found) {
                  return prev.map((m) =>
                    m.id?.startsWith('temp-first')
                      ? { ...m, content: evt.content ?? '' }
                      : m
                  )
                }
                return [
                  ...prev,
                  {
                    id: `msg-first-${Date.now()}`,
                    sessionId: session.id,
                    role: 'assistant' as const,
                    content: evt.content ?? '',
                    createdAt: new Date().toISOString(),
                    sequence: prev.length + 1,
                  },
                ]
              })
            }
          },
          abortCtrl.signal
        )
      } catch (e: unknown) {
        const err = e as Error & { name?: string }
        if (err?.name !== 'AbortError') {
          antMessage.error(t('mirror.loadFailed'))
          const msgs = await api.getMirrorMessages(session.id, sessionType)
          setMessages(msgs)
        }
      } finally {
        streamingFirstReplyRef.current = null
        setSending(false)
        onStartComplete?.()
      }
    } catch {
      antMessage.error(t('mirror.loadFailed'))
    }
  }, [createSession, sessionType, firstReplyPlaceholder, onStartComplete, t])

  const handleSend = useCallback(async () => {
    if (!input.trim() || !currentSession || sending) return
    const content = input.trim()
    setInput('')
    setSending(true)

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
        abortCtrl.signal,
        sessionType
      )
    } catch (e: unknown) {
      const err = e as Error & { name?: string }
      if (err?.name !== 'AbortError') {
        antMessage.error(t('chat.sendFailed'))
      }
    } finally {
      setSending(false)
      abortRef.current = null
    }
  }, [input, currentSession, sending, messages.length, sessionType, t])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey || !e.shiftKey)) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend]
  )

  const handleRenameSession = useCallback(
    async (sessionId: string) => {
      if (!editTitle.trim()) {
        setEditingSessionId(null)
        return
      }
      try {
        const updated = await api.renameMirrorSession(sessionId, editTitle.trim())
        setCurrentSession((prev) => (prev?.id === sessionId ? updated : prev))
        setSessions((prev) => prev.map((s) => (s.id === sessionId ? updated : s)))
        setEditingSessionId(null)
        antMessage.success(t('chat.sessionRenamed'))
      } catch {
        antMessage.error(t('mirror.loadFailed'))
      }
    },
    [editTitle, t]
  )

  const handleRetryAnalysis = useCallback(async () => {
    if (!currentSession) return
    setRetrying(true)
    try {
      const updated = (await api.retryMirrorAnalysis(currentSession.id)) as {
        analysisStatus?: string
      } & MirrorSession
      setCurrentSession(updated)
      setSessions((prev) => prev.map((s) => (s.id === updated.id ? updated : s)))
      if (updated.analysisStatus === 'success') {
        antMessage.success(t('mirror.retryAnalysisSuccess'))
      } else {
        antMessage.warning(t('mirror.analysisFailed'))
      }
    } catch {
      antMessage.error(t('mirror.loadFailed'))
    } finally {
      setRetrying(false)
    }
  }, [currentSession, t])

  const handleSeal = useCallback(
    (endLabelKey: string) => {
      if (!currentSession) return
      Modal.confirm({
        title: t(endLabelKey),
        content: t('mirror.endConfirm'),
        onOk: async () => {
          try {
            const sealed = (await api.sealMirrorSession(currentSession.id)) as {
              analysisStatus?: string
            } & MirrorSession
            setCurrentSession(sealed)
            setSessions((prev) => prev.map((s) => (s.id === sealed.id ? sealed : s)))
            if (sealed.analysisStatus === 'failed') {
              antMessage.warning(t('mirror.analysisFailed'), 5)
            }
          } catch {
            antMessage.error(t('mirror.loadFailed'))
          }
        },
      })
    },
    [currentSession, t]
  )

  const isSealed = currentSession?.status === 'sealed'

  return {
    sessions,
    setSessions,
    currentSession,
    setCurrentSession,
    messages,
    setMessages,
    input,
    setInput,
    loading,
    loadingSessions,
    sending,
    setSending,
    retrying,
    editingSessionId,
    setEditingSessionId,
    editTitle,
    setEditTitle,
    messagesEndRef,
    abortRef,
    handleStartSession,
    handleSend,
    handleKeyDown,
    handleRenameSession,
    handleRetryAnalysis,
    handleSeal,
    isSealed,
  }
}
