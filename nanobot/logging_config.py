"""
Centralized logging configuration for nanobot.

- File sink: ~/.nanobot/nanobot.log with rotation, captures all levels (DEBUG and up)
- Unhandled exceptions: Full traceback to stderr and file via sys.excepthook
- Use logger.exception() in catch blocks for detailed error logging with traceback
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

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
        )
    except Exception as e:
        logger.warning(f"Could not add log file sink to {log_path}: {e}")

    if capture_unhandled:
        sys.excepthook = _excepthook
