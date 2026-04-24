"""Critic Agent：批评草案。支持 LLM 增强。"""

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


CRITIC_SYSTEM_PROMPT = """你是一个严谨的评审专家，擅长发现方案中的风险、遗漏和不足。

你的任务是审查草案，从以下维度给出评审意见：
1. 【事实准确性】——草案中的事实是否与证据一致？有无夸大或错误？
2. 【完整性】——是否遗漏了重要证据？是否回答了任务目标的全部方面？
3. 【逻辑一致性】——论证过程是否逻辑自洽？有无前后矛盾？
4. 【可行性】——建议是否可执行？有无忽略现实约束？
5. 【改进建议】——具体如何改进？

输出格式：
- 风险列表：用 bullet points 列出
- 评分：0-1 之间，精确到两位小数
- 改进建议：具体、可操作

使用中文输出。"
"""


class CriticAgent(Capability):
    """批评 agent，消费 evidence 和 draft 产出评审。支持 LLM 增强。"""

    name = "critic_agent"
    kind = "agent"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        artifacts = context.get("artifacts", {})
        evidence = artifacts.get("evidence_bundle_v1") or {}
        draft = artifacts.get("draft_v1") or {}

        items = evidence.get("items", [])
        draft_summary = draft.get("summary", "")
        goal = draft.get("plan_goal", "") or request.get("goal", "")

        provider = context.get("provider")
        model = context.get("model")

        if provider and model and draft_summary:
            for attempt in range(3):
                try:
                    evidence_text = "\n".join(
                        f"- {item.get('title', '')}: {item.get('snippet', '') or item.get('content', '')}"
                        for item in items[:6] if isinstance(item, dict)
                    ) or "（无检索结果）"

                    messages = [
                        {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"【任务目标】\n{goal}\n\n"
                                f"【证据】\n{evidence_text}\n\n"
                                f"【草案】\n{draft_summary[:3000]}\n\n"
                                "请给出详细的评审意见。"
                            ),
                        },
                    ]
                    response = await provider.chat(
                        messages=messages,
                        model=model,
                        max_tokens=1024,
                        temperature=0.5,
                    )
                    critique_text = response.content or ""
                    if not critique_text.strip():
                        continue

                    # 尝试从文本中提取评分
                    score = 0.75
                    import re
                    score_match = re.search(r'评分[:：]\s*(\d+(?:\.\d+)?)', critique_text)
                    if score_match:
                        score = min(1.0, max(0.0, float(score_match.group(1))))

                    return CapabilityResult(
                        status="DONE",
                        output_artifact={
                            "artifact_type": "critique_v1",
                            "payload": {
                                "risks": [critique_text],
                                "score": score,
                                "llm_critique": critique_text,
                            },
                        },
                    )
                except Exception:
                    if attempt == 2:
                        break
                    import asyncio
                    await asyncio.sleep(0.5 * (attempt + 1))

        # 回退到 mock

        # 回退到 mock 行为
        risks = []
        if not items:
            risks.append("缺少证据")
        if not draft_summary:
            risks.append("草案尚未生成，无法进行完整评审")

        return CapabilityResult(
            status="DONE",
            output_artifact={
                "artifact_type": "critique_v1",
                "payload": {
                    "risks": risks,
                    "score": 0.76 if risks else 0.92,
                },
            },
        )
