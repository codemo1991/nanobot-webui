"""EditFile Tool：包装主 Agent 的 EditFileTool，能力对等。"""

from nanobot.agent.tools.filesystem import EditFileTool
from nanobot.agentloop.capabilities.base import Capability
from nanobot.agentloop.kernel.models import CapabilityResult


class EditFileCapability(Capability):
    """编辑文件（替换文本），产出 edit_result_v1 artifact。"""

    name = "edit_file_tool"
    kind = "tool"

    async def invoke(self, request: dict, context: dict) -> CapabilityResult:
        path = (request.get("path") or "").strip()
        if not path:
            return CapabilityResult(
                status="FAILED",
                error_code="EDIT_FILE_ERROR",
                error_message="path is required",
            )
        old_text = request.get("old_text", "")
        new_text = request.get("new_text", "")
        workspace = context.get("workspace")
        tool = EditFileTool(workspace=workspace, restrict_to_workspace=True)
        try:
            result = await tool.execute(path=path, old_text=old_text, new_text=new_text)
            if result.startswith("Error:") or result.startswith("Warning:"):
                return CapabilityResult(
                    status="FAILED",
                    error_code="EDIT_FILE_ERROR",
                    error_message=result,
                )
            return CapabilityResult(
                status="DONE",
                output_artifact={
                    "artifact_type": "edit_result_v1",
                    "payload": {"path": path, "message": result},
                },
            )
        except Exception as e:
            return CapabilityResult(
                status="FAILED",
                error_code="EDIT_FILE_ERROR",
                error_message=str(e),
            )
