import i18n from './i18n'
import type { ApiResponse, Session, SessionListResponse, Message, ChatResponse } from './types'

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
    throw new Error(errorData.error?.message || i18n.t('api.requestFailed'))
  }

  const data: ApiResponse<T> = await response.json()
  
  if (!data.success) {
    throw new Error(data.error?.message || i18n.t('api.requestFailed'))
  }

  return data.data!
}

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

  sendMessage: (sessionId: string, content: string, signal?: AbortSignal) =>
    request<ChatResponse>(`/chat/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
      signal,
    }),

  // Configuration
  getConfig: () => request<import('./types').ConfigData>('/config'),
  
  // IM Channels (WhatsApp, Telegram, Feishu)
  getChannels: () => request<import('./types').ChannelsConfig>('/channels'),
  
  updateChannels: (channels: Partial<import('./types').ChannelsConfig>) =>
    request<import('./types').ChannelsConfig>('/channels', {
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
    request<import('./types').McpServer>(`/mcps/${mcpId}`, {
      method: 'PUT',
      body: JSON.stringify(mcp),
    }),
  
  deleteMcp: (mcpId: string) =>
    request<{ deleted: boolean }>(`/mcps/${mcpId}`, {
      method: 'DELETE',
    }),
  
  testMcp: (mcpId: string) =>
    request<{ connected: boolean; message: string }>(`/mcps/${mcpId}/test`, {
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
  
  getSystemLogs: () => request<{ lines: string[] }>('/system/logs'),
  
  exportConfig: () => {
    window.location.href = '/api/v1/system/config/export'
  },

  switchWorkspace: (workspace: string) =>
    request<{ workspace: string }>('/system/workspace', {
      method: 'POST',
      body: JSON.stringify({ workspace }),
    }),

  importConfig: (config: object, reloadWorkspace = true) =>
    request<{ success: boolean; workspace: string }>('/system/config/import', {
      method: 'POST',
      body: JSON.stringify({ config, reloadWorkspace }),
    }),
}
