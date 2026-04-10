"""
部分模型（如 DeepSeek 系）在 tool 协议异常或重试后，会把「伪工具调用」以 DSML 文本写在 assistant.content 里，
而不走 OpenAI 的 message.tool_calls。此处解析为 ToolCallRequest，供主循环正常执行工具。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMResponse, ToolCallRequest


def _normalize_dsml_pipes(text: str) -> str:
    """全角竖线 U+FF5C 与 ASCII | 统一，便于匹配。"""
    return text.replace("\uff5c", "|")


def _parse_dsml_parameter_block(block: str) -> dict[str, Any]:
    """从 invoke 与闭合标签之间的片段解析参数字典。"""
    args: dict[str, Any] = {}
    # 行式：name="space_key" ... value: RUIYUN 或 value: 100
    for m in re.finditer(
        r'name\s*=\s*"([^"]+)"[^<\n]*?value\s*:\s*([^\s<\n]+)',
        block,
        re.DOTALL | re.IGNORECASE,
    ):
        args[m.group(1)] = _coerce_param_value(m.group(2).strip())

    if args:
        return args

    # 标签式：<|DSML|parameter name="x">y</|DSML|parameter>
    for m in re.finditer(
        r'<\|DSML\|\s*parameter[^>]*name\s*=\s*"([^"]+)"[^>]*>([^<]+)</',
        _normalize_dsml_pipes(block),
        re.DOTALL | re.IGNORECASE,
    ):
        args[m.group(1)] = _coerce_param_value(m.group(2).strip())

    return args


def _coerce_param_value(s: str) -> Any:
    sl = s.lower()
    if sl == "true":
        return True
    if sl == "false":
        return False
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        try:
            return int(s)
        except ValueError:
            pass
    try:
        if "." in s:
            return float(s)
    except ValueError:
        pass
    return s


def parse_dsml_invocations(content: str) -> list[ToolCallRequest]:
    """
    从 assistant 正文中解析 <|DSML|invoke name="tool_name"> ... </|DSML|invoke>。
    支持全角竖线、大小写变化。
    """
    if not content or "DSML" not in content.upper():
        return []

    text = _normalize_dsml_pipes(content)
    if "|DSML|" not in text and "DSML" not in text:
        return []

    results: list[ToolCallRequest] = []
    invoke_start = re.compile(
        r"<\|DSML\|\s*invoke\s+name\s*=\s*\"([^\"]+)\"\s*>",
        re.IGNORECASE,
    )
    invoke_end = re.compile(
        r"</\s*\|DSML\|\s*invoke\s*>",
        re.IGNORECASE,
    )

    for m in invoke_start.finditer(text):
        tool_name = m.group(1).strip()
        segment_start = m.end()
        rest = text[segment_start:]
        end_m = invoke_end.search(rest)
        block = rest[: end_m.start()] if end_m else rest
        arguments = _parse_dsml_parameter_block(block)
        results.append(
            ToolCallRequest(
                id=f"dsml_{uuid.uuid4().hex[:12]}",
                name=tool_name,
                arguments=arguments,
            )
        )

    return results


def strip_dsml_blocks_from_content(content: str) -> str:
    """去掉正文中的 DSML 块，保留之前的自然语言前缀。"""
    if not content:
        return ""
    m = re.search(r"<\s*[｜|]\s*DSML", content, re.IGNORECASE)
    if m:
        return content[: m.start()].strip()
    return content.strip()


def coerce_llm_response_dsml_tool_calls(response: LLMResponse) -> LLMResponse:
    """
    若 API 未返回 tool_calls，但 content 中含 DSML invoke，则转为标准 tool_calls 并截断展示用 content。
    """
    if response.has_tool_calls:
        return response
    content = response.content or ""
    parsed = parse_dsml_invocations(content)
    if not parsed:
        return response

    new_content = strip_dsml_blocks_from_content(content)
    logger.info(
        "[DSML] 将正文中的 DSML 解析为 tool_calls（{} 个）: {}",
        len(parsed),
        [p.name for p in parsed],
    )
    return LLMResponse(
        content=new_content if new_content else None,
        tool_calls=parsed,
        finish_reason=response.finish_reason,
        usage=dict(response.usage) if response.usage else {},
        thinking=response.thinking,
    )
