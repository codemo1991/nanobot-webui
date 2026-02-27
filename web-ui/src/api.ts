import i18n from './i18n'
import type { ApiResponse, Session, SessionListResponse, Message, ChatResponse, StreamEvent, SubagentProgressEvent, TokenUsage, Task, TaskListResponse } from './types'

const API_BASE = '/api/v1'

async function request<T>(path: string, options?: RequestInit & { skipJsonContentType?: boolean }): Promise<T> {
  const { skipJsonContentType, ...fetchOptions } = options ?? {}
  const headers: Record<string, string> =
    skipJsonContentType ? { ...(fetchOptions.headers as Record<string, string>) } : { 'Content-Type': 'application/json', ...(fetchOptions.headers as Record<string, string>) }
  const response = await fetch(`${API_BASE}${path}`, {
    ...fetchOptions,
    headers,
  })

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ success: false, error: { code: 'NETWORK_ERROR', message: i18n.t('api.networkError') } }))
    const err = new Error(errorData.error?.message || i18n.t('api.requestFailed'))
    ;(err as Error & { code?: string }).code = errorData.error?.code
    throw err
  }

  const data: ApiResponse<T> = await response.json()

  if (!data.success) {
    const err = new Error(data.error?.message || i18n.t('api.requestFailed'))
    ;(err as Error & { code?: string }).code = data.error?.code
    throw err
  }

  return data.data!
}

export { request }

export const api = {
  // Health check
  health: () => request<{ status: string }>('/health'),

  // Sessions
  getSessions: (page = 1, pageSize = 20) =>
    request<SessionListResponse>(`/chat/sessions?page=${page}&pageSize=${pageSize}`),

  createSession: (title?: string) =>
    request<Session>('/chat/sessions', {
      method: 'POST',
      body: JSON.stringify({ title }),
    }),

  deleteSession: (sessionId: string) =>
    request<{ deleted: boolean }>(`/chat/sessions/${sessionId}`, {
      method: 'DELETE',
    }),

  renameSession: (sessionId: string, title: string) =>
    request<{ id: string; title: string; updatedAt: string }>(`/chat/sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),

  // Messages
  getMessages: (sessionId: string, limit = 50, before?: number) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (before) params.append('before', String(before))
    return request<Message[]>(`/chat/sessions/${sessionId}/messages?${params}`)
  },

  getSessionTokenSummary: (sessionId: string) =>
    request<TokenUsage>(`/chat/sessions/${sessionId}/token-summary`),

  resetSessionTokenSummary: (sessionId: string) =>
    request<{ reset: boolean; scope: 'session'; sessionId: string }>(`/chat/sessions/${sessionId}/token-summary/reset`, {
      method: 'POST',
    }),

  sendMessage: (sessionId: string, content: string, signal?: AbortSignal) =>
    request<ChatResponse>(`/chat/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
      signal,
    }),

  stopAgent: (sessionId?: string) =>
    request<{ stopped: boolean }>('/chat/stop', {
      method: 'POST',
      body: sessionId ? JSON.stringify({ sessionId }) : undefined,
    }),

  /** Stream chat with SSE; calls onEvent for each progress event. Rejects on error. */
  async sendMessageStream(
    sessionId: string,
    content: string,
    onEvent: (evt: StreamEvent) => void,
    signal?: AbortSignal,
    images?: string[]
  ): Promise<ChatResponse> {
    const body: Record<string, unknown> = { content }
    if (images && images.length > 0) body.images = images
    const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}/messages?stream=1`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err?.error?.message || i18n.t('api.requestFailed'))
    }
    const reader = res.body?.getReader()
    if (!reader) throw new Error('Stream not supported')
    const dec = new TextDecoder()
    let buf = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n\n')
        buf = lines.pop() ?? ''
        for (const block of lines) {
          // SSE: data can be multi-line; collapse all "data:" lines
          const dataParts = block.split('\n')
            .filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trimStart())
          const dataStr = dataParts.join('\n')
          if (!dataStr) continue
          try {
            const evt = JSON.parse(dataStr) as StreamEvent
            onEvent(evt)
            if (evt.type === 'done') {
              return {
                content: 'content' in evt ? evt.content ?? '' : '',
                assistantMessage: 'assistantMessage' in evt ? evt.assistantMessage ?? null : null,
              }
            }
            if (evt.type === 'error') {
              throw new Error('message' in evt ? evt.message : 'Stream error')
            }
          } catch (e) {
            throw e  // 重抛任何异常，不吞掉非 Error 类型
          }
        }
      }
    } finally {
      reader.releaseLock()
    }
    throw new Error('Stream ended without done event')
  },

  // Configuration
  getConfig: () => request<import('./types').ConfigData>('/config'),

  updateAgentConfig: (agent: Partial<import('./types').AgentConfig>) =>
    request<import('./types').AgentConfig>('/config/agent', {
      method: 'PUT',
      body: JSON.stringify(agent),
    }),

  // 并发配置
  getConcurrencyConfig: () =>
    request<import('./types').ConcurrencyConfig>('/config/concurrency'),

  updateConcurrencyConfig: (config: Record<string, unknown>) =>
    request<import('./types').ConcurrencyConfig>('/config/concurrency', {
      method: 'PUT',
      body: JSON.stringify(config),
    }),

  // 监控指标
  getMetrics: () =>
    request<import('./types').Metrics>('/config/metrics'),

  resetMetrics: () =>
    request<void>('/config/metrics/reset', {
      method: 'POST',
    }),

  // Memory Configuration
  getMemoryConfig: () =>
    request<import('./types').MemoryConfig>('/config/memory'),

  updateMemoryConfig: (memory: Partial<import('./types').MemoryConfig>) =>
    request<import('./types').MemoryConfig>('/config/memory', {
      method: 'PUT',
      body: JSON.stringify(memory),
    }),

  // IM Channels (WhatsApp, Telegram, Feishu)
  getChannels: () => request<import('./types').ChannelsConfig>('/config/channels'),

  updateChannels: (channels: Partial<import('./types').ChannelsConfig>) =>
    request<import('./types').ChannelsConfig>('/config/channels', {
      method: 'PUT',
      body: JSON.stringify(channels),
    }),

  // AI Providers
  getProviders: () => request<import('./types').Provider[]>('/providers'),
  
  createProvider: (provider: Omit<import('./types').Provider, 'id'>) =>
    request<import('./types').Provider>('/providers', {
      method: 'POST',
      body: JSON.stringify(provider),
    }),
  
  updateProvider: (providerId: string, provider: Partial<import('./types').Provider>) =>
    request<import('./types').Provider>(`/providers/${providerId}`, {
      method: 'PUT',
      body: JSON.stringify(provider),
    }),
  
  deleteProvider: (providerId: string) =>
    request<{ deleted: boolean }>(`/providers/${providerId}`, {
      method: 'DELETE',
    }),

  // Models
  getModels: () => request<import('./types').Model[]>('/models'),
  
  createModel: (model: Omit<import('./types').Model, 'id'>) =>
    request<import('./types').Model>('/models', {
      method: 'POST',
      body: JSON.stringify(model),
    }),
  
  updateModel: (modelId: string, model: Partial<import('./types').Model>) =>
    request<import('./types').Model>(`/models/${modelId}`, {
      method: 'PUT',
      body: JSON.stringify(model),
    }),
  
  deleteModel: (modelId: string) =>
    request<{ deleted: boolean }>(`/models/${modelId}`, {
      method: 'DELETE',
    }),
  
  setDefaultModel: (modelId: string) =>
    request<{ success: boolean }>(`/models/${modelId}/set-default`, {
      method: 'POST',
    }),

  // MCPs
  getMcps: () => request<import('./types').McpServer[]>('/mcps'),
  
  createMcp: (mcp: Omit<import('./types').McpServer, 'id'> & { id?: string }) =>
    request<import('./types').McpServer>('/mcps', {
      method: 'POST',
      body: JSON.stringify(mcp),
    }),
  
  updateMcp: (mcpId: string, mcp: Partial<import('./types').McpServer>) =>
    request<import('./types').McpServer>(`/mcps/${encodeURIComponent(mcpId)}`, {
      method: 'PUT',
      body: JSON.stringify(mcp),
    }),
  
  deleteMcp: (mcpId: string) =>
    request<{ deleted: boolean }>(`/mcps/${encodeURIComponent(mcpId)}`, {
      method: 'DELETE',
    }),

  testMcp: (mcpId: string) =>
    request<{ connected: boolean; message: string }>(`/mcps/${encodeURIComponent(mcpId)}/test`, {
      method: 'POST',
    }),

  // Calendar
  getCalendarEvents: (start?: string, end?: string) => {
    const params = new URLSearchParams()
    if (start) params.append('start', start)
    if (end) params.append('end', end)
    const query = params.toString() ? `?${params}` : ''
    return request<import('./types').CalendarEvent[]>(`/calendar/events${query}`)
  },

  createCalendarEvent: (event: Partial<import('./types').CalendarEvent>) =>
    request<import('./types').CalendarEvent>('/calendar/events', {
      method: 'POST',
      body: JSON.stringify(event),
    }),

  updateCalendarEvent: (eventId: string, event: Partial<import('./types').CalendarEvent>) =>
    request<import('./types').CalendarEvent>(`/calendar/events/${eventId}`, {
      method: 'PATCH',
      body: JSON.stringify(event),
    }),

  deleteCalendarEvent: (eventId: string) =>
    request<{ deleted: boolean }>(`/calendar/events/${eventId}`, {
      method: 'DELETE',
    }),

  getCalendarSettings: () =>
    request<import('./types').CalendarSettings>('/calendar/settings'),

  updateCalendarSettings: (settings: Partial<import('./types').CalendarSettings>) =>
    request<import('./types').CalendarSettings>('/calendar/settings', {
      method: 'PATCH',
      body: JSON.stringify(settings),
    }),

  // 获取已启用的渠道列表
  getEnabledChannels: () =>
    request<{ id: string; name: string }[]>('/channels'),

  // 获取日历相关的定时任务
  getCalendarJobs: () =>
    request<any[]>('/calendar/jobs'),

  // Agent Templates
  getAgentTemplates: () =>
    request<import('./types').AgentTemplate[]>('/agent-templates'),

  getAgentTemplate: (name: string) =>
    request<import('./types').AgentTemplate>(`/agent-templates/${name}`),

  createAgentTemplate: (template: Partial<import('./types').AgentTemplate>) =>
    request<{ name: string; success: boolean }>('/agent-templates', {
      method: 'POST',
      body: JSON.stringify(template),
    }),

  updateAgentTemplate: (name: string, template: Partial<import('./types').AgentTemplate>) =>
    request<{ name: string; success: boolean }>(`/agent-templates/${name}`, {
      method: 'PATCH',
      body: JSON.stringify(template),
    }),

  deleteAgentTemplate: (name: string) =>
    request<{ name: string; success: boolean }>(`/agent-templates/${name}`, {
      method: 'DELETE',
    }),

  importAgentTemplates: (content: string, onConflict: 'skip' | 'replace' | 'rename' = 'skip') =>
    request<{ imported: any[]; skipped: string[]; errors: string[] }>('/agent-templates/import', {
      method: 'POST',
      body: JSON.stringify({ content, on_conflict: onConflict }),
    }),

  exportAgentTemplates: (names?: string[]) =>
    request<{ content: string }>('/agent-templates/export', {
      method: 'POST',
      body: JSON.stringify({ names }),
    }),

  getValidTools: () =>
    request<{ name: string; description: string }[]>('/agent-templates/tools/valid'),

  reloadAgentTemplates: () =>
    request<{ success: boolean }>('/agent-templates/reload', {
      method: 'POST',
    }),

  // Skills
  getInstalledSkills: () => request<import('./types').InstalledSkill[]>('/skills/installed'),

  uploadSkill: (formData: FormData) =>
    request<import('./types').InstalledSkill>('/skills/upload', {
      method: 'POST',
      body: formData,
      skipJsonContentType: true, // FormData sets Content-Type with boundary
    }),

  enableSkill: (skillId: string) =>
    request<{ enabled: boolean }>(`/skills/${skillId}/enable`, {
      method: 'POST',
    }),
  
  disableSkill: (skillId: string) =>
    request<{ enabled: boolean }>(`/skills/${skillId}/disable`, {
      method: 'POST',
    }),
  
  deleteSkill: (skillId: string) =>
    request<{ deleted: boolean }>(`/skills/${skillId}`, {
      method: 'DELETE',
    }),

  // System
  getSystemStatus: () => request<import('./types').SystemStatus>('/system/status'),

  resetGlobalTokenSummary: () =>
    request<{ reset: boolean; scope: 'global' }>('/system/token-summary/reset', {
      method: 'POST',
    }),
  
  getSystemLogs: () => request<{ lines: string[] }>('/system/logs'),
  
  exportConfig: () => {
    window.location.href = '/api/v1/system/config/export'
  },

  switchWorkspace: (workspace: string, copyDb?: boolean) =>
    request<{ workspace: string } | { needPrompt: boolean; hasDefaultDb: boolean; workspace: string }>('/system/workspace', {
      method: 'POST',
      body: JSON.stringify({ workspace, copy_db: copyDb }),
    }),

  importConfig: (config: object, reloadWorkspace = true) =>
    request<{ success: boolean; workspace: string }>('/system/config/import', {
      method: 'POST',
      body: JSON.stringify({ config, reloadWorkspace }),
    }),

  // ==================== Mirror Room ====================

  // Profile (吾)
  getMirrorProfile: () =>
    request<import('./types').MirrorProfile | null>('/mirror/profile'),

  generateMirrorProfile: () =>
    request<import('./types').MirrorProfile>('/mirror/profile/generate', { method: 'POST' }),

  // Mirror sessions (悟 / 辩)
  getMirrorSessions: (type: import('./types').MirrorSessionType, page = 1, pageSize = 20) =>
    request<{ items: import('./types').MirrorSession[]; total: number }>(
      `/mirror/sessions?type=${type}&page=${page}&pageSize=${pageSize}`
    ),

  createMirrorSession: (
    type: import('./types').MirrorSessionType,
    options?: { attackLevel?: import('./types').AttackLevel; topic?: string }
  ) =>
    request<import('./types').MirrorSession>('/mirror/sessions', {
      method: 'POST',
      body: JSON.stringify({ type, ...options }),
    }),

  getMirrorMessages: (sessionId: string, type?: import('./types').MirrorSessionType, limit = 50) =>
    request<import('./types').MirrorMessage[]>(
      `/mirror/sessions/${sessionId}/messages?limit=${limit}${type ? `&type=${type}` : ''}`
    ),

  sendMirrorMessage: (sessionId: string, content: string, signal?: AbortSignal) =>
    request<ChatResponse>(`/mirror/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
      signal,
    }),

  /** Stream chat for mirror session */
  async sendMirrorMessageStream(
    sessionId: string,
    content: string,
    onEvent: (evt: StreamEvent) => void,
    signal?: AbortSignal,
    type?: import('./types').MirrorSessionType
  ): Promise<ChatResponse> {
    const body: { content: string; type?: string } = { content }
    if (type) body.type = type
    const res = await fetch(`${API_BASE}/mirror/sessions/${sessionId}/messages?stream=1`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err?.error?.message || i18n.t('api.requestFailed'))
    }
    const reader = res.body?.getReader()
    if (!reader) throw new Error('Stream not supported')
    const dec = new TextDecoder()
    let buf = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n\n')
        buf = lines.pop() ?? ''
        for (const block of lines) {
          const dataParts = block.split('\n')
            .filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trimStart())
          const dataStr = dataParts.join('\n')
          if (!dataStr) continue
          try {
            const evt = JSON.parse(dataStr) as StreamEvent
            onEvent(evt)
            if (evt.type === 'done') {
              return {
                content: 'content' in evt ? evt.content ?? '' : '',
                assistantMessage: 'assistantMessage' in evt ? evt.assistantMessage ?? null : null,
              }
            }
            if (evt.type === 'error') {
              throw new Error('message' in evt ? evt.message : 'Stream error')
            }
          } catch (e) {
            throw e  // 重抛任何异常，不吞掉非 Error 类型
          }
        }
      }
    } finally {
      reader.releaseLock()
    }
    throw new Error('Stream ended without done event')
  },

  sealMirrorSession: (sessionId: string) =>
    request<import('./types').MirrorSession>(`/mirror/sessions/${sessionId}/seal`, {
      method: 'POST',
    }),

  renameMirrorSession: (sessionId: string, title: string) =>
    request<import('./types').MirrorSession>(`/mirror/sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),

  retryMirrorAnalysis: (sessionId: string) =>
    request<import('./types').MirrorSession & { analysisStatus?: string }>(
      `/mirror/sessions/${sessionId}/retry-analysis`,
      { method: 'POST' }
    ),

  deleteMirrorSession: (sessionId: string, type: 'wu' | 'bian') =>
    request<{ deleted: boolean }>(`/mirror/sessions/${sessionId}?type=${type}`, {
      method: 'DELETE',
    }),

  /** 悟/辩首次回复：新建会话后 AI 自动给出命题或辩题，流式返回 */
  async getMirrorFirstReplyStream(
    sessionId: string,
    type: 'wu' | 'bian',
    onEvent: (evt: import('./types').StreamEvent) => void,
    signal?: AbortSignal
  ): Promise<string> {
    const path = type === 'wu' ? 'wu-first-reply' : 'bian-first-reply'
    const res = await fetch(`${API_BASE}/mirror/sessions/${sessionId}/${path}?stream=1`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
      signal,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err?.error?.message || i18n.t('api.requestFailed'))
    }
    const reader = res.body?.getReader()
    if (!reader) throw new Error('Stream not supported')
    const dec = new TextDecoder()
    let buf = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n\n')
        buf = lines.pop() ?? ''
        for (const block of lines) {
          const dataParts = block.split('\n')
            .filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trimStart())
          const dataStr = dataParts.join('\n')
          if (!dataStr) continue
          try {
            const evt = JSON.parse(dataStr) as import('./types').StreamEvent
            onEvent(evt)
            if (evt.type === 'done' && 'content' in evt) {
              return evt.content ?? ''
            }
            if (evt.type === 'error') {
              throw new Error('message' in evt ? evt.message : 'Stream error')
            }
          } catch (e) {
            throw e  // 重抛任何异常，不吞掉非 Error 类型
          }
        }
      }
    } finally {
      reader.releaseLock()
    }
    throw new Error('Stream ended without done event')
  },

  getWuFirstReplyStream: (sessionId: string, onEvent: (evt: import('./types').StreamEvent) => void, signal?: AbortSignal) =>
    api.getMirrorFirstReplyStream(sessionId, 'wu', onEvent, signal),

  getBianFirstReplyStream: (sessionId: string, onEvent: (evt: import('./types').StreamEvent) => void, signal?: AbortSignal) =>
    api.getMirrorFirstReplyStream(sessionId, 'bian', onEvent, signal),

  // Shang (赏)
  getShangToday: () =>
    request<{ done: boolean; record: import('./types').ShangRecord | null }>('/mirror/shang/today'),

  getShangRecords: (page = 1, pageSize = 20) =>
    request<{ items: import('./types').ShangRecord[]; total: number }>(
      `/mirror/shang/records?page=${page}&pageSize=${pageSize}`
    ),

  startShang: () =>
    request<import('./types').ShangRecord>('/mirror/shang/start', { method: 'POST' }),

  regenerateShangImages: (recordId: string) =>
    request<import('./types').ShangRecord>(`/mirror/shang/${recordId}/regenerate-images`, {
      method: 'POST',
    }),

  submitShangChoice: (recordId: string, choice: 'A' | 'B', attribution?: string) =>
    request<import('./types').ShangRecord>(`/mirror/shang/${recordId}/choose`, {
      method: 'POST',
      body: JSON.stringify({ choice, attribution: attribution ?? '' }),
    }),

  deleteShangRecord: (recordId: string) =>
    request<{ deleted: boolean }>(`/mirror/shang/records/${recordId}`, {
      method: 'DELETE',
    }),

  // ==================== Claude Code Tasks ====================

  getTasks: (page = 1, pageSize = 20, status: 'all' | 'running' | 'done' | 'error' | 'timeout' | 'cancelled' = 'all') =>
    request<TaskListResponse>(`/tasks?page=${page}&pageSize=${pageSize}&status=${status}`),

  getTask: (taskId: string) => request<Task>(`/tasks/${taskId}`),

  // 轻量级任务状态查询（用于轮询）
  getTaskStatus: (taskId: string) =>
    request<{
      taskId: string
      status: 'pending' | 'running' | 'done' | 'error' | 'timeout' | 'cancelled'
      prompt: string
      startTime: string | null
      endTime: string | null
      result: string | null
    }>(`/tasks/${taskId}/status`),

  cancelTask: (taskId: string) =>
    request<{ cancelled: boolean }>(`/tasks/${taskId}/cancel`, {
      method: 'POST',
    }),

  /**
   * 订阅子 Agent 后台进度 SSE 流。
   * 调用方传入 onEvent 回调，连接保持到 signal 中止或服务端发送 timeout 事件。
   */
  async subagentProgressStream(
    sessionId: string,
    onEvent: (evt: SubagentProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    console.log('[SubagentProgress] Connecting to subagent-progress stream for session:', sessionId)
    const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}/subagent-progress`, {
      method: 'GET',
      headers: { Accept: 'text/event-stream' },
      signal,
    })
    if (!res.ok) {
      console.error('[SubagentProgress] Failed to connect:', res.status, res.statusText)
      return
    }
    console.log('[SubagentProgress] Connected to subagent-progress stream')
    const reader = res.body?.getReader()
    if (!reader) {
      console.error('[SubagentProgress] No reader available')
      return
    }
    const dec = new TextDecoder()
    let buf = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const blocks = buf.split('\n\n')
        buf = blocks.pop() ?? ''
        for (const block of blocks) {
          const dataLine = block.split('\n').find(l => l.startsWith('data:'))
          if (!dataLine) continue
          const dataStr = dataLine.slice(5).trimStart()
          if (!dataStr) continue
          try {
            const evt = JSON.parse(dataStr) as SubagentProgressEvent
            onEvent(evt)
            if (evt.type === 'timeout') return
          } catch {
            // 跳过非 JSON 行
          }
        }
      }
    } finally {
      reader.releaseLock()
    }
  },
}
