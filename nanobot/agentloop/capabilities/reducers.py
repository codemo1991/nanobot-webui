"""AgentLoop Reducer Capabilities。"""

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class RetrieverGroupReducer(Capability):
    """将多个 search_result 合并为 evidence_bundle。"""

    name = "retriever_group_reducer"
    kind = "reducer"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        artifact_list = context.get("artifact_list") or {}
        search_results = artifact_list.get("search_result_v1") or []

        merged = []
        seen = set()
        for result in search_results:
            if result is None:
                continue
            for item in result.get("items", []):
                if not isinstance(item, dict):
                    continue
                key = item.get("title", "")
                if key and key not in seen:
                    seen.add(key)
                    merged.append(item)

        merged.sort(key=lambda x: x.get("score", 0), reverse=True)

        return CapabilityResult(
            status="DONE",
            output_artifact={
                "artifact_type": "evidence_bundle_v1",
                "payload": {"items": merged[:10]},
            },
        )


class FinalReducer(Capability):
    """终局聚合：plan + evidence + draft + critique -> final_result。
    支持 LLM 增强聚合，无 LLM 时回退到模板拼接。
    """

    name = "final_reducer"
    kind = "reducer"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        artifacts = context.get("artifacts") or {}
        plan = artifacts.get("plan_v1") or {}
        evidence = artifacts.get("evidence_bundle_v1") or {}
        draft = artifacts.get("draft_v1") or {}
        critique = artifacts.get("critique_v1") or {}

        goal = plan.get("goal", request.get("goal", ""))
        llm_analysis = plan.get("llm_analysis", "")
        items = evidence.get("items", [])
        draft_summary = draft.get("summary", "")
        critique_text = ""
        if critique:
            critique_text = critique.get("llm_critique", "") or "\n".join(critique.get("risks", []))

        provider = context.get("provider")
        model = context.get("model")

        # 优先使用 LLM 做最终聚合（带重试）
        if provider and model:
            for attempt in range(3):
                try:
                    evidence_text = "\n".join(
                        f"- {item.get('title', '')}: {item.get('snippet', '') or item.get('content', '')}"
                        for item in items[:6] if isinstance(item, dict)
                    ) or "（无检索结果）"

                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "你是一个终局聚合专家。你的任务是把规划分析、草案、评审意见和证据"
                                "整合成一份简洁、完整、可直接呈现给用户的最终结果。"
                                "输出要求：\n"
                                "1. 先给出直接回答\n"
                                "2. 再给出关键依据\n"
                                "3. 最后给出建议或结论\n"
                                "不要提及'草案'、'评审'等内部术语。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"【任务目标】\n{goal}\n\n"
                                f"{'【规划分析】\n' + llm_analysis[:1000] + '\n\n' if llm_analysis else ''}"
                                f"{'【草案】\n' + draft_summary[:3000] + '\n\n' if draft_summary else ''}"
                                f"{'【评审意见】\n' + critique_text[:1500] + '\n\n' if critique_text else ''}"
                                f"【证据摘要】\n{evidence_text}\n\n"
                                "请整合以上内容，生成一份完整的最终结果。"
                            ),
                        },
                    ]
                    response = await provider.chat(
                        messages=messages,
                        model=model,
                        max_tokens=2048,
                        temperature=0.5,
                    )
                    final_text = response.content or ""
                    if final_text.strip():
                        break
                except Exception as exc:
                    if attempt == 2:
                        final_text = f"【LLM 聚合失败，回退到模板拼接】\n\n{draft_summary or '无可用草案。'}\n\n错误: {exc}"
                    else:
                        import asyncio
                        await asyncio.sleep(0.5 * (attempt + 1))
            if not final_text:
                final_text = draft_summary or ""
        else:
            # 无 LLM 时回退
            final_text = draft_summary or ""
            if not final_text or final_text.strip() in ("", "这是最终聚合结果。", "这是根据 plan + evidence 生成的草案。"):
                if items:
                    lines = []
                    for idx, item in enumerate(items[:8], 1):
                        if isinstance(item, dict):
                            title = item.get("title", "")
                            snippet = item.get("snippet", "") or item.get("content", "") or item.get("summary", "")
                            if title or snippet:
                                lines.append(f"[{idx}] {title}\n{snippet[:400]}")
                    final_text = "\n\n".join(lines) if lines else "已检索到相关证据，但未生成详细摘要。"
                else:
                    final_text = "微内核执行完成，但未获取到有效结果。"

        if isinstance(final_text, str) and len(final_text) > 2000:
            final_text = final_text[:2000] + "..."

        final_payload = {
            "goal": goal,
            "draft": draft,
            "critique": critique,
            "evidence_count": len(items),
            "final_text": final_text,
            "llm_aggregated": bool(provider and model),
        }

        return CapabilityResult(
            status="DONE",
            output_artifact={
                "artifact_type": "final_result_v1",
                "payload": final_payload,
            },
        )
