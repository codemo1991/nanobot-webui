import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Layout, Input, Button, List, Typography, Avatar, Space, Spin, message as antMessage, Empty, Collapse, Tooltip, Image, Dropdown, Badge, Tag, Modal, Popconfirm, Pagination } from 'antd'
import { SendOutlined, PlusOutlined, DeleteOutlined, EditOutlined, RobotOutlined, UserOutlined, StopOutlined, ToolOutlined, PictureOutlined, CloseCircleOutlined, SyncOutlined, TagsOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { api } from '../api'
import type { Session, Message, ToolStep, TokenUsage, Task, TaskListResponse } from '../types'
import './ChatPage.css'
import 'highlight.js/styles/github-dark.css'

const { Header, Sider, Content } = Layout
const { TextArea } = Input
const { Text } = Typography

const TOOL_STEPS_COLLAPSE_THRESHOLD = 5

// sessionStorage key 前缀
const STORAGE_PREFIX = 'nanobot_streaming_'

// 流式状态存储键
const getStreamingStateKey = (sessionId: string) => `${STORAGE_PREFIX}${sessionId}`

interface StreamingState {
  toolSteps: ToolStep[]
  thinking: boolean
  progress: string
  loading: boolean
  lastUpdate: number
}

// 保存流式状态到 sessionStorage
const saveStreamingState = (sessionId: string, state: Omit<StreamingState, 'lastUpdate'>) => {
  try {
    const fullState: StreamingState = { ...state, lastUpdate: Date.now() }
    sessionStorage.setItem(getStreamingStateKey(sessionId), JSON.stringify(fullState))
  } catch (e) {
    console.warn('Failed to save streaming state:', e)
  }
}

// 从 sessionStorage 恢复流式状态
const loadStreamingState = (sessionId: string): StreamingState | null => {
  try {
    const stored = sessionStorage.getItem(getStreamingStateKey(sessionId))
    if (!stored) return null
    const state = JSON.parse(stored) as StreamingState
    // 检查状态是否在合理时间内（5分钟内）
    if (Date.now() - state.lastUpdate > 5 * 60 * 1000) {
      sessionStorage.removeItem(getStreamingStateKey(sessionId))
      return null
    }
    return state
  } catch (e) {
    console.warn('Failed to load streaming state:', e)
    return null
  }
}

// 清除 sessionStorage 中的流式状态
const clearStreamingState = (sessionId: string) => {
  try {
    sessionStorage.removeItem(getStreamingStateKey(sessionId))
  } catch (e) {
    console.warn('Failed to clear streaming state:', e)
  }
}

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
  const [claudeCodeProgress, setClaudeCodeProgress] = useState('')
  const [pendingImages, setPendingImages] = useState<string[]>([])
  const [imageSendStatus, setImageSendStatus] = useState<Record<number, 'sending' | 'sent' | 'error'>>({})
  const [sessionTokenUsage, setSessionTokenUsage] = useState<TokenUsage>({
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
  })
  const [tasks, setTasks] = useState<Task[]>([])
  const [loadingTasks, setLoadingTasks] = useState(false)
  const [tasksDropdownOpen, setTasksDropdownOpen] = useState(false)
  const [selectedTask, setSelectedTask] = useState<Task | null>(null)
  const [taskDetailOpen, setTaskDetailOpen] = useState(false)
  // 分页状态
  const [tasksPage, setTasksPage] = useState(1)
  const [tasksPageSize, setTasksPageSize] = useState(20)
  const [tasksTotal, setTasksTotal] = useState(0)
  const [tasksLoadError, setTasksLoadError] = useState<string | null>(null)
  // 使用 ref 来跟踪是否需要轮询（避免 useEffect 依赖问题）
  const pollingEnabledRef = useRef(true)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const imageInputRef = useRef<HTMLInputElement>(null)
  // 追踪是否有活跃的流式请求
  const isStreamingRef = useRef(false)
  // 追踪页面是否刚恢复
  const isRestoringRef = useRef(false)

  // 页面可见性变化处理
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (!currentSession) return

      if (document.visibilityState === 'hidden') {
        // 页面隐藏时，保存当前流式状态
        // 只有当有活跃流式请求时才保存
        if (isStreamingRef.current && currentSession) {
          saveStreamingState(currentSession.id, {
            toolSteps: streamingToolSteps,
            thinking: streamingThinking,
            progress: claudeCodeProgress,
            loading,
          })
          console.log('Saved streaming state to sessionStorage (hidden)')
        }
      } else if (document.visibilityState === 'visible') {
        // 页面变为可见时，尝试恢复流式状态
        // 关键：检查是否有保存的状态，而不是检查 isStreamingRef.current
        // 因为 finally 块可能在 visibilitychange 恢复之前执行
        const savedState = loadStreamingState(currentSession.id)
        if (savedState) {
          // 恢复用户之前看到的流式状态
          isRestoringRef.current = true
          setStreamingToolSteps(savedState.toolSteps)
          setStreamingThinking(savedState.thinking)
          setClaudeCodeProgress(savedState.progress)

          // 根据保存的 loading 状态决定是否显示 loading
          // 注意：不需要在这里处理 loading，因为 handleSend 的 finally 块会处理
          // 这里只需要设置 isRestoringRef 标志，让 finally 块知道页面正在恢复
          console.log('Restored streaming state from sessionStorage')
        }
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [currentSession])

  // 当流式状态变化时，自动保存到 sessionStorage
  useEffect(() => {
    if (currentSession && isStreamingRef.current) {
      saveStreamingState(currentSession.id, {
        toolSteps: streamingToolSteps,
        thinking: streamingThinking,
        progress: claudeCodeProgress,
        loading,
      })
    }
  }, [currentSession, streamingToolSteps, streamingThinking, claudeCodeProgress, loading])

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

  // 加载任务列表
  useEffect(() => {
    loadTasks()
    // 定时刷新任务状态（当有运行中的任务时才轮询）
    const interval = setInterval(() => {
      if (pollingEnabledRef.current) {
        loadTasks()
      }
    }, 5000)
    return () => clearInterval(interval)
  }, [])

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

  const loadTasks = async (page = tasksPage, pageSize = tasksPageSize) => {
    try {
      setLoadingTasks(true)
      setTasksLoadError(null)
      const data: TaskListResponse = await api.getTasks(page, pageSize, 'all')
      setTasks(data.items)
      setTasksTotal(data.total)
      // 如果没有运行中的任务，停止轮询
      const runningCount = data.items.filter(task => task.status === 'running').length
      pollingEnabledRef.current = runningCount > 0
    } catch (error) {
      console.error(error)
      setTasksLoadError(error instanceof Error ? error.message : t('chat.tasks.loadFailed'))
    } finally {
      setLoadingTasks(false)
    }
  }

  const handleCancelTask = async (taskId: string) => {
    try {
      await api.cancelTask(taskId)
      antMessage.success(t('chat.tasks.cancelSuccess'))
      loadTasks()
    } catch (error) {
      antMessage.error(t('chat.tasks.cancelFailed'))
      console.error(error)
    }
  }

  const handleViewTaskDetails = async (task: Task) => {
    try {
      const fullTask = await api.getTask(task.task_id)
      setSelectedTask(fullTask)
      setTaskDetailOpen(true)
    } catch (error) {
      antMessage.error(t('chat.tasks.loadFailed'))
      console.error(error)
    }
  }

  // 分页变化处理
  const handleTasksPageChange = (page: number, pageSize: number) => {
    setTasksPage(page)
    setTasksPageSize(pageSize)
    loadTasks(page, pageSize)
  }

  // 获取运行中的任务数量
  const runningTasksCount = tasks.filter(task => task.status === 'running').length

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

  const handleStop = async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
      setLoading(false)
      antMessage.info(t('chat.generationStopped'))
    }
    try {
      await api.stopAgent()
    } catch (e) {
      console.warn('Stop agent request failed:', e)
    }
  }

  const handleSend = useCallback(async () => {
    if (!currentSession) return
    if (!input.trim() && !pendingImages.length) return
    if (loading) {
      handleStop()
      return
    }

    // 检查是否有运行中的后台任务
    const runningTasks = tasks.filter(t => t.status === 'running')
    if (runningTasks.length > 0) {
      const confirmed = await new Promise<boolean>((resolve) => {
        Modal.confirm({
          title: t('chat.tasks.pendingTasksTitle'),
          content: t('chat.tasks.pendingTasksContent', {
            count: runningTasks.length,
            prompt: runningTasks[0].prompt.slice(0, 50) + (runningTasks[0].prompt.length > 50 ? '...' : '')
          }),
          okText: t('chat.tasks.sendAnyway'),
          cancelText: t('common.cancel'),
          onOk: () => resolve(true),
          onCancel: () => resolve(false),
        })
      })
      if (!confirmed) return
    }

    const userMessage = input.trim()
    const imagesToSend = [...pendingImages]
    setInput('')
    setPendingImages([])
    setImageSendStatus({})
    setLoading(true)
    setStreamingToolSteps([])
    setStreamingThinking(false)
    setClaudeCodeProgress('')
    // 标记开始流式传输
    isStreamingRef.current = true

    const controller = new AbortController()
    abortControllerRef.current = controller

    const tempUserMsg: Message = {
      id: `temp-${Date.now()}`,
      sessionId: currentSession.id,
      role: 'user',
      content: userMessage,
      createdAt: new Date().toISOString(),
      sequence: messages.length + 1,
      images: imagesToSend.length > 0 ? imagesToSend : undefined,
    }
    setMessages(prev => [...prev, tempUserMsg])

    if (imagesToSend.length > 0) {
      const status: Record<number, 'sending' | 'sent' | 'error'> = {}
      imagesToSend.forEach((_, i) => { status[i] = 'sending' })
      setImageSendStatus(status)
    }

    const handleStreamEvent = (evt: { type: string; name?: string; arguments?: Record<string, unknown>; result?: string; subtype?: string; content?: string; tool_name?: string }) => {
      if (evt.type === 'thinking') {
        setStreamingThinking(true)
      } else if (evt.type === 'tool_start' && evt.name) {
        setStreamingThinking(false)
        if (evt.name === 'claude_code') {
          setClaudeCodeProgress('')
        }
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
        if (evt.name === 'claude_code') {
          setClaudeCodeProgress('')
        }
      } else if (evt.type === 'claude_code_progress') {
        const subtype = evt.subtype || 'text'
        const content = evt.content || ''
        let line = ''
        if (subtype === 'tool_use') {
          line = `[${evt.tool_name || 'Tool'}] ${content}`
        } else if (subtype === 'tool_result') {
          line = `  -> ${content}`
        } else if (subtype === 'assistant_text') {
          line = content.length > 120 ? content.slice(0, 120) + '...' : content
        } else {
          line = content
        }
        setClaudeCodeProgress(prev => {
          const newText = prev ? prev + '\n' + line : line
          const lines = newText.split('\n')
          return lines.length > 30 ? lines.slice(-30).join('\n') : newText
        })
      }
    }

    try {
      await api.sendMessageStream(
        currentSession.id,
        userMessage,
        handleStreamEvent,
        controller.signal,
        imagesToSend.length > 0 ? imagesToSend : undefined,
      )
      if (imagesToSend.length > 0) {
        setImageSendStatus(prev => {
          const newStatus = { ...prev }
          Object.keys(newStatus).forEach(k => { newStatus[Number(k)] = 'sent' })
          return newStatus
        })
      }

      // 页面恢复时，也需要调用 loadMessages 获取最新消息
      // 但需要先恢复流式状态（visibilitychange 中已处理），再获取消息
      await loadMessages(currentSession.id)

      // 恢复完成后清除恢复状态
      if (isRestoringRef.current) {
        isRestoringRef.current = false
        clearStreamingState(currentSession.id)
      }
      await loadSessionTokenUsage(currentSession.id)
      void loadSessions()
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return
      if (imagesToSend.length > 0) {
        setImageSendStatus(prev => {
          const newStatus = { ...prev }
          Object.keys(newStatus).forEach(k => { newStatus[Number(k)] = 'error' })
          return newStatus
        })
      }
      antMessage.error(t('chat.sendFailed'))
      if (err instanceof Error) console.error(err)
      setMessages(prev => prev.filter(m => !m.id.startsWith('temp-')))
    } finally {
      // 标记流式传输结束
      isStreamingRef.current = false

      // 如果页面正在恢复（可见性变化导致的状态恢复），则需要根据情况处理
      if (isRestoringRef.current && currentSession) {
        const savedState = loadStreamingState(currentSession.id)

        // 如果请求已完成（savedState.loading 为 false），清除 loading
        // 如果请求仍在进行中（savedState.loading 为 true），保留 loading
        if (savedState && !savedState.loading) {
          setLoading(false)
          abortControllerRef.current = null
        }
        // 无论哪种情况，都清除恢复标志和 sessionStorage
        clearStreamingState(currentSession.id)
        isRestoringRef.current = false
        return
      }

      // 正常完成时清除流式状态
      setStreamingToolSteps([])
      setStreamingThinking(false)
      setClaudeCodeProgress('')

      // 清除 sessionStorage 中的状态
      if (currentSession) {
        clearStreamingState(currentSession.id)
      }

      if (abortControllerRef.current === controller) {
        setLoading(false)
        abortControllerRef.current = null
      }
    }
  }, [input, loading, messages, currentSession, pendingImages.length, t])

  const handleImageSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (!files.length) return
    const remaining = 4 - pendingImages.length
    const toProcess = files.slice(0, remaining)
    toProcess.forEach(file => {
      if (!file.type.startsWith('image/')) return
      const reader = new FileReader()
      reader.onload = (ev) => {
        const dataUrl = ev.target?.result as string
        if (dataUrl) setPendingImages(prev => [...prev, dataUrl])
      }
      reader.readAsDataURL(file)
    })
    e.target.value = ''
  }, [pendingImages.length])

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
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Space>
              <Text strong style={{ fontSize: 16 }}>
                {currentSession?.title || t('chat.selectOrCreate')}
              </Text>
            </Space>
            <Space>
              {/* 任务状态指示器 */}
              <Dropdown
                dropdownRender={() => (
                  <div className="tasks-dropdown">
                    <div className="tasks-dropdown-header">
                      <Text strong>{t('chat.tasks.title')}</Text>
                      <Button type="link" size="small" onClick={() => loadTasks()}>
                        <SyncOutlined spin={loadingTasks} />
                      </Button>
                    </div>
                    {/* 加载错误显示 */}
                    {tasksLoadError && (
                      <div className="tasks-error">
                        <Text type="danger">{tasksLoadError}</Text>
                        <Button type="link" size="small" onClick={() => loadTasks()}>
                          {t('chat.tasks.retry')}
                        </Button>
                      </div>
                    )}
                    {/* 加载中显示 */}
                    {loadingTasks && tasks.length === 0 ? (
                      <div className="tasks-loading">
                        <Spin size="small" />
                      </div>
                    ) : tasks.length === 0 ? (
                      <div className="tasks-empty">
                        <Text type="secondary">{t('chat.tasks.empty')}</Text>
                      </div>
                    ) : (
                      <div className="tasks-list">
                        {tasks.map(task => (
                          <div key={task.task_id} className="task-item">
                            <div className="task-item-info">
                              <Tag
                                color={
                                  task.status === 'running' ? 'blue' :
                                  task.status === 'done' ? 'green' :
                                  task.status === 'cancelled' ? 'default' :
                                  'red'
                                }
                              >
                                {task.status === 'running' && <SyncOutlined spin style={{ marginRight: 4 }} />}
                                {t(`chat.tasks.${task.status}`)}
                              </Tag>
                              <Text ellipsis style={{ maxWidth: 200 }} title={task.prompt}>
                                {task.prompt}
                              </Text>
                            </div>
                            <div className="task-item-actions">
                              <Button
                                type="link"
                                size="small"
                                onClick={() => handleViewTaskDetails(task)}
                              >
                                {t('chat.tasks.viewDetails')}
                              </Button>
                              {task.status === 'running' && (
                                <Popconfirm
                                  title={t('chat.tasks.cancelConfirm')}
                                  onConfirm={() => handleCancelTask(task.task_id)}
                                  okText={t('common.yes')}
                                  cancelText={t('common.no')}
                                >
                                  <Button type="link" size="small" danger>
                                    {t('chat.tasks.cancel')}
                                  </Button>
                                </Popconfirm>
                              )}
                            </div>
                          </div>
                        ))}
                        {/* 分页组件 */}
                        {tasksTotal > tasksPageSize && (
                          <div className="tasks-pagination">
                            <Pagination
                              size="small"
                              current={tasksPage}
                              pageSize={tasksPageSize}
                              total={tasksTotal}
                              onChange={handleTasksPageChange}
                              showSizeChanger={false}
                              showTotal={(total) => `${t('chat.tasks.total', { total })}`}
                            />
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
                trigger={['click']}
                open={tasksDropdownOpen}
                onOpenChange={(open) => {
                  setTasksDropdownOpen(open)
                  // 当打开下拉菜单时，恢复轮询并刷新任务列表
                  if (open) {
                    pollingEnabledRef.current = true
                    loadTasks()
                  }
                }}
              >
                <Badge count={runningTasksCount} offset={[-2, 2]}>
                  <Button
                    type="text"
                    icon={<TagsOutlined />}
                    onClick={() => setTasksDropdownOpen(!tasksDropdownOpen)}
                  >
                    {runningTasksCount > 0 ? t('chat.tasks.runningCount', { count: runningTasksCount }) : t('chat.tasks.title')}
                  </Button>
                </Badge>
              </Dropdown>
            </Space>
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
                          <>
                            {message.images && message.images.length > 0 && (
                              <div className="message-images">
                                <Image.PreviewGroup>
                                  {message.images.map((src, i) => (
                                    <Image key={i} src={src} className="message-image-thumb" />
                                  ))}
                                </Image.PreviewGroup>
                              </div>
                            )}
                            <Text>{message.content}</Text>
                          </>
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
                        {claudeCodeProgress && (
                          <div className="claude-code-progress">
                            <pre>{claudeCodeProgress}</pre>
                          </div>
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
          {pendingImages.length > 0 && (
            <div className="pending-images-row">
              {pendingImages.map((src, i) => (
                <div key={i} className={`pending-image-wrapper ${imageSendStatus[i] || ''}`}>
                  <img src={src} className="pending-image-thumb" alt="" />
                  {imageSendStatus[i] && (
                    <span className="pending-image-status">
                      {imageSendStatus[i] === 'sending' ? t('chat.sending') : imageSendStatus[i] === 'sent' ? t('chat.sent') : t('chat.failed')}
                    </span>
                  )}
                  {imageSendStatus[i] === 'error' && (
                    <button
                      className="pending-image-retry"
                      onClick={() => {
                        setImageSendStatus(prev => {
                          const newStatus = { ...prev }
                          delete newStatus[i]
                          return newStatus
                        })
                      }}
                      title={t('chat.retry')}
                    >
                      ↻
                    </button>
                  )}
                  <button
                    className="pending-image-remove"
                    onClick={() => setPendingImages(prev => prev.filter((_, idx) => idx !== i))}
                  >
                    <CloseCircleOutlined />
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="chat-input-row">
            <input
              ref={imageInputRef}
              type="file"
              accept="image/*"
              multiple
              style={{ display: 'none' }}
              onChange={handleImageSelect}
            />
            <Tooltip title="上传图片 (最多4张)">
              <Button
                type="text"
                icon={<PictureOutlined />}
                onClick={() => imageInputRef.current?.click()}
                disabled={!currentSession || loading || pendingImages.length >= 4}
                className="image-upload-button"
              />
            </Tooltip>
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
              disabled={(!currentSession || (!input.trim() && pendingImages.length === 0)) && !loading}
              className="send-button"
            >
              {loading ? t('chat.stop') : t('chat.send')}
            </Button>
          </div>
          <div className="chat-token-summary">
            <Text type="secondary">
              {t('chat.tokenUsageSummary', {
                input: formatTokenNumber(sessionTokenUsage.promptTokens),
                output: formatTokenNumber(sessionTokenUsage.completionTokens),
                total: formatTokenNumber(sessionTokenUsage.totalTokens),
              })}
            </Text>
          </div>
        </div>

        {/* 任务详情弹窗 */}
        <Modal
          title={t('chat.tasks.title')}
          open={taskDetailOpen}
          onCancel={() => setTaskDetailOpen(false)}
          footer={[
            <Button key="close" onClick={() => setTaskDetailOpen(false)}>
              {t('common.close')}
            </Button>,
          ]}
          width={600}
        >
          {selectedTask && (
            <div className="task-detail">
              <div className="task-detail-row">
                <Text type="secondary">{t('chat.tasks.prompt')}:</Text>
                <Text>{selectedTask.prompt}</Text>
              </div>
              <div className="task-detail-row">
                <Text type="secondary">Status:</Text>
                <Tag
                  color={
                    selectedTask.status === 'running' ? 'blue' :
                    selectedTask.status === 'done' ? 'green' :
                    selectedTask.status === 'cancelled' ? 'default' :
                    'red'
                  }
                >
                  {selectedTask.status === 'running' && <SyncOutlined spin style={{ marginRight: 4 }} />}
                  {t(`chat.tasks.${selectedTask.status}`)}
                </Tag>
              </div>
              {selectedTask.workdir && (
                <div className="task-detail-row">
                  <Text type="secondary">{t('chat.tasks.workdir')}:</Text>
                  <Text code>{selectedTask.workdir}</Text>
                </div>
              )}
              {selectedTask.start_time && (
                <div className="task-detail-row">
                  <Text type="secondary">{t('chat.tasks.startTime')}:</Text>
                  <Text>{formatMessageTime(selectedTask.start_time)}</Text>
                </div>
              )}
              {selectedTask.end_time && (
                <div className="task-detail-row">
                  <Text type="secondary">{t('chat.tasks.endTime')}:</Text>
                  <Text>{formatMessageTime(selectedTask.end_time)}</Text>
                </div>
              )}
              {selectedTask.result && (
                <div className="task-detail-row">
                  <Text type="secondary">{t('chat.tasks.result')}:</Text>
                  <div className="task-result">
                    <pre>{selectedTask.result}</pre>
                  </div>
                </div>
              )}
            </div>
          )}
        </Modal>
      </Layout>
    </Layout>
  )
}

export default ChatPage
