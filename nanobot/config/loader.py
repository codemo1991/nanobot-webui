"""Configuration loading utilities."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.schema import Config
from nanobot.storage.config_repository import ConfigRepository


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".nanobot" / "config.json"


def get_db_path() -> Path:
    """Get the SQLite database path."""
    return Path.home() / ".nanobot" / "chat.db"


def get_config_repository() -> ConfigRepository:
    """Get the configuration repository instance."""
    return ConfigRepository(get_db_path())


def ensure_initial_config(config_path: Path | None = None) -> Config:
    """
    确保 .nanobot 目录和配置存在；若不存在则创建默认配置和工作空间。
    用于首次启动 web-ui 时自动初始化，用户无需先执行 nanobot onboard。
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    repo = get_config_repository()
    
    if not repo.has_config():
        if path.exists():
            _migrate_json_to_sqlite(path, repo)
        else:
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


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from SQLite (primary) or JSON file (fallback).
    
    Args:
        config_path: Optional path to config file for fallback.
    
    Returns:
        Loaded configuration object.
    """
    repo = get_config_repository()
    
    if repo.has_config():
        try:
            config_data = repo.load_full_config()
            data = convert_keys(config_data)
            return Config.model_validate(data)
        except Exception as e:
            logger.warning(f"Failed to load config from SQLite: {e}")
    
    path = config_path or get_config_path()
    
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            config = Config.model_validate(convert_keys(data))
            _migrate_json_to_sqlite(path, repo)
            return config
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to load config from {path}: {e}")
    
    return Config()


def _migrate_json_to_sqlite(json_path: Path, repo: ConfigRepository) -> None:
    """
    Migrate configuration from JSON file to SQLite.
    
    Args:
        json_path: Path to the JSON config file.
        repo: ConfigRepository instance.
    """
    try:
        with open(json_path) as f:
            data = json.load(f)
        
        repo.save_full_config(data)
        logger.info(f"Migrated config from {json_path} to SQLite")
        
        backup_path = json_path.with_suffix(".json.bak")
        if not backup_path.exists():
            import shutil
            shutil.copy(json_path, backup_path)
            logger.info(f"Created backup at {backup_path}")
    except Exception as e:
        logger.warning(f"Failed to migrate config to SQLite: {e}")


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to SQLite (primary) and optionally JSON file.
    Uses atomic write (temp file + rename) for JSON to avoid corruption.

    Args:
        config: Configuration to save.
        config_path: Optional path to save JSON backup. Uses default if not provided.
    """
    data = config.model_dump()
    data = convert_to_camel(data)
    
    repo = get_config_repository()
    try:
        repo.save_full_config(data)
        logger.debug("Config saved to SQLite")
    except Exception as e:
        logger.exception("Failed to save config to SQLite")
        raise
    
    path = Path(config_path) if config_path else get_config_path()
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
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
