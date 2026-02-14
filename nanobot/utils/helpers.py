"""Utility functions for nanobot."""

from pathlib import Path
from datetime import datetime


def ensure_dir(path: Path) -> Path:
    """Ensure a directory exists, creating it if necessary."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_path() -> Path:
    """Get the nanobot data directory (~/.nanobot)."""
    return ensure_dir(Path.home() / ".nanobot")


def get_workspace_path(workspace: str | None = None) -> Path:
    """
    Get the workspace path.
    
    Args:
        workspace: Optional workspace path. Defaults to ~/.nanobot/workspace.
    
    Returns:
        Expanded and ensured workspace path.
    """
    if workspace:
        path = Path(workspace).expanduser()
    else:
        path = Path.home() / ".nanobot" / "workspace"
    return ensure_dir(path)


def get_sessions_path() -> Path:
    """Get the sessions storage directory."""
    return ensure_dir(get_data_path() / "sessions")


def get_memory_path(workspace: Path | None = None) -> Path:
    """Get the memory directory within the workspace."""
    ws = workspace or get_workspace_path()
    return ensure_dir(ws / "memory")


def get_skills_path(workspace: Path | None = None) -> Path:
    """Get the skills directory within the workspace."""
    ws = workspace or get_workspace_path()
    return ensure_dir(ws / "skills")


def today_date() -> str:
    """Get today's date in YYYY-MM-DD format."""
    return datetime.now().strftime("%Y-%m-%d")


def timestamp() -> str:
    """Get current timestamp in ISO format."""
    return datetime.now().isoformat()


def truncate_string(s: str, max_len: int = 100, suffix: str = "...") -> str:
    """Truncate a string to max length, adding suffix if truncated."""
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix


def safe_filename(name: str) -> str:
    """Convert a string to a safe filename."""
    # Replace unsafe characters
    unsafe = '<>:"/\\|?*'
    for char in unsafe:
        name = name.replace(char, "_")
    return name.strip()


def parse_session_key(key: str) -> tuple[str, str]:
    """
    Parse a session key into channel and chat_id.
    
    Args:
        key: Session key in format "channel:chat_id"
    
    Returns:
        Tuple of (channel, chat_id)
    """
    parts = key.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid session key: {key}")
    return parts[0], parts[1]


def estimate_tokens(text: str) -> int:
    """
    估算文本的token数量。
    
    使用简化的估算方法：
    - 中文字符：约 1.5 字符/token
    - 英文字符：约 4 字符/token
    - 混合文本取加权平均
    
    Args:
        text: 要估算的文本
    
    Returns:
        估算的token数量
    """
    if not text:
        return 0
    
    CHARS_PER_TOKEN_CHINESE = 1.5
    CHARS_PER_TOKEN_ENGLISH = 4.0
    
    chinese_chars = 0
    total_chars = len(text)
    
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            chinese_chars += 1
    
    non_chinese = total_chars - chinese_chars
    
    chinese_tokens = chinese_chars / CHARS_PER_TOKEN_CHINESE
    english_tokens = non_chinese / CHARS_PER_TOKEN_ENGLISH
    
    return int(chinese_tokens + english_tokens)


def truncate_to_token_limit(text: str, max_tokens: int, suffix: str = "...") -> str:
    """
    将文本截断到指定的token限制内。
    
    Args:
        text: 要截断的文本
        max_tokens: 最大token数
        suffix: 截断后添加的后缀
    
    Returns:
        截断后的文本
    """
    if not text or max_tokens <= 0:
        return ""
    
    if estimate_tokens(text) <= max_tokens:
        return text
    
    min_chars = len(suffix) + 1
    target_chars = max(min_chars, int(max_tokens * 2.5))
    truncated = text[:target_chars]
    
    while len(truncated) > min_chars and estimate_tokens(truncated + suffix) > max_tokens:
        truncated = truncated[:-100]
    
    if len(truncated) <= min_chars:
        return text[:max(1, int(max_tokens * 2))]
    
    return truncated + suffix
