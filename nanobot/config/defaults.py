"""Default configuration for model profiles and initialization."""

from nanobot.storage.config_repository import ConfigRepository


# Default model profiles
DEFAULT_PROFILES = [
    {
        "id": "smart",
        "name": "深度思考",
        "description": "高质量回复，适合复杂推理任务",
        "model_chain": "claude-opus-4-6,claude-sonnet-4-6,gpt-4o,gemini-2.5-pro",
        "enabled": True,
    },
    {
        "id": "fast",
        "name": "快速响应",
        "description": "快速便宜，适合简单对话和大量生成",
        "model_chain": "claude-haiku-4-5,gpt-4o-mini,gemini-2.0-flash",
        "enabled": True,
    },
    {
        "id": "coding",
        "name": "代码助手",
        "description": "编程专用，支持工具调用和长上下文",
        "model_chain": "claude-sonnet-4-6,claude-opus-4-6,gpt-4o",
        "enabled": True,
    },
    {
        "id": "summarize",
        "name": "总结归纳",
        "description": "低成本总结，适合记忆维护和文本处理",
        "model_chain": "claude-haiku-4-5,gpt-4o-mini",
        "enabled": True,
    },
]


def init_default_profiles(repo: ConfigRepository) -> None:
    """Initialize default model profiles if not exist."""
    existing = repo.get_all_model_profiles()
    existing_ids = {p["id"] for p in existing}

    for profile in DEFAULT_PROFILES:
        if profile["id"] not in existing_ids:
            repo.set_model_profile(
                profile_id=profile["id"],
                name=profile["name"],
                description=profile["description"],
                model_chain=profile["model_chain"],
                enabled=profile["enabled"],
            )


def init_default_agent_config(repo: ConfigRepository) -> None:
    """Initialize default agent configuration if not exist."""
    # Check if default_profile is set
    default_profile = repo.get_config_value("agent", "default_profile")
    if default_profile is None:
        repo.set_config_value("agent", "default_profile", "smart")

    # Keep subagent_model for backward compatibility, default to fast profile
    subagent_model = repo.get_config_value("agent", "subagent_model")
    if subagent_model is None:
        repo.set_config_value("agent", "subagent_model", "fast")
