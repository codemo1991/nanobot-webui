export interface ApiResponse<T> {
  success: boolean
  data: T | null
  error: ApiError | null
}

export interface ApiError {
  code: string
  message: string
  details?: any
}

export interface Session {
  id: string
  title?: string
  createdAt: string
  updatedAt: string
  lastMessageAt: string
  messageCount: number
  status: string
}

export interface Message {
  id: string
  sessionId: string
  role: 'user' | 'assistant' | 'system'
  content: string
  createdAt: string
  sequence: number
}

export interface SessionListResponse {
  items: Session[]
  page: number
  pageSize: number
  total: number
}

export interface ChatResponse {
  content: string
  assistantMessage: Message | null
}

// Configuration Types

// External IM Channel (WhatsApp, Telegram, Feishu)
export interface WhatsAppChannel {
  enabled: boolean
  bridgeUrl: string
  allowFrom: string[]
}

export interface TelegramChannel {
  enabled: boolean
  token: string
  allowFrom: string[]
  proxy?: string | null
}

export interface FeishuChannel {
  enabled: boolean
  appId: string
  appSecret: string
  encryptKey: string
  verificationToken: string
  allowFrom: string[]
}

export interface DiscordChannel {
  enabled: boolean
  token: string
  allowFrom: string[]
}

export interface QQChannel {
  enabled: boolean
  appId: string
  secret: string
  allowFrom: string[]
}

export interface DingTalkChannel {
  enabled: boolean
  clientId: string
  clientSecret: string
  allowFrom: string[]
}

export interface GatewayConfig {
  running: boolean
}

export interface ChannelsConfig {
  gateway?: GatewayConfig
  whatsapp: WhatsAppChannel
  telegram: TelegramChannel
  feishu: FeishuChannel
  discord: DiscordChannel
  qq: QQChannel
  dingtalk: DingTalkChannel
}

// AI Model Provider
export interface Provider {
  id: string
  name: string
  type: 'openai' | 'anthropic' | 'azure' | 'deepseek' | 'openrouter' | 'groq' | 'zhipu' | 'dashscope' | 'gemini' | 'vllm'
  apiKey?: string
  apiBase?: string
  enabled: boolean
}

export interface ModelParameters {
  temperature?: number
  maxTokens?: number
  topP?: number
}

export interface Model {
  id: string
  name: string
  providerId: string
  modelName: string
  enabled: boolean
  isDefault?: boolean
  parameters?: ModelParameters
}

export interface McpServer {
  id: string
  name: string
  transport: 'stdio' | 'http' | 'sse' | 'streamable_http'
  command?: string
  args?: string[]
  url?: string
  enabled: boolean
}

export interface InstalledSkill {
  id: string
  name: string
  version: string
  description: string
  enabled: boolean
  author?: string
  tags?: string[]
}

export interface ConfigData {
  channels: ChannelsConfig
  providers: Provider[]
  models: Model[]
  mcps: McpServer[]
  skills: InstalledSkill[]
}

export interface SystemStatus {
  gateway: {
    running: boolean
    pid: number | null
    port: number
  }
  web: {
    version: string
    uptime: number
    workspace: string
  }
  environment: {
    python: string
    platform: string
  }
  stats: {
    sessions: number
    skills: number
  }
}
