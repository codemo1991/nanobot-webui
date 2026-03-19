"""Exec Tool：包装主 Agent 的 ExecTool，能力对等。"""

from nanobot.agent.tools.shell import ExecTool
from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class ExecCapability(Capability):
    """执行 shell 命令，产出 exec_output_v1 artifact。"""

    name = "exec_tool"
    kind = "tool"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        command = (request.get("command") or "").strip()
        if not command:
            return CapabilityResult(
                status="FAILED",
                error_code="EXEC_ERROR",
                error_message="command is required",
            )
        working_dir = request.get("working_dir")
        workspace = context.get("workspace")
        if not workspace:
            return CapabilityResult(
                status="FAILED",
                error_code="EXEC_ERROR",
                error_message="workspace is required for exec_tool",
            )
        cwd = str(workspace.resolve())
        tool = ExecTool(
            timeout=60,
            working_dir=working_dir or cwd,
            restrict_to_workspace=True,
        )
        try:
            result = await tool.execute(command=command, working_dir=working_dir or cwd)
            if result.startswith("Error:"):
                return CapabilityResult(
                    status="FAILED",
                    error_code="EXEC_ERROR",
                    error_message=result,
                )
            return CapabilityResult(
                status="DONE",
                output_artifact={
                    "artifact_type": "exec_output_v1",
                    "payload": {"command": command, "output": result},
                },
            )
        except Exception as e:
            return CapabilityResult(
                status="FAILED",
                error_code="EXEC_ERROR",
                error_message=str(e),
            )
