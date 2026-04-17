"""
将配置中的模型 ID 转为各厂商原生 API 所需的格式。

已移除 LiteLLM 后，库中仍可能保留 `provider/model` 形式的 litellm_id；
直连 OpenAI 兼容端（尤其 MiniMax 官方）时必须改为裸模型名。
"""

from __future__ import annotations

import re
from typing import Any

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def normalize_native_model_id(
    raw: str,
    *,
    api_base: str | None,
) -> str:
    """
    根据 provider 的 api_base 将模型 ID 规范为原生 API 字符串。

    - MiniMax 官方 (api.minimax.chat / api.minimax.io): 去掉 ``minimax/`` 前缀，
      并把 ``minimax-m2.7`` 等形式转为 ``MiniMax-M2.7``。
    - 直连 OpenAI (api.openai.com): 去掉遗留的 ``openai/`` 前缀。
    - 聚合商 (OpenRouter 等) 保留带命名空间的 ID，不做剥离。
    """
    if not raw or not str(raw).strip():
        return raw
    s = str(raw).strip()
    base = (api_base or "").lower()

    # —— MiniMax 官方 OpenAI 兼容层 ——
    if "minimax.chat" in base or "minimax.io" in base:
        if s.lower().startswith("minimax/"):
            s = s.split("/", 1)[1]
        # API 要求 MiniMax-M*，常见错误：minimax-m2.7
        m = re.match(r"^minimax-m([\d.]+(?:[-\w]*))$", s, re.IGNORECASE)
        if m:
            s = f"MiniMax-M{m.group(1)}"
        return s

    # —— 直连 OpenAI ——（非 Azure/OpenRouter）
    if "api.openai.com" in base and "openrouter" not in base:
        if s.lower().startswith("openai/"):
            s = s.split("/", 1)[1]
        return s

    # —— 直连 Anthropic ——
    if "anthropic.com" in base or "api.anthropic.com" in base:
        if s.lower().startswith("anthropic/"):
            s = s.split("/", 1)[1]
        return s

    # 聚合商（OpenRouter、PPIO 等）保留 `provider/model` 命名空间，不做剥离
    return s


def resolve_stored_model_id(model: dict[str, Any]) -> str:
    """优先使用与 API 一致的字段；litellm_id 仅作兼容列名。"""
    lit = (model.get("litellm_id") or "").strip()
    mid = (model.get("id") or "").strip()
    if mid and not lit:
        return mid
    if lit and not mid:
        return lit
    if mid and lit:
        # litellm 仍带 provider/model，而 id 已是裸模型名时以 id 为准（id 为 UUID 时仍以 lit 为准）
        if "/" in lit and "/" not in mid and not _UUID_RE.match(mid):
            return mid
        return lit or mid
    return mid or lit
