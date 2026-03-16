"""Critic Agent：批评草案。"""

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class CriticAgent(Capability):
    """批评 agent，消费 evidence 和 draft 产出评审。"""

    name = "critic_agent"
    kind = "agent"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        artifacts = context.get("artifacts", {})
        evidence = artifacts.get("evidence_bundle_v1") or {}
        draft = artifacts.get("draft_v1")

        risks = []
        if not evidence.get("items"):
            risks.append("缺少证据")

        if draft is None:
            risks.append("草案尚未生成，无法进行完整评审")

        return CapabilityResult(
            status="DONE",
            output_artifact={
                "artifact_type": "critique_v1",
                "payload": {
                    "risks": risks,
                    "score": 0.76,
                },
            },
        )
