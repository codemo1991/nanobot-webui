"""并行度判断服务（纯规则，零延迟）。

历史背景：
  原版使用二级 LLM（claude-haiku）做并行判断，已废弃。
  废弃原因：
    1. 主 LLM 在单次响应中批量返回多个 tool_calls，已经过完整上下文推理，隐含「可并行」判断；
    2. 二级 LLM 只拿到工具名（use_simple_prompt=True 时甚至不含参数），信息量远少于主 LLM；
    3. LLM roundtrip（300–800ms）通常超过轻量工具并行化带来的收益，造成净负优化；
    4. LLM 调用失败时原来的 fallback 方向错误（默认 parallel=True），可能引发数据竞争。

现在仅做静态规则分析（parallel_dependency_analyzer），零延迟且可预测。
保留 async 接口以维持 loop.py 中 `await decider.should_parallel(...)` 的调用兼容性。
"""

from typing import Any

from loguru import logger


class SmartParallelDecider:
    """
    并行度判断器（纯规则，零延迟）。

    构造参数 provider / model / use_simple_prompt 已废弃，
    保留仅为兼容旧调用方，传入后会打印警告并忽略。
    """

    def __init__(
        self,
        provider=None,
        model: str | None = None,
        use_simple_prompt: bool = True,
    ):
        if provider is not None or model is not None:
            logger.debug(
                "[SmartParallelDecider] provider/model 参数已废弃，"
                "不再使用 LLM 判断并行度，参数将被忽略"
            )

    async def should_parallel(self, tool_calls: list) -> dict[str, Any]:
        """
        判断工具调用是否应该并行执行（纯规则，零延迟）。

        Returns:
            {"parallel": bool, "groups": list, "reason": str}
        """
        if len(tool_calls) <= 1:
            return {
                "parallel": False,
                "groups": [tool_calls] if tool_calls else [],
                "reason": "单个工具无需并行",
            }

        from nanobot.services.parallel_dependency_analyzer import analyze
        result = analyze(tool_calls)
        return {
            "parallel": result["can_parallel"],
            "groups": result["groups"],
            "reason": result["reason"],
        }
