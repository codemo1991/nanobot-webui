"""
Centralized logging configuration for nanobot.

- File sink: ~/.nanobot/nanobot.log with rotation, captures all levels (DEBUG and up)
- Unhandled exceptions: Full traceback to stderr and file via sys.excepthook
- Use logger.exception() in catch blocks for detailed error logging with traceback
- On Windows: uses enqueue=True to avoid PermissionError [WinError 32] during rotation
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from loguru import logger

# Windows 下文件轮换时 os.rename 易触发 PermissionError，enqueue 可将写操作移到单独线程降低冲突
_USE_ENQUEUE = sys.platform == "win32"

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)


def _excepthook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
    """Log unhandled exceptions with full traceback."""
    logger.opt(exception=(exc_type, exc_value, exc_tb)).critical(
        f"Unhandled exception: {exc_type.__name__}: {exc_value}"
    )
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def setup_logging(
    *,
    log_file: Path | str | None = None,
    level: str | None = None,
    capture_unhandled: bool = True,
) -> None:
    """
    Configure nanobot logging.

    Args:
        log_file: Path to log file. Default: ~/.nanobot/nanobot.log
        level: Minimum log level for console. Default: INFO, or NANOBOT_LOG_LEVEL env var
        capture_unhandled: Install sys.excepthook to log unhandled exceptions with traceback
    """
    import os
    if level is None:
        level = os.environ.get("NANOBOT_LOG_LEVEL", "INFO").upper()
    # Remove default stderr sink to avoid duplicates when adding custom format
    logger.remove()

    # Console: configurable level
    logger.add(
        sys.stderr,
        format=LOG_FORMAT,
        level=level,
        colorize=True,
    )

    # File: DEBUG and above (capture all for debugging), with rotation
    if log_file is None:
        log_file = Path.home() / ".nanobot" / "nanobot.log"
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        logger.add(
            str(log_path),
            format=LOG_FORMAT,
            level="DEBUG",
            rotation="10 MB",
            retention="5 days",
            encoding="utf-8",
            enqueue=_USE_ENQUEUE,  # Windows 下避免轮换时 PermissionError [WinError 32]
        )
    except Exception as e:
        logger.warning(f"Could not add log file sink to {log_path}: {e}")

    if capture_unhandled:
        sys.excepthook = _excepthook


def reconfigure_logging(level: str) -> None:
    """
    Reconfigure console log level at runtime.

    Args:
        level: New log level for console (DEBUG, INFO, WARNING, ERROR, TRACE)
    """
    level = level.upper()
    # Remove existing console sink (stderr, keep file sink)
    # loguru 的 sink 存储在 logger._core.handlers 中
    # 更简单的方式：移除所有 sink，重新添加
    import os
    log_file = os.environ.get("NANOBOT_LOG_FILE") or Path.home() / ".nanobot" / "nanobot.log"

    logger.remove()

    # Re-add console with new level
    logger.add(
        sys.stderr,
        format=LOG_FORMAT,
        level=level,
        colorize=True,
    )

    # Re-add file sink (always DEBUG)
    try:
        logger.add(
            str(log_file),
            format=LOG_FORMAT,
            level="DEBUG",
            rotation="10 MB",
            retention="5 days",
            encoding="utf-8",
            enqueue=_USE_ENQUEUE,  # Windows 下避免轮换时 PermissionError [WinError 32]
        )
    except Exception:
        pass  # 文件 sink 可能已存在，忽略错误

    logger.debug(f"Log level reconfigured to {level}")
