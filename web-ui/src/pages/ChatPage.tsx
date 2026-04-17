import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation } from 'react-router-dom'
import { Layout, Input, Button, List, Typography, Avatar, Space, Spin, message as antMessage, Empty, Collapse, Tooltip, Image, Tag, Modal, Popconfirm, Checkbox, Popover } from 'antd'
import { SendOutlined, PlusOutlined, DeleteOutlined, EditOutlined, RobotOutlined, UserOutlined, StopOutlined, PictureOutlined, CloseCircleOutlined, SyncOutlined, MessageOutlined, ReloadOutlined, SettingOutlined, FolderOpenOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { Session, Message, ToolStep, TokenUsage, Task, TaskListResponse, StreamEvent, McpServer } from '../types'
import { useWebSocket } from '../hooks/useWebSocket'
import type { WsEvent } from '../hooks/useWebSocket'
import './ChatPage.css'
import 'highlight.js/styles/github-dark.css'
import { useTaskPolling } from '../hooks/useTaskPolling'
import { requestNotificationPermission, notifyTaskComplete } from '../utils/notification'
import { ToolStepsPanel } from '../components/ToolStepsPanel'
import { AssistantMarkdownContent } from '../components/AssistantMarkdownContent'
import WorkspaceFilePickerModal from '../components/WorkspaceFilePickerModal'

const WS_BASE_URL = `ws://${typeof window !== 'undefined' ? window.location.hostname : 'localhost'}:8765`

const { Header, Sider, Content } = Layout
// TextArea removed in favor of contentEditable input
const { Text } = Typography

// sessionStorage key 前缀
const STORAGE_PREFIX = 'nanobot_streaming_'

/** 用户主动选中的会话 ID，避免刷新/离开页面再回来时被「列表第一项」覆盖（流式会话往往排在最前） */
const SELECTED_CHAT_SESSION_KEY = 'nanobot_selected_chat_session_id'

function persistSelectedChatSessionId(sessionId: string | null) {
  try {
    if (sessionId) {
      sessionStorage.setItem(SELECTED_CHAT_SESSION_KEY, sessionId)
    } else {
      sessionStorage.removeItem(SELECTED_CHAT_SESSION_KEY)
    }
  } catch {
    /* ignore quota / private mode */
  }
}

function readPersistedSelectedChatSessionId(): string | null {
  try {
    return sessionStorage.getItem(SELECTED_CHAT_SESSION_KEY)
  } catch {
    return null
  }
}

function renderContentWithFileTags(content: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  const regex = /<file>(.*?)<\/file>/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = regex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(<span key={`t-${match.index}`}>{content.slice(lastIndex, match.index)}</span>)
    }
    nodes.push(
      <Tag key={`f-${match.index}`} color="blue" className="message-file-tag" style={{ marginRight: 4 }}>
        {match[1]}
      </Tag>
    )
    lastIndex = regex.lastIndex
  }
  if (lastIndex < content.length) {
    nodes.push(<span key="t-end">{content.slice(lastIndex)}</span>)
  }
  return nodes
}

function escapeHtml(str: string) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function inputToHTML(text: string) {
  let html = ''
  const regex = /<file>(.*?)<\/file>/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = regex.exec(text)) !== null) {
    html += escapeHtml(text.slice(last, m.index)).replace(/\n/g, '<br>')
    const path = escapeHtml(m[1])
    html += `<span contenteditable="false" class="chat-input-chip" data-path="${path}">${path}<span class="chat-input-chip-close">×</span></span>`
    last = m.index + m[0].length
  }
  html += escapeHtml(text.slice(last)).replace(/\n/g, '<br>')
  return html
}

function editableToInput(el: HTMLDivElement) {
  const clone = el.cloneNode(true) as HTMLDivElement
  clone.querySelectorAll('.chat-input-chip').forEach(chip => {
    const path = chip.getAttribute('data-path') || chip.textContent?.replace('×', '').trim() || ''
    chip.replaceWith(document.createTextNode(`<file>${path}</file>`))
  })
  let text = ''
  const walk = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent || ''
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const tag = (node as HTMLElement).tagName
      if (tag === 'BR') {
        text += '\n'
      } else {
        node.childNodes.forEach(walk)
        if (tag === 'DIV' || tag === 'P') {
          text += '\n'
        }
      }
    }
  }
  clone.childNodes.forEach(walk)
  return text.replace(/\n+$/, '')
}

function getInputOffset(el: HTMLDivElement, range: Range): number {
  const clone = el.cloneNode(true) as HTMLDivElement

  function getPath(root: Node, target: Node): number[] | null {
    if (root === target) return []
    for (let i = 0; i < root.childNodes.length; i++) {
      const child = root.childNodes[i]
      const subPath = getPath(child, target)
      if (subPath !== null) {
        return [i, ...subPath]
      }
    }
    return null
  }

  const path = getPath(el, range.startContainer)
  if (!path) return 0

  let clonedNode: Node = clone
  for (const idx of path) {
    clonedNode = clonedNode.childNodes[idx]
  }

  if (clonedNode.nodeType === Node.TEXT_NODE) {
    const text = clonedNode as Text
    text.textContent = text.textContent?.slice(0, range.startOffset) || ''
  } else if (clonedNode.nodeType === Node.ELEMENT_NODE && range.startOffset < clonedNode.childNodes.length) {
    while (clonedNode.childNodes.length > range.startOffset) {
      clonedNode.removeChild(clonedNode.lastChild!)
    }
  }

  function removeAfter(node: Node, root: Node) {
    if (node === root) return
    const parent = node.parentNode!
    let sibling = node.nextSibling
    while (sibling) {
      const next = sibling.nextSibling
      parent.removeChild(sibling)
      sibling = next
    }
    removeAfter(parent, root)
  }
  removeAfter(clonedNode, clone)

  return editableToInput(clone).length
}

function setCursorAtOffset(el: HTMLDivElement, targetOffset: number) {
  const sel = window.getSelection()
  const range = document.createRange()

  function walk(node: Node): boolean {
    if (node.nodeType === Node.TEXT_NODE) {
      const len = node.textContent?.length || 0
      if (targetOffset <= len) {
        range.setStart(node, targetOffset)
        range.setEnd(node, targetOffset)
        return true
      }
      targetOffset -= len
      return false
    }
    if (node.nodeType === Node.ELEMENT_NODE) {
      const elem = node as HTMLElement
      if (elem.classList?.contains('chat-input-chip')) {
        const path = elem.getAttribute('data-path') || ''
        const len = `<file>${path}</file>`.length
        if (targetOffset <= len) {
          range.setStartAfter(node)
          range.setEndAfter(node)
          return true
        }
        targetOffset -= len
        return false
      }
      if (elem.tagName === 'BR') {
        if (targetOffset <= 1) {
          range.setStartBefore(node)
          range.setEndBefore(node)
          return true
        }
        targetOffset -= 1
        return false
      }
      for (const child of Array.from(node.childNodes)) {
        if (walk(child)) return true
      }
      if (elem.tagName === 'DIV' || elem.tagName === 'P') {
        if (targetOffset <= 1) {
          range.setStartAfter(node)
          range.setEndAfter(node)
          return true
        }
        targetOffset -= 1
      }
    }
    return false
  }

  if (walk(el)) {
    sel?.removeAllRanges()
    sel?.addRange(range)
  } else {
    range.selectNodeContents(el)
    range.collapse(false)
    sel?.removeAllRanges()
    sel?.addRange(range)
  }
}

// 流式状态存储键
const getStreamingStateKey = (sessionId: string) => `${STORAGE_PREFIX}${sessionId}`

interface StreamingState {
  toolSteps: ToolStep[]
  thinking: boolean
  progress: string
  loading: boolean
  lastUpdate: number
  taskId?: string  // 关联的任务 ID，用于后台轮询
  sessionId?: string  // 关联的会话 ID
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
  const location = useLocation()
  const [sessions, setSessions] = useState<Session[]>([])
  const [currentSession, setCurrentSession] = useState<Session | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  // 正在流式传输的会话 ID 集合（支持并行多会话独立流式）
  const [streamingSessionIds, setStreamingSessionIds] = useState<Set<string>>(new Set())
  const streamingSessionIdsRef = useRef<Set<string>>(new Set())
  streamingSessionIdsRef.current = streamingSessionIds
  /** 本轮发送前 messages.length，用于识别「漏收 done」：切换会话时 WS 断开，服务端已落库助手消息但前端仍认为在流式中 */
  const streamBaselineBySessionRef = useRef<Map<string, number>>(new Map())
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [streamingToolSteps, setStreamingToolSteps] = useState<ToolStep[]>([])
  const [streamingThinking, setStreamingThinking] = useState(false)
  const [claudeCodeProgress, setClaudeCodeProgress] = useState('')
  // 与 state 同步，供 session 切换时快照到 bgSessionStatesRef
  const streamingToolStepsRef = useRef<ToolStep[]>([])
  streamingToolStepsRef.current = streamingToolSteps
  const streamingThinkingRef = useRef(false)
  streamingThinkingRef.current = streamingThinking
  const claudeCodeProgressRef = useRef('')
  claudeCodeProgressRef.current = claudeCodeProgress
  /** 从「正在流式」集合中移除会话（done / error / stop / abort 共用，避免 handleWsMessage 闭包依赖变化） */
  const removeFromStreamingSessionsRef = useRef<(id: string) => void>(() => {})
  removeFromStreamingSessionsRef.current = (sessionId: string) => {
    setStreamingSessionIds(prev => {
      const next = new Set(prev)
      next.delete(sessionId)
      return next
    })
  }
  // 跟踪待发送的用户消息内容（用于立即显示在加载区域）
  const pendingUserMessageRef = useRef<string>('')
  const [pendingImages, setPendingImages] = useState<string[]>([])
  const [imageSendStatus, setImageSendStatus] = useState<Record<number, 'sending' | 'sent' | 'error'>>({})
  const inputRef = useRef<HTMLDivElement>(null)
  const savedRangeRef = useRef<Range | null>(null)
  const savedCursorOffsetRef = useRef<number | null>(null)
  const pendingFiles = useMemo(() => {
    const files: string[] = []
    const regex = /<file>(.*?)<\/file>/g
    let m: RegExpExecArray | null
    while ((m = regex.exec(input)) !== null) {
      files.push(m[1])
    }
    return files
  }, [input])
  const [filePickerVisible, setFilePickerVisible] = useState(false)
  const [sessionTokenUsage, setSessionTokenUsage] = useState<TokenUsage>({
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
  })
  const [tasks, setTasks] = useState<Task[]>([])
  const [currentModelName, setCurrentModelName] = useState<string>('')
  // 工具模式选择
  const [toolMode, setToolMode] = useState<'disable' | 'auto' | 'specified'>('disable')
  const [selectedMcpServers, setSelectedMcpServers] = useState<string[]>([])
  const [availableMcps, setAvailableMcps] = useState<McpServer[]>([])
  // 使用 ref 来跟踪是否需要轮询（避免 useEffect 依赖问题）
  const pollingEnabledRef = useRef(true)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  // 当前会话 ID 的 ref（供异步流式回调路由使用，避免闭包捕获过期值）
  const currentSessionIdRef = useRef<string | null>(null)
  currentSessionIdRef.current = currentSession?.id ?? null
  // 每个会话独立的 AbortController（支持并行多会话各自停止）
  const perSessionAbortControllers = useRef<Map<string, AbortController>>(new Map())
  // 后台会话的流式 UI 状态缓存（切换回来时恢复 toolSteps/thinking/progress）
  const bgSessionStatesRef = useRef<Map<string, { toolSteps: ToolStep[], thinking: boolean, progress: string }>>(new Map())
  const imageInputRef = useRef<HTMLInputElement>(null)
  // 追踪是否有活跃的流式请求
  const isStreamingRef = useRef(false)
  // 每轮 render 与 streamingSessionIds 对齐：仅当「当前选中会话 ∈ 流式集合」才算正在流式。否则 saveStreamingState 的 effect 若先于会话切换 effect 执行，会把上一会话的 loading 误写入新会话的 sessionStorage，触发错误 tryReconnect + 卡住。
  if (currentSession) {
    isStreamingRef.current = streamingSessionIds.has(currentSession.id)
  } else {
    isStreamingRef.current = false
  }
  // 追踪 session switch effect 上一次处理的 session ID（区分「会话切换」和「流式状态变更」）
  const prevSessionIdForEffectRef = useRef<string | null>(null)
  // 追踪页面是否刚恢复
  const isRestoringRef = useRef(false)
  // 追踪当前运行的 Claude Code 任务 ID（用于后台轮询）
  const currentTaskIdRef = useRef<string | null>(null)
  // 追踪是否正在从 session 恢复 MCP 设置（避免触发保存）
  const isRestoringMcpRef = useRef(false)
  /** 在收到 SSE done 时刷新列表（定义在下方，每轮 render 末尾更新引用） */
  const refreshStreamDoneRef = useRef<(sessionId: string) => void>(() => {})
  const loadSessionsRef = useRef<() => void>(() => {})
  // bgAgent stream 是否已干净结束（收到 stream_done），用于避免切 tab 时无效重连
  const bgAgentsStreamFinishedRef = useRef(false)
  // 用于驱动轮询 hook 的 state（ref 不会触发重新渲染）
  const [pollingTaskId, setPollingTaskId] = useState<string | null>(null)
  // 用于批量处理 tool_stream_chunk 的 debounce refs（按 sessionId 分组，16ms 批量 flush）
  const pendingChunksRef = useRef<Map<string, Array<{ toolId: string; chunk: string; isError: boolean; timestamp: number }>>>(new Map())
  const chunkFlushTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())
  // 用于批量处理 LLM text delta 的 debounce refs
  const pendingTextDeltaRef = useRef('')
  const textDeltaFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 后台子 Agent 进度状态
  const [bgAgents, setBgAgents] = useState<Array<{
    taskId: string
    label: string
    status: 'running' | 'done' | 'error' | 'timeout' | 'cancelled'
    progress: string
    backend: string
    result?: string  // 完整结果
    disconnected?: boolean  // WebSocket 连接是否断开
  }>>([])
  const bgAgentsRef = useRef(bgAgents)  // Track current bgAgents for use in stream handler

  // WebSocket 连接 refs
  const wsSendRef = useRef<((data: object) => void) | null>(null)
  const wsDisconnectRef = useRef<(() => void) | null>(null)
  const wsClearPendingRef = useRef<(() => void) | null>(null)
  const lastStreamReconnectToastRef = useRef(0)
  // 存储当前的 stream event handler（避免 useWebSocket 依赖变化导致频繁重连）
  const streamHandlerRef = useRef<(evt: StreamEvent) => void>(() => {})
  // Track the streaming assistant message ID to prevent duplicate rendering
  const streamingAssistantIdRef = useRef<string | null>(null)
  const loadMessagesRef = useRef<(sessionId: string) => void>(() => {})

  // 使用任务轮询 hook，当页面从后台恢复时检查任务状态
  useTaskPolling({
    taskId: pollingTaskId,
    enabled: !!pollingTaskId && !isStreamingRef.current,
    interval: 3000,
    onComplete: (result) => {
      console.log('Task completed via polling:', result)
      // 任务完成，清理状态
      setLoading(false)
      setStreamingThinking(false)
      setClaudeCodeProgress('')
      currentTaskIdRef.current = null
      setPollingTaskId(null)

      // 发送浏览器通知
      if (result.status === 'done') {
        notifyTaskComplete(result.taskId, result.result || undefined)
      }

      // 刷新消息列表
      if (currentSession) {
        clearStreamingState(currentSession.id)
        void loadMessages(currentSession.id)
      }
    },
    onError: (err) => {
      console.error('Task polling error:', err)
    },
  })

  // 同步 bgAgentsRef，供 processStreamEvent 中读取当前值
  useEffect(() => {
    bgAgentsRef.current = bgAgents
  }, [bgAgents])

  // 批量 flush pending chunks 到 UI（16ms debounce ≈ 1 frame at 60fps，避免每条都重渲染）
  const flushPendingChunks = useCallback((sessionId: string) => {
    const pending = pendingChunksRef.current.get(sessionId)
    if (!pending || pending.length === 0) return

    setStreamingToolSteps(prev => {
      const updated = [...prev]
      let changed = false
      for (const { toolId, chunk, isError, timestamp } of pending) {
        const idx = updated.findIndex(s => s.id === toolId)
        if (idx === -1) continue
        const step = updated[idx]
        const existing = step.outputChunks || []
        // 限制 buffer 大小防止内存膨胀
        const merged = [...existing, { chunk, isError, timestamp }].slice(-100)
        updated[idx] = { ...step, outputChunks: merged }
        changed = true
      }
      return changed ? updated : prev
    })

    pendingChunksRef.current.set(sessionId, [])
    const timer = chunkFlushTimersRef.current.get(sessionId)
    if (timer) { clearTimeout(timer); chunkFlushTimersRef.current.delete(sessionId) }
  }, [])

  const flushTextDeltas = useCallback(() => {
    const text = pendingTextDeltaRef.current
    pendingTextDeltaRef.current = ''
    textDeltaFlushTimerRef.current = null
    if (!text) return
    const sessionId = currentSessionIdRef.current
    if (!sessionId) return
    setMessages(prev => {
      const tempAssistant = prev.find(m => m.id === 'temp-assistant')
      if (tempAssistant) {
        return prev.map(m => m.id === 'temp-assistant' ? { ...m, content: m.content + text } : m)
      }
      return [...prev, {
        id: 'temp-assistant',
        sessionId,
        role: 'assistant',
        content: text,
        createdAt: new Date().toISOString(),
        sequence: prev.length + 1,
      }]
    })
  }, [])

  // WebSocket 消息处理：将 WsEvent 转换为 StreamEvent 并路由
  const handleWsMessage = useCallback((event: WsEvent) => {
    console.log('[handleWsMessage] received:', JSON.stringify(event))
    const sessionId = currentSessionIdRef.current
    if (!sessionId) return

    // 用户点击停止后，后端中断 agent 并发送 cancelled（可带 assistantMessage：用户已取消操作）
    if (event.event?.type === 'cancelled') {
      if (sessionId === currentSessionIdRef.current) {
        setLoading(false)
        setStreamingThinking(false)
        setStreamingToolSteps([])
        setClaudeCodeProgress('')
        isStreamingRef.current = false
        streamingAssistantIdRef.current = null
        currentTaskIdRef.current = null
        setPollingTaskId(null)
        clearStreamingState(sessionId)
        abortControllerRef.current = null
        removeFromStreamingSessionsRef.current(sessionId)
        streamBaselineBySessionRef.current.delete(sessionId)
        const cancelAssistant = (event.event as { assistantMessage?: Message | null }).assistantMessage
        if (cancelAssistant) {
          setMessages(prev => {
            const withoutTempAssistant = prev.filter(m => !(m.id.startsWith('temp-') && m.role === 'assistant'))
            streamingAssistantIdRef.current = cancelAssistant.id
            return [...withoutTempAssistant, cancelAssistant]
          })
        }
        void loadMessages(sessionId)
        void loadSessionTokenUsage(sessionId)
      }
      void loadSessions()
      return
    }

    // 处理 done 事件
    if (event.event?.type === 'done') {
      console.log('[handleWsMessage] processing done, assistantMessage:', JSON.stringify(event.event.assistantMessage))
      if (sessionId === currentSessionIdRef.current) {
        setLoading(false)
        setStreamingThinking(false)
        setStreamingToolSteps([])
        setClaudeCodeProgress('')

        // 将真实 assistantMessage 数据合并到现有的 temp-assistant 中（避免 React key 变化导致整段内容重新挂载/闪烁）
        let assistantMsg = event.event.assistantMessage as Message | null | undefined
        // 防御性合并：如果后端 assistantMsg 缺少 toolSteps，但前端有正在展示的流式 toolSteps，则保留它们
        if (assistantMsg && !assistantMsg.toolSteps && streamingToolStepsRef.current.length > 0) {
          assistantMsg = { ...assistantMsg, toolSteps: streamingToolStepsRef.current }
        }
        console.log('[handleWsMessage] setting messages, assistantMsg:', JSON.stringify(assistantMsg))
        setMessages(prev => {
          const idx = prev.findIndex(m => m.id === 'temp-assistant')
          if (idx !== -1 && assistantMsg) {
            const next = [...prev]
            // 保留 temp-assistant 的 id 以避免 remount，但更新其余所有字段为真实消息数据
            next[idx] = { ...assistantMsg, id: 'temp-assistant' }
            streamingAssistantIdRef.current = assistantMsg.id
            return next
          }
          // 兜底：没有 temp-assistant 时直接追加
          const withoutTempAssistant = prev.filter(m => !(m.id.startsWith('temp-') && m.role === 'assistant'))
          if (assistantMsg) {
            streamingAssistantIdRef.current = assistantMsg.id
            return [...withoutTempAssistant, assistantMsg]
          }
          return withoutTempAssistant
        })
        // 重新加载消息，确保用户消息和助手消息都以真实 ID 显示
        void loadMessages(sessionId)
        void loadSessionTokenUsage(sessionId)
      } else {
        void loadSessions()
      }
      // 更新图片状态为已发送
      if (sessionId === currentSessionIdRef.current) {
        setImageSendStatus(prev => {
          const newStatus = { ...prev }
          Object.keys(newStatus).forEach(k => { newStatus[Number(k)] = 'sent' })
          return newStatus
        })
      }
      // 在清空前，把当前 toolSteps 快照到 bgSessionStatesRef，供后续 loadMessages 兜底恢复
      if (streamingToolStepsRef.current.length > 0) {
        bgSessionStatesRef.current.set(sessionId, {
          toolSteps: [...streamingToolStepsRef.current],
          thinking: streamingThinkingRef.current,
          progress: claudeCodeProgressRef.current,
        })
      }
      // 清理流式状态
      if (sessionId === currentSessionIdRef.current) {
        isStreamingRef.current = false
        setStreamingToolSteps([])
        setStreamingThinking(false)
        setClaudeCodeProgress('')
        streamingAssistantIdRef.current = null
        currentTaskIdRef.current = null
        setPollingTaskId(null)
        clearStreamingState(sessionId)
        abortControllerRef.current = null
        pendingUserMessageRef.current = ''  // 清除待发送消息
        removeFromStreamingSessionsRef.current(sessionId)
        streamBaselineBySessionRef.current.delete(sessionId)
      }
      void loadSessions()
      return
    }

    // 处理错误事件
    if (event.error) {
      console.error('WebSocket error:', event.error)
      antMessage.error(event.error)
      if (sessionId === currentSessionIdRef.current) {
        setLoading(false)
        setMessages(prev => prev.filter(m => !m.id.startsWith('temp-')))
        isStreamingRef.current = false
        setStreamingToolSteps([])
        setStreamingThinking(false)
        setClaudeCodeProgress('')
        streamingAssistantIdRef.current = null
        currentTaskIdRef.current = null
        setPollingTaskId(null)
        abortControllerRef.current = null
        removeFromStreamingSessionsRef.current(sessionId)
        streamBaselineBySessionRef.current.delete(sessionId)
      }
      return
    }

    // 处理其他 stream 事件
    if (event.event) {
      streamHandlerRef.current(event.event as StreamEvent)
    }
  }, [])

  // WebSocket 连接管理
  const wsUrl = currentSession ? `${WS_BASE_URL}/ws/${currentSession.id}` : ''
  const { send: wsSend, disconnect: wsDisconnect, clearPendingSend: wsClearPending } = useWebSocket({
    url: wsUrl,
    onMessage: handleWsMessage,
    onConnect: () => {
      console.log('[WebSocket] Connected')
      // 切回本会话并重连后补拉消息：若长任务在浏览其他会话时已完成，可能漏收 done
      const sid = currentSessionIdRef.current
      if (sid && streamingSessionIdsRef.current.has(sid)) {
        void loadMessagesRef.current(sid)
      }
    },
    onDisconnect: () => {
      console.log('[WebSocket] Disconnected')
    },
    onError: (error) => {
      console.error('[WebSocket] Error:', error)
    },
    reconnect: true,
    reconnectInterval: 3000,
  })

  // 更新 WebSocket refs（仅在连接变化时更新）
  useEffect(() => {
    wsSendRef.current = wsSend
    wsDisconnectRef.current = wsDisconnect
    wsClearPendingRef.current = wsClearPending
  }, [wsSend, wsDisconnect, wsClearPending])

  // 流式事件处理（WebSocket 与当前选中会话一一对应，事件均属当前会话）
  const processStreamEvent = useCallback((evt: StreamEvent) => {
    const sessionId = currentSessionIdRef.current
    if (!sessionId) return

    if (evt.type === 'done') {
      if (textDeltaFlushTimerRef.current) {
        clearTimeout(textDeltaFlushTimerRef.current)
        flushTextDeltas()
      }
      bgSessionStatesRef.current.delete(sessionId)
      // Skip reload if handleWsMessage already added assistantMessage
      if (streamingAssistantIdRef.current) {
        streamingAssistantIdRef.current = null
        setStreamingThinking(false)
        setStreamingToolSteps([])
        setClaudeCodeProgress('')
        return
      }
      setStreamingThinking(false)
      setStreamingToolSteps([])
      setClaudeCodeProgress('')
      refreshStreamDoneRef.current(sessionId)
      return
    }

    // 当前会话：更新 UI 状态
    if (evt.type === 'thinking') {
      setStreamingThinking(true)
    } else if (evt.type === 'delta') {
      pendingTextDeltaRef.current += (evt.text || '')
      if (!textDeltaFlushTimerRef.current) {
        textDeltaFlushTimerRef.current = setTimeout(() => flushTextDeltas(), 16)
      }
    } else if (evt.type === 'stream_end') {
      if (textDeltaFlushTimerRef.current) {
        clearTimeout(textDeltaFlushTimerRef.current)
        flushTextDeltas()
      }
      setStreamingThinking(false)
    } else if (evt.type === 'tool_start' && evt.id && evt.name) {
      setStreamingThinking(false)
      if (evt.name === 'claude_code') {
        setClaudeCodeProgress('')
      }
      // subagent 进度通过 WebSocket 接收，不再单独启动 SSE
      setStreamingToolSteps(prev => [...prev, {
        id: evt.id,
        name: evt.name,
        arguments: evt.arguments ?? {},
        result: '',
        status: 'running',
        startTime: Date.now(),
      }])
    } else if (evt.type === 'tool_progress' && evt.tool_id) {
      setStreamingToolSteps(prev => prev.map(step =>
        step.id === evt.tool_id
          ? { ...step, status: evt.status as 'running' | 'waiting', progress: { detail: evt.detail, percent: evt.progress_percent, lastUpdate: Date.now() } }
          : step
      ))
    } else if (evt.type === 'tool_stream_chunk' && evt.tool_id) {
      // Debounce: batch chunks every 16ms instead of per-event re-render
      if (!pendingChunksRef.current.has(sessionId)) {
        pendingChunksRef.current.set(sessionId, [])
      }
      pendingChunksRef.current.get(sessionId)!.push({
        toolId: evt.tool_id,
        chunk: evt.chunk,
        isError: evt.is_error || false,
        timestamp: Date.now(),
      })
      if (!chunkFlushTimersRef.current.has(sessionId)) {
        const timer = setTimeout(() => flushPendingChunks(sessionId), 8)
        chunkFlushTimersRef.current.set(sessionId, timer)
      }
    } else if (evt.type === 'tool_end' && evt.id) {
      setStreamingToolSteps(prev => prev.map(step =>
        (step.id === evt.id || step.name === evt.name) && step.status !== 'completed'
          ? { ...step, result: evt.result ?? '', status: 'completed', endTime: Date.now(), durationMs: step.startTime ? Date.now() - step.startTime : undefined }
          : step
      ))
      if (evt.name === 'claude_code') {
        setClaudeCodeProgress('')
      }
    } else if (evt.type === 'claude_code_progress') {
      const subtype = evt.subtype || 'text'
      const content = evt.content || ''
      if (evt.task_id && !currentTaskIdRef.current) {
        currentTaskIdRef.current = evt.task_id
        if (document.visibilityState === 'hidden') {
          setPollingTaskId(evt.task_id)
        }
      }
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
    } else if (evt.type === 'subagent_start') {
      // WebSocket 接收 subagent 进度事件（不再走 SSE）
      setBgAgents(prev => {
        if (prev.find(a => a.taskId === evt.task_id)) return prev
        return [...prev, {
          taskId: evt.task_id,
          label: evt.label,
          status: 'running',
          progress: evt.task.slice(0, 80),
          backend: evt.backend,
        }]
      })
      if (!currentTaskIdRef.current && evt.backend === 'claude_code') {
        currentTaskIdRef.current = evt.task_id
        if (document.visibilityState === 'hidden') {
          setPollingTaskId(evt.task_id)
        }
      }
    } else if (evt.type === 'subagent_progress') {
      setBgAgents(prev => prev.map(a =>
        a.taskId === evt.task_id
          ? { ...a, progress: evt.content ? (a.progress + '\n' + evt.content).slice(-500) : a.progress }
          : a
      ))
    } else if (evt.type === 'subagent_end') {
      const statusMap: Record<string, typeof bgAgents[number]['status']> = {
        ok: 'done', error: 'error', timeout: 'timeout', cancelled: 'cancelled',
      }
      setBgAgents(prev => prev.map(a =>
        a.taskId === evt.task_id ? { ...a, status: statusMap[evt.status] ?? 'error', result: evt.summary, disconnected: false } : a
      ))
    } else if (evt.type === 'subagent_summary') {
      setBgAgents(prev => prev.map(a =>
        (a.taskId === evt.task_id || (evt.task_ids && evt.task_ids.includes(a.taskId)))
          ? { ...a, result: evt.llm_summary, status: 'done' as const, disconnected: false }
          : a
      ))
      refreshStreamDoneRef.current(sessionId)
    } else if (evt.type === 'microkernel_end') {
      setBgAgents(prev => prev.map(a =>
        a.taskId === evt.task_id ? { ...a, status: 'done' as const, disconnected: false } : a
      ))
    } else if (evt.type === 'stream_done') {
      // 所有子 agent 已结束，标记（后续由 auto-clear 机制清理）
      bgAgentsStreamFinishedRef.current = true
    }
  }, [setBgAgents])

  // 更新 streamHandlerRef 当 session 变化时
  useEffect(() => {
    streamHandlerRef.current = processStreamEvent
  }, [processStreamEvent])

  // WebSocket 重连处理（刷新/切换 tab 后等待 WebSocket 自动重连，然后刷新数据）
  const tryReconnectChatStream = useCallback(async (sessionId: string) => {
    console.log('[WebSocket] Waiting for reconnection...')
    try {
      // 等待一小段时间让 WebSocket 自动重连
      await new Promise(resolve => setTimeout(resolve, 1000))
      // 刷新数据
      await loadMessages(sessionId)
      await loadSessionTokenUsage(sessionId)
      void loadSessions()
      const now = Date.now()
      if (now - lastStreamReconnectToastRef.current > 4000) {
        lastStreamReconnectToastRef.current = now
        antMessage.success(t('chat.streamReconnected'))
      }
    } catch (e) {
      console.error('[tryReconnectChatStream]', e)
    } finally {
      // 无论成功与否都清掉该会话的快照，并结束 loading；否则误触发重连后会一直卡在「加载中」直到用户点停止
      clearStreamingState(sessionId)
      if (currentSessionIdRef.current === sessionId) {
        setLoading(false)
        setStreamingThinking(false)
        setStreamingToolSteps([])
        setClaudeCodeProgress('')
        isRestoringRef.current = false
      }
    }
  }, [t])

  // 所有子 Agent 完成后，10 秒后自动清除面板
  // 注意：如果连接已断开，保留面板让用户手动刷新
  useEffect(() => {
    // 如果有正在运行的任务，或者连接已断开，不自动清除
    const hasRunning = bgAgents.some(a => a.status === 'running')
    const hasDisconnected = bgAgents.some(a => a.disconnected)

    console.log('[BgAgent] Auto-clear check:', { hasRunning, hasDisconnected, length: bgAgents.length })

    if (bgAgents.length > 0 && !hasRunning && !hasDisconnected) {
      console.log('[BgAgent] Scheduling auto-clear in 12 seconds')
      const timer = setTimeout(() => {
        console.log('[BgAgent] Auto-clear triggered, clearing bgAgents')
        setBgAgents([])
      }, 12000)
      return () => clearTimeout(timer)
    }
  }, [bgAgents])

  // 切换 session 时清除子 Agent 状态
  useEffect(() => {
    return () => {
      setBgAgents([])
    }
  }, [currentSession?.id])

  // 清理 chunk debounce timers（顶层清理，组件卸载时执行）
  useEffect(() => {
    return () => {
      for (const timer of chunkFlushTimersRef.current.values()) {
        clearTimeout(timer)
      }
      pendingChunksRef.current.clear()
      chunkFlushTimersRef.current.clear()
    }
  }, [])

  // 仅在「指定 MCP」时需要列表；禁用/自动时不请求 /mcps，避免首屏与首条消息变慢
  useEffect(() => {
    if (toolMode === 'specified') {
      api.getMcps().then(data => {
        setAvailableMcps(data || [])
      }).catch(console.error)
    } else {
      setAvailableMcps([])
    }
  }, [toolMode])

  // 页面可见性变化处理
  useEffect(() => {
    const handleVisibilityChange = () => {
      if (!currentSession) return

      if (document.visibilityState === 'hidden') {
        // 页面隐藏时，保存当前流式状态
        // 只要界面上还有工具面板/思考/loading 就保存，避免 isStreamingRef 竞态导致漏存
        if (
          currentSession &&
          (streamingToolSteps.length > 0 || streamingThinking || loading)
        ) {
          saveStreamingState(currentSession.id, {
            toolSteps: streamingToolSteps,
            thinking: streamingThinking,
            progress: claudeCodeProgress,
            loading,
            taskId: currentTaskIdRef.current || undefined,
            sessionId: currentSession.id,
          })
          console.log('Saved streaming state to sessionStorage (hidden)', streamingToolSteps.length)
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

          // 恢复 taskId 用于后台轮询
          if (savedState.taskId) {
            currentTaskIdRef.current = savedState.taskId
            // 延迟启动轮询，让 SSE 有机会先恢复
            setTimeout(() => {
              if (!isStreamingRef.current && savedState.taskId) {
                console.log('Starting task polling for:', savedState.taskId)
                setPollingTaskId(savedState.taskId)
              }
            }, 1000)
          }

          // 重要：检查 SSE 连接是否已断开，尝试重连 Chat 流
          setTimeout(() => {
            if (!isStreamingRef.current) {
              console.log('SSE connection lost during tab switch, attempting reconnect')
              // 尝试重连 Chat SSE 以继续接收推送
              if (savedState.loading && savedState.sessionId === currentSession.id) {
                void tryReconnectChatStream(currentSession.id)
              } else if (!savedState.taskId) {
                setLoading(false)
                setStreamingThinking(false)
                isRestoringRef.current = false
                clearStreamingState(currentSession.id)
                void loadMessages(currentSession.id)
              }
            }
          }, 500) // 延迟检查，让 finally 块有机会先执行
        }

        // 页面重新可见时，WebSocket 会自动重连并接收所有事件
        // 不需要额外处理（subagent 事件通过 processStreamEvent 处理）
        const hasDisconnectedAgents = bgAgents.some(a => a.disconnected)
        if (hasDisconnectedAgents) {
          // 仅刷新状态，移除断线标记
          setBgAgents(prev => prev.map(a => ({ ...a, disconnected: false })))
        }
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [currentSession, tryReconnectChatStream])

  // 当流式状态变化时，自动保存到 sessionStorage（必须同时属于 streamingSessionIds，避免切到另一会话的第一帧误写入）
  useEffect(() => {
    if (
      currentSession &&
      isStreamingRef.current &&
      streamingSessionIds.has(currentSession.id)
    ) {
      saveStreamingState(currentSession.id, {
        toolSteps: streamingToolSteps,
        thinking: streamingThinking,
        progress: claudeCodeProgress,
        loading,
        taskId: currentTaskIdRef.current || undefined,
        sessionId: currentSession.id,
      })
    }
  }, [currentSession, streamingSessionIds, streamingToolSteps, streamingThinking, claudeCodeProgress, loading])

  useEffect(() => {
    loadSessions()
    // 请求浏览器通知权限（用于后台任务完成提醒）
    void requestNotificationPermission()
  }, [])

  // 仅在启用工具（自动/指定 MCP）时预热后端 MCP，避免「禁用工具」时仍拉 MCP 导致首条消息变慢
  useEffect(() => {
    if (toolMode === 'disable') return
    api.warmup().catch(() => { /* 忽略预热错误 */ })
  }, [toolMode])

  // 加载当前使用的模型名称（页面可见时刷新，确保配置变更后能实时更新）
  const loadCurrentModel = useCallback(() => {
    api.getConfig().then((config) => {
      const defaultModel = config.models?.find((m) => m.isDefault) ?? config.models?.[0]
      setCurrentModelName(defaultModel?.modelName || defaultModel?.name || '')
    }).catch(() => {})
  }, [])
  useEffect(() => {
    loadCurrentModel()
  }, [loadCurrentModel])
  useEffect(() => {
    const onVisible = () => { loadCurrentModel() }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [loadCurrentModel])

  // 切回页面且为「指定 MCP」时刷新列表（禁用/自动不请求）
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && toolMode === 'specified') {
        api.getMcps().then(setAvailableMcps).catch(() => setAvailableMcps([]))
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [toolMode])

  // Session 切换时恢复状态（不中止后台流式，支持并行聊天）
  useEffect(() => {
    const sessionId = currentSession?.id ?? null
    const prevId = prevSessionIdForEffectRef.current
    const sessionActuallyChanged = sessionId !== prevId
    // 离开仍在流式中的会话时，把当前工具/思考 UI 快照到缓存，切回时可恢复
    if (sessionActuallyChanged && prevId && streamingSessionIds.has(prevId)) {
      bgSessionStatesRef.current.set(prevId, {
        toolSteps: [...streamingToolStepsRef.current],
        thinking: streamingThinkingRef.current,
        progress: claudeCodeProgressRef.current,
      })
    }
    prevSessionIdForEffectRef.current = sessionId

    // 只在真正切换会话时重置流式显示状态
    if (sessionActuallyChanged) {
      setStreamingToolSteps([])
      setStreamingThinking(false)
      setClaudeCodeProgress('')
    }

    if (currentSession && sessionId) {
      const isStreaming = streamingSessionIds.has(sessionId)

      if (isStreaming) {
        // 目标会话正在后台流式：恢复其缓存的 UI 状态
        const bgState = bgSessionStatesRef.current.get(sessionId)
        if (bgState) {
          setStreamingToolSteps(bgState.toolSteps)
          setStreamingThinking(bgState.thinking)
          setClaudeCodeProgress(bgState.progress)
        }
        setLoading(true)
        isStreamingRef.current = true
        abortControllerRef.current = perSessionAbortControllers.current.get(sessionId) || null
      } else {
        // 目标会话没有进行流式：清除 loading
        setLoading(false)
        isStreamingRef.current = false
        abortControllerRef.current = null
      }

      loadCurrentModel()
      // 仅在真正切换会话、或当前会话不处于流式状态时加载消息
      // 避免同一会话启动流式时（setStreamingSessionIds 触发本 effect）覆盖刚加入的 temp 用户消息
      if (sessionActuallyChanged || !isStreaming) {
        loadMessages(sessionId)
      }
      loadSessionTokenUsage(sessionId)

      // 非流式进行中时，尝试从 sessionStorage 恢复（用于页面刷新后恢复）
      if (!isStreaming) {
        const savedState = loadStreamingState(sessionId)
        if (savedState) {
          isRestoringRef.current = true
          setStreamingToolSteps(savedState.toolSteps || [])
          setStreamingThinking(savedState.thinking || false)
          setClaudeCodeProgress(savedState.progress || '')
          if (savedState.loading) setLoading(true)
          if (savedState.taskId) {
            currentTaskIdRef.current = savedState.taskId
            setPollingTaskId(savedState.taskId)
          }
          // 尝试重连 Chat SSE（刷新后恢复）
          setTimeout(() => {
            if (savedState.loading && savedState.sessionId === sessionId) {
              void tryReconnectChatStream(sessionId)
            } else if (!savedState.taskId) {
              setLoading(false)
              clearStreamingState(sessionId)
              void loadMessages(sessionId)
            }
          }, 300)
        }
      }
    } else {
      setLoading(false)
      isStreamingRef.current = false
      abortControllerRef.current = null
      setSessionTokenUsage({ promptTokens: 0, completionTokens: 0, totalTokens: 0 })
    }
  }, [currentSession, tryReconnectChatStream, loadCurrentModel, streamingSessionIds])

  // 监听路由变化，当从其他页面切回聊天页面时恢复状态
  useEffect(() => {
    if (location.pathname === '/chat') {
      loadCurrentModel()
    }
    // 只有当前 session 存在时才尝试恢复状态
    if (currentSession && location.pathname === '/chat') {
      const savedState = loadStreamingState(currentSession.id)
      if (savedState) {
        // 如果当前没有流式状态，但 sessionStorage 中有，则恢复
        if (!isStreamingRef.current && !streamingThinking && streamingToolSteps.length === 0) {
          // 先恢复状态显示给用户
          setStreamingToolSteps(savedState.toolSteps || [])
          setStreamingThinking(savedState.thinking || false)
          setClaudeCodeProgress(savedState.progress || '')
          if (savedState.loading) {
            setLoading(true)
          }
          console.log('Route changed: Restored streaming state for session:', currentSession.id)

          // 延迟检查 SSE 连接是否断开
          setTimeout(() => {
            if (!isStreamingRef.current && loading) {
              console.log('Route switch: SSE disconnected, clearing loading')
              setLoading(false)
              setStreamingThinking(false)
              clearStreamingState(currentSession.id)
              // 刷新消息获取最新状态
              void loadMessages(currentSession.id)
            }
          }, 500)
        }
      }
    }
  }, [location.pathname, currentSession, loadCurrentModel])

  useEffect(() => {
    scrollToBottom()
  }, [messages, loading, streamingToolSteps, streamingThinking, claudeCodeProgress])

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
      // 必须用 ref：loadSessions 常从 handleWsMessage（空依赖）等闭包中调用，await 后 state 中的 currentSession 可能仍是旧值
      if (data.items.length > 0 && !currentSessionIdRef.current) {
        const savedId = readPersistedSelectedChatSessionId()
        const fromPersist = savedId ? data.items.find((s) => s.id === savedId) : undefined
        handleSelectSession(fromPersist ?? data.items[0])
      }
    } catch (error) {
      console.error('[loadSessions]', error)
      antMessage.error(t('chat.loadSessionsFailed'))
    } finally {
      setLoadingSessions(false)
    }
  }

  const loadTasks = async (page = 1, pageSize = 20) => {
    try {
      const data: TaskListResponse = await api.getTasks(page, pageSize, 'all')
      setTasks(data.items)
      // 如果没有运行中的任务，停止轮询
      const runningCount = data.items.filter(task => task.status === 'running').length
      pollingEnabledRef.current = runningCount > 0
    } catch (error) {
      console.error(error)
    }
  }

  // 断线后的「running」仅用于展示卡片，不应占用主发送钮：否则会一直走 handleStop，无法 POST 新消息
  const runningBgAgentsCount = bgAgents.filter(
    a => a.status === 'running' && !a.disconnected
  ).length

  // 切换会话时恢复 MCP 工具设置（warmup 由 toolMode 的 useEffect 统一处理，禁用工具时不预热）
  const handleSelectSession = (session: Session) => {
    persistSelectedChatSessionId(session.id)
    isRestoringMcpRef.current = true
    setCurrentSession(session)
    setToolMode(session.toolMode || 'disable')
    setSelectedMcpServers(session.selectedMcpServers || [])
    setTimeout(() => { isRestoringMcpRef.current = false }, 50)
  }

  // MCP 工具设置变化时自动保存到当前会话
  useEffect(() => {
    if (!currentSession || isRestoringMcpRef.current) return
    void api.updateSession(currentSession.id, toolMode, selectedMcpServers)
  }, [toolMode, selectedMcpServers]) // eslint-disable-line react-hooks/exhaustive-deps

  const loadSessionTokenUsage = async (sessionId: string) => {
    try {
      const usage = await api.getSessionTokenSummary(sessionId)
      setSessionTokenUsage(usage)
    } catch (error) {
      console.error(error)
      setSessionTokenUsage({ promptTokens: 0, completionTokens: 0, totalTokens: 0 })
    }
  }

  const loadMessages = async (sessionId: string) => {
    try {
      let data = await api.getMessages(sessionId)
      // 诊断日志：检查 API 返回的最新 assistant 是否带了 toolSteps
      const lastAssistantForDebug = [...data].reverse().find(m => m.role === 'assistant')
      console.log('[loadMessages] lastAssistant toolSteps?', lastAssistantForDebug?.toolSteps?.length ?? 'missing')

      // 防御性合并：若 API 返回的 assistant 消息缺少 toolSteps，尝试从 sessionStorage / bgSessionStatesRef / 当前 ref 恢复
      let restoredToolSteps = loadStreamingState(sessionId)?.toolSteps
      if (!restoredToolSteps || restoredToolSteps.length === 0) {
        const bgState = bgSessionStatesRef.current.get(sessionId)
        if (bgState && bgState.toolSteps && bgState.toolSteps.length > 0) {
          restoredToolSteps = bgState.toolSteps
        }
      }
      if (!restoredToolSteps || restoredToolSteps.length === 0) {
        if (streamingToolStepsRef.current.length > 0) {
          restoredToolSteps = streamingToolStepsRef.current
        }
      }
      if (restoredToolSteps && restoredToolSteps.length > 0) {
        const revAssistantIdx = [...data].reverse().findIndex(m => m.role === 'assistant')
        if (revAssistantIdx !== -1) {
          const actualIndex = data.length - 1 - revAssistantIdx
          if (!data[actualIndex].toolSteps || data[actualIndex].toolSteps!.length === 0) {
            data = data.map((m, idx) =>
              idx === actualIndex ? { ...m, toolSteps: restoredToolSteps } : m
            )
            console.log('[loadMessages] restored toolSteps from fallback', restoredToolSteps.length)
          }
        }
      }
      setMessages(data)

      // 切换会话时当前会话的 WebSocket 会断开，done 可能未送达；若 DB 已有人机各一条新消息则对齐流式状态
      const baseline = streamBaselineBySessionRef.current.get(sessionId)
      if (
        baseline !== undefined &&
        streamingSessionIdsRef.current.has(sessionId) &&
        currentSessionIdRef.current === sessionId &&
        data.length >= baseline + 2
      ) {
        const last = data[data.length - 1]
        const lastUser = [...data].reverse().find(m => m.role === 'user')
        if (
          last.role === 'assistant' &&
          !String(last.id).startsWith('temp-') &&
          lastUser &&
          last.sequence > lastUser.sequence
        ) {
          console.log('[Chat] Reconciled missed WebSocket done from messages API, session:', sessionId)
          setLoading(false)
          setStreamingThinking(false)
          setStreamingToolSteps([])
          setClaudeCodeProgress('')
          isStreamingRef.current = false
          streamingAssistantIdRef.current = last.id
          currentTaskIdRef.current = null
          setPollingTaskId(null)
          clearStreamingState(sessionId)
          abortControllerRef.current = null
          pendingUserMessageRef.current = ''
          removeFromStreamingSessionsRef.current(sessionId)
          streamBaselineBySessionRef.current.delete(sessionId)
          void loadSessionTokenUsage(sessionId)
          void loadSessions()
        }
      }
    } catch (error) {
      antMessage.error(t('chat.loadMessagesFailed'))
      console.error(error)
    }
  }

  loadMessagesRef.current = loadMessages

  // 长任务在后台完成时 done 可能已丢失，轮询消息列表直到 reconcile 成功或收到 WS done
  useEffect(() => {
    const sid = currentSession?.id
    if (!sid || !loading) return
    const t = window.setInterval(() => {
      if (!streamingSessionIdsRef.current.has(sid)) return
      void loadMessagesRef.current(sid)
    }, 4000)
    return () => clearInterval(t)
  }, [currentSession?.id, loading])

  loadSessionsRef.current = () => {
    void loadSessions()
  }
  refreshStreamDoneRef.current = (sessionId: string) => {
    void loadMessages(sessionId)
    void loadSessionTokenUsage(sessionId)
    void loadSessions()
  }

  const handleCreateSession = async () => {
    try {
      const session = await api.createSession(t('chat.defaultTitle'))
      setSessions([session, ...sessions])
      // 新建会话时默认禁用 MCP 工具
      setToolMode('disable')
      setSelectedMcpServers([])
      handleSelectSession(session)
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
        const nextSession = newSessions[0]
        if (nextSession) {
          handleSelectSession(nextSession)
        } else {
          persistSelectedChatSessionId(null)
          setCurrentSession(null)
        }
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
    const sessionId = currentSession?.id
    // 清除 sessionStorage 流式快照，否则会话 effect 误认为仍在流式中并反复 tryReconnectChatStream + Toast
    if (sessionId) {
      clearStreamingState(sessionId)
    }
    wsClearPendingRef.current?.()
    // 停止当前会话的独立 AbortController
    const ctrl = sessionId ? perSessionAbortControllers.current.get(sessionId) : null
    const hadStream = ctrl !== null || abortControllerRef.current !== null
    if (ctrl) {
      ctrl.abort()
      if (sessionId) perSessionAbortControllers.current.delete(sessionId)
    } else if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    abortControllerRef.current = null
    setLoading(false)
    if (sessionId) {
      removeFromStreamingSessionsRef.current(sessionId)
      streamBaselineBySessionRef.current.delete(sessionId)
    }
    if (hadStream) {
      antMessage.info(t('chat.generationStopped'))
    }
    try {
      // 传递当前 session ID 以只取消该 session 的子代理
      await api.stopAgent(currentSession?.id)
    } catch (e) {
      console.warn('Stop agent request failed:', e)
    }
  }

  const handleSend = useCallback(async () => {
    if (!currentSession) return
    if (!input.trim() && !pendingImages.length && !pendingFiles.length) return
    // 若当前会话已在流式中则转为停止操作
    if (streamingSessionIds.has(currentSession.id)) {
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

    // 捕获当前会话 ID，避免闭包中使用过期值
    const sessionId = currentSession.id

    const userMessage = input.trim()
    const imagesToSend = [...pendingImages]
    setInput('')
    if (inputRef.current) {
      inputRef.current.innerHTML = ''
    }
    savedRangeRef.current = null
    savedCursorOffsetRef.current = null
    setPendingImages([])
    setImageSendStatus({})
    setLoading(true)
    setStreamingToolSteps([])
    setStreamingThinking(false)
    setClaudeCodeProgress('')
    // 将此会话加入流式集合（支持并行多会话）
    setStreamingSessionIds(prev => new Set([...prev, sessionId]))
    isStreamingRef.current = true

    const controller = new AbortController()
    perSessionAbortControllers.current.set(sessionId, controller)
    abortControllerRef.current = controller

    streamingAssistantIdRef.current = null  // Reset before new stream
    // 实际发送内容：输入框中已包含 <file>path</file>
    const actualContent = userMessage
    // 跟踪待发送的用户消息，用于立即显示
    pendingUserMessageRef.current = actualContent
    const tempUserMsg: Message = {
      id: `temp-${Date.now()}`,
      sessionId,
      role: 'user',
      content: actualContent,
      createdAt: new Date().toISOString(),
      sequence: messages.length + 1,
      images: imagesToSend.length > 0 ? imagesToSend : undefined,
    }
    // 记录发送前条数：用于 loadMessages 判断服务端是否已落库「用户+助手」以补收漏掉的 done
    streamBaselineBySessionRef.current.set(sessionId, messages.length)
    setMessages(prev => {
      // 清理上一轮可能残留的 temp-assistant（避免新一轮流式追加到旧消息上）
      const cleaned = prev.filter(m => m.id !== 'temp-assistant')
      return [...cleaned, tempUserMsg]
    })

    if (imagesToSend.length > 0) {
      const status: Record<number, 'sending' | 'sent' | 'error'> = {}
      imagesToSend.forEach((_, i) => { status[i] = 'sending' })
      setImageSendStatus(status)
    }

    // 通过 WebSocket 发送消息（done/error 由 handleWsMessage 处理）
    const sendFn = wsSendRef.current
    if (!sendFn) {
      // WebSocket 未连接，消息已在 hook 中缓冲，连接恢复后会自动发送
      console.warn('[WebSocket] Not connected, message buffered for reconnection')
      // 保持用户消息可见，只清除 loading 状态
      setLoading(false)
      removeFromStreamingSessionsRef.current(sessionId)
      streamBaselineBySessionRef.current.delete(sessionId)
      bgSessionStatesRef.current.delete(sessionId)
      isStreamingRef.current = false
      antMessage.warning(t('chat.reconnecting'))
      return
    }

    // 监听 abort 以清理状态
    controller.signal.addEventListener('abort', () => {
      console.log('[WebSocket] Message sending aborted')
      clearStreamingState(sessionId)
      wsClearPendingRef.current?.()
      perSessionAbortControllers.current.delete(sessionId)
      removeFromStreamingSessionsRef.current(sessionId)
      streamBaselineBySessionRef.current.delete(sessionId)
      bgSessionStatesRef.current.delete(sessionId)
      setLoading(false)
      isStreamingRef.current = false
      setStreamingToolSteps([])
      setStreamingThinking(false)
      setClaudeCodeProgress('')
      currentTaskIdRef.current = null
      setPollingTaskId(null)
      abortControllerRef.current = null
    })

    // 通过 WebSocket 发送消息
    sendFn({
      type: 'message',
      content: actualContent,
      media: imagesToSend.length > 0 ? imagesToSend : undefined,
      toolMode,
      selectedMcpServers: toolMode === 'specified' ? selectedMcpServers : undefined,
    })
  }, [input, currentSession, pendingImages, t, toolMode, selectedMcpServers, tasks, messages, streamingSessionIds])

  const handleFileInsert = useCallback((path: string) => {
    setFilePickerVisible(false)
    if (!inputRef.current) {
      const tag = `<file>${path}</file>`
      setInput(prev => (prev ? `${prev} ${tag}` : tag))
      return
    }
    const el = inputRef.current
    const offset = savedCursorOffsetRef.current ?? 0
    const clampedOffset = Math.max(0, Math.min(offset, input.length))

    const tag = `<file>${path}</file>`
    const before = input.slice(0, clampedOffset)
    const after = input.slice(clampedOffset)
    const needSpaceBefore = before.length > 0 && !before.endsWith(' ') && !before.endsWith('\n')
    const needSpaceAfter = after.length > 0 && !after.startsWith(' ') && !after.startsWith('\n')
    const spacerBefore = needSpaceBefore ? ' ' : ''
    const spacerAfter = needSpaceAfter ? ' ' : ''
    const newInput = before + spacerBefore + tag + spacerAfter + after

    setInput(newInput)
    el.innerHTML = inputToHTML(newInput)

    const chipEndOffset = clampedOffset + spacerBefore.length + tag.length
    el.focus()
    setCursorAtOffset(el, chipEndOffset)

    savedCursorOffsetRef.current = null
    savedRangeRef.current = null
  }, [input])

  const handleEditableInput = () => {
    if (!inputRef.current) return
    setInput(editableToInput(inputRef.current))
  }

  const handleEditablePaste = (e: React.ClipboardEvent<HTMLDivElement>) => {
    e.preventDefault()
    const text = e.clipboardData.getData('text/plain')
    if (!text) return
    document.execCommand('insertText', false, text)
  }

  const handleEditableKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
      return
    }
    if (!inputRef.current) return
    const sel = window.getSelection()
    if (!sel || sel.rangeCount === 0) return
    const range = sel.getRangeAt(0)

    const isAtChipBoundary = (dir: 'before' | 'after') => {
      if (!range.collapsed) return false
      if (dir === 'before') {
        if (range.startOffset !== 0) return false
        const prev = range.startContainer.previousSibling
        return prev && (prev as HTMLElement).classList?.contains('chat-input-chip')
      } else {
        const len = range.startContainer.textContent?.length || 0
        if (range.startOffset !== len) return false
        const next = range.startContainer.nextSibling
        return next && (next as HTMLElement).classList?.contains('chat-input-chip')
      }
    }

    if (e.key === 'Backspace' && isAtChipBoundary('before')) {
      e.preventDefault()
      range.startContainer.previousSibling!.remove()
      handleEditableInput()
      return
    }
    if (e.key === 'Delete' && isAtChipBoundary('after')) {
      e.preventDefault()
      range.startContainer.nextSibling!.remove()
      handleEditableInput()
      return
    }
  }

  const handleEditableClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement
    if (target.closest('.chat-input-chip-close')) {
      const chip = target.closest('.chat-input-chip') as HTMLElement | null
      if (chip && inputRef.current) {
        chip.remove()
        handleEditableInput()
      }
    }
  }

  const handleEditableBlur = () => {
    const sel = window.getSelection()
    if (sel && sel.rangeCount > 0 && inputRef.current) {
      const r = sel.getRangeAt(0)
      if (inputRef.current.contains(r.commonAncestorContainer)) {
        savedRangeRef.current = r.cloneRange()
        savedCursorOffsetRef.current = getInputOffset(inputRef.current, r)
      }
    }
  }

  // 当 input 从外部变更（发送清空、删除 tag 等）时同步到 DOM
  useEffect(() => {
    if (!inputRef.current) return
    // 若输入框正被聚焦，说明用户在主动输入，避免重写 innerHTML 导致光标跳动/IME 中断
    if (document.activeElement === inputRef.current) return
    const current = editableToInput(inputRef.current)
    if (current !== input) {
      inputRef.current.innerHTML = inputToHTML(input)
    }
  }, [input])

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

  return (
    <>
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
                  onClick={() => handleSelectSession(session)}
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
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 4 }}>
                          {streamingSessionIds.has(session.id) && (
                            <SyncOutlined spin style={{ fontSize: 12, color: '#1890ff', flexShrink: 0 }} />
                          )}
                          <Text ellipsis className="session-title" style={{ display: 'block' }}>
                            {session.title || t('chat.defaultTitle')}
                          </Text>
                        </div>
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
              {/* 当前模型名称 */}
              {currentModelName && (
                <Tag color="blue">{currentModelName}</Tag>
              )}
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
                              <ToolStepsPanel
                                steps={message.toolSteps}
                                showRunningOnLast={false}
                                maxVisibleBeforeCollapse={6}
                              />
                            )}
                            <AssistantMarkdownContent content={message.content} />
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
                            <div className="message-user-content">{renderContentWithFileTags(message.content)}</div>
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
                      <div className="message-text loading-text streaming-assistant-panel">
                        {streamingThinking && (
                          <div className="streaming-thinking-row">
                            <span className="pulse-dot" aria-hidden />
                            <Text type="secondary">{t('chat.thinking')}</Text>
                          </div>
                        )}
                        {streamingToolSteps.length > 0 && (
                          <ToolStepsPanel steps={streamingToolSteps} showRunningOnLast maxVisibleBeforeCollapse={6} />
                        )}
                        {!!claudeCodeProgress && (
                          <Collapse ghost size="small" className="claude-code-progress-collapse">
                            <Collapse.Panel header="Claude Code" key="ccp">
                              <pre className="claude-code-progress-pre">{claudeCodeProgress}</pre>
                            </Collapse.Panel>
                          </Collapse>
                        )}
                        <div className="loading-status">
                          <Spin size="small" />
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </Content>

        {/* 后台子 Agent 进度面板 */}
        {bgAgents.length > 0 && (
          <div className="bg-agents-panel">
            <div className="bg-agents-header">
              <SyncOutlined spin={bgAgents.some(a => a.status === 'running')} style={{ marginRight: 6 }} />
              <span>后台子 Agent ({bgAgents.filter(a => a.status === 'running').length} 运行中)</span>
              {(bgAgents.some(a => a.disconnected) || bgAgents.every(a => a.status !== 'running')) && (
                <Button
                  type="link"
                  size="small"
                  onClick={() => {
                    if (currentSession) {
                      // WebSocket 自动重连，刷新消息获取最新状态
                      void loadMessages(currentSession.id)
                      void loadSessionTokenUsage(currentSession.id)
                      // 移除断线标记
                      setBgAgents(prev => prev.map(a => ({ ...a, disconnected: false })))
                    }
                  }}
                  style={{ marginLeft: 'auto', padding: '0 4px' }}
                >
                  刷新 <ReloadOutlined />
                </Button>
              )}
            </div>
            {bgAgents.map(agent => (
              <div key={agent.taskId} className={`bg-agent-item ${agent.status}`}>
                <div className="bg-agent-title">
                  {agent.status === 'running' && <Spin size="small" style={{ marginRight: 6 }} />}
                  {agent.status === 'done' && <span style={{ marginRight: 6 }}>✅</span>}
                  {agent.status === 'timeout' && <span style={{ marginRight: 6 }}>⏳</span>}
                  {agent.status === 'error' && <span style={{ marginRight: 6 }}>❌</span>}
                  {agent.status === 'cancelled' && <span style={{ marginRight: 6 }}>🛑</span>}
                  {agent.disconnected && <span style={{ marginRight: 6 }}>🔌</span>}
                  <span className="bg-agent-label">{agent.label}</span>
                  <Tag className="bg-agent-backend" color={agent.backend === 'claude_code' ? 'orange' : 'blue'}>
                    {agent.backend === 'claude_code' ? 'Claude Code' : 'native'}
                  </Tag>
                  {agent.status === 'running' && (
                    <Popconfirm
                      title={t('chat.tasks.cancelConfirm')}
                      onConfirm={async () => {
                        try {
                          await api.cancelTask(agent.taskId)
                          antMessage.success(t('chat.tasks.cancelSuccess'))
                          setBgAgents(prev => prev.map(a =>
                            a.taskId === agent.taskId ? { ...a, status: 'cancelled' as const } : a
                          ))
                        } catch (e) {
                          antMessage.error(t('chat.tasks.cancelFailed'))
                          console.error(e)
                        }
                      }}
                      okText={t('chat.tasks.cancel')}
                      cancelText={t('common.cancel')}
                    >
                      <Button type="link" size="small" danger style={{ padding: '0 4px', marginLeft: 'auto' }}>
                        <CloseCircleOutlined /> {t('chat.tasks.cancel')}
                      </Button>
                    </Popconfirm>
                  )}
                  {(agent.disconnected || agent.status === 'done') && agent.result && (
                    <Tooltip title="自动发送消息询问执行结果">
                      <Button
                        type="link"
                        size="small"
                        onClick={async () => {
                          // 直接发送消息询问子agent的执行结果
                          // 主agent会自动通过 get_subagent_results 工具获取结果
                          // 先清除该agent的状态，避免重复询问
                          setBgAgents(prev => prev.filter(a => a.taskId !== agent.taskId))
                          // 发送简短的消息
                          setInput(`查看执行结果`)
                          // 等待状态更新后触发发送
                          setTimeout(() => {
                            const btn = document.querySelector('.chat-send-button') as HTMLButtonElement
                            if (btn) btn.click()
                          }, 50)
                        }}
                        style={{ padding: '0 4px', marginLeft: 'auto' }}
                      >
                        继续对话 <MessageOutlined />
                      </Button>
                    </Tooltip>
                  )}
                </div>
                {agent.status === 'done' && agent.result ? (
                  <Collapse ghost size="small">
                    <Collapse.Panel key="result" header="查看完整结果">
                      <pre className="bg-agent-result">{agent.result || ''}</pre>
                    </Collapse.Panel>
                  </Collapse>
                ) : agent.progress ? (
                  <pre className="bg-agent-progress">{agent.progress}</pre>
                ) : null}
              </div>
            ))}
          </div>
        )}

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
            <div className="chat-input-wrapper">
              <div
                ref={inputRef}
                className="chat-input chat-input-editable"
                contentEditable={!currentSession || loading ? false : true}
                onInput={handleEditableInput}
                onKeyDown={handleEditableKeyDown}
                onPaste={handleEditablePaste}
                onClick={handleEditableClick}
                onBlur={handleEditableBlur}
                data-placeholder={t('chat.inputPlaceholder')}
                aria-placeholder={t('chat.inputPlaceholder')}
                suppressContentEditableWarning
              />
            </div>
            <Button
              type="primary"
              icon={(loading || runningBgAgentsCount > 0) ? <StopOutlined /> : <SendOutlined />}
              onClick={(loading || runningBgAgentsCount > 0) ? handleStop : handleSend}
              danger={loading || runningBgAgentsCount > 0}
              disabled={(!currentSession || (!input.trim() && pendingImages.length === 0 && pendingFiles.length === 0)) && !loading && runningBgAgentsCount === 0}
              className="send-button"
            >
              {(loading || runningBgAgentsCount > 0) ? t('chat.stop') : t('chat.send')}
            </Button>
          </div>
          <div className="chat-tool-status-row">
            <Popover
              trigger="click"
              placement="bottomLeft"
              content={
                toolMode !== 'specified' ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 120 }}>
                    {(['disable', 'auto', 'specified'] as const).map(mode => (
                      <Button
                        key={mode}
                        type={toolMode === mode ? 'primary' : 'text'}
                        size="small"
                        block
                        onClick={() => setToolMode(mode)}
                        style={{ textAlign: 'left' }}
                      >
                        {mode === 'disable' ? t('chat.toolModeDisable') : mode === 'auto' ? t('chat.toolModeAuto') : t('chat.toolModeSpecified')}
                      </Button>
                    ))}
                  </div>
                ) : (
                  <div style={{ minWidth: 180 }}>
                    <div style={{ marginBottom: 8, fontSize: 12, color: '#888' }}>{t('chat.toolModeServerSelect')}</div>
                    {availableMcps.length === 0 ? (
                      <div style={{ color: '#888', fontSize: 12 }}>{t('chat.toolModeNoServers')}</div>
                    ) : (
                      <Checkbox.Group
                        value={selectedMcpServers}
                        onChange={(vals) => setSelectedMcpServers(vals as string[])}
                        style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
                      >
                        {availableMcps.filter(m => m.enabled).map(mcp => (
                          <Checkbox key={mcp.id} value={mcp.id} style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                            {mcp.name}
                          </Checkbox>
                        ))}
                      </Checkbox.Group>
                    )}
                    <Button
                      type="text"
                      size="small"
                      onClick={() => setToolMode('auto')}
                      style={{ marginTop: 8, fontSize: 12, padding: '0' }}
                    >
                      ← {t('chat.toolModeAuto')}
                    </Button>
                  </div>
                )
              }
            >
              <Tooltip title={t('chat.toolMode')}>
                <Button
                  type="text"
                  icon={<SettingOutlined />}
                  disabled={!currentSession || loading}
                  style={{
                    color: toolMode === 'disable' ? '#bbb' : '#1890ff',
                    fontSize: 16,
                  }}
                />
              </Tooltip>
            </Popover>
            <Tooltip title={t('chat.filePicker')}>
              <Button
                type="text"
                icon={<FolderOpenOutlined />}
                onMouseDown={() => {
                  const sel = window.getSelection()
                  if (sel && sel.rangeCount > 0 && inputRef.current) {
                    const r = sel.getRangeAt(0)
                    if (inputRef.current.contains(r.commonAncestorContainer)) {
                      savedRangeRef.current = r.cloneRange()
                      savedCursorOffsetRef.current = getInputOffset(inputRef.current, r)
                    }
                  }
                }}
                onClick={() => setFilePickerVisible(true)}
                disabled={!currentSession || loading}
                className="file-picker-button"
                style={{ fontSize: 16, marginLeft: 4 }}
              />
            </Tooltip>
            <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap', marginLeft: 'auto' }}>
              {t('chat.tokenUsageInline', {
                input: formatTokenNumber(sessionTokenUsage.promptTokens),
                output: formatTokenNumber(sessionTokenUsage.completionTokens),
                total: formatTokenNumber(sessionTokenUsage.totalTokens),
              })}
            </Text>
          </div>
        </div>
      </Layout>
    </Layout>

    <WorkspaceFilePickerModal
      open={filePickerVisible}
      onClose={() => setFilePickerVisible(false)}
      onInsert={handleFileInsert}
    />
    </>
  )
}

export default ChatPage
