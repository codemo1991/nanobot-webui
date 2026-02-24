"""Configuration schema using Pydantic."""

from pathlib import Path
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class WhatsAppConfig(BaseModel):
    """WhatsApp channel configuration."""
    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    allow_from: list[str] = Field(default_factory=list)  # Allowed phone numbers


class TelegramConfig(BaseModel):
    """Telegram channel configuration."""
    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"


class FeishuConfig(BaseModel):
    """Feishu/Lark channel configuration using WebSocket long connection."""
    enabled: bool = False
    app_id: str = ""  # App ID from Feishu Open Platform
    app_secret: str = ""  # App Secret from Feishu Open Platform
    encrypt_key: str = ""  # Encrypt Key for event subscription (optional)
    verification_token: str = ""  # Verification Token for event subscription (optional)
    allow_from: list[str] = Field(default_factory=list)  # Allowed user open_ids


class DiscordConfig(BaseModel):
    """Discord channel configuration using Gateway WebSocket."""
    enabled: bool = False
    token: str = ""  # Bot token from Discord Developer Portal
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT


class QQConfig(BaseModel):
    """QQ channel configuration using botpy SDK."""
    enabled: bool = False
    app_id: str = ""  # 机器人 ID (AppID) from q.qq.com
    secret: str = ""  # 机器人密钥 (AppSecret) from q.qq.com
    allow_from: list[str] = Field(default_factory=list)  # Allowed user openids (empty = public)


class DingTalkConfig(BaseModel):
    """DingTalk channel configuration using Stream Mode."""
    enabled: bool = False
    client_id: str = ""  # AppKey from DingTalk Open Platform
    client_secret: str = ""  # AppSecret from DingTalk Open Platform
    allow_from: list[str] = Field(default_factory=list)  # Allowed staff_ids


class ChannelsConfig(BaseModel):
    """Configuration for chat channels."""
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)


class AgentDefaults(BaseModel):
    """Default agent configuration."""
    workspace: str = "~/.nanobot/web-ui"
    model: str = "anthropic/claude-opus-4-5"
    subagent_model: str = ""  # 子 Agent 使用的模型，留空则与主 Agent 相同
    max_tokens: int = 8192
    temperature: float = 0.7
    max_tool_iterations: int = 40
    max_execution_time: int = 600
    max_history_messages: int = 30
    max_message_length: int = 8000
    tool_result_max_length: int = 2000
    smart_tool_selection: bool = True
    system_prompt_max_tokens: int = 5000
    memory_max_tokens: int = 2000


class AgentsConfig(BaseModel):
    """Agent configuration."""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(BaseModel):
    """LLM provider configuration."""
    api_key: str = ""
    api_base: str | None = None


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # Qwen via Aliyun DashScope
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)


class MirrorConfig(BaseModel):
    """镜室（赏图生成）配置。"""
    qwen_image_model: str = ""  # 如 qwen-image-plus，留空则赏 Tab 用文字描述


class MemoryConfig(BaseModel):
    """记忆系统配置。"""
    # 自动整合配置
    auto_integrate_enabled: bool = True  # 是否启用自动记忆整合
    auto_integrate_interval_minutes: int = 30  # 自动整合间隔（分钟）
    lookback_minutes: int = 60  # 每次回溯时间窗口（分钟）
    max_messages: int = 100  # 每次最多处理消息数

    # 长期记忆阈值（超过时触发总结）
    max_entries: int = 200  # 最大记忆条数
    max_chars: int = 200 * 1024  # 最大字符数 (200KB)

    # 读取配置（用于上下文构建）
    read_max_entries: int = 80  # 超过此条数全量读取
    read_max_chars: int = 25 * 1024  # 超过此字符截断 (25KB)


class GatewayConfig(BaseModel):
    """Gateway/server configuration."""
    host: str = "0.0.0.0"
    port: int = 18790


class WebSearchConfig(BaseModel):
    """Web search tool configuration."""
    api_key: str = ""  # Brave Search API key
    max_results: int = 5


class WebToolsConfig(BaseModel):
    """Web tools configuration."""
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(BaseModel):
    """Shell exec tool configuration."""
    timeout: int = 60
    restrict_to_workspace: bool = False  # If true, block commands accessing paths outside workspace


class FilesystemToolConfig(BaseModel):
    """Filesystem tools (read_file, write_file, edit_file, list_dir) configuration."""
    restrict_to_workspace: bool = False  # If true, only allow paths inside workspace


class ClaudeCodeConfig(BaseModel):
    """Claude Code CLI integration configuration."""
    enabled: bool = True
    default_timeout: int = 600  # Default task timeout in seconds
    max_concurrent_tasks: int = 3  # Maximum concurrent Claude Code tasks


class McpServerConfig(BaseModel):
    """MCP (Model Context Protocol) server configuration."""
    id: str = ""
    name: str = ""
    transport: str = "stdio"  # stdio | http | sse | streamable_http
    command: str | None = None  # Required for stdio
    args: list[str] = Field(default_factory=list)
    url: str | None = None  # Required for http/sse/streamable_http
    enabled: bool = True


class ToolsConfig(BaseModel):
    """Tools configuration."""
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    filesystem: FilesystemToolConfig = Field(default_factory=FilesystemToolConfig)
    claude_code: ClaudeCodeConfig = Field(default_factory=ClaudeCodeConfig)


class Config(BaseSettings):
    """Root configuration for nanobot."""
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    mirror: MirrorConfig = Field(default_factory=MirrorConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    mcps: list[McpServerConfig] = Field(default_factory=list)
    
    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()
    
    _MODEL_PROVIDER_MAP: dict[tuple[str, ...], str] = {
        ("openrouter/", "openrouter"): "openrouter",
        ("zhipu/", "zai/", "glm", "zhipu"): "zhipu",
        ("dashscope/", "qwen", "dashscope"): "dashscope",
        ("deepseek/", "deepseek"): "deepseek",
        ("anthropic/", "claude"): "anthropic",
        ("openai/", "gpt"): "openai",
        ("gemini/", "gemini"): "gemini",
        ("groq/", "groq"): "groq",
        ("vllm/", "vllm"): "vllm",
        ("minimax/", "minimax"): "minimax",
    }
    
    _FALLBACK_PROVIDER_ORDER: list[str] = [
        "openrouter", "deepseek", "anthropic", "openai",
        "gemini", "zhipu", "dashscope", "groq", "vllm", "minimax"
    ]
    
    _MODEL_API_BASE_MAP: dict[str, str | None] = {
        "openrouter": "https://openrouter.ai/api/v1",
        "zhipu": None,
        "dashscope": None,
        "deepseek": None,
        "anthropic": None,
        "openai": None,
        "gemini": None,
        "groq": None,
        "vllm": None,
        "minimax": "https://api.minimax.chat/v1",
    }

    def _get_provider_for_model(self, model: str | None) -> str | None:
        """Get provider name for the given model."""
        model_lower = (model or self.agents.defaults.model).lower()
        for prefixes, provider in self._MODEL_PROVIDER_MAP.items():
            for prefix in prefixes:
                if model_lower.startswith(prefix) or prefix in model_lower:
                    return provider
        return None
    
    def get_api_key(self, model: str | None = None) -> str | None:
        """
        Get API key for the given model, or first available in priority order.
        When model is specified, returns the key for the matching provider.
        """
        provider = self._get_provider_for_model(model)
        if provider:
            key = getattr(self.providers, provider).api_key
            if key:
                return key
        
        for fallback_provider in self._FALLBACK_PROVIDER_ORDER:
            key = getattr(self.providers, fallback_provider).api_key
            if key:
                return key
        return None

    def get_api_base(self, model: str | None = None) -> str | None:
        """
        Get API base URL for the given model.
        When model is specified, returns the base for the matching provider.
        """
        provider = self._get_provider_for_model(model)
        if provider:
            provider_base = getattr(self.providers, provider).api_base
            return provider_base or self._MODEL_API_BASE_MAP.get(provider)
        return None
    
    class Config:
        env_prefix = "NANOBOT_"
        env_nested_delimiter = "__"
