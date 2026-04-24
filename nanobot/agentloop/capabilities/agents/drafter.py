"""Drafter Agent：根据 plan + evidence 起草。支持 LLM 增强。"""

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


DRAFTER_SYSTEM_PROMPT = """你是一个专业的研究助手和方案起草专家。

你的任务是根据【任务目标】、【规划分析】和【检索到的证据】生成一份高质量的草案。

输出要求：
1. 先给出【关键发现】——用 3-5 个 bullet points 列出最重要的发现
2. 再给出【详细分析】——对证据进行深入分析，解释它们如何回答任务目标
3. 最后给出【结论和建议】——给出明确的结论和可执行的建议

注意事项：
- 直接引用证据中的具体内容来支撑你的观点
- 如果证据之间存在矛盾，指出来并分析原因
- 不要编造证据中没有的信息
- 使用中文输出
"""


class DrafterAgent(Capability):
    """起草 agent，消费 plan 和 evidence 产出草案。支持 LLM 增强。"""

    name = "drafter_agent"
    kind = "agent"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        artifacts = context.get("artifacts", {})
        plan = artifacts.get("plan_v1") or {}
        evidence = artifacts.get("evidence_bundle_v1") or {}
        doc_content = artifacts.get("doc_content_v1") or {}
        exec_output = artifacts.get("exec_output_v1") or {}

        goal = plan.get("goal", request.get("goal", ""))
        llm_analysis = plan.get("llm_analysis", "")
        items = evidence.get("items", [])
        file_content = (doc_content.get("content") or "")[:3000]
        cmd_output = (exec_output.get("output") or "")[:2000]

        provider = context.get("provider")
        model = context.get("model")

        # 构建证据文本
        evidence_lines = []
        for idx, item in enumerate(items[:8], 1):
            if isinstance(item, dict):
                title = item.get("title", "")
                snippet = item.get("snippet", "") or item.get("content", "") or item.get("summary", "")
                url = item.get("url", "")
                if title or snippet:
                    evidence_lines.append(f"[{idx}] {title}\n{snippet[:400]}")
        evidence_text = "\n\n".join(evidence_lines) if evidence_lines else "（无检索结果）"

        # 优先使用 LLM 生成草案（带重试）
        summary = ""
        if provider and model:
            for attempt in range(3):
                try:
                    user_content_parts = [f"【任务目标】\n{goal}\n"]
                    if llm_analysis:
                        user_content_parts.append(f"\n【规划分析】\n{llm_analysis[:1000]}\n")
                    user_content_parts.append(f"\n【检索到的证据】\n{evidence_text}\n")
                    if file_content:
                        user_content_parts.append(f"\n【文件内容】\n{file_content[:2000]}\n")
                    if cmd_output:
                        user_content_parts.append(f"\n【命令输出】\n{cmd_output[:1000]}\n")

                    messages = [
                        {"role": "system", "content": DRAFTER_SYSTEM_PROMPT},
                        {"role": "user", "content": "\n".join(user_content_parts)},
                    ]
                    response = await provider.chat(
                        messages=messages,
                        model=model,
                        max_tokens=2048,
                        temperature=0.7,
                    )
                    summary = response.content or ""
                    if summary.strip():
                        break
                except Exception as exc:
                    if attempt == 2:
                        summary = self._fallback_summary(goal, llm_analysis, evidence_text, file_content, cmd_output, exc)
                    else:
                        import asyncio
                        await asyncio.sleep(0.5 * (attempt + 1))

        if not summary:
            summary = self._fallback_summary(goal, llm_analysis, evidence_text, file_content, cmd_output)

        return CapabilityResult(
            status="DONE",
            output_artifact={
                "artifact_type": "draft_v1",
                "payload": {
                    "summary": summary,
                    "plan_goal": goal,
                    "evidence_count": len(items),
                },
            },
        )

    def _fallback_summary(
        self, goal: str, llm_analysis: str, evidence_text: str, file_content: str, cmd_output: str, exc=None
    ) -> str:
        """LLM 不可用或失败时的回退拼接。"""
        parts = [f"任务目标: {goal}"]
        if llm_analysis:
            parts.append(f"\n规划分析:\n{llm_analysis[:1000]}")
        if evidence_text and evidence_text != "（无检索结果）":
            parts.append(f"\n检索到的证据:\n{evidence_text}")
        if file_content:
            parts.append(f"\n文件内容摘要:\n{file_content[:1500]}{'...' if len(file_content) > 1500 else ''}")
        if cmd_output:
            parts.append(f"\n命令输出:\n{cmd_output[:1000]}")
        if exc:
            parts.append(f"\n【注】LLM 调用失败，回退到原始拼接。错误: {exc}")
        if len(parts) == 1:
            parts.append("\n暂无可用证据或文件内容。")
        return "\n\n".join(parts)
