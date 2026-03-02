"""语音转写工具，使用 DashScope Qwen3-ASR-Flash 或 Groq Whisper。"""

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class VoiceTranscribeTool(Tool):
    """
    将音频文件转写为文字。
    使用 DashScope Qwen3-ASR-Flash。
    """

    @property
    def name(self) -> str:
        return "voice_transcribe"

    @property
    def description(self) -> str:
        return (
            "将音频文件转写为文字。传入 file_path（本地文件绝对路径）。"
            "支持格式：mp3, wav, m4a, ogg, opus 等。返回转写文本。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "音频文件的本地绝对路径，如 /path/to/audio.ogg",
                },
            },
            "required": ["file_path"],
        }

    async def execute(self, file_path: str, **kwargs: Any) -> str:
        """执行转写。"""
        path = Path(file_path)
        if not path.is_absolute():
            return f"Error: file_path 需要是绝对路径: {file_path}"
        if not path.exists():
            return f"Error: 文件不存在: {file_path}"

        try:
            import os
            from nanobot.config.loader import load_config
            cfg = load_config()

            # 尝试 DashScope ASR
            model = "dashscope/qwen3-asr-flash"
            dashscope_key = cfg.get_api_key(model) or (cfg.providers.dashscope.api_key or "").strip() or os.environ.get("DASHSCOPE_API_KEY", "")
            dashscope_base = cfg.get_api_base(model)

            if dashscope_key:
                from nanobot.providers.transcription import DashScopeASRTranscriptionProvider
                provider = DashScopeASRTranscriptionProvider(api_key=dashscope_key, api_base=dashscope_base)
                result = await provider.transcribe(path)
                if result:
                    return result
                # DashScope 转写失败（返回空），尝试 Groq 作为备选
                logger.warning("DashScope ASR 转写返回空，尝试 Groq 作为备选...")

            # 尝试 Groq Whisper 作为备选
            groq_key = cfg.get_api_key("groq/whisper-large-v3") or cfg.providers.groq.api_key or os.environ.get("GROQ_API_KEY", "")
            if groq_key:
                from nanobot.providers.transcription import GroqTranscriptionProvider
                provider = GroqTranscriptionProvider(api_key=groq_key)
                result = await provider.transcribe(path)
                if result:
                    return result
                logger.warning("Groq 转写也返回空")

            return "Error: 请配置 DashScope API Key（Qwen 通义）或 Groq API Key 以使用语音转写。"
        except Exception as e:
            from loguru import logger
            logger.exception("voice_transcribe 执行失败")
            return f"Error: {e}"
