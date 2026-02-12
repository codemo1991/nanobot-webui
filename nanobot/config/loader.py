"""Configuration loading utilities."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.schema import Config


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".nanobot" / "config.json"


def ensure_initial_config(config_path: Path | None = None) -> Config:
    """
    确保 .nanobot 目录和配置文件存在；若不存在则创建默认配置和工作空间。
    用于首次启动 web-ui 时自动初始化，用户无需先执行 nanobot onboard。
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        config = Config()
        save_config(config, path)
        # 创建工作空间目录
        ws_path = config.workspace_path
        ws_path.mkdir(parents=True, exist_ok=True)
        logger.info(
            "已创建默认配置 ~/.nanobot/config.json 和工作空间目录，请在配置页添加 API Key 以使用对话功能"
        )
        return config
    return load_config(path)


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.utils.helpers import get_data_path
    return get_data_path()


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.
    
    Args:
        config_path: Optional path to config file. Uses default if not provided.
    
    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()
    
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            return Config.model_validate(convert_keys(data))
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")
    
    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.
    Uses atomic write (temp file + rename) to avoid corruption on concurrent save.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = Path(config_path) if config_path else get_config_path()
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump()
    data = convert_to_camel(data)

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
