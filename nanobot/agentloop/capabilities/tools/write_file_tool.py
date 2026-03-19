"""WriteFile Tool：包装主 Agent 的 WriteFileTool，能力对等。"""

from nanobot.agent.tools.filesystem import WriteFileTool
from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class WriteFileCapability(Capability):
    """写入文件，产出 write_result_v1 artifact。"""

    name = "write_file_tool"
    kind = "tool"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        path = (request.get("path") or "").strip()
        if not path:
            return CapabilityResult(
                status="FAILED",
                error_code="WRITE_FILE_ERROR",
                error_message="path is required",
            )
        content = request.get("content", "")
        workspace = context.get("workspace")
        tool = WriteFileTool(workspace=workspace, restrict_to_workspace=True)
        try:
            result = await tool.execute(path=path, content=content)
            if result.startswith("Error:"):
                return CapabilityResult(
                    status="FAILED",
                    error_code="WRITE_FILE_ERROR",
                    error_message=result,
                )
            return CapabilityResult(
                status="DONE",
                output_artifact={
                    "artifact_type": "write_result_v1",
                    "payload": {"path": path, "message": result},
                },
            )
        except Exception as e:
            return CapabilityResult(
                status="FAILED",
                error_code="WRITE_FILE_ERROR",
                error_message=str(e),
            )
