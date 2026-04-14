"""Configuration loading utilities."""

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.schema import Config
from nanobot.storage.config_repository import ConfigRepository


def get_system_db_path() -> Path:
    """系统配置数据库路径，仅存放配置相关（~/.nanobot/system.db）。"""
    return Path.home() / ".nanobot" / "system.db"


_config_repo: ConfigRepository | None = None


def get_config_repository() -> ConfigRepository:
    """Get the configuration repository instance (cached)."""
    global _config_repo
    if _config_repo is None:
        _config_repo = ConfigRepository(get_system_db_path())
    return _config_repo


def ensure_system_db_initialized() -> None:
    """
    启动时初始化系统数据库。若目录或 system.db 不存在则创建，缺失表则建立。
    新装机用户首次启动时会自动完成初始化。
    """
    from nanobot.storage.config_repository import ConfigRepository
    from nanobot.storage.cron_repository import CronRepository

    system_db = get_system_db_path()
    system_db.parent.mkdir(parents=True, exist_ok=True)
    # ConfigRepository 和 CronRepository 的 __init__ 会执行 _init_tables，自动建表
    ConfigRepository(system_db)
    CronRepository(system_db)
    logger.debug(f"System DB initialized: {system_db}")


def init_system_providers(repo: "ConfigRepository") -> None:
    """Initialize system providers (is_system=True, user cannot delete).

    Writes to SQLite as the authoritative source. Existing SQLite records
    are preserved (ON CONFLICT DO UPDATE with api_key/api_base only when
    the new value is non-empty).
    """
    from nanobot.providers.system_providers import SYSTEM_PROVIDERS

    logger.info(f"Initializing {len(SYSTEM_PROVIDERS)} system providers...")
    total_models = 0
    for sp in SYSTEM_PROVIDERS:
        sp_id = sp["id"]
        now = repo._get_timestamp()
        with repo._connect() as conn:
            conn.execute(
                """
                INSERT INTO config_providers (id, name, api_key, api_base, enabled, priority, updated_at, display_name, provider_type, is_system, sort_order, config_json, api_version, azure_deployment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    api_key=CASE WHEN excluded.api_key != '' THEN excluded.api_key ELSE api_key END,
                    api_base=CASE WHEN excluded.api_base IS NOT NULL AND excluded.api_base != '' THEN excluded.api_base ELSE api_base END,
                    display_name=excluded.display_name,
                    api_version=CASE WHEN excluded.api_version != '' THEN excluded.api_version ELSE api_version END,
                    azure_deployment=CASE WHEN excluded.azure_deployment != '' THEN excluded.azure_deployment ELSE azure_deployment END
                """,
                (sp_id, sp_id, "", sp.get("api_base"), 1, 0, now,
                 sp["display_name"], sp["provider_type"], 1, 0, "{}", "", ""),
            )
        # Write default models only if they don't exist (preserve user modifications)
        for m in sp.get("models", []):
            existing = repo.get_model(m["id"])
            if existing:
                continue  # Skip existing models to preserve user modifications
            repo.set_model(
                model_id=m["id"],
                provider_id=sp_id,
                name=m["name"],
                litellm_id=m["id"],
                model_type=m.get("model_type", "chat"),
                context_window=m.get("context_window", 128000),
                max_tokens=m.get("max_tokens", 4096),
                supports_vision=m.get("supports_vision", False),
                supports_function_calling=m.get("supports_function_calling", True),
                supports_streaming=True,
                is_default=(m["id"] == sp.get("default_model")),
            )
            total_models += 1
    logger.info(f"System providers initialized: {len(SYSTEM_PROVIDERS)} providers, {total_models} models")


def init_dynamic_providers(
    repo: "ConfigRepository",
    provider_manager: "ProviderManager",
) -> None:
    """
    Initialize all enabled providers from database as dynamic instances.

    This registers all providers (including system providers from
    system_providers.py and user-created providers) with the
    ProviderManager so they can be used for model routing.
    """
    providers = repo.get_all_providers()

    for p in providers:
        if not p.get("enabled"):
            continue

        provider_id = p["id"]
        api_key = p.get("api_key", "")
        api_base = p.get("api_base")
        provider_type = p.get("provider_type", "openai")

        if not api_key:
            logger.debug(f"Skipping provider '{provider_id}': enabled but no API key set")
            continue

        provider_manager.register_provider(
            provider_id=provider_id,
            api_key=api_key,
            api_base=api_base,
            provider_type=provider_type,
        )

    count = sum(1 for p in providers if p.get("enabled") and p.get("api_key"))
    logger.debug(f"Dynamic providers initialized from database: {count} providers")


def ensure_models_populated(repo: "ConfigRepository") -> None:
    """确保 config_models 表有系统模型数据。容错：列缺失时不崩溃，仅记录警告。"""
    from nanobot.providers.system_providers import SYSTEM_PROVIDERS

    total = 0
    skipped = 0
    for sp in SYSTEM_PROVIDERS:
        for m in sp.get("models", []):
            try:
                # Only insert if model doesn't exist (preserve user modifications)
                existing = repo.get_model(m["id"])
                if existing:
                    continue
                repo.set_model(
                    model_id=m["id"],
                    provider_id=sp["id"],
                    name=m["name"],
                    litellm_id=m["id"],
                    model_type=m.get("model_type", "chat"),
                    context_window=m.get("context_window", 128000),
                    max_tokens=m.get("max_tokens", 4096),
                    supports_vision=m.get("supports_vision", False),
                    supports_function_calling=m.get("supports_function_calling", True),
                    supports_streaming=True,
                    is_default=(m["id"] == sp.get("default_model")),
                )
                total += 1
            except Exception as e:
                logger.warning(f"ensure_models_populated: set_model({m['id']}) failed: {e}")
                skipped += 1
    if skipped > 0:
        logger.warning(f"ensure_models_populated: {skipped} models skipped due to errors, {total} inserted")
    else:
        logger.debug(f"ensure_models_populated: {total} models ready")


def ensure_initial_config() -> Config:
    """
    确保 .nanobot 目录和配置存在；若不存在则创建默认配置和工作空间。
    用于首次启动 web-ui 时自动初始化。
    """
    ensure_system_db_initialized()
    repo = get_config_repository()

    # Only check config table (not config_providers) to determine if user config exists.
    # config_providers is populated by init_system_providers, so checking it would
    # always return True after init_system_providers is called.
    if not repo.has_config():
        init_system_providers(repo)
        config = Config()
        save_config(config)
        ws_path = config.workspace_path
        ws_path.mkdir(parents=True, exist_ok=True)
        logger.info(
            "已创建默认配置到 SQLite 数据库和工作空间目录，请在配置页添加 API Key 以使用对话功能"
        )
        return config

    init_system_providers(repo)
    return load_config()


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.utils.helpers import get_data_path
    return get_data_path()


def get_effective_model() -> str:
    """从 ModelRouter 解析当前生效的模型（由 default_profile 决定）。"""
    from nanobot.providers.router import ModelNotFoundError, ModelRouter
    repo = get_config_repository()
    default_profile = repo.get_config_value("agent", "default_profile", "smart")
    router = ModelRouter(repo)
    try:
        return router.get(default_profile).model
    except (KeyError, ValueError, AttributeError, TypeError, ModelNotFoundError) as e:
        logger.debug(f"get_effective_model fallback: {e}")
        return "anthropic/claude-opus-4-6"


def load_config() -> Config:
    """Load configuration from SQLite.

    Applies schema defaults for new fields added after initial installation
    (e.g., browser channel enabled flag).
    """
    repo = get_config_repository()
    try:
        config_data = repo.load_full_config()
        data = convert_keys(config_data)
        # 移除 Config schema 中不存在的扩展字段，避免 Pydantic 校验失败
        for key in ("models", "model_profiles"):
            data.pop(key, None)

        # Apply schema defaults for missing browser channel config
        # browser channel was added later with schema default enabled=True,
        # but old configs may have enabled=False stored. Always use True.
        if "channels" not in data:
            data["channels"] = {}
        if "browser" not in data["channels"]:
            data["channels"]["browser"] = {}
        data["channels"]["browser"]["enabled"] = True

        return Config.model_validate(data)
    except Exception as e:
        logger.warning(f"Failed to load config from SQLite: {e}, using defaults")
        return Config()


def save_config(config: Config) -> None:
    """Save configuration to SQLite."""
    data = config.model_dump()
    data = convert_to_camel(data)

    repo = get_config_repository()
    try:
        repo.save_full_config(data)
        logger.debug("Config saved to SQLite")
    except Exception as e:
        logger.exception("Failed to save config to SQLite")
        raise


def convert_keys(data: Any) -> Any:
    """Convert camelCase keys to snake_case for Pydantic."""
    if isinstance(data, dict):
        return {
            camel_to_snake(k): (v if k in ("env", "headers") else convert_keys(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [convert_keys(item) for item in data]
    return data


def convert_to_camel(data: Any) -> Any:
    """Convert snake_case keys to camelCase."""
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            camel_key = snake_to_camel(k)
            # Don't convert keys inside env/headers dicts — env vars & HTTP headers must stay as-is
            if k in ("env", "headers") and isinstance(v, dict):
                result[camel_key] = v
            else:
                result[camel_key] = convert_to_camel(v)
        return result
    if isinstance(data, list):
        return [convert_to_camel(item) for item in data]
    return data


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    result = []
    for i, char in enumerate(name):
        if char.isupper() and i > 0:
            result.append("_")
        result.append(char.lower())
    return "".join(result)


def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    components = name.split("_")
    return components[0] + "".join(x.title() for x in components[1:])
