"""Configuration loading utilities."""

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.schema import Config
from nanobot.storage.config_repository import ConfigRepository


def get_db_path() -> Path:
    """Get the SQLite database path."""
    return Path.home() / ".nanobot" / "chat.db"


def get_config_repository() -> ConfigRepository:
    """Get the configuration repository instance."""
    return ConfigRepository(get_db_path())


def ensure_initial_config() -> Config:
    """
    确保 .nanobot 目录和配置存在；若不存在则创建默认配置和工作空间。
    用于首次启动 web-ui 时自动初始化。
    """
    get_db_path().parent.mkdir(parents=True, exist_ok=True)

    repo = get_config_repository()

    if not repo.has_config():
        config = Config()
        save_config(config)
        ws_path = config.workspace_path
        ws_path.mkdir(parents=True, exist_ok=True)
        logger.info(
            "已创建默认配置到 SQLite 数据库和工作空间目录，请在配置页添加 API Key 以使用对话功能"
        )
        return config

    return load_config()


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.utils.helpers import get_data_path
    return get_data_path()


def load_config() -> Config:
    """Load configuration from SQLite."""
    repo = get_config_repository()
    try:
        config_data = repo.load_full_config()
        data = convert_keys(config_data)
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
        return {camel_to_snake(k): convert_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_keys(item) for item in data]
    return data


def convert_to_camel(data: Any) -> Any:
    """Convert snake_case keys to camelCase."""
    if isinstance(data, dict):
        return {snake_to_camel(k): convert_to_camel(v) for k, v in data.items()}
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
