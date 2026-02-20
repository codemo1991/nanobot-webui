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

export interface ToolStep {
  name: string
  arguments: Record<string, unknown> | string
  result: string
}

export interface TokenUsage {
  promptTokens: number
  completionTokens: number
  totalTokens: number
}

export interface Message {
  id: string
  sessionId: string
  role: 'user' | 'assistant' | 'system'
  content: string
  createdAt: string
  sequence: number
  toolSteps?: ToolStep[]
  tokenUsage?: TokenUsage
  images?: string[]  // base64 data URLs for user-uploaded images
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

export type StreamEvent =
  | { type: 'thinking' }
  | { type: 'tool_start'; name: string; arguments: Record<string, unknown> }
  | { type: 'tool_end'; name: string; arguments: Record<string, unknown>; result: string }
  | { type: 'claude_code_progress'; task_id: string; subtype: string; content: string; tool_name?: string; timestamp?: string }
  | { type: 'done'; content: string; assistantMessage: Message | null }
  | { type: 'error'; message: string }

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
  /** 赏图生成模型，如 qwen-image-plus，留空则赏 Tab 用文字描述 */
  qwenImageModel?: string
  /** 子 Agent 使用的模型，留空则与主 Agent 相同 */
  subagentModel?: string
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

export interface AgentConfig {
  maxToolIterations: number
  maxExecutionTime: number
}

// ==================== Mirror Room Types ====================

export type MirrorSessionType = 'wu' | 'bian'
export type MirrorSessionStatus = 'active' | 'sealed'
export type AttackLevel = 'light' | 'medium' | 'heavy'

export interface MirrorSession {
  id: string
  type: MirrorSessionType
  title?: string
  status: MirrorSessionStatus
  createdAt: string
  updatedAt: string
  sealedAt?: string
  messageCount: number
  attackLevel?: AttackLevel // 辩专用
  topic?: string
  insight?: string // 核心洞察摘要（封存后填充）
}

export interface MirrorMessage {
  id: string
  sessionId: string
  role: 'user' | 'assistant' | 'system'
  content: string
  createdAt: string
  sequence: number
  toolSteps?: ToolStep[]
}

export interface ShangRecord {
  id: string
  date: string
  topic: string
  imageA?: string // URL 或 base64
  imageB?: string
  descriptionA: string
  descriptionB: string
  choice: 'A' | 'B' | null
  attribution: string // 归因
  analysis?: ShangAnalysis
  status: 'generating' | 'choosing' | 'attributing' | 'done'
}

export interface ShangAnalysis {
  jungType?: { function: string; typeCode: string; description: string }
  bigFive?: Record<string, string>
  archetype?: { primary: string; secondary: string; fear: string; need: string }
  crossValidation?: { consistentWithWu: boolean; consistentWithBian: boolean; note: string }
}

export interface BigFiveScore {
  openness: number
  conscientiousness: number
  extraversion: number
  agreeableness: number
  neuroticism: number
}

export interface JungArchetype {
  primary: string
  secondary: string
}

export interface Driver {
  need: string
  evidence: string
  suggestion: string
}

export interface Conflict {
  explicit: string
  implicit: string
  type: string
}

export interface MbtiDimension {
  倾向: string
  得分: string
  置信度: number
  关键证据: string[]
}

export interface CognitiveFunction {
  功能: string
  强度: number
  表现: string
}

export interface ContextMask {
  情境: string
  显现类型: string
  面具厚度: number
}

export interface GrowthSuggestion {
  挑战: string
  练习: string
  预期: string
}

export interface MbtiAnalysis {
  当前类型: string
  历史类型分布: string
  类型漂移: string
  维度: {
    EI: MbtiDimension
    SN: MbtiDimension
    TF: MbtiDimension
    JP: MbtiDimension
  }
  认知功能栈: {
    主导: CognitiveFunction
    辅助: CognitiveFunction
    第三: CognitiveFunction
    劣势: CognitiveFunction
  }
  情境面具: ContextMask[]
  成长建议: GrowthSuggestion[]
}

export interface MirrorProfile {
  version: string
  updateTime: string
  bigFive: BigFiveScore
  jungArchetype: JungArchetype
  drivers: Driver[]
  conflicts: Conflict[]
  suggestions: string[]
  mbti?: MbtiAnalysis
}

export interface ConfigData {
  channels: ChannelsConfig
  providers: Provider[]
  models: Model[]
  agent?: AgentConfig
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
    tokens: TokenUsage
  }
}
