"""模型 API Key 加载与注入工具。

将 DashScope/Qwen 等模型的 API Key 多级回退逻辑集中在此，
供 subagent、backends 等复用，避免在业务代码中散落注入逻辑。
"""

import os
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider


def _is_dashscope_model(model: str) -> bool:
    """判断是否为 DashScope/Qwen 模型。"""
    return any(k in (model or "").lower() for k in ("dashscope", "qwen"))


def get_model_api_credentials(model: str) -> tuple[str | None, str | None]:
    """
    获取模型对应的 API key 和 base URL（多级回退）。

    回退顺序：
    1. load_config().get_api_key(model)
    2. cfg.providers.dashscope.api_key
    3. DASHSCOPE_API_KEY 环境变量
    4. config_providers 表

    Returns:
        (api_key, api_base)，未找到时返回 (None, None)
    """
    try:
        from nanobot.config.loader import load_config, get_config_repository

        cfg = load_config()
        api_key = cfg.get_api_key(model)
        api_base = cfg.get_api_base(model)

        if _is_dashscope_model(model):
            if not api_key or not str(api_key).strip():
                api_key = (
                    (cfg.providers.dashscope.api_key or "").strip()
                    or os.environ.get("DASHSCOPE_API_KEY", "")
                ) or None
            if not api_key:
                try:
                    prov = get_config_repository().get_provider("dashscope")
                    if prov and (prov.get("api_key") or "").strip():
                        api_key = prov["api_key"].strip()
                        if not api_base and prov.get("api_base"):
                            api_base = prov.get("api_base")
                except Exception:
                    pass
            if api_key and (not api_base or not str(api_base).strip()):
                api_base = os.environ.get("DASHSCOPE_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1"

        return (
            (api_key.strip() or None) if api_key else None,
            (api_base.strip() or None) if api_base else None,
        )
    except Exception as e:
        logger.debug(f"get_model_api_credentials failed: {e}")
        return (None, None)


def ensure_model_api_key(
    model: str,
    provider: Optional["LLMProvider"] = None,
) -> tuple[str | None, str | None]:
    """
    确保模型 API Key 已加载并注入到 provider / 环境变量。

    供 subagent native 路径、dashscope_vision 等调用。
    仅对 DashScope/Qwen 模型执行 env 注入。

    Args:
        model: 模型名称
        provider: 可选，若有 ensure_api_key_for_model 则调用

    Returns:
        (api_key, api_base)，供 chat 调用时传入
    """
    api_key, api_base = get_model_api_credentials(model)

    if _is_dashscope_model(model):
        if api_key and provider and hasattr(provider, "ensure_api_key_for_model"):
            provider.ensure_api_key_for_model(model, api_key, api_base)
        if api_key:
            os.environ["DASHSCOPE_API_KEY"] = api_key
            if api_base:
                os.environ["DASHSCOPE_API_BASE"] = api_base
        if not api_key:
            logger.warning(
                f"模型 {model} 需要 DashScope API Key，"
                "请在配置页 Provider 中填写 Qwen（通义）的 apiKey，或设置环境变量 DASHSCOPE_API_KEY"
            )

    return (api_key, api_base)
