"""智能并行度判断服务 - 使用 LLM 决定工具是否适合并行执行."""

import json
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMProvider


# 轻量级提示词，用于快速判断
PARALLEL_DECISION_PROMPT = """你是一个工具并行执行顾问。请判断以下工具调用是否可以并行执行。

要求：
1. 如果工具之间没有依赖关系（不共享参数、不需要彼此的输出），可以并行
2. 如果工具会修改相同文件或资源，必须串行
3. 如果工具需要用户交互（如 message 工具），应该串行
4. 如果工具是 CPU 密集型（如 exec 执行复杂计算），可以并行

返回 JSON 格式：
{{
  "parallel": true/false,
  "groups": [["tool1", "tool2"], ["tool3"]],  // 分组串行执行的工具
  "reason": "简短原因"
}}

工具调用列表：
{tool_calls}

只返回 JSON，不要其他内容。"""

# 更简单的判断提示词（更少 token）
SIMPLE_PARALLEL_PROMPT = """判断这些工具能否并行执行。

工具列表：{tool_names}

规则：
- 读文件(read_file)之间可以并行
- 写文件(write_file)与读文件可并行
- 相同文件的写操作必须串行
- exec/spawn/claude_code 建议并行
- message 必须串行

JSON: {{"parallel": bool, "reason": "..."}}"""


class SmartParallelDecider:
    """
    智能并行度判断器。

    使用 LLM 轻量级判断工具调用是否适合并行执行。
    """

    def __init__(
        self,
        provider: LLMProvider,
        model: str | None = None,
        use_simple_prompt: bool = True,
    ):
        """
        初始化智能并行度判断器。

        Args:
            provider: LLM Provider
            model: 使用的模型（默认使用轻量模型）
            use_simple_prompt: 是否使用简单提示词（更少 token）
        """
        self.provider = provider
        self.model = model or "anthropic/claude-haiku-4-20250307"  # 使用轻量快速模型
        self.use_simple_prompt = use_simple_prompt

    def _build_tool_description(self, tool_calls: list) -> str:
        """
        构建工具描述。

        Args:
            tool_calls: 工具调用列表

        Returns:
            工具描述字符串
        """
        descriptions = []
        for i, tc in enumerate(tool_calls):
            name = tc.name if hasattr(tc, 'name') else tc.get('name', 'unknown')
            args = tc.arguments if hasattr(tc, 'arguments') else tc.get('arguments', {})
            descriptions.append(f"{i+1}. {name}({json.dumps(args)[:100]})")
        return "\n".join(descriptions)

    async def should_parallel(
        self,
        tool_calls: list,
    ) -> dict[str, Any]:
        """
        判断工具调用是否应该并行执行。

        Args:
            tool_calls: 工具调用列表

        Returns:
            判断结果字典，包含：
            - parallel: 是否并行
            - groups: 分组（用于串行执行）
            - reason: 原因
        """
        # 单个工具直接返回串行（不需要判断）
        if len(tool_calls) <= 1:
            return {
                "parallel": False,
                "groups": [tool_calls] if tool_calls else [],
                "reason": "单个工具无需并行",
            }

        # 快速检查：是否有必须串行的工具
        tool_names = [tc.name if hasattr(tc, 'name') else tc.get('name', '') for tc in tool_calls]

        # message 工具必须串行
        if 'message' in tool_names:
            return {
                "parallel": False,
                "groups": [tool_calls],
                "reason": "message 工具需要用户交互",
            }

        # 快速启发式判断（不调用 LLM）
        if self._quick_check_parallel(tool_calls):
            return {
                "parallel": True,
                "groups": [tool_calls],
                "reason": "快速判断：工具之间无依赖",
            }

        # 调用 LLM 进行智能判断
        try:
            return await self._llm_decide(tool_calls)
        except Exception as e:
            logger.warning(f"LLM parallel decision failed, using default: {e}")
            # 默认返回并行（激进策略）
            return {
                "parallel": True,
                "groups": [tool_calls],
                "reason": f"LLM 调用失败，默认并行: {str(e)[:50]}",
            }

    def _quick_check_parallel(self, tool_calls: list) -> bool:
        """
        快速启发式判断（无需 LLM）。

        Args:
            tool_calls: 工具调用列表

        Returns:
            是否可以并行
        """
        # 提取工具名和参数
        tools_info = []
        for tc in tool_calls:
            name = tc.name if hasattr(tc, 'name') else tc.get('name', '')
            args = tc.arguments if hasattr(tc, 'arguments') else tc.get('arguments', {})
            tools_info.append((name, args))

        # 检查是否有写操作
        write_tools = {'write_file', 'edit_file', 'exec', 'spawn', 'claude_code', 'cron'}
        read_tools = {'read_file', 'list_dir', 'web_search', 'web_fetch'}

        has_write = any(name in write_tools for name, _ in tools_info)
        has_read = any(name in read_tools for name, _ in tools_info)

        # 如果只有读操作，可以并行
        if has_read and not has_write:
            return True

        # 如果都是 exec/spawn/claude_code，可以并行
        all_background = all(
            name in {'exec', 'spawn', 'claude_code', 'web_search', 'web_fetch', 'read_file', 'list_dir'}
            for name, _ in tools_info
        )
        if all_background:
            return True

        return False

    async def _llm_decide(self, tool_calls: list) -> dict[str, Any]:
        """
        使用 LLM 判断并行度。

        Args:
            tool_calls: 工具调用列表

        Returns:
            判断结果
        """
        # 构建工具描述
        tool_names = [tc.name if hasattr(tc, 'name') else tc.get('name', '') for tc in tool_calls]
        tool_desc = self._build_tool_description(tool_calls)

        # 选择提示词
        if self.use_simple_prompt:
            prompt = SIMPLE_PARALLEL_PROMPT.format(tool_names=", ".join(tool_names))
        else:
            prompt = PARALLEL_DECISION_PROMPT.format(tool_calls=tool_desc)

        # 调用 LLM
        response = await self.provider.chat(
            messages=[
                {"role": "user", "content": prompt}
            ],
            model=self.model,
            max_tokens=300,
            temperature=0.3,
        )

        # 解析响应
        content = response.content.strip()

        # 尝试提取 JSON
        try:
            # 尝试直接解析
            result = json.loads(content)
            return result
        except json.JSONDecodeError:
            # 尝试从文本中提取 JSON
            import re
            json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    return result
                except json.JSONDecodeError:
                    pass

        # 解析失败，默认并行
        logger.warning(f"Failed to parse LLM response: {content[:200]}")
        return {
            "parallel": True,
            "groups": [tool_calls],
            "reason": "LLM 响应解析失败，默认并行",
        }
