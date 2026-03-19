"""Root Agent：根任务，spawn Planner。"""

from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult, TaskSpec


class RootAgent(Capability):
    """根 agent，spawn planner 处理用户请求。"""

    name = "root_agent"
    kind = "agent"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        goal = request.get("user_goal", request.get("goal", ""))

        return CapabilityResult(
            status="WAITING_CHILDREN",
            output_artifact=None,
            spawn_specs=[
                TaskSpec(
                    task_kind="AGENT",
                    capability_name="planner_agent",
                    intent="plan_tasks",
                    priority=15,
                    output_schema="plan_v1",
                    request_payload={
                        "user_goal": goal,
                        "goal": goal,
                        "attempted_steps": request.get("attempted_steps", []),
                        "initial_artifacts_keys": request.get("initial_artifacts_keys", []),
                    },
                ),
            ],
        )
