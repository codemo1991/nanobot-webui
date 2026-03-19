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
        doc_content = artifacts.get("doc_content_v1") or {}
        exec_output = artifacts.get("exec_output_v1") or {}

        goal = plan.get("goal", request.get("goal", ""))
        items = evidence.get("items", [])
        file_content = (doc_content.get("content") or "")[:3000]
        cmd_output = (exec_output.get("output") or "")[:2000]

        extra = []
        if file_content:
            excerpt = file_content[:1500]
            extra.append(f"文件内容摘要:\n{excerpt}{'...' if len(file_content) > 1500 else ''}")
        if cmd_output:
            excerpt = cmd_output[:1000]
            extra.append(f"命令输出:\n{excerpt}{'...' if len(cmd_output) > 1000 else ''}")
        summary = "这是根据 plan + evidence 生成的草案。"
        if extra:
            summary += "\n\n" + "\n\n".join(extra)

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
