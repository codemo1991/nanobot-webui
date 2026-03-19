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
    """终局聚合：plan + evidence + draft + critique -> final_result。"""

    name = "final_reducer"
    kind = "reducer"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        artifacts = context.get("artifacts") or {}
        plan = artifacts.get("plan_v1") or {}
        evidence = artifacts.get("evidence_bundle_v1") or {}
        draft = artifacts.get("draft_v1") or {}
        critique = artifacts.get("critique_v1") or {}

        final_text = draft.get("summary", "这是最终聚合结果。")
        if isinstance(final_text, str) and len(final_text) > 500:
            final_text = final_text[:500] + "..."

        final_payload = {
            "goal": plan.get("goal", ""),
            "draft": draft,
            "critique": critique,
            "evidence_count": len(evidence.get("items", [])),
            "final_text": final_text,
        }

        return CapabilityResult(
            status="DONE",
            output_artifact={
                "artifact_type": "final_result_v1",
                "payload": final_payload,
            },
        )
