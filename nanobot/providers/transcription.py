"""Voice transcription providers: Groq Whisper and DashScope Qwen3-ASR-Flash."""

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx
from loguru import logger


def _get_audio_mime(path: str | Path) -> str:
    """根据扩展名返回 MIME 类型。"""
    ext = Path(path).suffix.lower()
    mime_map = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
    }
    return mime_map.get(ext) or mimetypes.guess_type(str(path))[0] or "audio/mpeg"


# DashScope 兼容模式 API 默认端点（与主模型 litellm 配置解耦，避免 api_base 全局污染）
DASHSCOPE_DEFAULT_BASE_CN = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_DEFAULT_BASE_INTL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


class DashScopeASRTranscriptionProvider:
    """
    使用 DashScope Qwen3-ASR-Flash 的语音转录。
    通过 httpx 直连 DashScope 兼容模式 API，完全绕过 LiteLLM，避免与主模型（如 vLLM、OpenRouter）
    共用 litellm.api_base 时导致的 endpoint 冲突。
    """

    DEFAULT_MODEL = "qwen3-asr-flash"

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        # 未配置时使用中国区端点；用户可配置 dashscope.api_base 指定国际区等
        self.api_base = (api_base or "").strip().rstrip("/") or DASHSCOPE_DEFAULT_BASE_CN

    async def transcribe(self, file_path: str | Path) -> str:
        """将本地音频转为文字。"""
        if not self.api_key:
            logger.warning("DashScope API key 未配置，无法使用 Qwen3-ASR-Flash 转写")
            return ""

        path = Path(file_path)
        if not path.exists():
            logger.error(f"音频文件不存在: {file_path}")
            return ""

        # 10MB 限制，base64 后略大
        if path.stat().st_size > 9 * 1024 * 1024:
            logger.warning("音频文件超过 9MB，Qwen3-ASR-Flash 可能不支持")
            return ""

        mime = _get_audio_mime(path)
        b64 = base64.b64encode(path.read_bytes()).decode()
        data_uri = f"data:{mime};base64,{b64}"

        url = f"{self.api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.DEFAULT_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "input_audio", "input_audio": {"data": data_uri}}],
                }
            ],
            "stream": False,
            "asr_options": {"enable_itn": False},
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"DashScope ASR HTTP 错误: {e.response.status_code} - {e.response.text[:500]}")
            return ""
        except Exception as e:
            logger.error(f"DashScope ASR 转写失败: {e}")
            return ""

        try:
            choices = data.get("choices", [])
            if not choices:
                logger.warning("DashScope ASR 返回无 choices")
                return ""
            msg = choices[0].get("message", {})
            text = (msg.get("content") or "").strip()
            if text:
                logger.info(f"DashScope ASR 转写完成: {len(text)} 字符")
            return text
        except Exception as e:
            logger.error(f"DashScope ASR 响应解析失败: {e}")
            return ""


class GroqTranscriptionProvider:
    """
    Voice transcription provider using Groq's Whisper API.
    
    Groq offers extremely fast transcription with a generous free tier.
    """
    
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.api_url = "https://api.groq.com/openai/v1/audio/transcriptions"
    
    async def transcribe(self, file_path: str | Path) -> str:
        """
        Transcribe an audio file using Groq.
        
        Args:
            file_path: Path to the audio file.
            
        Returns:
            Transcribed text.
        """
        if not self.api_key:
            logger.warning("Groq API key not configured for transcription")
            return ""
        
        path = Path(file_path)
        if not path.exists():
            logger.error(f"Audio file not found: {file_path}")
            return ""
        
        try:
            async with httpx.AsyncClient() as client:
                with open(path, "rb") as f:
                    files = {
                        "file": (path.name, f),
                        "model": (None, "whisper-large-v3"),
                    }
                    headers = {
                        "Authorization": f"Bearer {self.api_key}",
                    }
                    
                    response = await client.post(
                        self.api_url,
                        headers=headers,
                        files=files,
                        timeout=60.0
                    )
                    
                    response.raise_for_status()
                    data = response.json()
                    return data.get("text", "")
                    
        except Exception as e:
            logger.error(f"Groq transcription error: {e}")
            return ""
