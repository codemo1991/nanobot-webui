import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation } from 'react-router-dom'
import { Layout, Input, Button, List, Typography, Avatar, Space, Spin, message as antMessage, Empty, Collapse, Tooltip, Image, Tag, Modal, Popconfirm, Checkbox, Popover } from 'antd'
import { SendOutlined, PlusOutlined, DeleteOutlined, EditOutlined, RobotOutlined, UserOutlined, StopOutlined, PictureOutlined, CloseCircleOutlined, SyncOutlined, MessageOutlined, ReloadOutlined, SettingOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { api } from '../api'
import type { Session, Message, ToolStep, TokenUsage, Task, TaskListResponse, SubagentProgressEvent, StreamEvent, McpServer } from '../types'
import { ToolStepsPanel } from '../components/ToolStepsPanel'
import './ChatPage.css'
import 'highlight.js/styles/github-dark.css'
import { useTaskPolling } from '../hooks/useTaskPolling'
import { requestNotificationPermission, notifyTaskComplete } from '../utils/notification'

const { Header, Sider, Content } = Layout
const { TextArea } = Input
const { Text } = Typography

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
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [streamingToolSteps, setStreamingToolSteps] = useState<ToolStep[]>([])
  const [streamingThinking, setStreamingThinking] = useState(false)
  const [streamingCursorFast, setStreamingCursorFast] = useState(true)
  const [cursorVisible, setCursorVisible] = useState(false)
  const cursorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [claudeCodeProgress, setClaudeCodeProgress] = useState('')
  const claudeCodeLineCount = useMemo(
    () => (claudeCodeProgress ? claudeCodeProgress.split('\n').length : 0),
    [claudeCodeProgress],
  )
  const [pendingImages, setPendingImages] = useState<string[]>([])
  const [imageSendStatus, setImageSendStatus] = useState<Record<number, 'sending' | 'sent' | 'error'>>({})
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
  // 每个会话独立的 AbortController（支持并行多会话各自停止）
  const perSessionAbortControllers = useRef<Map<string, AbortController>>(new Map())
  // 后台会话的流式 UI 状态缓存（切换回来时恢复 toolSteps/thinking/progress）
  const bgSessionStatesRef = useRef<Map<string, { toolSteps: ToolStep[], thinking: boolean, progress: string }>>(new Map())
  const imageInputRef = useRef<HTMLInputElement>(null)
  // 追踪是否有活跃的流式请求
  const isStreamingRef = useRef(false)
  // 追踪页面是否刚恢复
  const isRestoringRef = useRef(false)
  // 追踪当前运行的 Claude Code 任务 ID（用于后台轮询）
  const currentTaskIdRef = useRef<string | null>(null)
  // 追踪是否正在从 session 恢复 MCP 设置（避免触发保存）
  const isRestoringMcpRef = useRef(false)
  // 已处理过的 subagent_summary message_id 集合，防止 replay 时重复插入消息
  // （loadMessages 会将 id 从 "msg_subagent_xxx" 替换为 "msg_{seq}"，导致朴素 id 比对失效）
  const seenSummaryIdsRef = useRef<Set<string>>(new Set())
  // 已发送过浏览器通知的 task_id，防止 replay 时重复通知
  const notifiedTasksRef = useRef<Set<string>>(new Set())
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

  // 后台子 Agent 进度状态
  const [bgAgents, setBgAgents] = useState<Array<{
    taskId: string
    label: string
    status: 'running' | 'done' | 'error' | 'timeout' | 'cancelled'
    progress: string
    backend: string
    result?: string  // 完整结果
    disconnected?: boolean  // SSE连接是否断开
  }>>([])
  const bgAgentsAbortRef = useRef<AbortController | null>(null)
  const bgAgentsSessionRef = useRef<string | null>(null)

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

  // 启动子 Agent 进度 SSE 订阅
  const startBgAgentStream = useCallback((sessionId: string) => {
    // 已在监听同一 session，不重复创建
    if (bgAgentsAbortRef.current && bgAgentsSessionRef.current === sessionId) return
    // 关闭旧的连接
    if (bgAgentsAbortRef.current) {
      bgAgentsAbortRef.current.abort()
      bgAgentsAbortRef.current = null
    }
    // 新连接开始，重置 finished 标志
    bgAgentsStreamFinishedRef.current = false
    // 清理旧会话的 bgAgents 状态，防止内存泄漏
    setBgAgents([])
    console.log('[BgAgent] Starting subagent progress stream for session:', sessionId)
    const ctrl = new AbortController()
    bgAgentsAbortRef.current = ctrl
    bgAgentsSessionRef.current = sessionId

    const handleEvt = (evt: SubagentProgressEvent) => {
      try {
        console.log('[BgAgent] Received event:', evt.type, 'task_id' in evt ? evt.task_id : undefined, 'label' in evt ? evt.label : undefined)
        console.log('[BgAgent] Current bgAgents before:', JSON.stringify(bgAgents))
        if (evt.type === 'subagent_start') {
          // 提取需要的数据，避免在 setBgAgents 回调中访问联合类型的可能未定义属性
          const currentTaskId = evt.task_id
          const currentLabel = evt.label
          const currentTask = evt.task
          const currentBackend = evt.backend
          setBgAgents(prev => {
            if (prev.find(a => a.taskId === currentTaskId)) return prev
            return [...prev, {
            taskId: currentTaskId,
            label: currentLabel,
            status: 'running',
            progress: currentTask.slice(0, 80),
            backend: currentBackend,
          }]
        })
      } else if (evt.type === 'subagent_progress') {
        const content = evt.content || ''
        let line = ''
        if (evt.subtype === 'tool_use') {
          line = `[${evt.tool_name || 'Tool'}] ${content.slice(0, 100)}`
        } else if (evt.subtype === 'assistant_text') {
          line = content.length > 120 ? content.slice(0, 120) + '...' : content
        } else if (evt.subtype === 'subagent_start') {
          line = `🤖 ${content.slice(0, 80)}`
        } else {
          return
        }
        // 提取需要的数据，避免在 setBgAgents 回调中访问联合类型的可能未定义属性
        const currentTaskId = evt.task_id
        setBgAgents(prev => prev.map(a => {
          if (a.taskId !== currentTaskId) return a
          const lines = a.progress ? a.progress.split('\n') : []
          lines.push(line)
          const trimmed = lines.length > 20 ? lines.slice(-20) : lines
          return { ...a, progress: trimmed.join('\n') }
        }))
      } else if (evt.type === 'subagent_end') {
        // 提取需要的数据，避免在 setBgAgents 回调中访问联合类型的可能未定义属性
        const currentTaskId = evt.task_id
        const currentLabel = evt.label
        const currentStatus = evt.status
        const currentSummary = evt.summary
        const currentResult = 'result' in evt ? evt.result : undefined
        const currentBackend = ('backend' in evt && typeof evt.backend === 'string') ? evt.backend : 'unknown'
        setBgAgents(prev => {
          const existing = prev.find(a => a.taskId === currentTaskId)
          if (existing) {
            return prev.map(a =>
              a.taskId === currentTaskId
                ? {
                    ...a,
                    status: currentStatus === 'ok' ? 'done' : currentStatus === 'timeout' ? 'timeout' : currentStatus === 'cancelled' ? 'cancelled' : 'error',
                    progress: currentSummary,
                    result: currentResult,
                  }
                : a
            )
          }
          // 未收到 subagent_start 时（如 trim_buffer 或连接时机导致），subagent_end 仍创建卡片以展示结果
          return [...prev, {
            taskId: currentTaskId,
            label: currentLabel,
            status: currentStatus === 'ok' ? 'done' : currentStatus === 'timeout' ? 'timeout' : currentStatus === 'cancelled' ? 'cancelled' : 'error',
            progress: currentSummary,
            backend: currentBackend,
            result: currentResult,
          }]
        })
        // 发送浏览器通知（用 ref 去重，避免 replay 时重复通知）
        if (currentStatus === 'ok' && currentResult && !notifiedTasksRef.current.has(currentTaskId)) {
          notifiedTasksRef.current.add(currentTaskId)
          notifyTaskComplete(currentTaskId, currentResult)
        }
        console.log('[BgAgent] Current bgAgents after subagent_end:', JSON.stringify(bgAgents))
      } else if (evt.type === 'subagent_summary') {
        // seenSummaryIdsRef 去重：loadMessages 会把 id 从 "msg_subagent_xxx" 换成 "msg_{seq}"，
        // 导致朴素的 m.id 比对在 tab 切回 replay 时失效，必须用独立 ref 记录已处理过的 message_id
        if (seenSummaryIdsRef.current.has(evt.message_id)) {
          console.log('[BgAgent] Skipping duplicate subagent_summary:', evt.message_id)
          return
        }
        seenSummaryIdsRef.current.add(evt.message_id)
        // 将 LLM 总结追加为主对话中的 assistant 消息
        const summaryMsg: import('../types').Message = {
          id: evt.message_id,
          sessionId,
          role: 'assistant',
          content: evt.llm_summary,
          createdAt: evt.timestamp || new Date().toISOString(),
          sequence: Date.now(),
        }
        setMessages(prev => {
          // 双重保险：若 messages 中已有相同 id（loadMessages 前后 id 一致的情况）也跳过
          if (prev.some(m => m.id === summaryMsg.id)) return prev
          return [...prev, summaryMsg]
        })
        // 收到 summary 后自动关闭对应的后台 agent 卡片
        const idsToRemove = evt.task_ids && evt.task_ids.length > 0
          ? evt.task_ids
          : [evt.task_id]
        setBgAgents(prev => prev.filter(a => !idsToRemove.includes(a.taskId)))
        console.log('[BgAgent] Appended subagent_summary, closed cards:', idsToRemove)
      } else if (evt.type === 'timeout') {
        console.log('[BgAgent] Received timeout event')
        bgAgentsAbortRef.current = null
        bgAgentsSessionRef.current = null
      } else if (evt.type === 'stream_done') {
        console.log('[BgAgent] All subagents finished, stream closed')
        bgAgentsStreamFinishedRef.current = true  // 标记已干净结束，切 tab 时不再重连
        bgAgentsAbortRef.current = null
        bgAgentsSessionRef.current = null
      }
    } catch (err) {
      console.error('[BgAgent] Error handling event:', err)
    }
  }

    api.subagentProgressStream(sessionId, handleEvt, ctrl.signal).catch((err) => {
      console.error('[BgAgent] SSE connection lost:', err)
      console.log('[BgAgent] Current bgAgents at disconnect:', JSON.stringify(bgAgents))
      // SSE连接断开时，标记所有运行中的任务为断开状态，保留结果让用户手动获取
      // 不要清除 bgAgents，因为子agent可能还在后台运行
      setBgAgents(prev => {
        const updated = prev.map(a => {
          if (a.status === 'running') {
            return { ...a, disconnected: true, progress: a.progress + '\n(连接已断开，点击刷新获取结果)' }
          }
          return a
        })
        console.log('[BgAgent] bgAgents after disconnect handling:', JSON.stringify(updated))
        return updated
      })
      // 断开引用，避免重复处理
      bgAgentsAbortRef.current = null
      bgAgentsSessionRef.current = null
    })
  }, [])

  // 同步 currentSessionIdRef，供异步流式回调判断事件归属
  useEffect(() => {
    currentSessionIdRef.current = currentSession?.id ?? null
  }, [currentSession])

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

  // 流式事件处理工厂（按会话 ID 路由：当前会话更新 UI，后台会话更新缓存）
  const makeStreamEventHandler = useCallback((sessionId: string) => (evt: StreamEvent) => {
    const isCurrent = sessionId === currentSessionIdRef.current

    if (evt.type === 'done') {
      bgSessionStatesRef.current.delete(sessionId)
      if (isCurrent) {
        setStreamingThinking(false)
        setStreamingToolSteps([])
        setClaudeCodeProgress('')
        // 收到 done 立即拉消息，避免仅依赖 sendMessageStream 返回后的 loadMessages（消除竞态与遗漏）
        refreshStreamDoneRef.current(sessionId)
      } else {
        loadSessionsRef.current()
      }
      return
    }

    if (!isCurrent) {
      // 后台会话：只更新状态缓存，不触发 UI 重渲染
      const bgState = bgSessionStatesRef.current.get(sessionId) || { toolSteps: [], thinking: false, progress: '' }
      if (evt.type === 'thinking') {
        bgState.thinking = true
      } else if (evt.type === 'tool_start' && evt.id && evt.name) {
        bgState.thinking = false
        if (evt.name === 'claude_code') bgState.progress = ''
        bgState.toolSteps = [...bgState.toolSteps, {
          id: evt.id,
          name: evt.name,
          arguments: evt.arguments ?? {},
          result: '',
          status: 'running',
          startTime: Date.now(),
        }]
      } else if (evt.type === 'tool_progress' && evt.tool_id) {
        bgState.toolSteps = bgState.toolSteps.map(step =>
          step.id === evt.tool_id
            ? { ...step, status: evt.status as 'running' | 'waiting', progress: { detail: evt.detail, percent: evt.progress_percent, lastUpdate: Date.now() } }
            : step
        )
      } else if (evt.type === 'tool_stream_chunk' && evt.tool_id) {
        bgState.toolSteps = bgState.toolSteps.map(step =>
          step.id === evt.tool_id
            ? { ...step, outputChunks: [...(step.outputChunks || []), { chunk: evt.chunk, isError: evt.is_error || false, timestamp: Date.now() }].slice(-100) }
            : step
        )
      } else if (evt.type === 'tool_end' && evt.id) {
        const steps = [...bgState.toolSteps]
        for (let i = steps.length - 1; i >= 0; i--) {
          const step = steps[i]
          if (step && (step.id === evt.id || step.name === evt.name) && !step.result) {
            steps[i] = { ...step, result: evt.result ?? '', status: 'completed', endTime: Date.now(), durationMs: step.startTime ? Date.now() - step.startTime : undefined }
            break
          }
        }
        bgState.toolSteps = steps
        if (evt.name === 'claude_code') bgState.progress = ''
      } else if (evt.type === 'claude_code_progress') {
        const subtype = evt.subtype || 'text'
        const content = evt.content || ''
        let line = ''
        if (subtype === 'tool_use') line = `[${evt.tool_name || 'Tool'}] ${content}`
        else if (subtype === 'tool_result') line = `  -> ${content}`
        else if (subtype === 'assistant_text') line = content.length > 120 ? content.slice(0, 120) + '...' : content
        else line = content
        const newText = bgState.progress ? bgState.progress + '\n' + line : line
        const lines = newText.split('\n')
        bgState.progress = lines.length > 30 ? lines.slice(-30).join('\n') : newText
      }
      bgSessionStatesRef.current.set(sessionId, bgState)
      return
    }

    // 当前会话：正常更新 UI 状态（原 handleStreamEvent 逻辑）
    if (evt.type === 'thinking') {
      setStreamingThinking(true)
    } else if (evt.type === 'tool_start' && evt.id && evt.name) {
      setStreamingThinking(false)
      if (evt.name === 'claude_code') {
        setClaudeCodeProgress('')
      }
      if (evt.name === 'spawn') {
        startBgAgentStream(sessionId)
      }
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
        const timer = setTimeout(() => flushPendingChunks(sessionId), 16)
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
    }
  }, [startBgAgentStream])

  // 尝试重连 Chat SSE（刷新/切换 tab 后继续接收推送）
  const tryReconnectChatStream = useCallback(async (sessionId: string) => {
    const ctrl = new AbortController()
    perSessionAbortControllers.current.set(sessionId, ctrl)
    // 若为当前会话则同步 compat ref
    if (sessionId === currentSessionIdRef.current) {
      abortControllerRef.current = ctrl
      isStreamingRef.current = true
      setLoading(true)
    }
    setStreamingSessionIds(prev => new Set([...prev, sessionId]))
    try {
      const result = await api.subscribeToChatStream(sessionId, makeStreamEventHandler(sessionId), ctrl.signal)
      if (sessionId === currentSessionIdRef.current) {
        await loadMessages(sessionId)
        await loadSessionTokenUsage(sessionId)
      }
      void loadSessions()
      if (result && sessionId === currentSessionIdRef.current) {
        antMessage.success(t('chat.streamReconnected'))
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') return
      console.warn('Chat stream reconnect failed:', err)
    } finally {
      perSessionAbortControllers.current.delete(sessionId)
      setStreamingSessionIds(prev => {
        const next = new Set(prev)
        next.delete(sessionId)
        return next
      })
      // 与 handleSend 一致：重连结束即清除 loading，避免切换会话后 ref 不匹配一直转圈
      setLoading(false)
      if (sessionId === currentSessionIdRef.current) {
        isStreamingRef.current = false
        isRestoringRef.current = false
        setStreamingToolSteps([])
        setStreamingThinking(false)
        setClaudeCodeProgress('')
        currentTaskIdRef.current = null
        setPollingTaskId(null)
        abortControllerRef.current = null
      }
      clearStreamingState(sessionId)
      isRestoringRef.current = false
    }
  }, [makeStreamEventHandler, t])

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
        if (bgAgentsAbortRef.current) {
          bgAgentsAbortRef.current.abort()
          bgAgentsAbortRef.current = null
          bgAgentsSessionRef.current = null
        }
      }, 12000)
      return () => clearTimeout(timer)
    }
  }, [bgAgents])

  // 切换 session 时清除子 Agent 状态
  useEffect(() => {
    return () => {
      if (bgAgentsAbortRef.current) {
        bgAgentsAbortRef.current.abort()
        bgAgentsAbortRef.current = null
        bgAgentsSessionRef.current = null
      }
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

  // 工具模式切换到 specified 时加载 MCP 列表
  useEffect(() => {
    if (toolMode === 'specified') {
      api.getMcps().then(data => {
        setAvailableMcps(data || [])
      }).catch(console.error)
    }
  }, [toolMode])

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
            taskId: currentTaskIdRef.current || undefined,
            sessionId: currentSession.id,
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

        // 页面重新可见时，仅在以下情况重连 subagent 进度流：
        // 1. stream 未干净结束（bgAgentsStreamFinishedRef=false）：可能有未接收的事件
        // 2. 有已断线的 agent：用户需要重新拉取结果
        // 避免 stream 已完成时无意义重连触发 buffer replay 导致消息重复
        const hasDisconnectedAgents = bgAgents.some(a => a.disconnected)
        if (!bgAgentsStreamFinishedRef.current || hasDisconnectedAgents) {
          startBgAgentStream(currentSession.id)
        }
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [currentSession, tryReconnectChatStream])

  // 当流式状态变化时，自动保存到 sessionStorage
  useEffect(() => {
    if (currentSession && isStreamingRef.current) {
      saveStreamingState(currentSession.id, {
        toolSteps: streamingToolSteps,
        thinking: streamingThinking,
        progress: claudeCodeProgress,
        loading,
        taskId: currentTaskIdRef.current || undefined,
        sessionId: currentSession.id,
      })
    }
  }, [currentSession, streamingToolSteps, streamingThinking, claudeCodeProgress, loading])

  useEffect(() => {
    loadSessions()
    // 请求浏览器通知权限（用于后台任务完成提醒）
    void requestNotificationPermission()
  }, [])

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

  // 加载 MCP 服务器列表（仅在可见性变化时刷新，避免每次 session 切换都请求）
  useEffect(() => {
    api.getMcps().then(setAvailableMcps).catch(() => setAvailableMcps([]))
  }, [])
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        api.getMcps().then(setAvailableMcps).catch(() => setAvailableMcps([]))
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])

  // Session 切换时恢复状态（不中止后台流式，支持并行聊天）
  useEffect(() => {
    const sessionId = currentSession?.id ?? null

    // 重置当前 UI 的流式显示状态（将由目标会话状态填充）
    setStreamingToolSteps([])
    setStreamingThinking(false)
    setClaudeCodeProgress('')

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
      loadMessages(sessionId)
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
        handleSelectSession(data.items[0])
      }
    } catch (error) {
      antMessage.error(t('chat.loadSessionsFailed'))
      console.error(error)
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

  // 切换会话时恢复 MCP 工具设置
  const handleSelectSession = (session: Session) => {
    isRestoringMcpRef.current = true
    setCurrentSession(session)
    setToolMode(session.toolMode || 'disable')
    setSelectedMcpServers(session.selectedMcpServers || [])
    // 恢复完成后允许正常保存
    setTimeout(() => { isRestoringMcpRef.current = false }, 50)
  }

  // MCP 工具设置变化时自动保存到当前会话
  useEffect(() => {
    if (!currentSession || isRestoringMcpRef.current) return
    void api.updateSession(currentSession.id, toolMode, selectedMcpServers)
  }, [toolMode, selectedMcpServers]) // eslint-disable-line react-hooks/exhaustive-deps

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

  loadSessionsRef.current = () => {
    void loadSessions()
  }
  refreshStreamDoneRef.current = (sessionId: string) => {
    void loadMessages(sessionId)
    void loadSessionTokenUsage(sessionId)
    void loadSessions()
  }

  // Cursor blink: fast while streaming, slow after done
  useEffect(() => {
    if (loading && streamingThinking) {
      setCursorVisible(true)
      setStreamingCursorFast(true)
    } else if (loading) {
      // text streaming, start with fast
      setCursorVisible(true)
      setStreamingCursorFast(true)
    } else {
      // done, wait 300ms then switch to slow blink then hide
      if (cursorTimerRef.current) clearTimeout(cursorTimerRef.current)
      cursorTimerRef.current = setTimeout(() => {
        setStreamingCursorFast(false)
        setTimeout(() => setCursorVisible(false), 1500)
      }, 300)
    }
    return () => { if (cursorTimerRef.current) clearTimeout(cursorTimerRef.current) }
  }, [loading, streamingThinking])

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
      setStreamingSessionIds(prev => {
        const next = new Set(prev)
        next.delete(sessionId)
        return next
      })
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
    if (!input.trim() && !pendingImages.length) return
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

    const tempUserMsg: Message = {
      id: `temp-${Date.now()}`,
      sessionId,
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

    try {
      await api.sendMessageStream(
        sessionId,
        userMessage,
        makeStreamEventHandler(sessionId),
        controller.signal,
        imagesToSend.length > 0 ? imagesToSend : undefined,
        toolMode,
        toolMode === 'specified' ? selectedMcpServers : undefined,
      )
      if (imagesToSend.length > 0 && sessionId === currentSessionIdRef.current) {
        setImageSendStatus(prev => {
          const newStatus = { ...prev }
          Object.keys(newStatus).forEach(k => { newStatus[Number(k)] = 'sent' })
          return newStatus
        })
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        // 仍走 finally 从服务端拉列表，避免停止后界面停在临时消息
      } else {
        if (imagesToSend.length > 0 && sessionId === currentSessionIdRef.current) {
          setImageSendStatus(prev => {
            const newStatus = { ...prev }
            Object.keys(newStatus).forEach(k => { newStatus[Number(k)] = 'error' })
            return newStatus
          })
        }
        antMessage.error(t('chat.sendFailed'))
        if (err instanceof Error) console.error(err)
        if (sessionId === currentSessionIdRef.current) {
          setMessages(prev => prev.filter(m => !m.id.startsWith('temp-')))
        }
      }
    } finally {
      // 从并行流式集合移除该会话
      perSessionAbortControllers.current.delete(sessionId)
      setStreamingSessionIds(prev => {
        const next = new Set(prev)
        next.delete(sessionId)
        return next
      })
      bgSessionStatesRef.current.delete(sessionId)

      // 流结束始终清除 loading：若用户在请求完成前切换了会话，仅匹配 currentSessionIdRef 会漏清导致一直「请求中」
      setLoading(false)

      if (sessionId === currentSessionIdRef.current) {
        isStreamingRef.current = false
        if (isRestoringRef.current && currentSession) {
          clearStreamingState(sessionId)
          isRestoringRef.current = false
          abortControllerRef.current = null
          currentTaskIdRef.current = null
          setPollingTaskId(null)
          setStreamingToolSteps([])
          setStreamingThinking(false)
          setClaudeCodeProgress('')
        } else {
          setStreamingToolSteps([])
          setStreamingThinking(false)
          setClaudeCodeProgress('')
          currentTaskIdRef.current = null
          setPollingTaskId(null)
          clearStreamingState(sessionId)
          abortControllerRef.current = null
        }
      }
      // 无论是否收到 SSE done、是否 Abort，只要会话仍在前台，都用服务端数据刷新列表（修复第二次起偶发不刷新）
      if (sessionId === currentSessionIdRef.current) {
        void loadMessages(sessionId)
        void loadSessionTokenUsage(sessionId)
      }
      void loadSessions()
    }
  }, [input, loading, messages, currentSession, pendingImages.length, t, makeStreamEventHandler])

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
                              <ToolStepsPanel steps={message.toolSteps} />
                            )}
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              rehypePlugins={[rehypeHighlight]}
                              className="markdown-body"
                              components={{
                                table: ({ children }) => (
                                  <div className="markdown-table-wrapper">
                                    <table>{children}</table>
                                  </div>
                                ),
                              }}
                            >
                              {message.content}
                            </ReactMarkdown>
                            {cursorVisible && message.role === 'assistant' && loading && (
                              <span className={`stream-cursor ${streamingCursorFast ? '' : 'idle'}`}>|</span>
                            )}
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
                          {streamingThinking ? (
                            <span className="pulse-dot" />
                          ) : (
                            <Spin size="small" />
                          )}
                          <span>{streamingThinking ? t('chat.thinking') : streamingToolSteps.length > 0 ? t('chat.callingTool') : t('chat.thinkingOrTool')}</span>
                        </div>
                        {streamingToolSteps.length > 0 && (
                          <ToolStepsPanel steps={streamingToolSteps} showRunningOnLast />
                        )}
                        {claudeCodeProgress && (
                          <div className="claude-code-progress">
                            <div className="claude-code-output-header">
                              <span />
                              <span className="claude-code-lines-badge">
                                [{claudeCodeLineCount} lines]
                              </span>
                            </div>
                            <div className="claude-code-progress-inner">
                              <pre>{claudeCodeProgress}</pre>
                            </div>
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
                      startBgAgentStream(currentSession.id)
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
              icon={(loading || runningBgAgentsCount > 0) ? <StopOutlined /> : <SendOutlined />}
              onClick={(loading || runningBgAgentsCount > 0) ? handleStop : handleSend}
              danger={loading || runningBgAgentsCount > 0}
              disabled={(!currentSession || (!input.trim() && pendingImages.length === 0)) && !loading && runningBgAgentsCount === 0}
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
            <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
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
  )
}

export default ChatPage
