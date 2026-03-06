"""Subagent backend modules.

This package contains backend implementations for subagent execution.
Each backend module should register itself with BackendRegistry on import.

Available backends:
- native: Built-in LLM-based subagent (default)
- claude_code: Claude Code CLI backend (requires explicit register with ClaudeCodeManager)
- voice: Voice transcription (template=voice + audio)
- dashscope_vision: DashScope image recognition (images + DashScope model)
"""

# 自注册 backends（无需外部依赖，import 本包时自动注册）
from nanobot.agent.backends import voice
from nanobot.agent.backends import dashscope_vision

voice.register()
dashscope_vision.register()
