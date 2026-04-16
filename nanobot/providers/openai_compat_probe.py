"""
OpenAI 兼容 API 的连通性探测与 MiniMax 无 /models 时的模型回退。

部分厂商（尤其 MiniMax 官方）不提供标准 GET /v1/models，需用对话接口验证密钥与 Base URL。
"""

from __future__ import annotations

import json
from typing import Any

# MiniMax 官方常见模型（与 system_providers 中 minimax 条目对齐，用于无列表接口时的发现回退）
MINIMAX_FALLBACK_MODEL_ROWS: list[dict[str, Any]] = [
    {"id": "MiniMax-M2.7", "name": "MiniMax-M2.7", "context_window": 32768},
    {"id": "MiniMax-M2.5", "name": "MiniMax-M2.5", "context_window": 32768},
    {"id": "MiniMax-M2.5-highspeed", "name": "MiniMax-M2.5-highspeed", "context_window": 32768},
    {"id": "MiniMax-M2.5-lightning", "name": "MiniMax-M2.5-lightning", "context_window": 32768},
    {"id": "MiniMax-M2.1", "name": "MiniMax-M2.1", "context_window": 32768},
    {"id": "MiniMax-M2.1-lightning", "name": "MiniMax-M2.1-lightning", "context_window": 32768},
    {"id": "MiniMax-M2", "name": "MiniMax-M2", "context_window": 32768},
]

# 用于 POST 探测的模型顺序（优先与 default_model 一致）
MINIMAX_PROBE_MODELS: list[str] = [
    "MiniMax-M2.7",
    "MiniMax-M2.5",
    "MiniMax-M2.1",
    "MiniMax-M2",
]


def is_minimax_openai_base(api_base: str | None) -> bool:
    if not api_base:
        return False
    b = api_base.lower()
    return "minimax.chat" in b or "minimax.io" in b


def is_kimi_code_base(api_base: str | None) -> bool:
    if not api_base:
        return False
    b = api_base.lower()
    return "api.kimi.com" in b or "kimi.com/coding" in b


def probe_openai_compatible_connection(
    api_base: str,
    api_key: str,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """
    先 GET {base}/models；若为 MiniMax 且返回 404，则依次 POST /chat/completions 做轻量探测。

    返回: {"ok": bool, "status": int, "detail": str}
    """
    import httpx

    base = str(api_base).strip().rstrip("/")
    if not base:
        return {"ok": False, "status": 0, "detail": "请先填写 API Base URL"}

    models_url = base + "/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if is_kimi_code_base(base):
        headers["User-Agent"] = "RooCode/3.0.0"

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(models_url, headers=headers)
            if r.status_code == 200:
                return {"ok": True, "status": 200, "detail": "连接成功"}

            if r.status_code != 404 or not (is_minimax_openai_base(base) or is_kimi_code_base(base)):
                return {
                    "ok": False,
                    "status": r.status_code,
                    "detail": (r.text or "")[:500] or f"HTTP {r.status_code}",
                }

            # MiniMax / Kimi Code：部分环境无模型列表接口，用对话接口验证
            chat_url = base + "/chat/completions"
            last_err = ""
            probe_models = MINIMAX_PROBE_MODELS if is_minimax_openai_base(base) else ["kimi-k2.5", "kimi-for-coding", "moonshot-v1-auto"]
            for model in probe_models:
                body = {
                    "model": model,
                    "messages": [{"role": "user", "content": "."}],
                    "max_tokens": 1,
                }
                pr = client.post(chat_url, headers=headers, json=body)
                if pr.status_code == 200:
                    return {
                        "ok": True,
                        "status": 200,
                        "detail": "连接成功（模型列表接口不可用，已通过对话接口验证）",
                    }
                if pr.status_code == 401:
                    return {
                        "ok": False,
                        "status": 401,
                        "detail": (pr.text or "Unauthorized")[:500],
                    }
                try:
                    err_obj = pr.json()
                    last_err = json.dumps(err_obj, ensure_ascii=False)[:500]
                except Exception:
                    last_err = (pr.text or "")[:500]
                # 换下一个模型名重试（账号可见模型集合可能不同）
                continue

            return {
                "ok": False,
                "status": 404,
                "detail": last_err or "模型列表不可用且对话探测失败，请检查 Base URL 与 API Key",
            }
    except Exception as e:
        return {"ok": False, "status": 0, "detail": str(e)}
