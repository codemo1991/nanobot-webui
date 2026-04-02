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
                    env_json TEXT DEFAULT '{}',
                    headers_json TEXT DEFAULT '{}',
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

                CREATE TABLE IF NOT EXISTS config_models (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    litellm_id TEXT NOT NULL,
                    aliases TEXT DEFAULT '',
                    capabilities TEXT DEFAULT '',
                    context_window INTEGER DEFAULT 128000,
                    cost_rank INTEGER,
                    quality_rank INTEGER,
                    enabled INTEGER DEFAULT 1,
                    is_default INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (provider_id) REFERENCES config_providers(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS config_model_profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    model_chain TEXT NOT NULL,
                    rules TEXT,
                    enabled INTEGER DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_config_category ON config(category);
                CREATE INDEX IF NOT EXISTS idx_config_providers_enabled ON config_providers(enabled);
                CREATE INDEX IF NOT EXISTS idx_config_channels_enabled ON config_channels(enabled);
                CREATE INDEX IF NOT EXISTS idx_models_provider ON config_models(provider_id);
                CREATE INDEX IF NOT EXISTS idx_models_enabled ON config_models(enabled);
                CREATE INDEX IF NOT EXISTS idx_profiles_enabled ON config_model_profiles(enabled);
                """
            )
            conn.commit()
            # 迁移：已有 config_mcps 表增加 env_json 列
            try:
                cols = {d[1] for d in conn.execute("PRAGMA table_info(config_mcps)").fetchall()}
                if "env_json" not in cols:
                    conn.execute("ALTER TABLE config_mcps ADD COLUMN env_json TEXT DEFAULT '{}'")
                    conn.commit()
            except Exception:
                pass
            # 迁移：已有 config_mcps 表增加 headers_json 列
            try:
                cols = {d[1] for d in conn.execute("PRAGMA table_info(config_mcps)").fetchall()}
                if "headers_json" not in cols:
                    conn.execute("ALTER TABLE config_mcps ADD COLUMN headers_json TEXT DEFAULT '{}'")
                    conn.commit()
            except Exception:
                pass
            # 迁移：已有 config_mcps 表增加 scope_json 列
            try:
                cols = {d[1] for d in conn.execute("PRAGMA table_info(config_mcps)").fetchall()}
                if "scope_json" not in cols:
                    conn.execute("ALTER TABLE config_mcps ADD COLUMN scope_json TEXT DEFAULT '[]'")
                    conn.commit()
            except Exception:
                pass
            # 迁移：已有 config_mcps 表增加 tools_json 列（存储 discover 的工具列表）
            try:
                cols = {d[1] for d in conn.execute("PRAGMA table_info(config_mcps)").fetchall()}
                if "tools_json" not in cols:
                    conn.execute("ALTER TABLE config_mcps ADD COLUMN tools_json TEXT DEFAULT '[]'")
                    conn.commit()
            except Exception:
                pass
            # 迁移：扩展 config_providers 表
            for col, col_type, default in [
                ("display_name", "TEXT", "''"),
                ("provider_type", "TEXT", "'openai'"),
                ("is_system", "INTEGER", "0"),
                ("sort_order", "INTEGER", "0"),
                ("config_json", "TEXT", "'{}'"),
            ]:
                try:
                    cols = {d[1] for d in conn.execute("PRAGMA table_info(config_providers)").fetchall()}
                    if col not in cols:
                        conn.execute(f"ALTER TABLE config_providers ADD COLUMN {col} {col_type} DEFAULT {default}")
                        conn.commit()
                except Exception:
                    pass
            # 迁移：扩展 config_models 表
            for col, col_type, default in [
                ("model_type", "TEXT", "'chat'"),
                ("max_tokens", "INTEGER", "4096"),
                ("supports_vision", "INTEGER", "0"),
                ("supports_function_calling", "INTEGER", "1"),
                ("supports_streaming", "INTEGER", "1"),
            ]:
                try:
                    cols = {d[1] for d in conn.execute("PRAGMA table_info(config_models)").fetchall()}
                    if col not in cols:
                        conn.execute(f"ALTER TABLE config_models ADD COLUMN {col} {col_type} DEFAULT {default}")
                        conn.commit()
                except Exception:
                    pass
            conn.close()
            logger.debug("Base config tables initialized")
        except Exception as e:
            logger.exception("Failed to initialize base config tables")
            raise

        # 独立初始化模型相关表，避免外键约束问题影响核心表创建
        self._init_model_tables()

    def _init_model_tables(self) -> None:
        """初始化模型相关表（独立方法，便于错误隔离）。"""
        try:
            conn = self._connect()
            cursor = conn.cursor()

            # 模型元数据表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS config_models (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    litellm_id TEXT NOT NULL,
                    aliases TEXT DEFAULT '',
                    capabilities TEXT DEFAULT '',
                    context_window INTEGER DEFAULT 128000,
                    cost_rank INTEGER,
                    quality_rank INTEGER,
                    enabled INTEGER DEFAULT 1,
                    is_default INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (provider_id) REFERENCES config_providers(id)
                        ON DELETE CASCADE
                )
                """
            )

            # 模型场景配置表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS config_model_profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    model_chain TEXT NOT NULL,
                    rules TEXT,
                    enabled INTEGER DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )

            # 创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_models_provider ON config_models(provider_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_models_enabled ON config_models(enabled)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_profiles_enabled ON config_model_profiles(enabled)")

            conn.commit()
            conn.close()
            logger.info("Model config tables initialized (config_models, config_model_profiles)")
        except Exception as e:
            # 模型表创建失败不应阻止应用启动，记录错误但继续
            logger.warning(f"Failed to initialize model tables (non-critical): {e}")
            logger.debug("Model table init error details", exc_info=True)

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
                    "display_name": row["display_name"] or row["name"],
                    "provider_type": row["provider_type"] or "openai",
                    "is_system": bool(row["is_system"]),
                    "sort_order": row["sort_order"],
                    "config_json": row["config_json"] or "{}",
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
                        "display_name": row["display_name"] or row["name"],
                        "provider_type": row["provider_type"] or "openai",
                        "is_system": bool(row["is_system"]),
                        "sort_order": row["sort_order"],
                        "config_json": row["config_json"] or "{}",
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to get all providers: {e}")
            return []

    def set_provider(self, provider_id: str, name: str, api_key: str = "",
                     api_base: str | None = None, enabled: bool = False,
                     priority: int = 0, display_name: str = "",
                     provider_type: str = "openai", is_system: bool = False,
                     sort_order: int = 0, config_json: str = "{}") -> None:
        """设置 Provider 配置。"""
        updated_at = self._get_timestamp()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO config_providers (id, name, api_key, api_base, enabled, priority, updated_at, display_name, provider_type, is_system, sort_order, config_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        api_key=excluded.api_key,
                        api_base=excluded.api_base,
                        enabled=excluded.enabled,
                        priority=excluded.priority,
                        updated_at=excluded.updated_at,
                        display_name=excluded.display_name,
                        provider_type=excluded.provider_type,
                        is_system=excluded.is_system,
                        sort_order=excluded.sort_order,
                        config_json=excluded.config_json
                    """,
                    (provider_id, name, api_key, api_base, int(enabled), priority, updated_at, display_name, provider_type, int(is_system), sort_order, config_json)
                )
        except Exception as e:
            logger.exception(f"Failed to set provider {provider_id}")
            raise

    def delete_provider(self, provider_id: str) -> bool:
        """删除 Provider（系统预置不可删除）。"""
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM config_providers WHERE id = ? AND is_system = 0",
                    (provider_id,)
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.warning(f"Failed to delete provider {provider_id}: {e}")
            return False

    def get_system_providers(self) -> list[dict[str, Any]]:
        """获取所有系统预置 Provider。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM config_providers WHERE is_system = 1 ORDER BY sort_order, name"
                ).fetchall()
                return [
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "display_name": row["display_name"] or row["name"],
                        "provider_type": row["provider_type"] or "openai",
                        "api_key": "",
                        "api_base": row["api_base"],
                        "enabled": bool(row["enabled"]),
                        "is_system": True,
                        "sort_order": row["sort_order"],
                        "config_json": row["config_json"] or "{}",
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to get system providers: {e}")
            return []

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
                    "env": json.loads(row["env_json"]) if isinstance(row["env_json"], str) else (row["env_json"] if isinstance(row["env_json"], dict) else {}),
                    "headers": json.loads(row["headers_json"]) if isinstance(row["headers_json"], str) else (row["headers_json"] if isinstance(row["headers_json"], dict) else {}),
                    "scope": json.loads(row["scope_json"]) if isinstance(row["scope_json"], str) else (row["scope_json"] if isinstance(row["scope_json"], list) else []),
                    "tools": json.loads(row["tools_json"]) if isinstance(row["tools_json"], str) else (row["tools_json"] if isinstance(row["tools_json"], list) else []),
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
                        "env": json.loads(row["env_json"]) if isinstance(row["env_json"], str) else (row["env_json"] if isinstance(row["env_json"], dict) else {}),
                        "headers": json.loads(row["headers_json"]) if isinstance(row["headers_json"], str) else (row["headers_json"] if isinstance(row["headers_json"], dict) else {}),
                        "scope": json.loads(row["scope_json"]) if isinstance(row["scope_json"], str) else (row["scope_json"] if isinstance(row["scope_json"], list) else []),
                        "tools": json.loads(row["tools_json"]) if isinstance(row["tools_json"], str) else (row["tools_json"] if isinstance(row["tools_json"], list) else []),
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to get all MCPs: {e}")
            return []

    def set_mcp(self, mcp_id: str, name: str, transport: str = "stdio",
                command: str | None = None, args: list[str] | None = None,
                url: str | None = None, enabled: bool = True,
                env: dict[str, str] | None = None,
                headers: dict[str, str] | None = None,
                scope: list[str] | None = None,
                tools: list[dict[str, Any]] | None = None) -> None:
        """设置 MCP 配置。"""
        args_json = json.dumps(args or [])
        env_json = json.dumps(env or {})
        headers_json = json.dumps(headers or {})
        scope_json = json.dumps(scope or [])
        tools_json = json.dumps(tools or [])
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
                    INSERT INTO config_mcps (id, name, transport, command, args_json, url, enabled, env_json, headers_json, scope_json, tools_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        transport=excluded.transport,
                        command=excluded.command,
                        args_json=excluded.args_json,
                        url=excluded.url,
                        enabled=excluded.enabled,
                        env_json=excluded.env_json,
                        headers_json=excluded.headers_json,
                        scope_json=excluded.scope_json,
                        tools_json=excluded.tools_json,
                        updated_at=excluded.updated_at
                    """,
                    (mcp_id, name, transport, command, args_json, url, int(enabled), env_json, headers_json, scope_json, tools_json, created_at, updated_at)
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
                "displayName": provider["display_name"],
                "priority": provider["priority"],
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

        # Add models and profiles
        config["models"] = self.get_all_models()
        config["modelProfiles"] = self.get_all_model_profiles()

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
            "ollama": "Ollama",
            "gemini": "Gemini",
            "minimax": "Minimax",
        }
        for provider_id, provider_data in providers_config.items():
            api_key = provider_data.get("apiKey", "")
            api_base = provider_data.get("apiBase")
            self.set_provider(
                provider_id=provider_id,
                name=provider_names.get(provider_id, provider_id),
                display_name=provider_names.get(provider_id, provider_id),
                provider_type="openai",
                is_system=False,
                sort_order=0,
                config_json="{}",
                api_key=api_key,
                api_base=api_base,
                enabled=bool(api_key),
                priority=provider_data.get("priority", 0),
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
                env=mcp_data.get("env"),
                headers=mcp_data.get("headers"),
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

    # =========================================================================
    # Model Management
    # =========================================================================

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """获取模型配置。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM config_models WHERE id = ?",
                    (model_id,)
                ).fetchone()
                if row is None:
                    return None
                return {
                    "id": row["id"],
                    "provider_id": row["provider_id"],
                    "name": row["name"],
                    "litellm_id": row["litellm_id"],
                    "aliases": row["aliases"] or "",
                    "capabilities": row["capabilities"] or "",
                    "context_window": row["context_window"],
                    "cost_rank": row["cost_rank"],
                    "quality_rank": row["quality_rank"],
                    "enabled": bool(row["enabled"]),
                    "is_default": bool(row["is_default"]),
                    "model_type": row["model_type"] or "chat",
                    "max_tokens": row["max_tokens"],
                    "supports_vision": bool(row["supports_vision"]),
                    "supports_function_calling": bool(row["supports_function_calling"]),
                    "supports_streaming": bool(row["supports_streaming"]),
                }
        except Exception as e:
            logger.warning(f"Failed to get model {model_id}: {e}")
            return None

    def get_model_by_alias(self, alias: str) -> dict[str, Any] | None:
        """通过别名查找模型。"""
        try:
            with self._connect() as conn:
                # 查找 aliases 包含该别名的模型
                row = conn.execute(
                    """SELECT * FROM config_models
                       WHERE ',' || aliases || ',' LIKE '%,' || ? || ',%'
                       AND enabled = 1
                       LIMIT 1""",
                    (alias,)
                ).fetchone()
                if row:
                    return {
                        "id": row["id"],
                        "provider_id": row["provider_id"],
                        "name": row["name"],
                        "litellm_id": row["litellm_id"],
                        "aliases": row["aliases"] or "",
                        "capabilities": row["capabilities"] or "",
                        "context_window": row["context_window"],
                        "cost_rank": row["cost_rank"],
                        "quality_rank": row["quality_rank"],
                        "enabled": bool(row["enabled"]),
                        "is_default": bool(row["is_default"]),
                        "model_type": row["model_type"] or "chat",
                        "max_tokens": row["max_tokens"],
                        "supports_vision": bool(row["supports_vision"]),
                        "supports_function_calling": bool(row["supports_function_calling"]),
                        "supports_streaming": bool(row["supports_streaming"]),
                    }
                return None
        except Exception as e:
            logger.warning(f"Failed to get model by alias {alias}: {e}")
            return None

    def get_all_models(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        """获取所有模型，或指定 provider 的模型。"""
        try:
            with self._connect() as conn:
                if provider_id:
                    rows = conn.execute(
                        "SELECT * FROM config_models WHERE provider_id = ? ORDER BY name",
                        (provider_id,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM config_models ORDER BY name"
                    ).fetchall()

                return [
                    {
                        "id": row["id"],
                        "provider_id": row["provider_id"],
                        "name": row["name"],
                        "litellm_id": row["litellm_id"],
                        "aliases": row["aliases"] or "",
                        "capabilities": row["capabilities"] or "",
                        "context_window": row["context_window"],
                        "cost_rank": row["cost_rank"],
                        "quality_rank": row["quality_rank"],
                        "enabled": bool(row["enabled"]),
                        "is_default": bool(row["is_default"]),
                        "model_type": row["model_type"] or "chat",
                        "max_tokens": row["max_tokens"],
                        "supports_vision": bool(row["supports_vision"]),
                        "supports_function_calling": bool(row["supports_function_calling"]),
                        "supports_streaming": bool(row["supports_streaming"]),
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to get all models: {e}")
            return []

    def get_enabled_models(self) -> list[dict[str, Any]]:
        """获取所有启用的模型。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT m.*, p.enabled as provider_enabled
                       FROM config_models m
                       JOIN config_providers p ON m.provider_id = p.id
                       WHERE m.enabled = 1 AND p.enabled = 1
                       ORDER BY m.name"""
                ).fetchall()

                return [
                    {
                        "id": row["id"],
                        "provider_id": row["provider_id"],
                        "name": row["name"],
                        "litellm_id": row["litellm_id"],
                        "aliases": row["aliases"] or "",
                        "capabilities": row["capabilities"] or "",
                        "context_window": row["context_window"],
                        "cost_rank": row["cost_rank"],
                        "quality_rank": row["quality_rank"],
                        "enabled": True,
                        "is_default": bool(row["is_default"]),
                        "model_type": row["model_type"] or "chat",
                        "max_tokens": row["max_tokens"],
                        "supports_vision": bool(row["supports_vision"]),
                        "supports_function_calling": bool(row["supports_function_calling"]),
                        "supports_streaming": bool(row["supports_streaming"]),
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to get enabled models: {e}")
            return []

    def clear_default_for_all_models_except(self, except_model_id: str | None = None) -> None:
        """清除所有模型的默认状态，仅保留指定模型。用于保证全局只有一个默认模型。"""
        try:
            with self._connect() as conn:
                if except_model_id:
                    conn.execute(
                        "UPDATE config_models SET is_default = 0 WHERE id != ?",
                        (except_model_id,)
                    )
                else:
                    conn.execute("UPDATE config_models SET is_default = 0")
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to clear default status: {e}")
            raise

    def set_model(self, model_id: str, provider_id: str, name: str, litellm_id: str,
                  aliases: str = "", capabilities: str = "", context_window: int = 128000,
                  cost_rank: int | None = None, quality_rank: int | None = None,
                  enabled: bool = True, is_default: bool = False,
                  model_type: str = "chat", max_tokens: int = 4096,
                  supports_vision: bool = False,
                  supports_function_calling: bool = True,
                  supports_streaming: bool = True) -> None:
        """设置模型配置。"""
        updated_at = self._get_timestamp()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO config_models (id, provider_id, name, litellm_id, aliases,
                        capabilities, context_window, cost_rank, quality_rank, enabled,
                        is_default, updated_at, model_type, max_tokens,
                        supports_vision, supports_function_calling, supports_streaming)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        provider_id=excluded.provider_id,
                        name=excluded.name,
                        litellm_id=excluded.litellm_id,
                        aliases=excluded.aliases,
                        capabilities=excluded.capabilities,
                        context_window=excluded.context_window,
                        cost_rank=excluded.cost_rank,
                        quality_rank=excluded.quality_rank,
                        enabled=excluded.enabled,
                        is_default=excluded.is_default,
                        updated_at=excluded.updated_at,
                        model_type=excluded.model_type,
                        max_tokens=excluded.max_tokens,
                        supports_vision=excluded.supports_vision,
                        supports_function_calling=excluded.supports_function_calling,
                        supports_streaming=excluded.supports_streaming
                    """,
                    (model_id, provider_id, name, litellm_id, aliases, capabilities,
                     context_window, cost_rank, quality_rank, int(enabled), int(is_default),
                     updated_at, model_type, max_tokens,
                     int(supports_vision), int(supports_function_calling),
                     int(supports_streaming))
                )
        except Exception as e:
            logger.exception(f"Failed to set model {model_id}")
            raise

    def delete_model(self, model_id: str) -> bool:
        """删除模型配置。"""
        try:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM config_models WHERE id = ?", (model_id,))
                return cursor.rowcount > 0
        except Exception as e:
            logger.warning(f"Failed to delete model {model_id}: {e}")
            return False

    # =========================================================================
    # Model Profile Management
    # =========================================================================

    def get_model_profile(self, profile_id: str) -> dict[str, Any] | None:
        """获取模型场景配置。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM config_model_profiles WHERE id = ?",
                    (profile_id,)
                ).fetchone()
                if row is None:
                    return None
                return {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"] or "",
                    "model_chain": row["model_chain"],
                    "rules": row["rules"] or "",
                    "enabled": bool(row["enabled"]),
                }
        except Exception as e:
            logger.warning(f"Failed to get model profile {profile_id}: {e}")
            return None

    def get_all_model_profiles(self) -> list[dict[str, Any]]:
        """获取所有模型场景配置。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM config_model_profiles ORDER BY id"
                ).fetchall()

                return [
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "description": row["description"] or "",
                        "model_chain": row["model_chain"],
                        "rules": row["rules"] or "",
                        "enabled": bool(row["enabled"]),
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to get all model profiles: {e}")
            return []

    def set_model_profile(self, profile_id: str, name: str, model_chain: str,
                          description: str = "", rules: str = "",
                          enabled: bool = True) -> None:
        """设置模型场景配置。"""
        updated_at = self._get_timestamp()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO config_model_profiles (id, name, description, model_chain,
                        rules, enabled, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        description=excluded.description,
                        model_chain=excluded.model_chain,
                        rules=excluded.rules,
                        enabled=excluded.enabled,
                        updated_at=excluded.updated_at
                    """,
                    (profile_id, name, description, model_chain, rules,
                     int(enabled), updated_at)
                )
        except Exception as e:
            logger.exception(f"Failed to set model profile {profile_id}")
            raise

    def delete_model_profile(self, profile_id: str) -> bool:
        """删除模型场景配置。"""
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM config_model_profiles WHERE id = ?",
                    (profile_id,)
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.warning(f"Failed to delete model profile {profile_id}: {e}")
            return False
