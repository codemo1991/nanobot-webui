"""Voice transcription providers: Groq Whisper and DashScope Qwen3-ASR-Flash."""

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx
from litellm import acompletion
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


class DashScopeASRTranscriptionProvider:
    """
    使用 DashScope Qwen3-ASR-Flash 的语音转录。
    通过 LiteLLM 调用 dashscope/qwen3-asr-flash，复用主配置中的 qwen/dashscope Provider。
    """

    DEFAULT_MODEL = "dashscope/qwen3-asr-flash"

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        self.api_base = api_base

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

        messages = [
            {
                "role": "user",
                "content": [{"type": "input_audio", "input_audio": {"data": data_uri}}],
            }
        ]

        kwargs: dict[str, Any] = {
            "model": self.DEFAULT_MODEL,
            "messages": messages,
            "api_key": self.api_key,
            "timeout": 60.0,
            "extra_body": {"asr_options": {"enable_itn": False}},
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base.rstrip("/")

        try:
            response = await acompletion(**kwargs)
            # 处理 content 为 None 的情况（DashScope ASR 可能返回 annotations 但 content 为 None）
            message_content = response.choices[0].message.content
            text = ""
            if message_content:
                text = message_content.strip()
            elif hasattr(response.choices[0].message, 'annotations') and response.choices[0].message.annotations:
                # 如果 content 为空但有 annotations，尝试从 annotations 中提取信息
                logger.warning(f"DashScope ASR 返回 content 为空，但有 annotations: {response.choices[0].message.annotations}")
                # annotations 包含音频元信息，实际转写内容可能在别处
                # 这种情况下返回空字符串
                text = ""
            if text:
                logger.info(f"DashScope ASR 转写完成: {len(text)} 字符")
            return text
        except Exception as e:
            logger.error(f"DashScope ASR 转写失败: {e}")
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
