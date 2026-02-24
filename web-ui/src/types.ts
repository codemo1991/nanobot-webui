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
  type: 'openai' | 'anthropic' | 'azure' | 'deepseek' | 'openrouter' | 'groq' | 'zhipu' | 'dashscope' | 'gemini' | 'vllm' | 'minimax'
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
  memory?: MemoryConfig
}

export interface MemoryConfig {
  auto_integrate_enabled: boolean
  auto_integrate_interval_minutes: number
  lookback_minutes: number
  max_messages: number
  max_entries: number
  max_chars: number
  read_max_entries: number
  read_max_chars: number
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

// ==================== Calendar Types ====================

export interface Reminder {
  id?: string
  time: number  // 提前分钟数，0表示事件发生时
  notified?: boolean  // 是否已提醒
}

export interface RecurrenceRule {
  frequency: 'daily' | 'weekly' | 'monthly' | 'yearly'
  interval: number
  endType?: 'never' | 'count' | 'until'
  endCount?: number
  endDate?: string
  weekdays?: number[]
}

// 紧急程度颜色映射
export const priorityColors = {
  high: {
    bg: '#fff2f0',
    border: '#ffccc7',
    text: '#cf1322',
    dot: '#FF4D4F',
    css: '#FF4D4F'
  },
  medium: {
    bg: '#fffbe6',
    border: '#ffe58f',
    text: '#d48806',
    dot: '#FAAD14',
    css: '#FAAD14'
  },
  low: {
    bg: '#f6ffed',
    border: '#b7eb8f',
    text: '#389e0d',
    dot: '#52C41A',
    css: '#52C41A'
  }
}

// 提醒时间选项
export const reminderOptions = [
  { label: '事件发生时', value: 0 },
  { label: '提前 5 分钟', value: 5 },
  { label: '提前 15 分钟', value: 15 },
  { label: '提前 30 分钟', value: 30 },
  { label: '提前 1 小时', value: 60 },
  { label: '提前 1 天', value: 1440 },
];

export interface CalendarEvent {
  id: string
  title: string
  description?: string
  start: string
  end: string
  isAllDay: boolean
  priority: 'high' | 'medium' | 'low'
  reminders?: Reminder[]
  recurrence?: RecurrenceRule
  recurrenceId?: string
  createdAt?: string
  updatedAt?: string
}

export interface CalendarSettings {
  defaultView: 'dayGridMonth' | 'timeGridWeek' | 'timeGridDay' | 'listWeek'
  defaultPriority: 'high' | 'medium' | 'low'
  soundEnabled: boolean
  notificationEnabled: boolean
}

// ==================== Claude Code Task Types ====================

export type TaskStatus = 'running' | 'done' | 'timeout' | 'error' | 'cancelled'

export interface TaskOrigin {
  channel?: string
  chat_id?: string
}

export interface Task {
  task_id: string
  prompt: string
  status: TaskStatus
  start_time: string | null
  end_time: string | null
  result: string | null
  workdir: string | null
  origin: TaskOrigin
}

export interface TaskListResponse {
  items: Task[]
  page: number
  pageSize: number
  total: number
}
