"""LiteLLM provider implementation for multi-provider support."""

import json
import os
from typing import Any

import litellm
from loguru import logger
from litellm import acompletion

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest

# 单条消息内容在日志中的最大长度，超出则截断
_LLM_LOG_CONTENT_MAX = 1500

# Provider 前缀映射表：用于自动添加正确的 LiteLLM 前缀
PROVIDER_PREFIX_MAP = {
    "openrouter": "openrouter",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "gpt": "openai",
    "gemini": "gemini",
    "zhipu": "zhipu",
    "glm": "zai",
    "zai": "zai",
    "dashscope": "dashscope",
    "qwen": "dashscope",
    "groq": "groq",
    "deepseek": "deepseek",
    "minimax": "minimax",
    "01ai": "01ai",
    "moonshot": "moonshot",
    "kimi": "moonshot",
}

# Provider 环境变量映射表
PROVIDER_ENV_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gpt": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "zhipu": "ZHIPUAI_API_KEY",
    "glm": "ZHIPUAI_API_KEY",
    "zai": "ZHIPUAI_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "01ai": "01AI_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
}


def _format_msg_for_log(msg: dict[str, Any], max_len: int = _LLM_LOG_CONTENT_MAX) -> str:
    """格式化单条消息用于日志输出"""
    role = msg.get("role", "?")
    content = msg.get("content")
    if isinstance(content, list):
        # 可能是多模态 content，简单拼接
        parts = []
        for part in content:
            if isinstance(part, dict):
                if "text" in part:
                    parts.append(part["text"])
                elif "type" in part:
                    parts.append(f"[{part['type']}]")
            else:
                parts.append(str(part))
        content = "".join(parts)
    content = str(content) if content is not None else ""
    if len(content) > max_len:
        content = content[:max_len] + f"... (截断, 共 {len(content)} 字)"
    return f"[{role}] {content}"


def _detect_provider_from_model(model: str) -> str | None:
    """从模型名称中检测 provider 类型"""
    model_lower = model.lower()
    for key, provider in PROVIDER_PREFIX_MAP.items():
        if key in model_lower:
            return provider
    return None


def _ensure_model_prefix(model: str, is_openrouter: bool = False, is_vllm: bool = False) -> str:
    """
    确保模型名称带有正确的 provider 前缀。
    
    Args:
        model: 原始模型名称
        is_openrouter: 是否使用 OpenRouter
        is_vllm: 是否使用 vLLM 自定义端点
    
    Returns:
        带正确前缀的模型名称
    """
    # 已经被正确前缀的不需要处理
    if "/" in model:
        return model
    
    # OpenRouter 特殊处理
    if is_openrouter:
        return f"openrouter/{model}"
    
    # vLLM 特殊处理
    if is_vllm:
        return f"hosted_vllm/{model}"
    
    # 从模型名检测 provider
    provider = _detect_provider_from_model(model)
    if provider:
        prefix = PROVIDER_PREFIX_MAP.get(provider, provider)
        return f"{prefix}/{model}"
    
    return model


def _set_provider_env_key(api_key: str, model: str) -> None:
    """根据模型名称设置正确的环境变量"""
    provider = _detect_provider_from_model(model)
    if provider:
        env_key = PROVIDER_ENV_KEYS.get(provider)
        if env_key:
            os.environ[env_key] = api_key


def _set_provider_env_key_by_provider(api_key: str, provider: str) -> None:
    """根据 provider 类型设置正确的环境变量"""
    env_key = PROVIDER_ENV_KEYS.get(provider)
    if env_key:
        os.environ[env_key] = api_key


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
    
    Supports OpenRouter, Anthropic, OpenAI, Gemini, and many other providers through
    a unified interface.
    """
    
    # 用于检测自定义 API 端点的域名关键字
    PROVIDER_DOMAIN_KEYS = {
        "openrouter": "openrouter",
        "minimax": "minimax",
        "deepseek": "deepseek",
        "zhipu": "zhipuai",
        "zhipuai": "zhipuai",
    }
    
    def __init__(
        self, 
        api_key: str | None = None, 
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5"
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        
        # 检测自定义 API 端点类型
        self._detected_provider = self._detect_provider_from_api_base(api_base)
        
        # Detect OpenRouter by api_key prefix or explicit api_base
        self.is_openrouter = (
            (api_key and api_key.startswith("sk-or-")) or
            (api_base and "openrouter" in api_base) or
            self._detected_provider == "openrouter"
        )
        
        # Track if using custom endpoint (vLLM, etc.)
        # 排除已识别的 provider
        self.is_vllm = (
            bool(api_base) and 
            not self.is_openrouter and 
            self._detected_provider is None
        )
        
        # Configure LiteLLM environment based on provider
        if api_key:
            if self.is_openrouter:
                os.environ["OPENROUTER_API_KEY"] = api_key
            elif self.is_vllm:
                os.environ["OPENAI_API_KEY"] = api_key
            else:
                # 使用检测到的 provider 或从模型名推断
                provider = self._detected_provider or _detect_provider_from_model(default_model)
                if provider:
                    _set_provider_env_key_by_provider(api_key, provider)
                else:
                    _set_provider_env_key(api_key, default_model)
        
        if api_base:
            litellm.api_base = api_base
        
        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
    
    def _detect_provider_from_api_base(self, api_base: str | None) -> str | None:
        """从 API base URL 检测 provider 类型"""
        if not api_base:
            return None
        api_base_lower = api_base.lower()
        for key, provider in self.PROVIDER_DOMAIN_KEYS.items():
            if key in api_base_lower:
                return provider
        return None

    def update_config(
        self,
        model: str,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """
        Update model and credentials at runtime (hot config).
        """
        self.default_model = model
        if api_key is not None:
            self.api_key = api_key
        if api_base is not None:
            self.api_base = api_base

        # 重新检测 provider
        self._detected_provider = self._detect_provider_from_api_base(self.api_base)
        
        self.is_openrouter = (
            (self.api_key and self.api_key.startswith("sk-or-")) or
            (self.api_base and "openrouter" in (self.api_base or "")) or
            self._detected_provider == "openrouter"
        )
        
        # 排除已识别的 provider
        self.is_vllm = (
            bool(self.api_base) and 
            not self.is_openrouter and 
            self._detected_provider is None
        )

        if self.api_key:
            if self.is_openrouter:
                os.environ["OPENROUTER_API_KEY"] = self.api_key
            elif self.is_vllm:
                os.environ["OPENAI_API_KEY"] = self.api_key
            else:
                provider = self._detected_provider or _detect_provider_from_model(model)
                if provider:
                    _set_provider_env_key_by_provider(self.api_key, provider)
                else:
                    _set_provider_env_key(self.api_key, model)

        if self.api_base:
            litellm.api_base = self.api_base
    
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM.
        
        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
        
        Returns:
            LLMResponse with content and/or tool calls.
        """
        model = model or self.default_model
        
        # 使用统一的函数确保模型前缀正确
        model = _ensure_model_prefix(
            model, 
            is_openrouter=self.is_openrouter, 
            is_vllm=self.is_vllm
        )
        
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        # Pass api_base directly for custom endpoints (vLLM, etc.)
        if self.api_base:
            kwargs["api_base"] = self.api_base
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        
        # DEBUG 级别输出 LLM 对话日志（使用 nanobot web-ui -v 启用）
        logger.debug("LLM 请求: model={}, messages={}", model, len(messages))
        for i, m in enumerate(messages):
            logger.debug("  [{}] {}", i + 1, _format_msg_for_log(m))
        if tools:
            logger.debug("  工具数量: {}", len(tools))
        
        try:
            response = await acompletion(**kwargs)
            result = self._parse_response(response)
            
            # DEBUG 级别输出 LLM 响应日志
            usage = result.usage or {}
            if result.tool_calls:
                tc_names = [tc.name for tc in result.tool_calls]
                logger.debug("LLM 响应: tool_calls={}, usage={}", tc_names, usage)
                for tc in result.tool_calls:
                    args_preview = json.dumps(tc.arguments, ensure_ascii=False)
                    if len(args_preview) > 300:
                        args_preview = args_preview[:300] + "..."
                    logger.debug("  -> {}({})", tc.name, args_preview)
            else:
                content_preview = (result.content or "")[:800]
                if len(result.content or "") > 800:
                    content_preview += f"... (共 {len(result.content)} 字)"
                logger.debug("LLM 响应: content={}, usage={}", content_preview, usage)
            
            return result
        except Exception as e:
            logger.exception("LLM API call failed")
            # Return error as content for graceful handling
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )
    
    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        choice = response.choices[0]
        message = choice.message
        
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string if needed
                args = tc.function.arguments
                if isinstance(args, str):
                    import json
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                
                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))
        
        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
        )
    
    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
