"""Configuration repository for SQLite-based config storage."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class ConfigRepository:
    """配置数据仓库，负责配置的 SQLite 存储与检索。"""

    def __init__(self, db_path: Path):
        """
        初始化配置仓库。

        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _connect(self):
        """创建数据库连接。"""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        """初始化配置相关表结构。"""
        try:
            conn = self._connect()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS config (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    value_type TEXT DEFAULT 'str',
                    updated_at TEXT NOT NULL,
                    UNIQUE(category, key)
                );

                CREATE TABLE IF NOT EXISTS config_providers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    api_key TEXT DEFAULT '',
                    api_base TEXT,
                    enabled INTEGER DEFAULT 0,
                    priority INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS config_channels (
                    id TEXT PRIMARY KEY,
                    enabled INTEGER DEFAULT 0,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS config_mcps (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    transport TEXT DEFAULT 'stdio',
                    command TEXT,
                    args_json TEXT DEFAULT '[]',
                    url TEXT,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS config_tools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(tool_type, key)
                );

                CREATE INDEX IF NOT EXISTS idx_config_category ON config(category);
                CREATE INDEX IF NOT EXISTS idx_config_providers_enabled ON config_providers(enabled);
                CREATE INDEX IF NOT EXISTS idx_config_channels_enabled ON config_channels(enabled);
                """
            )
            conn.commit()
            conn.close()
            logger.debug("Config tables initialized")
        except Exception as e:
            logger.exception("Failed to initialize config tables")
            raise

    def _get_timestamp(self) -> str:
        """获取当前时间戳。"""
        return datetime.now().isoformat()

    def get_config_value(self, category: str, key: str, default: Any = None) -> Any:
        """
        获取核心配置值。

        Args:
            category: 配置分类
            key: 配置键
            default: 默认值

        Returns:
            配置值，不存在返回默认值
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value, value_type FROM config WHERE category = ? AND key = ?",
                    (category, key)
                ).fetchone()
                if row is None:
                    return default
                return self._parse_value(row["value"], row["value_type"])
        except Exception as e:
            logger.warning(f"Failed to get config {category}.{key}: {e}")
            return default

    def set_config_value(self, category: str, key: str, value: Any, value_type: str | None = None) -> None:
        """
        设置核心配置值。

        Args:
            category: 配置分类
            key: 配置键
            value: 配置值
            value_type: 值类型，自动推断
        """
        if value_type is None:
            value_type = self._infer_type(value)
        str_value = self._serialize_value(value, value_type)
        updated_at = self._get_timestamp()

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO config (category, key, value, value_type, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(category, key) DO UPDATE SET
                        value=excluded.value,
                        value_type=excluded.value_type,
                        updated_at=excluded.updated_at
                    """,
                    (category, key, str_value, value_type, updated_at)
                )
        except Exception as e:
            logger.exception(f"Failed to set config {category}.{key}")
            raise

    def get_all_config(self, category: str | None = None) -> dict[str, Any]:
        """
        获取所有配置或指定分类的配置。

        Args:
            category: 可选的分类过滤

        Returns:
            配置字典
        """
        result: dict[str, Any] = {}
        try:
            with self._connect() as conn:
                if category:
                    rows = conn.execute(
                        "SELECT category, key, value, value_type FROM config WHERE category = ?",
                        (category,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT category, key, value, value_type FROM config"
                    ).fetchall()

                for row in rows:
                    cat = row["category"]
                    key = row["key"]
                    value = self._parse_value(row["value"], row["value_type"])
                    if cat not in result:
                        result[cat] = {}
                    result[cat][key] = value
        except Exception as e:
            logger.warning(f"Failed to get all config: {e}")
        return result

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        """获取 Provider 配置。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM config_providers WHERE id = ?",
                    (provider_id,)
                ).fetchone()
                if row is None:
                    return None
                return {
                    "id": row["id"],
                    "name": row["name"],
                    "api_key": row["api_key"] or "",
                    "api_base": row["api_base"],
                    "enabled": bool(row["enabled"]),
                    "priority": row["priority"],
                }
        except Exception as e:
            logger.warning(f"Failed to get provider {provider_id}: {e}")
            return None

    def get_all_providers(self) -> list[dict[str, Any]]:
        """获取所有 Provider 配置。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM config_providers ORDER BY priority, id"
                ).fetchall()
                return [
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "api_key": row["api_key"] or "",
                        "api_base": row["api_base"],
                        "enabled": bool(row["enabled"]),
                        "priority": row["priority"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to get all providers: {e}")
            return []

    def set_provider(self, provider_id: str, name: str, api_key: str = "",
                     api_base: str | None = None, enabled: bool = False,
                     priority: int = 0) -> None:
        """设置 Provider 配置。"""
        updated_at = self._get_timestamp()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO config_providers (id, name, api_key, api_base, enabled, priority, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        api_key=excluded.api_key,
                        api_base=excluded.api_base,
                        enabled=excluded.enabled,
                        priority=excluded.priority,
                        updated_at=excluded.updated_at
                    """,
                    (provider_id, name, api_key, api_base, int(enabled), priority, updated_at)
                )
        except Exception as e:
            logger.exception(f"Failed to set provider {provider_id}")
            raise

    def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        """获取 Channel 配置。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM config_channels WHERE id = ?",
                    (channel_id,)
                ).fetchone()
                if row is None:
                    return None
                config_json = json.loads(row["config_json"]) if row["config_json"] else {}
                return {
                    "id": row["id"],
                    "enabled": bool(row["enabled"]),
                    **config_json
                }
        except Exception as e:
            logger.warning(f"Failed to get channel {channel_id}: {e}")
            return None

    def get_all_channels(self) -> dict[str, dict[str, Any]]:
        """获取所有 Channel 配置。"""
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM config_channels").fetchall()
                result = {}
                for row in rows:
                    config_json = json.loads(row["config_json"]) if row["config_json"] else {}
                    result[row["id"]] = {
                        "id": row["id"],
                        "enabled": bool(row["enabled"]),
                        **config_json
                    }
                return result
        except Exception as e:
            logger.warning(f"Failed to get all channels: {e}")
            return {}

    def set_channel(self, channel_id: str, enabled: bool = False,
                    config: dict[str, Any] | None = None) -> None:
        """设置 Channel 配置。"""
        config = config or {}
        config_json = json.dumps(config)
        updated_at = self._get_timestamp()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO config_channels (id, enabled, config_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        enabled=excluded.enabled,
                        config_json=excluded.config_json,
                        updated_at=excluded.updated_at
                    """,
                    (channel_id, int(enabled), config_json, updated_at)
                )
        except Exception as e:
            logger.exception(f"Failed to set channel {channel_id}")
            raise

    def get_mcp(self, mcp_id: str) -> dict[str, Any] | None:
        """获取 MCP 配置。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM config_mcps WHERE id = ?",
                    (mcp_id,)
                ).fetchone()
                if row is None:
                    return None
                return {
                    "id": row["id"],
                    "name": row["name"],
                    "transport": row["transport"],
                    "command": row["command"],
                    "args": json.loads(row["args_json"]) if row["args_json"] else [],
                    "url": row["url"],
                    "enabled": bool(row["enabled"]),
                }
        except Exception as e:
            logger.warning(f"Failed to get MCP {mcp_id}: {e}")
            return None

    def get_all_mcps(self) -> list[dict[str, Any]]:
        """获取所有 MCP 配置。"""
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM config_mcps ORDER BY id").fetchall()
                return [
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "transport": row["transport"],
                        "command": row["command"],
                        "args": json.loads(row["args_json"]) if row["args_json"] else [],
                        "url": row["url"],
                        "enabled": bool(row["enabled"]),
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to get all MCPs: {e}")
            return []

    def set_mcp(self, mcp_id: str, name: str, transport: str = "stdio",
                command: str | None = None, args: list[str] | None = None,
                url: str | None = None, enabled: bool = True) -> None:
        """设置 MCP 配置。"""
        args_json = json.dumps(args or [])
        updated_at = self._get_timestamp()
        created_at = updated_at
        try:
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT created_at FROM config_mcps WHERE id = ?", (mcp_id,)
                ).fetchone()
                if existing:
                    created_at = existing["created_at"]
                conn.execute(
                    """
                    INSERT INTO config_mcps (id, name, transport, command, args_json, url, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        transport=excluded.transport,
                        command=excluded.command,
                        args_json=excluded.args_json,
                        url=excluded.url,
                        enabled=excluded.enabled,
                        updated_at=excluded.updated_at
                    """,
                    (mcp_id, name, transport, command, args_json, url, int(enabled), created_at, updated_at)
                )
        except Exception as e:
            logger.exception(f"Failed to set MCP {mcp_id}")
            raise

    def delete_mcp(self, mcp_id: str) -> bool:
        """删除 MCP 配置。"""
        try:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM config_mcps WHERE id = ?", (mcp_id,))
                return cursor.rowcount > 0
        except Exception as e:
            logger.warning(f"Failed to delete MCP {mcp_id}: {e}")
            return False

    def get_tool_config(self, tool_type: str) -> dict[str, Any]:
        """获取工具配置。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT key, value FROM config_tools WHERE tool_type = ?",
                    (tool_type,)
                ).fetchall()
                return {row["key"]: row["value"] for row in rows}
        except Exception as e:
            logger.warning(f"Failed to get tool config {tool_type}: {e}")
            return {}

    def set_tool_config(self, tool_type: str, key: str, value: Any) -> None:
        """设置工具配置。"""
        str_value = str(value)
        updated_at = self._get_timestamp()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO config_tools (tool_type, key, value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(tool_type, key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (tool_type, key, str_value, updated_at)
                )
        except Exception as e:
            logger.exception(f"Failed to set tool config {tool_type}.{key}")
            raise

    def has_config(self) -> bool:
        """检查是否已有配置数据。"""
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) as cnt FROM config").fetchone()
                if row and row["cnt"] > 0:
                    return True
                row = conn.execute("SELECT COUNT(*) as cnt FROM config_providers").fetchone()
                if row and row["cnt"] > 0:
                    return True
                return False
        except Exception:
            return False

    def _infer_type(self, value: Any) -> str:
        """推断值类型。"""
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, (dict, list)):
            return "json"
        return "str"

    def _serialize_value(self, value: Any, value_type: str) -> str:
        """序列化值。"""
        if value_type == "json":
            return json.dumps(value)
        if value_type == "bool":
            return "1" if value else "0"
        return str(value)

    def _parse_value(self, value: str, value_type: str) -> Any:
        """解析值。"""
        if value_type == "int":
            return int(value)
        if value_type == "float":
            return float(value)
        if value_type == "bool":
            return value.lower() in ("1", "true", "yes")
        if value_type == "json":
            return json.loads(value)
        return value

    def load_full_config(self) -> dict[str, Any]:
        """
        加载完整配置，返回与 config.json 兼容的字典结构。
        """
        config: dict[str, Any] = {
            "agents": {"defaults": {}},
            "channels": {},
            "providers": {},
            "gateway": {},
            "tools": {"web": {}, "exec": {}, "filesystem": {}},
            "mirror": {},
            "mcps": [],
        }

        all_config = self.get_all_config()
        if "agent" in all_config:
            config["agents"]["defaults"] = all_config["agent"]
        if "gateway" in all_config:
            config["gateway"] = all_config["gateway"]
        if "mirror" in all_config:
            config["mirror"] = all_config["mirror"]

        for provider in self.get_all_providers():
            config["providers"][provider["id"]] = {
                "apiKey": provider["api_key"],
                "apiBase": provider["api_base"],
            }

        for channel_id, channel_data in self.get_all_channels().items():
            channel_config = {"enabled": channel_data.get("enabled", False)}
            for k, v in channel_data.items():
                if k not in ("id", "enabled"):
                    channel_config[self._snake_to_camel(k)] = v
            config["channels"][channel_id] = channel_config

        for tool_type in ["web", "exec", "filesystem"]:
            tool_config = self.get_tool_config(tool_type)
            if tool_config:
                result_config: dict[str, Any] = {}
                for k, v in tool_config.items():
                    parts = k.split("_", 1)
                    if len(parts) == 2:
                        parent_key, sub_key = parts
                        camel_parent = self._snake_to_camel(parent_key)
                        camel_sub = self._snake_to_camel(sub_key)
                        if camel_parent not in result_config:
                            result_config[camel_parent] = {}
                        result_config[camel_parent][camel_sub] = v
                    else:
                        result_config[self._snake_to_camel(k)] = v
                config["tools"][tool_type] = result_config

        config["mcps"] = self.get_all_mcps()

        return config

    def save_full_config(self, config_data: dict[str, Any]) -> None:
        """
        保存完整配置，接收与 config.json 兼容的字典结构。
        """
        agents_defaults = config_data.get("agents", {}).get("defaults", {})
        for key, value in agents_defaults.items():
            self.set_config_value("agent", self._camel_to_snake(key), value)

        gateway_config = config_data.get("gateway", {})
        for key, value in gateway_config.items():
            self.set_config_value("gateway", self._camel_to_snake(key), value)

        mirror_config = config_data.get("mirror", {})
        for key, value in mirror_config.items():
            self.set_config_value("mirror", self._camel_to_snake(key), value)

        providers_config = config_data.get("providers", {})
        provider_names = {
            "anthropic": "Anthropic",
            "openai": "OpenAI",
            "openrouter": "OpenRouter",
            "deepseek": "DeepSeek",
            "groq": "Groq",
            "zhipu": "Zhipu",
            "dashscope": "DashScope",
            "vllm": "vLLM",
            "gemini": "Gemini",
            "minimax": "Minimax",
        }
        for provider_id, provider_data in providers_config.items():
            api_key = provider_data.get("apiKey", "")
            api_base = provider_data.get("apiBase")
            self.set_provider(
                provider_id=provider_id,
                name=provider_names.get(provider_id, provider_id),
                api_key=api_key,
                api_base=api_base,
                enabled=bool(api_key),
            )

        channels_config = config_data.get("channels", {})
        for channel_id, channel_data in channels_config.items():
            enabled = channel_data.get("enabled", False)
            extra_config = {k: v for k, v in channel_data.items() if k != "enabled"}
            snake_config = {self._camel_to_snake(k): v for k, v in extra_config.items()}
            self.set_channel(channel_id, enabled=enabled, config=snake_config)

        tools_config = config_data.get("tools", {})
        for tool_type, tool_data in tools_config.items():
            if isinstance(tool_data, dict):
                for key, value in tool_data.items():
                    if isinstance(value, dict):
                        for sub_key, sub_value in value.items():
                            self.set_tool_config(tool_type, f"{self._camel_to_snake(key)}_{self._camel_to_snake(sub_key)}", sub_value)
                    else:
                        self.set_tool_config(tool_type, self._camel_to_snake(key), value)

        mcps_config = config_data.get("mcps", [])
        for mcp_data in mcps_config:
            self.set_mcp(
                mcp_id=mcp_data.get("id", ""),
                name=mcp_data.get("name", ""),
                transport=mcp_data.get("transport", "stdio"),
                command=mcp_data.get("command"),
                args=mcp_data.get("args", []),
                url=mcp_data.get("url"),
                enabled=mcp_data.get("enabled", True),
            )

    def _camel_to_snake(self, name: str) -> str:
        """Convert camelCase to snake_case."""
        result = []
        for i, char in enumerate(name):
            if char.isupper() and i > 0:
                result.append("_")
            result.append(char.lower())
        return "".join(result)

    def _snake_to_camel(self, name: str) -> str:
        """Convert snake_case to camelCase."""
        components = name.split("_")
        return components[0] + "".join(x.title() for x in components[1:])
