import i18n from './i18n'
import type { ApiResponse, Session, SessionListResponse, Message, ChatResponse, StreamEvent, SubagentProgressEvent, TokenUsage, Task, TaskListResponse, TraceSummary, RecentSpan, TraceDetail, Anomaly } from './types'

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

  updateSession: (
    sessionId: string,
    toolMode?: 'disable' | 'auto' | 'specified',
    selectedMcpServers?: string[],
  ) =>
    request<{ id: string; toolMode: string; selectedMcpServers: string[]; updatedAt: string }>(
      `/chat/sessions/${sessionId}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ tool_mode: toolMode, selected_mcp_servers: selectedMcpServers }),
      },
    ),

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
    images?: string[],
    toolMode?: 'disable' | 'auto' | 'specified',
    selectedMcpServers?: string[]
  ): Promise<ChatResponse> {
    const body: Record<string, unknown> = { content }
    if (images && images.length > 0) body.images = images
    if (toolMode) body.tool_mode = toolMode
    if (selectedMcpServers && selectedMcpServers.length > 0) body.selected_mcp_servers = selectedMcpServers
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
        if (value) buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n\n')
        buf = done ? '' : (lines.pop() ?? '')
        for (const block of lines) {
          // SSE: data can be multi-line; collapse all "data:" lines
          const dataParts = block.split('\n')
            .filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trimStart())
          const dataStr = dataParts.join('\n')
          if (!dataStr || dataStr === ': heartbeat') continue
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
            if (e instanceof SyntaxError) continue  // 跳过格式错误的 SSE 行，不终止整个流
            throw e
          }
        }
        if (done) break
      }
    } finally {
      reader.releaseLock()
    }
    throw new Error('Stream ended without done event')
  },

  /** 重连 Chat SSE 流（刷新/切换 tab 后继续接收推送） */
  async subscribeToChatStream(
    sessionId: string,
    onEvent: (evt: StreamEvent) => void,
    signal?: AbortSignal
  ): Promise<ChatResponse | null> {
    const MAX_RETRIES = 3
    const RETRY_DELAYS = [1000, 2000, 3000] // 线性递增间隔

    const doSubscribe = async (): Promise<ChatResponse | null> => {
      const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}/stream`, { signal })
      if (!res.ok) {
        // 附加 status 到 error 以便 retry 逻辑判断
        const err = new Error(`Stream reconnect failed: HTTP ${res.status}`) as Error & { status?: number }
        err.status = res.status
        throw err
      }
      const reader = res.body?.getReader()
      if (!reader) throw new Error('Stream not supported')
      const dec = new TextDecoder()
      let buf = ''
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (value) buf += dec.decode(value, { stream: true })
          const lines = buf.split('\n\n')
          buf = done ? '' : (lines.pop() ?? '')
          for (const block of lines) {
            const dataParts = block.split('\n')
              .filter(line => line.startsWith('data:'))
              .map(line => line.slice(5).trimStart())
            const dataStr = dataParts.join('\n')
            if (!dataStr || dataStr === ': heartbeat') continue
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
              if (evt.type === 'timeout') return null
            } catch (e) {
              if (e instanceof Error && e.message === 'Stream error') throw e
            }
          }
          if (done) break
        }
      } finally {
        reader.releaseLock()
      }
      return null
    }

    // 重连循环
    let lastError: Error | null = null
    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      // 检查是否已取消
      if (signal?.aborted) throw new Error('Stream cancelled')

      try {
        return await doSubscribe()
      } catch (e) {
        lastError = e instanceof Error ? e : new Error(String(e))

        // 不重连的情况
        if (signal?.aborted) throw lastError
        const httpStatus = (lastError as Error & { status?: number }).status
        if (httpStatus !== undefined && httpStatus >= 400 && httpStatus < 500) {
          throw lastError
        }

        // 还有重试机会，等待后重试
        if (attempt < MAX_RETRIES - 1) {
          console.warn(`[ChatStream] Connection failed (attempt ${attempt + 1}/${MAX_RETRIES}), retrying in ${RETRY_DELAYS[attempt]}ms...`, lastError.message)
          await new Promise(resolve => setTimeout(resolve, RETRY_DELAYS[attempt]))
        }
      }
    }

    throw lastError
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
  
  createProvider: (provider: Partial<import('./types').Provider> & { id: string }) =>
    request<import('./types').Provider>('/providers', {
      method: 'POST',
      body: JSON.stringify(provider),
    }),
  
  updateProvider: (providerId: string, provider: Partial<import('./types').Provider>) =>
    request<import('./types').Provider>(`/providers/${providerId}`, {
      method: 'PUT',
      body: JSON.stringify(provider),
    }),

  batchDisableProviders: (providerIds: string[]) =>
    request<{ disabled: number }>('/providers/batch-disable', {
      method: 'POST',
      body: JSON.stringify({ provider_ids: providerIds }),
    }),
  
  deleteProvider: (providerId: string) =>
    request<{ deleted: boolean }>(`/providers/${providerId}`, {
      method: 'DELETE',
    }),

  // Models
  getModels: (providerId?: string) =>
    request<import('./types').ModelInfo[]>(
      providerId ? `/models?provider_id=${encodeURIComponent(providerId)}` : '/models'
    ),
  
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

  // Model Discovery - 仅查询可用模型（不保存），供添加模型时 LiteLLM ID 下拉选择
  discoverModels: (providerId: string) =>
    request<import('./types').DiscoveredModel[]>(`/providers/${providerId}/discover`, {
      method: 'GET',
    }),

  // Model Profiles (New)
  getModelProfiles: () =>
    request<import('./types').ModelProfile[]>('/model-profiles'),

  createModelProfile: (profile: Omit<import('./types').ModelProfile, 'id'>) =>
    request<import('./types').ModelProfile>('/model-profiles', {
      method: 'POST',
      body: JSON.stringify(profile),
    }),

  updateModelProfile: (profileId: string, profile: Partial<import('./types').ModelProfile>) =>
    request<import('./types').ModelProfile>(`/model-profiles/${profileId}`, {
      method: 'PUT',
      body: JSON.stringify(profile),
    }),

  deleteModelProfile: (profileId: string) =>
    request<{ deleted: boolean }>(`/model-profiles/${profileId}`, {
      method: 'DELETE',
    }),

  // MCPs
  getMcps: () => request<import('./types').McpServer[]>('/mcps'),

  getMcpsWithTools: () => request<import('./types').McpServer[]>('/mcps/with-tools'),

  discoverMcpTools: (mcpId: string) => request<{ tools: Array<{ name: string; description: string; parameters: Record<string, unknown> }> }>(`/mcps/${encodeURIComponent(mcpId)}/discover`, { method: 'POST' }),
  
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

  // 主 Agent 系统提示词 (Identity)
  getMainAgentPrompt: () =>
    request<{ identity_content: string; updated_at: string }>('/main-agent-prompt'),

  updateMainAgentPrompt: (identity_content: string) =>
    request<{ identity_content: string; updated_at: string }>('/main-agent-prompt', {
      method: 'POST',
      body: JSON.stringify({ identity_content }),
    }),

  resetMainAgentPrompt: () =>
    request<{ success: boolean }>('/main-agent-prompt/reset', {
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

  // ==================== Auth / Login ====================

  /** 发送短信验证码 */
  sendSmsCode: (phone: string, captcha: string, captchaKey: number, action: 'login' | 'register' | 'reset') =>
    request<{ sent: boolean }>('/auth/sms/send', {
      method: 'POST',
      body: JSON.stringify({ phone, captcha, captcha_key: captchaKey, action }),
    }),

  /** 验证短信验证码（登录/注册/重置密码） */
  verifySmsCode: (phone: string, code: string, action: 'login' | 'register' | 'reset') =>
    request<{ success: boolean; token?: string }>('/auth/sms/verify', {
      method: 'POST',
      body: JSON.stringify({ phone, code, action }),
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
            if (evt.type === 'timeout' || evt.type === 'stream_done') return
          } catch {
            // 跳过非 JSON 行
          }
        }
      }
    } finally {
      reader.releaseLock()
    }
  },

  // ==================== Trace APIs ====================

  getTraceSummary: () =>
    request<TraceSummary>('/traces/summary'),

  getTraceRecent: (limit = 50) =>
    request<RecentSpan[]>(`/traces/recent?limit=${limit}`),

  getTraceDetail: (traceId: string) =>
    request<TraceDetail>(`/traces/${traceId}`),

  getTraceAnomalies: () =>
    request<Anomaly[]>('/traces/anomalies'),

  /** 订阅 Trace SSE 流 */
  async subscribeTraceStream(
    onEvent: (evt: { type: string; data: any }) => void,
    signal?: AbortSignal
  ): Promise<void> {
    const MAX_RETRIES = 3
    const RETRY_DELAYS = [1000, 2000, 3000]

    const doSubscribe = async () => {
      const res = await fetch(`${API_BASE}/traces/stream`, {
        headers: { Accept: 'text/event-stream' },
        signal,
      })
      if (!res.ok) {
        const err = new Error(`Trace stream failed: HTTP ${res.status}`) as Error & { status?: number }
        err.status = res.status
        throw err
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
          for (const line of lines) {
            const dataLine = line.split('\n').find(l => l.startsWith('data:'))
            if (!dataLine) continue
            const dataStr = dataLine.slice(5).trim()
            if (!dataStr) continue
            try {
              const evt = JSON.parse(dataStr)
              onEvent(evt)
            } catch { /* skip */ }
          }
        }
      } finally {
        reader.releaseLock()
      }
    }

    // 重连循环
    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      if (signal?.aborted) throw new Error('Stream cancelled')
      try {
        await doSubscribe()
        return
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e))
        if (signal?.aborted) throw err
        const httpStatus = (err as Error & { status?: number }).status
        if (httpStatus !== undefined && httpStatus >= 400 && httpStatus < 500) {
          throw err
        }
        if (attempt < MAX_RETRIES - 1) {
          console.warn(`[TraceStream] Connection failed (attempt ${attempt + 1}/${MAX_RETRIES}), retrying...`)
          await new Promise(resolve => setTimeout(resolve, RETRY_DELAYS[attempt]))
        }
      }
    }
    throw new Error('Trace stream failed after all retries')
  },
}
