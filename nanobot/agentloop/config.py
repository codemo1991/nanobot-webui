"""AgentLoop 数据库路径配置。

数据库路径约定：
- 业务 DB (chat.db): {workspace}/.nanobot/chat.db 或 ~/.nanobot/chat.db
  示例: E:\\workSpace\\.nanobot\\chat.db
- 系统 DB (system.db): ~/.nanobot/system.db
  示例: C:\\Users\\<user>\\.nanobot\\system.db
"""

from pathlib import Path


def get_chat_db_path(workspace: Path | None = None) -> Path:
    """
    获取业务数据库路径（traces, tasks, artifacts, events）。
    优先使用 workspace/.nanobot/chat.db，否则使用 ~/.nanobot/chat.db。
    """
    if workspace:
        return Path(workspace).expanduser().resolve() / ".nanobot" / "chat.db"
    return Path.home() / ".nanobot" / "chat.db"


def get_system_db_path() -> Path:
    """获取系统配置数据库路径（capability_registry）。"""
    return Path.home() / ".nanobot" / "system.db"
