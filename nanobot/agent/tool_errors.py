"""工具错误标准化：结构化错误格式，便于 LLM 区分可恢复/不可恢复错误。"""

import asyncio

# 可重试的异常类型（网络抖动、临时故障等）
RETRYABLE_EXCEPTIONS: tuple[type, ...] = (
    ConnectionError,
    TimeoutError,
    OSError,  # 包含 ConnectionRefusedError, BrokenPipeError 等
    asyncio.TimeoutError,
)

# HTTP 相关可重试状态
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

# 错误前缀，供 LLM 识别
PREFIX_RETRYABLE = "[RETRYABLE]"
PREFIX_PERMANENT = "[ERROR]"


def is_retryable_error(exc: BaseException) -> bool:
    """判断异常是否可重试。"""
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        return True
    # HTTP 错误（如 aiohttp.ClientResponseError）
    msg = str(exc).lower()
    error_type = type(exc).__name__.lower()
    combined = msg + " " + error_type

    # 可重试的关键字模式
    retryable_keywords = {
        "429", "timeout", "connection",
        "503", "502", "504", "500",
        "overloaded", "rate_limit", "529",
        "unavailable", "internal_error"
    }

    return any(kw in combined for kw in retryable_keywords)


def format_tool_error(
    tool_name: str,
    exc: BaseException,
    *,
    retryable: bool | None = None,
) -> str:
    """
    将工具执行异常格式化为标准化错误字符串。

    Args:
        tool_name: 工具名称
        exc: 异常对象
        retryable: 是否可重试。若为 None，则根据异常类型自动判断

    Returns:
        标准化错误字符串，格式为：
        - [RETRYABLE] 可重试: 工具名 - 错误信息
        - [ERROR] 永久性错误: 工具名 - 错误信息
    """
    if retryable is None:
        retryable = is_retryable_error(exc)
    msg = str(exc).strip() or type(exc).__name__
    if retryable:
        return f"{PREFIX_RETRYABLE} 可重试: {tool_name} - {msg}"
    return f"{PREFIX_PERMANENT} 永久性错误: {tool_name} - {msg}"


def format_tool_not_found(tool_name: str) -> str:
    """工具未找到时的标准化错误。"""
    return f"{PREFIX_PERMANENT} 工具不存在: {tool_name}"


def format_invalid_params(tool_name: str, errors: list[str]) -> str:
    """参数校验失败时的标准化错误。"""
    detail = "; ".join(errors)
    return f"{PREFIX_PERMANENT} 参数无效: {tool_name} - {detail}"


def is_structured_error(result: str) -> bool:
    """判断字符串是否为标准化错误格式。"""
    return result.startswith(PREFIX_RETRYABLE) or result.startswith(PREFIX_PERMANENT)


def is_retryable_result(result: str) -> bool:
    """从标准化错误字符串判断是否可重试。"""
    return result.startswith(PREFIX_RETRYABLE)
