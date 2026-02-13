"""Qwen-Image 文生图（阿里云 DashScope）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from loguru import logger

# 北京地域
DASHSCOPE_IMAGE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"


def generate_image(
    prompt: str,
    api_key: str,
    model: str = "qwen-image-plus",
    size: str = "1024*1024",
) -> str | None:
    """
    调用 Qwen-Image 生成单张图，返回图片 URL（有效期 24 小时）。
    api_key 为空时返回 None。
    """
    if not api_key or not prompt.strip():
        return None
    payload = {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt[:800]}],
                }
            ]
        },
        "parameters": {
            "size": size,
            "watermark": False,
            "prompt_extend": False,
            "negative_prompt": "低分辨率，低画质，文字，水印。",
        },
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                DASHSCOPE_IMAGE_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json=payload,
            )
            if resp.status_code != 200:
                logger.warning("Qwen-Image failed: %s", resp.text)
                return None
            data = resp.json()
        choices = (data.get("output") or {}).get("choices") or []
        if not choices:
            return None
        content = (choices[0].get("message") or {}).get("content") or []
        for item in content:
            if isinstance(item, dict) and "image" in item:
                return item["image"]
        return None
    except Exception as e:
        logger.warning("Qwen-Image request failed: %s", e)
        return None


def download_and_save_image(url: str, save_path: str) -> bool:
    """下载图片并保存到本地，返回是否成功。"""
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return False
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            Path(save_path).write_bytes(resp.content)
            return True
    except Exception as e:
        logger.warning("Image download failed: %s", e)
        return False


def get_shang_prompts_for_topic(topic: str) -> tuple[str, str]:
    """根据赏主题返回 A/B 图的中文 prompt。"""
    prompts: dict[str, tuple[str, str]] = {
        "内在力量": (
            "一张温暖的画面：明亮的阳光洒在独自攀爬高峰的人身上，写实风格，暖色调，展现内在坚韧与力量。",
            "一张深沉的画面：黑暗中一盏孤灯，人物 silhouette 静坐，冷色调，深沉内敛，油画质感。",
        ),
        "孤独与连接": (
            "一个人独自仰望星空的画面，极简主义，冷色调，孤独感。",
            "篝火旁一群人围坐谈笑的画面，暖色调，印象派风格，连接与温暖。",
        ),
        "秩序与混沌": (
            "几何图形组成的规整画面，线条清晰，冷色调，秩序感。",
            "抽象泼墨，色彩流动，混沌而富有动感，暖色与冷色交织。",
        ),
        "自由与束缚": (
            "鸟儿挣脱笼子飞向天空，写实风格，明亮的蓝天。",
            "透明的玻璃墙内的人物，试图触碰外界，冷色调，压抑感。",
        ),
        "创造与毁灭": (
            "双手从废墟中捧起新芽，希望与重生，暖色调。",
            "火焰吞噬旧物的抽象画面，深红与黑色，毁灭与转化。",
        ),
        "光明与阴影": (
            "阳光穿过树叶的光斑，温暖明亮，自然摄影风格。",
            "深邃的阴影与一缕光线形成强烈对比，黑白摄影风格。",
        ),
        "旅程与归宿": (
            "远方的道路消失在 horizon，旅人背影，写实风格，暖色调。",
            "温馨的家的窗口透出灯光，归来的意象，印象派。",
        ),
        "真实与面具": (
            "镜子中破碎的反射，真实与虚假的对比，冷色调，超现实主义。",
            "人物摘下面具的瞬间，柔和光线，写实风格，暖色调。",
        ),
    }
    return prompts.get(topic, prompts["内在力量"])
