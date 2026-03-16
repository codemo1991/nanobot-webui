"""Drafter Agent：根据 plan + evidence 起草。"""

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class DrafterAgent(Capability):
    """起草 agent，消费 plan 和 evidence 产出草案。"""

    name = "drafter_agent"
    kind = "agent"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        artifacts = context.get("artifacts", {})
        plan = artifacts.get("plan_v1") or {}
        evidence = artifacts.get("evidence_bundle_v1") or {}

        goal = plan.get("goal", request.get("goal", ""))
        items = evidence.get("items", [])

        return CapabilityResult(
            status="DONE",
            output_artifact={
                "artifact_type": "draft_v1",
                "payload": {
                    "summary": "这是根据 plan + evidence 生成的草案。",
                    "plan_goal": goal,
                    "evidence_count": len(items),
                },
            },
        )
