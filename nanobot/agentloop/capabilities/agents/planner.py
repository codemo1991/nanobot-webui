"""Planner Agent：规划任务并 spawn 子任务。"""

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult, TaskSpec


class PlannerAgent(Capability):
    """规划 agent，产出计划并 spawn 检索、起草、批评等子任务。"""

    name = "planner_agent"
    kind = "agent"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        goal = request.get("user_goal", request.get("goal", ""))

        spawn_specs = [
            TaskSpec(
                task_kind="TOOL",
                capability_name="search_tool",
                intent="retrieve_evidence",
                priority=20,
                output_schema="search_result_v1",
                request_payload={"query": goal, "slot": 1},
            ),
            TaskSpec(
                task_kind="TOOL",
                capability_name="search_tool",
                intent="retrieve_evidence",
                priority=20,
                output_schema="search_result_v1",
                request_payload={"query": goal, "slot": 2},
            ),
            TaskSpec(
                task_kind="REDUCER",
                capability_name="retriever_group_reducer",
                intent="merge_evidence",
                priority=22,
                input_schema="search_result_v1",
                output_schema="evidence_bundle_v1",
                request_payload={"goal": goal},
            ),
            TaskSpec(
                task_kind="AGENT",
                capability_name="drafter_agent",
                intent="draft_solution",
                priority=30,
                input_schema="plan_and_evidence_v1",
                output_schema="draft_v1",
                request_payload={"goal": goal},
            ),
            TaskSpec(
                task_kind="AGENT",
                capability_name="critic_agent",
                intent="critic_solution",
                priority=35,
                input_schema="plan_and_evidence_v1",
                output_schema="critique_v1",
                request_payload={"goal": goal},
            ),
            TaskSpec(
                task_kind="REDUCER",
                capability_name="final_reducer",
                intent="aggregate_final",
                priority=40,
                input_schema="final_input_v1",
                output_schema="final_result_v1",
                request_payload={"goal": goal},
            ),
        ]

        return CapabilityResult(
            status="WAITING_CHILDREN",
            output_artifact={
                "artifact_type": "plan_v1",
                "payload": {
                    "goal": goal,
                    "subtasks": [
                        "retrieve_evidence",
                        "merge_evidence",
                        "draft_solution",
                        "critic_solution",
                        "aggregate_final",
                    ],
                },
            },
            spawn_specs=spawn_specs,
        )
